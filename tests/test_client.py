from __future__ import annotations

import base64
import json
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock


_SPEC = importlib.util.spec_from_file_location("test_client_script", Path(__file__).parents[1] / "scripts" / "test_client.py")
assert _SPEC and _SPEC.loader
test_client = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(test_client)


def _access_token(roles: tuple[str, ...]) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "scp": "agent.invoke tasks.read tasks.execute",
                "roles": list(roles),
            }
        ).encode()
    ).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_reader_profile_rejects_operator_account() -> None:
    token = _access_token(("Task.Reader", "Task.Operator"))

    try:
        test_client._validate_access_profile(token, "reader")
    except RuntimeError as error:
        assert "resolves to operator" in str(error)
    else:
        raise AssertionError("Reader profile accepted tasks.execute")


def test_access_profiles_accept_their_expected_scopes() -> None:
    test_client._validate_access_profile(_access_token(("Task.Reader",)), "reader")
    test_client._validate_access_profile(
        _access_token(("Task.Reader", "Task.Operator")),
        "operator",
    )


def test_prepare_device_login_opens_browser_and_copies_code(monkeypatch, capsys) -> None:
    copied_codes: list[str] = []
    opened_urls: list[str] = []
    monkeypatch.setattr(test_client, "_copy_device_code", lambda code: copied_codes.append(code) or True)
    monkeypatch.setattr(test_client, "_open_verification_page", lambda url: opened_urls.append(url) or True)

    test_client._prepare_device_login(
        {
            "message": "Sign in with the displayed code.",
            "user_code": "TEST-CODE",
            "verification_uri": "https://example.test/device",
        },
        open_browser=True,
    )

    assert copied_codes == ["TEST-CODE"]
    assert opened_urls == ["https://example.test/device"]
    assert "opened" in capsys.readouterr().out


def test_call_agent_returns_assistant_text(monkeypatch) -> None:
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": "Task list"}}]}
    post = MagicMock(return_value=response)
    monkeypatch.setattr(test_client.httpx, "post", post)

    result = test_client._call_agent("http://agent.test/chat", "test-token", "List tasks")

    assert result == "Task list"
    response.raise_for_status.assert_called_once_with()
    assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer test-token"}
    assert post.call_args.kwargs["json"]["messages"][0]["content"] == "List tasks"


def test_authorization_scenario_reuses_one_token_per_access_level(monkeypatch) -> None:
    acquired_access: list[str] = []
    prompts: list[tuple[str, str]] = []

    def acquire_token(_app, _api_client_id, access, *, open_browser):
        assert open_browser is True
        acquired_access.append(access)
        return f"{access}-token"

    def call_agent(_url, token, prompt):
        prompts.append((token, prompt))
        return "ok"

    monkeypatch.setattr(test_client, "_acquire_token", acquire_token)
    monkeypatch.setattr(test_client, "_call_agent", call_agent)

    result = test_client._run_authorization_scenario(
        MagicMock(),
        "api-client",
        "http://agent.test/chat",
        "reader",
        open_browser=True,
    )

    assert result == 0
    assert acquired_access == ["reader"]
    assert len(prompts) == 3
    assert all(token == "reader-token" for token, _prompt in prompts)
    assert prompts[1][1] == "Execute task-1."