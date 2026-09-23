"""Entra JWT validation for the small cloud dashboard/controller image."""

import asyncio
from types import SimpleNamespace

import jwt

from task_agent.console.identity import EntraTestSettings
from task_agent.console.live_workspace import WorkspaceIdentityVerifier, WorkspaceLiveError


class CloudJwtProvider:
    def __init__(self, settings: EntraTestSettings) -> None:
        self.issuer = f"https://login.microsoftonline.com/{settings.tenant_id}/v2.0"
        self.audience = settings.api_client_id
        self.keys = jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{settings.tenant_id}/discovery/v2.0/keys",
            cache_keys=True, timeout=10,
        )

    async def verify(self, token: str):
        def decode():
            key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, key.key, algorithms=["RS256"], audience=self.audience, issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "iss", "aud", "azp"]},
            )
            return SimpleNamespace(active=True, subject=claims["sub"], client_id=claims["azp"], iat=claims["iat"], tenant_id=claims.get("tid"), object_id=claims.get("oid"))
        try:
            return await asyncio.to_thread(decode)
        except Exception as error:
            raise WorkspaceLiveError("Entra token verification failed") from error


class CloudIdentityVerifier(WorkspaceIdentityVerifier):
    def __init__(self, settings: EntraTestSettings) -> None:
        self.settings = settings
        self.provider = CloudJwtProvider(settings)