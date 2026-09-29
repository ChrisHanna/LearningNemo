"""Anonymous browser personas for the brokered public Azure demo."""

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


class PublicDemoAuthManager:
    """Keep one opaque guest subject isolated to one server-side browser session."""

    brokered = True

    def __init__(self) -> None:
        self._user: AuthenticatedUser | None = None
        self._expires_at: float | None = None
        self._subject = secrets.token_hex(32)
        self._fingerprint = hashlib.sha256(self._subject.encode()).hexdigest()[:8].upper()

    @property
    def broker_subject(self) -> str:
        return self._subject

    def snapshot(self) -> dict:
        if self._user is not None and self._expires_at is not None and self._expires_at <= time.time():
            self.clear()
        user = self._user
        return {
            "status": "authenticated" if user else "signed_out",
            "authMode": "public-demo",
            "persona": user.persona if user else None,
            "expectedScopes": ["agent.invoke", "tasks.read", "tasks.execute", "plans.review"],
            "acceptedRoles": ["Task.Operator", "Task.Approver"],
            "grantedScopes": sorted(user.scopes) if user else [],
            "grantedRoles": sorted(user.roles) if user else [],
            "accountFingerprint": self._fingerprint if user else None,
            "storageKey": hashlib.sha256(("public-demo:" + self._subject).encode()).hexdigest() if user else None,
            "userCode": None,
            "verificationUri": None,
            "expiresAt": self._expires_at,
            "error": None,
        }

    def start(self, persona: Role | None = None) -> dict:
        if persona not in _ROLE_ACCESS:
            raise RuntimeError("Choose the Operator or Approver demo role")
        scopes, roles = _ROLE_ACCESS[persona]
        self._expires_at = time.time() + 4 * 60 * 60
        self._user = AuthenticatedUser(
            persona=persona,
            access_token="public-demo-broker",
            scopes=scopes,
            roles=roles,
            account_fingerprint=self._fingerprint,
        )
        return self.snapshot()

    def start_review(self) -> dict:
        if self.current_user().persona != "approver":
            raise RuntimeError("Approver account required")
        return self.snapshot()

    def current_user(self) -> AuthenticatedUser:
        self.snapshot()
        if self._user is None:
            raise RuntimeError("Choose a demo role before continuing")
        return self._user

    def clear(self) -> dict:
        self._user = None
        self._expires_at = None
        return self.snapshot()
