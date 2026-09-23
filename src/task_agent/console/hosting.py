"""Explicit trust boundaries for localhost and a single HTTPS console origin."""

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ConsoleHosting:
    public_origin: str | None = None

    def __post_init__(self) -> None:
        if self.public_origin is None:
            return
        parsed = urlsplit(self.public_origin)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.path or parsed.query or parsed.fragment
            or parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            or "*" in parsed.netloc
        ):
            raise ValueError("Cloud console requires one exact HTTPS origin without a path")

    @property
    def cloud(self) -> bool:
        return self.public_origin is not None

    def accepts_host(self, hostname: str | None) -> bool:
        if self.cloud:
            return hostname == urlsplit(self.public_origin).hostname
        return hostname in {"127.0.0.1", "localhost", "::1", "testserver"}

    def expected_origin(self, request_origin: str) -> str:
        return self.public_origin or request_origin