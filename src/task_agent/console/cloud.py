"""Cloud console entry point; no operator credentials or loopback backends."""

import os
from urllib.parse import urlsplit

import uvicorn
from azure.identity import ManagedIdentityCredential

from task_agent.console.app import create_app
from task_agent.console.browser_sessions import BrowserSessionRegistry
from task_agent.console.cloud_auth import CloudIdentityVerifier
from task_agent.console.hosting import ConsoleHosting
from task_agent.console.identity import EntraTestSettings
from task_agent.console.remote_workspace import RemoteWorkspace
from task_agent.console.remote_review import RemoteReviewService
from task_agent.console.remote_incident import RemoteIncidentService
from task_agent.console.remote_execution import RemoteExecutionService
from task_agent.console.remote_invoice import RemoteInvoiceService
from task_agent.console.public_demo_auth import PublicDemoAuthManager


def main() -> None:
    settings = EntraTestSettings.from_sources()
    public_origin = os.environ["LEARNINGNEMO_PUBLIC_ORIGIN"]
    controller_origin = os.environ["LEARNINGNEMO_CONTROLLER_ORIGIN"]
    agent_url = os.environ["LEARNINGNEMO_CLOUD_AGENT_URL"]
    review_origin = os.environ.get("LEARNINGNEMO_REVIEW_ORIGIN")
    incident_origin = os.environ.get("LEARNINGNEMO_INCIDENT_ORIGIN")
    execution_origin = os.environ.get("LEARNINGNEMO_EXECUTION_ORIGIN")
    invoice_origin = os.environ.get('LEARNINGNEMO_INVOICE_OPERATOR_ORIGIN')
    invoice_review = os.environ.get('LEARNINGNEMO_INVOICE_REVIEW_ORIGIN')
    auth_mode = os.environ.get("LEARNINGNEMO_AUTH_MODE", "entra")
    if auth_mode not in {"entra", "public-demo"}:
        raise ValueError("Unsupported cloud authentication mode")
    if bool(invoice_origin) != bool(invoice_review):
        raise ValueError('both invoice service origins must be configured')
    parsed = urlsplit(agent_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname in {"localhost", "127.0.0.1"}:
        raise ValueError("Cloud agent must have an HTTPS cloud endpoint")
    session_registry = None
    broker_credential = None
    if auth_mode == "public-demo":
        session_registry = BrowserSessionRegistry(settings, auth_factory=PublicDemoAuthManager)
        broker_credential = ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"])
    app = create_app(
        settings, agent_url, hosting=ConsoleHosting(public_origin),
        session_registry=session_registry,
        live_workspace=RemoteWorkspace(controller_origin), workspace_verifier=CloudIdentityVerifier(settings),
        review_service=RemoteReviewService(review_origin) if review_origin else None,
        incident_service=RemoteIncidentService(incident_origin) if incident_origin else None,
        execution_service=RemoteExecutionService(execution_origin) if execution_origin else None,
        invoice_service=RemoteInvoiceService(
            invoice_origin,
            invoice_review,
            broker_credential=broker_credential,
            broker_audience=f"api://{settings.api_client_id}" if broker_credential else None,
        ) if invoice_origin else None,
    )
    uvicorn.run(app, host="0.0.0.0", port=8080, workers=1, proxy_headers=False)


if __name__ == "__main__":
    main()