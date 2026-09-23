"""Private fixed-operation workspace controller using Azure managed identity."""

from __future__ import annotations

import asyncio
import argparse
import json
import os

import httpx
import uvicorn
from azure.identity import ManagedIdentityCredential
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from task_agent.console.cloud_auth import CloudIdentityVerifier
from task_agent.console.identity import EntraTestSettings
from task_agent.console.live_workspace import LiveWorkspace, WorkspaceLiveError


class ManagedWorkspace(LiveWorkspace):
    def __init__(self, subscription: str, client_id: str) -> None:
        super().__init__(subscription)
        self.credential = ManagedIdentityCredential(client_id=client_id)
        self.saw = f"/subscriptions/{subscription}/resourceGroups/rg-learningnemo-saw-dev/providers/"
        self.platform = f"/subscriptions/{subscription}/resourceGroups/rg-learningnemo-platform-dev/providers/"

    def _get(self, path: str, version: str) -> dict:
        try:
            token = self.credential.get_token("https://management.azure.com/.default").token
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                response = client.get(
                    "https://management.azure.com" + path, params={"api-version": version},
                    headers={"Authorization": f"Bearer {token}"},
                )
            response.raise_for_status()
            return response.json()
        except Exception as error:
            raise WorkspaceLiveError("Managed-identity workspace query failed") from error

    def _az(self, arguments: list[str], timeout: int = 30):
        if arguments[:2] == ["vm", "show"]:
            value = self._get(self.saw + "Microsoft.Compute/virtualMachines/vm-learningnemo-saw-dev", "2024-07-01")
            view = self._get(self.saw + "Microsoft.Compute/virtualMachines/vm-learningnemo-saw-dev/instanceView", "2024-07-01")
            nic = self._get(self.saw + "Microsoft.Network/networkInterfaces/nic-vm-learningnemo-saw-dev", "2024-05-01")
            states = {item.get("code") for item in view.get("statuses", [])}
            return {
                **value, **value.get("properties", {}),
                "powerState": "VM running" if "PowerState/running" in states else "VM stopped",
                "publicIps": "present" if any(item.get("properties", {}).get("publicIPAddress") for item in nic.get("properties", {}).get("ipConfigurations", [])) else "",
            }
        if arguments[:3] == ["stack", "group", "show"]:
            value = self._get(self.saw + "Microsoft.Resources/deploymentStacks/learningnemo-saw-runtime-lock-dev", "2024-03-01")
            return value.get("properties", {})
        if arguments[:4] == ["network", "nsg", "rule", "list"]:
            value = self._get(self.saw + "Microsoft.Network/networkSecurityGroups/nsg-vnet-learningnemo-saw-dev-workspace/securityRules", "2024-05-01")
            return [{"name": item.get("name"), **item.get("properties", {})} for item in value.get("value", [])]
        if arguments[:4] == ["network", "vnet", "subnet", "show"]:
            return self._get(self.saw + "Microsoft.Network/virtualNetworks/vnet-learningnemo-saw-dev/subnets/snet-workspace", "2024-05-01").get("properties", {})
        if arguments[:4] == ["network", "nat", "gateway", "show"] and arguments[-1] == "nat-learningnemo-saw-runtime-dev":
            return self._get(self.saw + "Microsoft.Network/natGateways/nat-learningnemo-saw-runtime-dev", "2024-05-01")
        if arguments[:2] == ["containerapp", "show"]:
            return self._get(self.platform + "Microsoft.App/containerApps/ca-learningnemo-diagnostic-dev", "2025-01-01")
        if arguments[:3] == ["vm", "run-command", "invoke"]:
            from azure.mgmt.compute import ComputeManagementClient
            try:
                script = arguments[arguments.index("--scripts") + 1]
                with ComputeManagementClient(self.credential, self.subscription, polling_interval=3) as client:
                    operation = client.virtual_machines.begin_run_command(
                        "rg-learningnemo-saw-dev", "vm-learningnemo-saw-dev",
                        {"command_id": "RunShellScript", "script": script.splitlines()},
                    )
                    result = operation.result(timeout=timeout)
                return {"value": [{"code": item.code, "message": item.message} for item in result.value or []]}
            except Exception as error:
                raise WorkspaceLiveError("Managed-identity proof failed or timed out; no success is assumed") from error
        raise WorkspaceLiveError("Operation is outside the fixed workspace controller contract")

    def run(self, run_id: str) -> dict:
        result = super().run(run_id)
        result["transport"] = "Cloud dashboard -> private managed-identity controller -> Azure Run Command -> OpenShell -> Planning MicroVM"
        return result


class ProofRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runId: str = Field(pattern=r"^[a-f0-9]{32}$")


def create_controller(settings: EntraTestSettings, workspace: LiveWorkspace, verifier=None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    verifier = verifier or CloudIdentityVerifier(settings)

    async def authorize(request: Request, *, execute: bool = False):
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Bearer identity required")
        try:
            decision = await verifier.verify(authorization[7:])
        except Exception as error:
            raise HTTPException(401, "Invalid Entra identity") from error
        if not decision.get("allowed" if execute else "canRead"):
            raise HTTPException(403, "Required demo permission is absent")

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.post("/workspace/check")
    async def check(request: Request):
        await authorize(request)
        try:
            return await asyncio.to_thread(workspace.check)
        except WorkspaceLiveError as error:
            raise HTTPException(503, str(error)) from error

    @app.post("/workspace/run")
    async def run(request: Request, body: ProofRequest):
        await authorize(request, execute=True)
        try:
            return await asyncio.to_thread(workspace.run, body.runId)
        except WorkspaceLiveError as error:
            raise HTTPException(503, str(error)) from error

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Query fixed workspace state through managed identity, then exit")
    args = parser.parse_args()
    settings = EntraTestSettings.from_sources()
    workspace = ManagedWorkspace(os.environ["AZURE_SUBSCRIPTION_ID"], os.environ["AZURE_CLIENT_ID"])
    if args.check:
        print(json.dumps(workspace.check()))
        return
    uvicorn.run(create_controller(settings, workspace), host="0.0.0.0", port=8080, workers=1, proxy_headers=False)


if __name__ == "__main__":
    main()