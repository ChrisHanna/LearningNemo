"""Server-side Microsoft Entra device-flow sessions for the local console."""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any
from typing import Literal

import msal

from task_agent.console.identity import CLIENT_SCOPES
from task_agent.console.identity import AccessProfileError
from task_agent.console.identity import EntraTestSettings
from task_agent.console.identity import Role
from task_agent.console.identity import access_token_claims
from task_agent.console.identity import validate_signed_in_user


AuthStatus = Literal["signed_out", "pending", "authenticated", "expired", "error"]
MAX_TOKEN_AGE_SECONDS = 15 * 60
logger = logging.getLogger(__name__)


@dataclass
class AuthSession:
    status: AuthStatus = "signed_out"
    persona: Role | None = None
    flow_id: str | None = None
    user_code: str | None = None
    verification_uri: str | None = None
    expires_at: float | None = None
    access_token: str | None = None
    granted_scopes: tuple[str, ...] = ()
    granted_roles: tuple[str, ...] = ()
    account_fingerprint: str | None = None
    storage_key: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class AuthenticatedUser:
    persona: Role
    access_token: str
    scopes: frozenset[str]
    roles: frozenset[str]
    account_fingerprint: str | None


class DeviceAuthManager:
    """Keep short-lived tokens in memory and return only browser-safe metadata."""

    def __init__(
        self,
        settings: EntraTestSettings,
        app: msal.PublicClientApplication | None = None,
        *,
        review_enabled: bool = False,
    ) -> None:
        self._settings = settings
        self._review_enabled = review_enabled
        self._default_scopes = (*CLIENT_SCOPES, "plans.review") if review_enabled else CLIENT_SCOPES
        self._requested_scopes = self._default_scopes
        self._app = app or settings.create_public_client_app()
        self._session = AuthSession()
        self._lock = threading.Lock()
        self._fingerprint_salt = secrets.token_bytes(16)

    def _expire_if_needed(self, session: AuthSession) -> None:
        if session.status in ("pending", "authenticated") and session.expires_at and session.expires_at <= time.time():
            session.status = "expired"
            session.access_token = None
            session.flow_id = None
            session.user_code = None
            session.verification_uri = None

    def _public_session(self, session: AuthSession) -> dict[str, Any]:
        self._expire_if_needed(session)
        return {
            "status": session.status,
            "persona": session.persona,
            "expectedScopes": list(self._requested_scopes),
            "acceptedRoles": ["Task.Reader", "Task.Operator", "Task.Approver"],
            "grantedScopes": list(session.granted_scopes),
            "grantedRoles": list(session.granted_roles),
            "accountFingerprint": session.account_fingerprint,
            "storageKey": session.storage_key if session.status == 'authenticated' else None,
            "userCode": session.user_code if session.status == "pending" else None,
            "verificationUri": session.verification_uri if session.status == "pending" else None,
            "expiresAt": session.expires_at,
            "error": session.error,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._public_session(self._session)

    def start(self, persona: Role | None = None) -> dict[str, Any]:
        if persona is not None:
            raise RuntimeError("Microsoft sign-in does not accept a browser-selected role")
        with self._lock:
            current = self._session
            self._expire_if_needed(current)
            if current.status in ("pending", "authenticated"):
                return self._public_session(current)

        scopes = [f"api://{self._settings.api_client_id}/{scope}" for scope in self._requested_scopes]
        flow = self._app.initiate_device_flow(scopes=scopes)
        if "user_code" not in flow:
            raise RuntimeError("Microsoft Entra did not return a device-code flow")

        flow_id = uuid.uuid4().hex
        session = AuthSession(
            status="pending",
            flow_id=flow_id,
            user_code=str(flow["user_code"]),
            verification_uri=str(flow.get("verification_uri_complete") or flow["verification_uri"]),
            expires_at=float(flow.get("expires_at") or time.time() + 900),
        )
        with self._lock:
            self._session = session

        thread = threading.Thread(target=self._poll, args=(flow_id, flow), daemon=True)
        thread.start()
        return self.snapshot()

    def start_review(self) -> dict[str, Any]:
        if not self._review_enabled:
            raise RuntimeError("Review service is not configured")
        user = self.current_user()
        if user.persona != "approver":
            raise AccessProfileError("A separately assigned Approver is required")
        if "plans.review" in user.scopes:
            return self.snapshot()
        self.clear()
        self._requested_scopes = ("agent.invoke", "plans.review")
        return self.start()

    def _poll(self, flow_id: str, flow: dict[str, Any]) -> None:
        try:
            token_result = self._app.acquire_token_by_device_flow(flow)
            access_token = token_result.get("access_token")
            if not access_token:
                raise RuntimeError(token_result.get("error_description", "Authentication failed"))
            access_token = str(access_token)
            profile = validate_signed_in_user(access_token)
            claims = access_token_claims(access_token)
            expiration = claims.get("exp")
            expires_at = float(expiration) if isinstance(expiration, (int, float)) else time.time() + 3600
            issued_at = claims.get("iat")
            if isinstance(issued_at, (int, float)):
                expires_at = min(expires_at, float(issued_at) + MAX_TOKEN_AGE_SECONDS)
            account_id = claims.get("oid") or claims.get("sub")
            account_fingerprint = (
                hashlib.sha256(self._fingerprint_salt + str(account_id).encode()).hexdigest()[:8].upper()
                if account_id
                else None
            )
            storage_key = hashlib.sha256(('learningnemo-browser-state-v1:' + self._settings.tenant_id + ':' + self._settings.api_client_id + ':' + str(account_id)).encode()).hexdigest() if account_id else None
        except AccessProfileError as error:
            logger.warning("entra_profile_rejected: %s", error)
            with self._lock:
                session = self._session
                if session.flow_id == flow_id:
                    session.status = "error"
                    session.error = "This account is not authorized for LearningNeMo. Use an assigned Reader, Operator, or separate Approver account."
                    session.access_token = None
            return
        except Exception:
            logger.exception("entra_device_flow_failed")
            with self._lock:
                session = self._session
                if session.flow_id == flow_id:
                    session.status = "error"
                    session.error = "Authentication failed. Try signing in again."
                    session.access_token = None
            return

        with self._lock:
            session = self._session
            self._expire_if_needed(session)
            if session.flow_id != flow_id:
                return
            session.status = "authenticated"
            session.persona = profile.persona
            session.user_code = None
            session.verification_uri = None
            session.expires_at = expires_at
            session.access_token = access_token
            session.granted_scopes = tuple(sorted(profile.scopes))
            session.granted_roles = tuple(sorted(profile.roles))
            session.account_fingerprint = account_fingerprint
            session.storage_key = storage_key
            session.error = None

    def current_user(self) -> AuthenticatedUser:
        """Return the role-derived current user or fail closed."""
        with self._lock:
            session = self._session
            self._expire_if_needed(session)
            if session.status != "authenticated" or not session.access_token or not session.persona:
                raise RuntimeError("Sign in before calling the agent")
            return AuthenticatedUser(
                persona=session.persona,
                access_token=session.access_token,
                scopes=frozenset(session.granted_scopes),
                roles=frozenset(session.granted_roles),
                account_fingerprint=session.account_fingerprint,
            )

    def clear(self) -> dict[str, Any]:
        with self._lock:
            self._session = AuthSession()
            self._requested_scopes = self._default_scopes
            return self._public_session(self._session)