import asyncio
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from task_agent.console.cloud_auth import CloudIdentityVerifier
from task_agent.console.identity import EntraTestSettings
from task_agent.console.live_workspace import WorkspaceLiveError


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.mark.parametrize("variation", ["valid", "signature", "issuer", "audience", "expired", "client", "roles", "approver", "mixed", "approver-reader", "approver-no-scope"])
def test_cloud_identity_verifies_signature_and_claims(signing_key, variation):
    verifier = CloudIdentityVerifier(EntraTestSettings("tenant", "api", "client"))
    verifier.provider.keys = SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=signing_key.public_key()),
    )
    claims = {
        "iss": "https://login.microsoftonline.com/tenant/v2.0", "aud": "api",
        "sub": "user", "azp": "client", "iat": int(time.time()) - 10,
        "exp": int(time.time()) + 120, "scp": "agent.invoke tasks.read tasks.execute",
        "roles": ["Task.Reader", "Task.Operator"],
    }
    if variation == "issuer": claims["iss"] = "https://other.example.test"
    if variation == "audience": claims["aud"] = "other-api"
    if variation == "expired": claims["exp"] = int(time.time()) - 1
    if variation == "client": claims["azp"] = "other-client"
    if variation == "roles": claims["roles"] = ["Task.Reader"]
    if variation in {"approver", "approver-no-scope"}: claims["roles"] = ["Task.Approver"]
    if variation == "approver-reader": claims["roles"] = ["Task.Approver", "Task.Reader"]
    if variation == "mixed": claims["roles"] = ["Task.Approver", "Task.Operator", "Task.Reader"]
    if variation == "approver-no-scope": claims["scp"] = "tasks.read"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if variation == "signature" else signing_key
    token = jwt.encode(claims, key, algorithm="RS256")
    if variation.startswith("approver"):
        decision = asyncio.run(verifier.verify(token))
        assert decision["canReview"] is (variation != "approver-no-scope")
        assert not decision["canRead"] and not decision["allowed"]
    elif variation in {"valid", "roles"}:
        decision = asyncio.run(verifier.verify(token))
        assert decision["canRead"]
        assert decision["allowed"] is (variation == "valid")
    else:
        with pytest.raises(WorkspaceLiveError):
            asyncio.run(verifier.verify(token))