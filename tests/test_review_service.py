from datetime import UTC, datetime
from pathlib import Path
import time
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt
import pytest
from sqlfluff.core import Linter

from task_agent.console.identity import EntraTestSettings
from task_agent.console.review_service import EntraReviewVerifier, ReviewIdentity, create_review_app
from task_agent.control.canonical import subject_hash
from task_agent.control.review import SqlReviewRepository
from task_agent.control.sql_backend import SqlProcedureUnavailableError
from test_review_repository import ReviewClient


class VerifiedFixture:
    async def verify(self, token):
        if token == "invalid":
            raise ValueError("invalid token")
        return ReviewIdentity(persona="approver" if token == "author" else token,
                              subject_hash=subject_hash(token))


@pytest.mark.parametrize("persona,status", [("approver", 200), ("operator", 403), ("reader", 403), ("author", 409), ("invalid", 401)])
def test_review_api_requires_independent_approver(persona, status):
    store = ReviewClient()
    store.expires = datetime.now(UTC).replace(year=2099)
    client = TestClient(create_review_app(SqlReviewRepository(store), VerifiedFixture()))
    response = client.post("/reviews/plan-review/decision", headers={"Authorization": f"Bearer {persona}"},
                           json={"plan_hash": store.plan.plan_hash, "plan_version": 1, "decision": "approve"})
    assert response.status_code == status
    assert bool(store.writes) is (status == 200)
    assert response.headers["Cache-Control"] == "no-store"
    assert "receipt_json" not in response.text


def test_review_service_has_no_anonymous_data_or_execution_route():
    store = ReviewClient()
    client = TestClient(create_review_app(SqlReviewRepository(store), VerifiedFixture()))
    assert client.get("/reviews").status_code == 401
    assert client.post("/reviews/plan-review/execute", headers={"Authorization": "Bearer approver"}).status_code == 404
    assert client.post("/reviews/plan-review/decision", headers={"Authorization": "Bearer approver"},
                       json={"plan_hash": store.plan.plan_hash, "plan_version": 1, "decision": "approve", "reviewer_hash": subject_hash("other")}).status_code == 422
    assert not store.writes


def test_review_api_does_not_turn_sql_failure_into_an_empty_queue():
    class Unavailable:
        async def list_plans(self):
            raise SqlProcedureUnavailableError("private connection details")
    client = TestClient(create_review_app(Unavailable(), VerifiedFixture()))
    response = client.get("/reviews", headers={"Authorization": "Bearer approver"})
    assert response.status_code == 503
    assert "private connection details" not in response.text
    assert "plans" not in response.json()


def test_expired_service_rejects_decisions_and_readiness():
    store = ReviewClient()
    client = TestClient(create_review_app(SqlReviewRepository(store), VerifiedFixture(), expires_at=datetime(2020, 1, 1, tzinfo=UTC)))
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 503
    response = client.post("/reviews/plan-review/decision", headers={"Authorization": "Bearer approver"},
        json={"plan_hash": store.plan.plan_hash, "plan_version": 1, "decision": "approve"})
    assert response.status_code == 503
    assert not store.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("variation", ["valid", "missing_scope", "mixed", "client", "stale", "signature", "audience", "missing_oid", "wrong_tenant"])
async def test_review_jwt_requires_signature_freshness_client_and_review_scope(variation):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tenant = "11111111-1111-4111-8111-111111111111"
    object_id = "22222222-2222-4222-8222-222222222222"
    verifier = EntraReviewVerifier(EntraTestSettings(tenant, "api", "client"))
    verifier.provider.keys = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key()))
    claims = {"iss": f"https://login.microsoftonline.com/{tenant}/v2.0", "aud": "api", "azp": "client", "sub": "reviewer", "tid": tenant, "oid": object_id,
              "iat": int(time.time()) - 10, "exp": int(time.time()) + 120, "scp": "agent.invoke plans.review", "roles": ["Task.Approver"]}
    if variation == "missing_scope": claims["scp"] = "agent.invoke"
    if variation == "mixed": claims["roles"].append("Task.Operator")
    if variation == "client": claims["azp"] = "another-client"
    if variation == "stale": claims["iat"] -= 901
    if variation == "audience": claims["aud"] = "another-api"
    if variation == "missing_oid": del claims["oid"]
    if variation == "wrong_tenant": claims["tid"] = object_id
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if variation == "signature" else key
    token = jwt.encode(claims, signing_key, algorithm="RS256")
    if variation == "valid":
        identity = await verifier.verify(token)
        assert identity.persona == "approver"
        assert identity.subject_hash == subject_hash(f"entra-tenant-oid-v1:{tenant}:{object_id}")
    else:
        with pytest.raises(Exception):
            await verifier.verify(token)


def test_staged_sql_parses_and_preserves_closed_review_boundary():
    path = Path(__file__).parents[1] / "infra/next-phase/review-service/001_review_boundary.sql"
    source = path.read_text()
    linter = Linter(dialect="tsql")
    violations = [str(violation) for batch in source.split("\nGO\n") if batch.strip()
                  for violation in linter.parse_string(batch).violations]
    assert not violations
    assert "WITH (UPDLOCK, HOLDLOCK)" in source
    assert "plan_record.CreatedByHash <> @reviewer_hash" in source
    assert "plan_record.Version = @expected_plan_version" in source
    assert "EXEC control.usp_issue_approval" in source
    assert "SYSUTCDATETIME()" in source
    assert "ops.usp_activate_cycle_safe_query" not in source
    assert "GRANT" not in source and "CREATE USER" not in source