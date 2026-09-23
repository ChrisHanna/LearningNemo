"""Private human-review API. No workspace commands or remediation execution."""

from datetime import UTC, datetime
import hashlib
import os
import time
from uuid import UUID
import uvicorn
from typing import Protocol

from fastapi import Depends, FastAPI, HTTPException, Path
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from task_agent.console.cloud_auth import CloudJwtProvider
from task_agent.console.identity import EntraTestSettings, validate_signed_in_user
from task_agent.control.operations import OperationDeniedError
from task_agent.control.review import ReviewDecision, SqlReviewRepository
from task_agent.control.sql_backend import SqlProcedureUnavailableError


class ReviewIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)
    subject_hash: str
    persona: str
    scopes: frozenset[str] = frozenset()


class ReviewVerifier(Protocol):
    async def verify(self, token: str) -> ReviewIdentity: ...


class EntraReviewVerifier:
    def __init__(self, settings: EntraTestSettings):
        self.provider = CloudJwtProvider(settings)
        self.settings = settings

    async def verify(self, token: str) -> ReviewIdentity:
        verified = await self.provider.verify(token)
        if not verified.active or not verified.subject or verified.client_id != self.settings.public_client_id:
            raise ValueError("review client denied")
        if not isinstance(verified.iat, (int, float)) or not 0 <= time.time() - verified.iat <= 900:
            raise ValueError("review token is stale")
        profile = validate_signed_in_user(token)
        if "agent.invoke" not in profile.scopes:
            raise ValueError("review scope missing")
        if profile.persona == "approver" and "plans.review" not in profile.scopes:
            raise ValueError("approval-specific scope missing")
        if verified.tenant_id != self.settings.tenant_id or not verified.object_id:
            raise ValueError("stable tenant and object identity required")
        subject = f"entra-tenant-oid-v1:{UUID(verified.tenant_id)}:{UUID(verified.object_id)}"
        return ReviewIdentity(subject_hash=hashlib.sha256(subject.encode()).hexdigest(), persona=profile.persona, scopes=frozenset(profile.scopes))


def create_review_app(repository: SqlReviewRepository, verifier: ReviewVerifier, *, expires_at: datetime | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    bearer = HTTPBearer(auto_error=False)

    async def identity(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> ReviewIdentity:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="An assigned Entra account is required")
        try:
            return await verifier.verify(credentials.credentials)
        except Exception as error:
            raise HTTPException(status_code=401, detail="Entra review identity verification failed") from error

    @app.middleware("http")
    async def no_store(request, call_next):
        if expires_at is not None and datetime.now(UTC) >= expires_at and request.url.path != "/healthz":
            return JSONResponse({"detail": "Review service lease expired; no decision accepted"}, status_code=503, headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready():
        try:
            await repository.list_plans()
        except (SqlProcedureUnavailableError, OperationDeniedError) as error:
            raise HTTPException(status_code=503, detail="Review repository unavailable") from error
        return {"status": "ready"}

    @app.get("/reviews")
    async def list_reviews(user: ReviewIdentity = Depends(identity)):
        if user.persona not in {"operator", "approver"}:
            raise HTTPException(status_code=403, detail="Operator or Approver account required")
        try:
            records = await repository.list_plans()
            return {"source": "azure-sql", "plans": [
                {**record.model_dump(mode="json"), "canDecide": user.persona == "approver"
                 and record.plan.state == "awaiting_approval"
                 and record.plan.created_by_hash != user.subject_hash
                 and record.expires_at > datetime.now(UTC)} for record in records
            ]}
        except (SqlProcedureUnavailableError, OperationDeniedError) as error:
            raise HTTPException(status_code=503, detail="Review repository unavailable; no empty queue is assumed") from error

    @app.post("/reviews/{plan_id}/decision")
    async def decide_review(decision: ReviewDecision,
                            plan_id: str = Path(pattern=r"^plan-[A-Za-z0-9-]{1,120}$"),
                            user: ReviewIdentity = Depends(identity)):
        if user.persona != "approver":
            raise HTTPException(status_code=403, detail="A separate Approver account is required")
        try:
            return await repository.decide(plan_id, decision, reviewer_hash=user.subject_hash, now=datetime.now(UTC))
        except OperationDeniedError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except SqlProcedureUnavailableError as error:
            raise HTTPException(status_code=503, detail="Decision could not be confirmed; refresh before retrying") from error

    return app


def deployed_review_app() -> FastAPI:
    from task_agent.control.mssql_client import MssqlProcedureClient
    expiry = datetime.fromisoformat(os.environ["LEARNINGNEMO_REVIEW_EXPIRES_AT"].replace("Z", "+00:00"))
    if expiry.tzinfo is None or not 0 < (expiry - datetime.now(UTC)).total_seconds() <= 7200:
        raise ValueError("Review service needs an explicit lease of at most two hours")
    client = MssqlProcedureClient(
        server=os.environ["LEARNINGNEMO_SQL_SERVER"], database=os.environ["LEARNINGNEMO_SQL_DATABASE"],
        client_id=os.environ["AZURE_CLIENT_ID"], application_name="LearningNeMoHumanReview",
        query_timeout_seconds=15,
    )
    return create_review_app(SqlReviewRepository(client), EntraReviewVerifier(EntraTestSettings.from_sources()), expires_at=expiry)


def main() -> None:
    uvicorn.run(deployed_review_app(), host="0.0.0.0", port=8080, workers=1, proxy_headers=False)


if __name__ == "__main__":
    main()