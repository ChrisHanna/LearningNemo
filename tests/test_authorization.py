from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from unittest.mock import patch
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest
import yaml
from starlette.datastructures import Headers

from nat.data_models.authentication import TokenValidationResult
from task_agent.security.authorization import AuthenticationRequiredError
from task_agent.security.authorization import EntraAuthenticationConfig
from task_agent.security.authorization import EntraAuthenticationMiddleware
from task_agent.security.authorization import InsufficientRoleError
from task_agent.security.authorization import InsufficientScopeError
from task_agent.security.authorization import RequireAccessConfig
from task_agent.security.authorization import RequireAccessMiddleware
from task_agent.security.authorization import VerifiedPrincipal
from task_agent.security.authorization import _current_principal
from task_agent.security.authorization import get_verified_principal
from task_agent.security.policies import TOOL_ACCESS_POLICIES
from nat.middleware.middleware import FunctionMiddlewareContext


def _jwt(
    subject: str = "user-1",
    roles: tuple[str, ...] = ("Task.Reader",),
    scopes: tuple[str, ...] | None = ("agent.invoke", "tasks.read"),
) -> str:
    import base64
    import json

    claims: dict[str, object] = {"sub": subject, "roles": list(roles)}
    if scopes is not None:
        claims["scp"] = " ".join(scopes)
    payload = base64.urlsafe_b64encode(
        json.dumps(claims).encode()
    ).decode().rstrip("=")
    return f"header.{payload}.signature"


def _middleware_context() -> FunctionMiddlewareContext:
    return FunctionMiddlewareContext(
        name="test_tool",
        config=None,
        description=None,
        input_schema=None,
        single_output_schema=type(None),
        stream_output_schema=type(None),
    )


def test_configured_tool_access_matches_explained_policies() -> None:
    config_path = Path(__file__).parents[1] / "configs" / "agent.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    configured_tools = set(config["workflow"]["tool_names"])
    assert configured_tools == set(config["functions"]) == set(TOOL_ACCESS_POLICIES)

    for tool_name in configured_tools:
        policy = TOOL_ACCESS_POLICIES[tool_name]
        middleware_name = config["functions"][tool_name]["middleware"][0]
        middleware = config["middleware"][middleware_name]
        assert middleware["_type"] == "require_access"
        assert set(middleware["required_scopes"]) == policy.scopes
        assert set(middleware["required_roles"]) == policy.roles


async def test_scope_middleware_allows_required_scope() -> None:
    middleware = RequireAccessMiddleware(
        RequireAccessConfig(required_scopes=["tasks.read"], required_roles=["Task.Reader"])
    )
    principal_token = _current_principal.set(
        VerifiedPrincipal(
            subject="user-1",
            scopes=frozenset({"tasks.read"}),
            roles=frozenset({"Task.Reader"}),
            client_id="client-1",
            request_id="request-allow",
        )
    )
    try:
        call_next = AsyncMock(return_value="allowed")
        result = await middleware.function_middleware_invoke(
            "input",
            call_next=call_next,
            context=_middleware_context(),
        )
        assert result == "allowed"
        call_next.assert_awaited_once()
    finally:
        _current_principal.reset(principal_token)


async def test_scope_middleware_denies_missing_scope_without_calling_tool() -> None:
    middleware = RequireAccessMiddleware(
        RequireAccessConfig(required_scopes=["tasks.execute"], required_roles=["Task.Operator"])
    )
    principal_token = _current_principal.set(
        VerifiedPrincipal(
            subject="user-1",
            scopes=frozenset({"tasks.read"}),
            roles=frozenset({"Task.Reader"}),
            client_id="client-1",
            request_id="request-deny-scope",
        )
    )
    try:
        call_next = AsyncMock()
        with pytest.raises(InsufficientScopeError) as error:
            await middleware.function_middleware_invoke(
                "input",
                call_next=call_next,
                context=_middleware_context(),
            )
        assert error.value.missing_scopes == frozenset({"tasks.execute"})
        call_next.assert_not_awaited()
    finally:
        _current_principal.reset(principal_token)


async def test_access_middleware_denies_missing_role_even_with_execute_scope() -> None:
    middleware = RequireAccessMiddleware(
        RequireAccessConfig(required_scopes=["tasks.execute"], required_roles=["Task.Operator"])
    )
    principal_token = _current_principal.set(
        VerifiedPrincipal(
            subject="reader-user",
            scopes=frozenset({"tasks.execute"}),
            roles=frozenset({"Task.Reader"}),
            client_id="learningnemo-client",
            request_id="request-123",
        )
    )
    try:
        call_next = AsyncMock()
        with pytest.raises(InsufficientRoleError) as error:
            await middleware.function_middleware_invoke(
                "input",
                call_next=call_next,
                context=_middleware_context(),
            )
        assert error.value.missing_roles == frozenset({"Task.Operator"})
        call_next.assert_not_awaited()
    finally:
        _current_principal.reset(principal_token)


async def test_scope_middleware_denies_missing_identity() -> None:
    middleware = RequireAccessMiddleware(
        RequireAccessConfig(required_scopes=["tasks.read"], required_roles=["Task.Reader"])
    )
    call_next = AsyncMock()
    with pytest.raises(AuthenticationRequiredError):
        await middleware.function_middleware_invoke(
            "input",
            call_next=call_next,
            context=_middleware_context(),
        )
    call_next.assert_not_awaited()


async def test_authentication_establishes_and_clears_verified_principal() -> None:
    provider = MagicMock()
    provider.verify = AsyncMock(
        return_value=TokenValidationResult(
            client_id="public-client",
            scopes=["agent.invoke", "tasks.read"],
            subject="user-1",
            token_type="bearer",
            active=True,
            iat=int(time.time()),
        )
    )
    middleware = EntraAuthenticationMiddleware(
        EntraAuthenticationConfig(
            auth_provider="entra_jwt",
            allowed_client_ids=["public-client"],
            max_token_age_seconds=900,
            required_scopes=["agent.invoke"],
            required_roles=["Task.Reader"],
        ),
        provider,
    )
    nat_context = MagicMock()
    nat_context.metadata.headers = Headers({"Authorization": f"Bearer {_jwt()}"})

    async def call_next(value: str) -> str:
        principal = get_verified_principal()
        assert principal.subject == "user-1"
        assert principal.scopes == frozenset({"agent.invoke", "tasks.read"})
        assert principal.roles == frozenset({"Task.Reader"})
        return value

    with patch("task_agent.security.authorization.Context.get", return_value=nat_context):
        result = await middleware.function_middleware_invoke(
            "allowed",
            call_next=call_next,
            context=_middleware_context(),
        )

    assert result == "allowed"
    provider.verify.assert_awaited_once_with(_jwt())
    with pytest.raises(AuthenticationRequiredError):
        get_verified_principal()


async def test_authentication_denies_missing_workflow_scope() -> None:
    provider = MagicMock()
    provider.verify = AsyncMock(
        return_value=TokenValidationResult(
            client_id="public-client",
            scopes=["agent.invoke", "tasks.read"],
            subject="user-1",
            token_type="bearer",
            active=True,
            iat=int(time.time()),
        )
    )
    middleware = EntraAuthenticationMiddleware(
        EntraAuthenticationConfig(
            auth_provider="entra_jwt",
            allowed_client_ids=["public-client"],
            max_token_age_seconds=900,
            required_scopes=["agent.invoke"],
            required_roles=["Task.Reader"],
        ),
        provider,
    )
    nat_context = MagicMock()
    nat_context.metadata.headers = Headers({
        "Authorization": f"Bearer {_jwt(scopes=("tasks.read",))}"
    })
    call_next = AsyncMock()

    with patch("task_agent.security.authorization.Context.get", return_value=nat_context):
        with pytest.raises(InsufficientScopeError) as error:
            await middleware.function_middleware_invoke(
                "blocked",
                call_next=call_next,
                context=_middleware_context(),
            )
            assert error.value.missing_scopes == frozenset({"agent.invoke"})

    call_next.assert_not_awaited()


async def test_authentication_denies_missing_scope_claim() -> None:
    provider = MagicMock()
    provider.verify = AsyncMock(
        return_value=TokenValidationResult(
            client_id="public-client",
            scopes=["agent.invoke"],
            subject="user-1",
            token_type="bearer",
            active=True,
            iat=int(time.time()),
        )
    )
    middleware = EntraAuthenticationMiddleware(
        EntraAuthenticationConfig(
            auth_provider="entra_jwt",
            allowed_client_ids=["public-client"],
            max_token_age_seconds=900,
            required_scopes=["agent.invoke"],
            required_roles=["Task.Reader"],
        ),
        provider,
    )
    nat_context = MagicMock()
    nat_context.metadata.headers = Headers({
        "Authorization": f"Bearer {_jwt(scopes=None)}"
    })
    call_next = AsyncMock()

    with patch("task_agent.security.authorization.Context.get", return_value=nat_context):
        with pytest.raises(AuthenticationRequiredError, match="missing the required scp claim"):
            await middleware.function_middleware_invoke(
                "blocked",
                call_next=call_next,
                context=_middleware_context(),
            )

    call_next.assert_not_awaited()


async def test_authentication_denies_unapproved_calling_client() -> None:
    provider = MagicMock()
    provider.verify = AsyncMock(
        return_value=TokenValidationResult(
            client_id="different-client",
            scopes=["agent.invoke"],
            subject="user-1",
            token_type="bearer",
            active=True,
            iat=int(time.time()),
        )
    )
    middleware = EntraAuthenticationMiddleware(
        EntraAuthenticationConfig(
            auth_provider="entra_jwt",
            allowed_client_ids=["learningnemo-client"],
            max_token_age_seconds=900,
            required_scopes=["agent.invoke"],
            required_roles=["Task.Reader"],
        ),
        provider,
    )
    nat_context = MagicMock()
    nat_context.metadata.headers = Headers({"Authorization": f"Bearer {_jwt()}"})
    call_next = AsyncMock()

    with patch("task_agent.security.authorization.Context.get", return_value=nat_context):
        with pytest.raises(AuthenticationRequiredError, match="unauthorized client"):
            await middleware.function_middleware_invoke(
                "blocked",
                call_next=call_next,
                context=_middleware_context(),
            )

    call_next.assert_not_awaited()


async def test_authentication_denies_token_older_than_maximum_age() -> None:
    provider = MagicMock()
    provider.verify = AsyncMock(
        return_value=TokenValidationResult(
            client_id="learningnemo-client",
            scopes=["agent.invoke"],
            subject="user-1",
            token_type="bearer",
            active=True,
            iat=int(time.time()) - 901,
        )
    )
    middleware = EntraAuthenticationMiddleware(
        EntraAuthenticationConfig(
            auth_provider="entra_jwt",
            allowed_client_ids=["learningnemo-client"],
            max_token_age_seconds=900,
            required_scopes=["agent.invoke"],
            required_roles=["Task.Reader"],
        ),
        provider,
    )
    nat_context = MagicMock()
    nat_context.metadata.headers = Headers({"Authorization": f"Bearer {_jwt()}"})

    with patch("task_agent.security.authorization.Context.get", return_value=nat_context):
        with pytest.raises(AuthenticationRequiredError, match="too old"):
            await middleware.function_middleware_invoke(
                "blocked",
                call_next=AsyncMock(),
                context=_middleware_context(),
            )


async def test_access_decision_emits_structured_pseudonymous_audit_event(caplog) -> None:
    middleware = RequireAccessMiddleware(
        RequireAccessConfig(required_scopes=["tasks.read"], required_roles=["Task.Reader"])
    )
    principal_token = _current_principal.set(
        VerifiedPrincipal(
            subject="sensitive-subject-id",
            scopes=frozenset({"tasks.read"}),
            roles=frozenset({"Task.Reader"}),
            client_id="learningnemo-client",
            request_id="request-123",
        )
    )
    try:
        with caplog.at_level(logging.INFO, logger="task_agent.security.authorization"):
            await middleware.function_middleware_invoke(
                "input",
                call_next=AsyncMock(return_value="allowed"),
                context=_middleware_context(),
            )
    finally:
        _current_principal.reset(principal_token)

    record = next(record for record in caplog.records if record.message.startswith("authorization_decision "))
    event = json.loads(record.message.removeprefix("authorization_decision "))
    assert event["outcome"] == "allow"
    assert event["resource"] == "test_tool"
    assert event["client_id"] == "learningnemo-client"
    assert event["request_id"]
    assert event["subject_hash"] != "sensitive-subject-id"
    assert "sensitive-subject-id" not in record.message