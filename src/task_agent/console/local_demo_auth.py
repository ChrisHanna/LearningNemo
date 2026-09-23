"""Loopback-only identities for the deterministic local invoice demo."""

from __future__ import annotations

import hashlib
import secrets
import time

from task_agent.console.identity import Role
from task_agent.console.sessions import AuthenticatedUser


_ROLE_ACCESS = {
    "operator": (
        frozenset({"agent.invoke", "tasks.read", "tasks.execute"}),
        frozenset({"Task.Reader", "Task.Operator"}),
    ),
    "approver": (
        frozenset({"agent.invoke", "plans.review"}),
        frozenset({"Task.Approver"}),
    ),
}


class LocalDemoAuthManager:
    """Create isolated demo identities without accepting arbitrary claims."""

    def __init__(self) -> None:
        self._user: AuthenticatedUser | None = None
        self._expires_at: float | None = None
        self._salt = secrets.token_bytes(16)

    def snapshot(self) -> dict:
        if self._user is not None and self._expires_at is not None and self._expires_at <= time.time():
            self.clear()
        user = self._user
        return {
            "status": "authenticated" if user else "signed_out",
            "authMode": "local-demo",
            "persona": user.persona if user else None,
            "expectedScopes": ["agent.invoke", "tasks.read", "tasks.execute", "plans.review"],
            "acceptedRoles": ["Task.Operator", "Task.Approver"],
            "grantedScopes": sorted(user.scopes) if user else [],
            "grantedRoles": sorted(user.roles) if user else [],
            "accountFingerprint": user.account_fingerprint if user else None,
            "storageKey": hashlib.sha256(("local-demo:" + user.persona).encode()).hexdigest() if user else None,
            "userCode": None,
            "verificationUri": None,
            "expiresAt": self._expires_at,
            "error": None,
        }

    def start(self, persona: Role | None = None) -> dict:
        if persona not in _ROLE_ACCESS:
            raise RuntimeError("Choose the Operator or Approver demo account")
        scopes, roles = _ROLE_ACCESS[persona]
        fingerprint = hashlib.sha256(self._salt + persona.encode()).hexdigest()[:8].upper()
        self._expires_at = time.time() + 4 * 60 * 60
        self._user = AuthenticatedUser(
            persona=persona,
            access_token=f"local-demo-{persona}",
            scopes=scopes,
            roles=roles,
            account_fingerprint=fingerprint,
        )
        return self.snapshot()

    def start_review(self) -> dict:
        if self.current_user().persona != "approver":
            raise RuntimeError("Approver account required")
        return self.snapshot()

    def current_user(self) -> AuthenticatedUser:
        self.snapshot()
        if self._user is None:
            raise RuntimeError("Sign in before using the demo")
        return self._user

    def clear(self) -> dict:
        self._user = None
        self._expires_at = None
        return self.snapshot()