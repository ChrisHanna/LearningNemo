"""Per-browser server-side sessions for the localhost learning console."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from task_agent.console.identity import EntraTestSettings
from task_agent.console.sessions import DeviceAuthManager


SESSION_COOKIE = "learningnemo_session"
CSRF_HEADER = "X-LearningNeMo-CSRF"


@dataclass
class BrowserSession:
    id: str
    csrf_token: str
    auth: DeviceAuthManager
    last_seen: float


class BrowserSessionRegistry:
    """Map an opaque browser cookie to one isolated device-auth manager."""

    def __init__(
        self,
        settings: EntraTestSettings,
        auth_factory: Callable[[], DeviceAuthManager] | None = None,
        ttl_seconds: int = 4 * 60 * 60,
    ) -> None:
        self._auth_factory = auth_factory or (lambda: DeviceAuthManager(settings))
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str | None) -> BrowserSession | None:
        if not session_id:
            return None
        now = time.time()
        with self._lock:
            self._prune(now)
            session = self._sessions.get(session_id)
            if session is not None:
                session.last_seen = now
            return session

    def create(self) -> BrowserSession:
        now = time.time()
        session = BrowserSession(
            id=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            auth=self._auth_factory(),
            last_seen=now,
        )
        with self._lock:
            self._prune(now)
            self._sessions[session.id] = session
        return session

    def _prune(self, now: float) -> None:
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if now - session.last_seen > self._ttl_seconds
        ]
        for session_id in expired:
            del self._sessions[session_id]