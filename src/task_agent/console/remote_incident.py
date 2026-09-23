"""Fixed incident service routes; the dashboard never receives SQL authority."""

import re
from urllib.parse import urlsplit

import httpx

from task_agent.console.incident_contract import IncidentQueue, IncidentStart, PlanSubmission
from task_agent.console.analysis_contract import AnalysisReceipt, ProposalRequest


class IncidentUnavailable(RuntimeError):
    def __init__(self, detail: str, status_code: int = 503):
        super().__init__(detail)
        self.status_code = status_code


class RemoteIncidentService:
    def __init__(self, origin: str):
        parsed = urlsplit(origin)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port or parsed.path or parsed.query or parsed.fragment
                or re.fullmatch(r"[a-z0-9-]+\.internal\.[a-z0-9.-]+\.azurecontainerapps\.io", parsed.hostname) is None):
            raise ValueError("Incident service requires an exact private Container Apps HTTPS origin")
        self.origin = origin

    async def _request(self, method: str, path: str, token: str, body: dict | None = None) -> dict:
        try:
            async with httpx.AsyncClient(timeout=90 if path == "/analysis" else 30, follow_redirects=False) as client:
                response = await client.request(method, self.origin + path, headers={"Authorization": f"Bearer {token}"}, json=body)
            if response.status_code == 401:
                raise IncidentUnavailable("Incident API requires a fresh Operator sign-in", 401)
            if response.status_code == 403:
                raise IncidentUnavailable("The current account cannot access Operator investigations", 403)
            if response.status_code == 503:
                try:
                    expired = response.json().get('detail') == 'Incident service lease expired'
                except (ValueError, AttributeError):
                    expired = False
                if expired:
                    raise IncidentUnavailable("Incident service lease expired. Its admission window must be renewed; refreshing cannot start an investigation.")
            if response.status_code == 409:
                raise IncidentUnavailable("Investigation is busy, changed, or needs reconciliation. Keep the request ID and refresh.", 409)
            if response.status_code != 200:
                raise IncidentUnavailable("Incident service unavailable or access denied; no successful result is assumed")
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("invalid result")
            return result
        except httpx.TimeoutException as error:
            raise IncidentUnavailable("Incident API connection timed out. No empty queue or successful operation is assumed.", 504) from error
        except (httpx.HTTPError, ValueError) as error:
            raise IncidentUnavailable("Incident service unavailable; refresh before retrying") from error

    async def list_incidents(self, token: str) -> dict:
        result = await self._request("GET", "/incidents", token)
        try:
            return IncidentQueue.model_validate(result).model_dump(mode="json")
        except ValueError as error:
            raise IncidentUnavailable("Incident service returned an unverified investigation") from error

    async def analysis(self, token: str, *, refresh=False):
        result = await self._request('POST' if refresh else 'GET', '/analysis', token)
        try:
            if set(result) != {'available', 'analysis'} or result['available'] is not True:
                raise ValueError('unexpected analysis response')
            if result['analysis'] is not None:
                result['analysis'] = AnalysisReceipt.model_validate(result['analysis']).model_dump(mode='json')
            elif refresh:
                raise ValueError('analysis receipt absent')
            return result
        except (ValueError, TypeError) as error:
            raise IncidentUnavailable('Analysis receipt is not verified') from error

    async def propose(self, body: ProposalRequest, token: str):
        result = await self._request('POST', '/analysis/propose', token, body.model_dump())
        if (set(result) != {'planId', 'planHash', 'state'} or result['state'] != 'draft'
                or result['planId'] != body.analysis_id.replace('analysis-', 'plan-', 1)
                or not isinstance(result['planHash'], str) or not re.fullmatch('[a-f0-9]{64}', result['planHash'])):
            raise IncidentUnavailable('Proposal not confirmed; refresh saved plans')
        return result

    async def start(self, body: IncidentStart, token: str) -> dict:
        result = await self._request("POST", "/incidents/start", token, body.model_dump())
        if (set(result) != {"state", "planId", "requestId", "replayed"} or result["state"] != "draft"
                or result["requestId"] != body.request_id or type(result["replayed"]) is not bool
                or not isinstance(result["planId"], str) or re.fullmatch(r"plan-[a-f0-9]{32}", result["planId"]) is None):
            raise IncidentUnavailable("Initiation unconfirmed; keep the request ID and refresh")
        return result

    async def submit(self, plan_id: str, submission: PlanSubmission, token: str) -> dict:
        if re.fullmatch(r"plan-[A-Za-z0-9-]{1,120}", plan_id) is None:
            raise ValueError("invalid plan identifier")
        result = await self._request("POST", f"/incidents/{plan_id}/submit", token, submission.model_dump())
        if result != {"planId": plan_id, "planHash": submission.plan_hash, "state": "awaiting_approval"}:
            raise IncidentUnavailable("Submission unconfirmed; refresh before retrying")
        return result