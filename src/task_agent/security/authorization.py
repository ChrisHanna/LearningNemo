"""Request-scoped Entra authentication and tool authorization."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import logging
import time
from typing import Any
import uuid

from pydantic import Field

from nat.authentication.jwt.jwt_auth_provider import JwtAuthProvider
from nat.builder.builder import Builder
from nat.builder.context import Context
from nat.cli.register_workflow import register_middleware
from nat.data_models.component_ref import AuthenticationRef
from nat.data_models.middleware import FunctionMiddlewareBaseConfig
from nat.middleware.function_middleware import FunctionMiddleware
from nat.middleware.middleware import CallNext
from nat.middleware.middleware import CallNextStream
from nat.middleware.middleware import FunctionMiddlewareContext
from nat.middleware.middleware import InvocationContext

from task_agent.security.jwt_claims import decode_jwt_payload
from task_agent.security.jwt_claims import string_claim_values


logger = logging.getLogger(__name__)


class AuthenticationRequiredError(PermissionError):
    """Raised when a request has no valid bearer identity."""


class InsufficientScopeError(PermissionError):
    """Raised when a verified identity lacks a required scope."""

    def __init__(self, missing_scopes: set[str]) -> None:
        self.missing_scopes = frozenset(missing_scopes)
        super().__init__("Insufficient permissions")


class InsufficientRoleError(PermissionError):
    """Raised when a verified identity lacks a required app role."""

    def __init__(self, missing_roles: set[str]) -> None:
        self.missing_roles = frozenset(missing_roles)
        super().__init__("Insufficient permissions")


@dataclass(frozen=True)
class VerifiedPrincipal:
    """Minimal verified identity used for authorization decisions."""

    subject: str
    scopes: frozenset[str]
    roles: frozenset[str]
    client_id: str | None
    request_id: str
    read_only: bool = False


_current_principal: ContextVar[VerifiedPrincipal | None] = ContextVar(
    "task_agent_verified_principal",
    default=None,
)


def get_verified_principal() -> VerifiedPrincipal:
    """Return the request's verified principal or fail closed."""
    principal = _current_principal.get()
    if principal is None:
        raise AuthenticationRequiredError("An authenticated Entra identity is required")
    return principal


def _extract_bearer_token() -> str:
    headers = Context.get().metadata.headers
    authorization = headers.get("authorization") if headers is not None else None
    if not authorization:
        raise AuthenticationRequiredError("A bearer access token is required")

    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationRequiredError("A valid bearer access token is required")
    return token.strip()


def _require_access(
    principal: VerifiedPrincipal,
    required_scopes: set[str],
    required_roles: set[str],
    resource: str,
) -> None:
    missing_scopes = required_scopes - principal.scopes
    if principal.read_only:
        missing_scopes |= required_scopes & {'tasks.execute'}
    missing_roles = required_roles - principal.roles
    _audit_authorization_decision(
        principal=principal,
        resource=resource,
        required_scopes=required_scopes,
        required_roles=required_roles,
        missing_scopes=missing_scopes,
        missing_roles=missing_roles,
    )
    if missing_scopes:
        raise InsufficientScopeError(missing_scopes)
    if missing_roles:
        raise InsufficientRoleError(missing_roles)


def _audit_authorization_decision(
    principal: VerifiedPrincipal,
    resource: str,
    required_scopes: set[str],
    required_roles: set[str],
    missing_scopes: set[str],
    missing_roles: set[str],
) -> None:
    event = {
        "event": "authorization_decision",
        "request_id": principal.request_id,
        "subject_hash": hashlib.sha256(principal.subject.encode()).hexdigest()[:16],
        "client_id": principal.client_id,
        "resource": resource,
        "outcome": "deny" if missing_scopes or missing_roles else "allow",
        "required_scopes": sorted(required_scopes),
        "required_roles": sorted(required_roles),
        "granted_scopes": sorted(principal.scopes),
        "granted_roles": sorted(principal.roles),
        "missing_scopes": sorted(missing_scopes),
        "missing_roles": sorted(missing_roles),
    }
    log = logger.warning if event["outcome"] == "deny" else logger.info
    log("authorization_decision %s", json.dumps(event, separators=(",", ":")))


class EntraAuthenticationConfig(FunctionMiddlewareBaseConfig, name="entra_authentication"):
    """Validate the incoming Entra token and establish a request principal."""

    auth_provider: AuthenticationRef
    allowed_client_ids: list[str] = Field(min_length=1)
    max_token_age_seconds: int = Field(gt=0, le=3600)
    required_scopes: list[str] = Field(min_length=1)
    required_roles: list[str] = Field(min_length=1)


class RequireAccessConfig(FunctionMiddlewareBaseConfig, name="require_access"):
    """Require delegated scopes and app roles from the verified principal."""

    required_scopes: list[str] = Field(min_length=1)
    required_roles: list[str] = Field(min_length=1)


class EntraAuthenticationMiddleware(FunctionMiddleware):
    """Validate one access token for the duration of a workflow invocation."""

    def __init__(self, config: EntraAuthenticationConfig, provider: JwtAuthProvider) -> None:
        super().__init__(is_final=False)
        self._required_scopes = set(config.required_scopes)
        self._required_roles = set(config.required_roles)
        self._allowed_client_ids = set(config.allowed_client_ids)
        self._max_token_age_seconds = config.max_token_age_seconds
        self._provider = provider

    async def _authenticate(self) -> VerifiedPrincipal:
        access_token = _extract_bearer_token()
        result = await self._provider.verify(access_token)
        if not result.active or not result.subject:
            raise AuthenticationRequiredError("The bearer access token is invalid")
        if not result.client_id or result.client_id not in self._allowed_client_ids:
            raise AuthenticationRequiredError("The bearer access token was issued to an unauthorized client")
        if result.iat is None or time.time() - result.iat > self._max_token_age_seconds:
            raise AuthenticationRequiredError("The bearer access token is too old; sign in again")

        try:
            claims = decode_jwt_payload(access_token)
            if claims.get("sub") != result.subject:
                raise ValueError("The verified subject does not match the token payload")
            scopes = string_claim_values(claims, "scp")
            roles = string_claim_values(claims, "roles")
        except ValueError as error:
            raise AuthenticationRequiredError(str(error)) from error

        principal = VerifiedPrincipal(
            subject=result.subject,
            scopes=scopes,
            roles=roles,
            client_id=result.client_id,
            request_id=(Context.get().metadata.headers or {}).get("x-request-id") or uuid.uuid4().hex,
            read_only=(Context.get().metadata.headers or {}).get("x-learningnemo-read-only") == "true",
        )
        _require_access(
            principal,
            self._required_scopes,
            self._required_roles,
            resource="<workflow>",
        )
        return principal

    async def function_middleware_invoke(
        self,
        *args: Any,
        call_next: CallNext,
        context: FunctionMiddlewareContext,
        **kwargs: Any,
    ) -> Any:
        del context
        principal = await self._authenticate()
        principal_token = _current_principal.set(principal)
        try:
            return await call_next(*args, **kwargs)
        finally:
            _current_principal.reset(principal_token)

    async def function_middleware_stream(
        self,
        *args: Any,
        call_next: CallNextStream,
        context: FunctionMiddlewareContext,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        del context
        principal = await self._authenticate()
        principal_token = _current_principal.set(principal)
        try:
            async for chunk in call_next(*args, **kwargs):
                yield chunk
        finally:
            _current_principal.reset(principal_token)


class RequireAccessMiddleware(FunctionMiddleware):
    """Enforce delegated scopes and user roles before every tool invocation."""

    def __init__(self, config: RequireAccessConfig) -> None:
        super().__init__(is_final=False)
        self._required_scopes = set(config.required_scopes)
        self._required_roles = set(config.required_roles)

    async def pre_invoke(self, context: InvocationContext) -> InvocationContext | None:
        _require_access(
            get_verified_principal(),
            self._required_scopes,
            self._required_roles,
            resource=context.function_context.name,
        )
        return None


@register_middleware(config_type=EntraAuthenticationConfig)
async def entra_authentication_middleware(
    config: EntraAuthenticationConfig,
    builder: Builder,
) -> AsyncGenerator[EntraAuthenticationMiddleware, None]:
    provider = await builder.get_auth_provider(config.auth_provider)
    if not isinstance(provider, JwtAuthProvider):
        raise TypeError("entra_authentication requires a JWT authentication provider")
    yield EntraAuthenticationMiddleware(config=config, provider=provider)


@register_middleware(config_type=RequireAccessConfig)
async def require_access_middleware(
    config: RequireAccessConfig,
    _builder: Builder,
) -> AsyncGenerator[RequireAccessMiddleware, None]:
    yield RequireAccessMiddleware(config=config)