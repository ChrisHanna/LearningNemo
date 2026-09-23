"""Run the deterministic invoice demonstration on loopback."""

from __future__ import annotations

import argparse
import threading

import uvicorn

from task_agent.console.agent_client import AgentReply
from task_agent.console.app import _open_browser, create_app
from task_agent.console.browser_sessions import BrowserSessionRegistry
from task_agent.console.identity import EntraTestSettings
from task_agent.console.local_demo_auth import LocalDemoAuthManager
from task_agent.console.local_invoice_demo import LocalInvoiceDemoService


class LocalDemoAgentClient:
    async def health(self) -> dict:
        return {"status": "online", "httpStatus": 200, "durationMs": 0}

    async def chat(self, _access_token: str, _prompt: str, **_kwargs) -> AgentReply:
        return AgentReply(
            content="Use the Invoice workflow for the deterministic local demonstration.",
            status_code=200,
            duration_ms=0,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("The local invoice demo may only bind to loopback")

    settings = EntraTestSettings("local-demo", "local-demo-api", "local-demo-client")
    registry = BrowserSessionRegistry(settings, auth_factory=LocalDemoAuthManager)
    app = create_app(
        settings,
        "local-demo://agent",
        session_registry=registry,
        agent_client=LocalDemoAgentClient(),
        invoice_service=LocalInvoiceDemoService(),
    )
    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        threading.Timer(1, _open_browser, args=(url,)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()