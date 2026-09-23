"""Cloud console entry point; no operator credentials or loopback backends."""

import os
from urllib.parse import urlsplit

import uvicorn

from task_agent.console.app import create_app
from task_agent.console.cloud_auth import CloudIdentityVerifier
from task_agent.console.hosting import ConsoleHosting
from task_agent.console.identity import EntraTestSettings
from task_agent.console.remote_workspace import RemoteWorkspace
from task_agent.console.remote_review import RemoteReviewService
from task_agent.console.remote_incident import RemoteIncidentService
from task_agent.console.remote_execution import RemoteExecutionService
from task_agent.console.remote_invoice import RemoteInvoiceService


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
    if bool(invoice_origin) != bool(invoice_review):
        raise ValueError('both invoice service origins must be configured')
    parsed = urlsplit(agent_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname in {"localhost", "127.0.0.1"}:
        raise ValueError("Cloud agent must have an HTTPS cloud endpoint")
    app = create_app(
        settings, agent_url, hosting=ConsoleHosting(public_origin),
        live_workspace=RemoteWorkspace(controller_origin), workspace_verifier=CloudIdentityVerifier(settings),
        review_service=RemoteReviewService(review_origin) if review_origin else None,
        incident_service=RemoteIncidentService(incident_origin) if incident_origin else None,
        execution_service=RemoteExecutionService(execution_origin) if execution_origin else None,
        invoice_service=RemoteInvoiceService(invoice_origin, invoice_review) if invoice_origin else None,
    )
    uvicorn.run(app, host="0.0.0.0", port=8080, workers=1, proxy_headers=False)


if __name__ == "__main__":
    main()