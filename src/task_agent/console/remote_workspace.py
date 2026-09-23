"""Forward fixed workspace requests to one private controller; no Azure login."""

import httpx

from task_agent.console.hosting import ConsoleHosting
from task_agent.console.live_workspace import WorkspaceLiveError


class RemoteWorkspace:
    enabled = True

    def __init__(self, origin: str) -> None:
        ConsoleHosting(origin)
        self.origin = origin

    def _post(self, path: str, token: str, body: dict | None = None) -> dict:
        try:
            with httpx.Client(timeout=230, follow_redirects=False) as client:
                result = client.post(
                    self.origin + path, headers={"Authorization": f"Bearer {token}"}, json=body or {},
                )
            if result.status_code != 200:
                raise WorkspaceLiveError("Cloud controller denied or could not complete this operation")
            value = result.json()
            if not isinstance(value, dict):
                raise WorkspaceLiveError("Cloud controller returned an invalid response")
            return value
        except (httpx.HTTPError, ValueError) as error:
            raise WorkspaceLiveError("Cloud controller unavailable; no completion is assumed") from error

    def check(self, token: str) -> dict:
        return self._post("/workspace/check", token)

    def run(self, run_id: str, token: str) -> dict:
        return self._post("/workspace/run", token, {"runId": run_id})