"""Fixed private review-service routes; no SQL access in the dashboard."""

import re
from urllib.parse import urlsplit

import httpx

from task_agent.console.review_contract import ReviewDecision, ReviewQueue


class ReviewUnavailable(RuntimeError):
    pass


class RemoteReviewService:
    def __init__(self, origin: str) -> None:
        parsed = urlsplit(origin)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port or parsed.path or parsed.query or parsed.fragment
                or re.fullmatch(r"[a-z0-9-]+\.internal\.[a-z0-9.-]+\.azurecontainerapps\.io", parsed.hostname) is None):
            raise ValueError("Review service requires an exact private Container Apps HTTPS origin")
        self.origin = origin

    async def _request(self, method: str, path: str, token: str, body: dict | None = None) -> dict:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                response = await client.request(method, self.origin + path,
                    headers={"Authorization": f"Bearer {token}"}, json=body)
            if response.status_code != 200:
                raise ReviewUnavailable("Review service denied or could not confirm this request; refresh before retrying")
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("invalid result")
            return result
        except (httpx.HTTPError, ValueError) as error:
            raise ReviewUnavailable("Review service unavailable; no decision or empty queue is assumed") from error

    async def list_plans(self, token: str) -> dict:
        result = await self._request("GET", "/reviews", token)
        try:
            return ReviewQueue.model_validate(result).model_dump(mode="json")
        except ValueError as error:
            raise ReviewUnavailable("Review service returned an unverified queue") from error

    async def decide(self, plan_id: str, decision: ReviewDecision, token: str) -> dict:
        if re.fullmatch(r"plan-[A-Za-z0-9-]{1,120}", plan_id) is None:
            raise ValueError("invalid plan identifier")
        result = await self._request("POST", f"/reviews/{plan_id}/decision", token, decision.model_dump())
        expected = "approved" if decision.decision == "approve" else "rejected"
        if result != {"planId": plan_id, "state": expected, "planHash": decision.plan_hash}:
            raise ReviewUnavailable("Review decision result is unverified; refresh before retrying")
        return result