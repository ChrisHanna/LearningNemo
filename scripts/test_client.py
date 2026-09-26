"""Use a guided Entra login to exercise the protected task agent."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import msal

from task_agent.console.identity import CLIENT_SCOPES
from task_agent.console.identity import DEFAULT_SETTINGS_PATH
from task_agent.console.identity import EntraTestSettings
from task_agent.console.identity import PERSONA_ROLES
from task_agent.console.identity import validate_access_profile as _validate_access_profile
from task_agent.console.walkthrough import walkthrough_manifest


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--access", choices=PERSONA_ROLES, default="reader")
    parser.add_argument("--url", default="http://127.0.0.1:8001/v1/chat/completions")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prompt", help="Send one prompt and exit")
    mode.add_argument("--interactive", action="store_true", help="Start an interactive session (the default)")
    mode.add_argument("--scenario", choices=("authorization",), help="Run a guided end-to-end walkthrough")
    parser.add_argument(
        "--settings",
        type=Path,
        default=DEFAULT_SETTINGS_PATH,
        help=f"Non-secret Entra settings file (default: {DEFAULT_SETTINGS_PATH.name})",
    )
    parser.add_argument("--no-browser", action="store_true", help="Do not open the Microsoft sign-in page")
    return parser.parse_args(argv)


def _open_verification_page(url: str) -> bool:
    if os.getenv("WSL_DISTRO_NAME"):
        explorer = shutil.which("explorer.exe")
        if explorer:
            subprocess.Popen(
                [explorer, url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
    return webbrowser.open(url)


def _copy_device_code(user_code: str) -> bool:
    clipboard = shutil.which("clip.exe")
    if not clipboard:
        return False
    result = subprocess.run(
        [clipboard],
        input=user_code,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _prepare_device_login(flow: dict[str, Any], *, open_browser: bool) -> None:
    print(flow["message"])
    if not open_browser:
        return

    user_code = str(flow["user_code"])
    verification_url = str(flow.get("verification_uri_complete") or flow["verification_uri"])
    copied = _copy_device_code(user_code)
    opened = _open_verification_page(verification_url)
    if opened and copied:
        print("The sign-in page was opened and the device code was copied to the clipboard.")
    elif opened:
        print("The sign-in page was opened; enter the device code shown above.")


def _acquire_token(
    app: msal.PublicClientApplication,
    api_client_id: str,
    access: str,
    *,
    open_browser: bool,
) -> str:
    scopes = [f"api://{api_client_id}/{scope}" for scope in CLIENT_SCOPES]
    print(f"\nSigning in with the {access} test account")
    print(f"Expected app role(s): {', '.join(PERSONA_ROLES[access])}")
    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" not in flow:
        raise RuntimeError("Microsoft Entra did not return a device-code flow")

    _prepare_device_login(flow, open_browser=open_browser)
    token_result = app.acquire_token_by_device_flow(flow)
    access_token = token_result.get("access_token")
    if not access_token:
        raise RuntimeError(token_result.get("error_description", "Authentication failed"))
    access_token = str(access_token)
    _validate_access_profile(access_token, access)
    return access_token


def _call_agent(url: str, access_token: str, prompt: str) -> str:
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("The agent returned an unexpected response shape") from error
    if not isinstance(content, str):
        raise RuntimeError("The agent response did not contain text")
    return content


def _run_authorization_scenario(
    app: msal.PublicClientApplication,
    api_client_id: str,
    url: str,
    access: str,
    *,
    open_browser: bool,
) -> int:
    access_token = _acquire_token(app, api_client_id, access, open_browser=open_browser)
    steps = walkthrough_manifest(access)
    print(f"\nDetected walkthrough: {access}")
    for index, step in enumerate(steps, start=1):
        print(f"\n[{index}/{len(steps)}] {step['title']}")
        print(f"Expected: {step['expected']}")
        print(f"> {step['prompt']}")
        print(_call_agent(url, access_token, step["prompt"]))
    return 0


def _run_interactive(
    app: msal.PublicClientApplication,
    api_client_id: str,
    url: str,
    initial_access: str,
    *,
    open_browser: bool,
) -> int:
    access_token = _acquire_token(app, api_client_id, initial_access, open_browser=open_browser)
    print("\nEnter a prompt. Use /quit to exit.")

    while True:
        try:
            prompt = input(f"{initial_access}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not prompt:
            continue
        if prompt == "/quit":
            return 0
        print(_call_agent(url, access_token, prompt))


def _print_error(error: Exception) -> None:
    if isinstance(error, httpx.HTTPStatusError):
        response_text = error.response.text.strip()
        detail = f": {response_text}" if response_text else ""
        print(f"Agent returned HTTP {error.response.status_code}{detail}", file=sys.stderr)
        return
    print(str(error), file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        settings = EntraTestSettings.from_sources(args.settings)
        api_client_id = settings.api_client_id
        app = settings.create_public_client_app()
        open_browser = not args.no_browser

        if args.scenario == "authorization":
            return _run_authorization_scenario(
                app,
                api_client_id,
                args.url,
                args.access,
                open_browser=open_browser,
            )
        if args.prompt is not None:
            access_token = _acquire_token(app, api_client_id, args.access, open_browser=open_browser)
            print(_call_agent(args.url, access_token, args.prompt))
            return 0
        return _run_interactive(
            app,
            api_client_id,
            args.url,
            args.access,
            open_browser=open_browser,
        )
    except (httpx.HTTPError, RuntimeError) as error:
        _print_error(error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())