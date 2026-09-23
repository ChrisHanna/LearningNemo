"""Load one client configuration and validate role-based test personas."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Literal

import msal

from task_agent.security.jwt_claims import decode_jwt_payload
from task_agent.security.jwt_claims import string_claim_values


Role = Literal["reader", "operator", "approver"]
CLIENT_SCOPES = ("agent.invoke", "tasks.read", "tasks.execute")
PERSONA_ROLES: dict[Role, tuple[str, ...]] = {
    "reader": ("Task.Reader",),
    "operator": ("Task.Reader", "Task.Operator"),
    "approver": ("Task.Approver",),
}
DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parents[3] / ".nemo-test-client.json"


class AccessProfileError(RuntimeError):
    """Safe explanation of why an Entra account cannot use LearningNeMo."""


def _load_settings(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read test settings from {path}") from error
    if not isinstance(values, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in values.items()
    ):
        raise RuntimeError(f"Test settings in {path} must be a JSON object of string values")
    return values


@dataclass(frozen=True)
class EntraTestSettings:
    tenant_id: str
    api_client_id: str
    public_client_id: str

    @classmethod
    def from_sources(cls, path: Path = DEFAULT_SETTINGS_PATH) -> EntraTestSettings:
        values = _load_settings(path)

        def optional(name: str) -> str | None:
            return os.getenv(name) or values.get(name)

        def required(name: str) -> str:
            value = optional(name)
            if not value:
                raise RuntimeError(f"Set {name} or configure it in {path.name}")
            return value

        return cls(
            tenant_id=required("ENTRA_TENANT_ID"),
            api_client_id=required("ENTRA_CLIENT_ID"),
            public_client_id=required("ENTRA_PUBLIC_CLIENT_ID"),
        )

    def create_public_client_app(self) -> msal.PublicClientApplication:
        authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        return msal.PublicClientApplication(client_id=self.public_client_id, authority=authority)


def access_token_claims(access_token: str) -> dict[str, Any]:
    try:
        return decode_jwt_payload(access_token)
    except ValueError as error:
        raise RuntimeError("Microsoft Entra returned an unreadable access token") from error


def access_token_scopes(access_token: str) -> frozenset[str]:
    try:
        return string_claim_values(access_token_claims(access_token), "scp")
    except ValueError as error:
        raise RuntimeError("The access token has an invalid scp claim")


def access_token_roles(access_token: str) -> frozenset[str]:
    try:
        return string_claim_values(access_token_claims(access_token), "roles")
    except ValueError as error:
        raise RuntimeError("The access token has an invalid roles claim") from error


@dataclass(frozen=True)
class ValidatedAccessProfile:
    persona: Role
    scopes: frozenset[str]
    roles: frozenset[str]


def validate_signed_in_user(access_token: str) -> ValidatedAccessProfile:
    """Validate shared client claims and derive the persona from app roles."""
    try:
        granted_scopes = access_token_scopes(access_token)
        granted_roles = access_token_roles(access_token)
    except RuntimeError as error:
        raise AccessProfileError("Required demo scope or app-role assignment is missing or invalid") from error
    is_approver = "Task.Approver" in granted_roles
    if is_approver and "Task.Operator" in granted_roles:
        raise AccessProfileError("Approver and Operator assignments must be separate")
    expected_scopes = {"agent.invoke"} if is_approver else set(CLIENT_SCOPES)
    missing_scopes = expected_scopes - granted_scopes
    if missing_scopes:
        raise AccessProfileError(f"The access token is missing scope(s): {', '.join(sorted(missing_scopes))}")

    if not is_approver and "Task.Reader" not in granted_roles:
        raise AccessProfileError("The signed-in account is missing role: Task.Reader")

    persona: Role = "approver" if is_approver else "operator" if "Task.Operator" in granted_roles else "reader"
    return ValidatedAccessProfile(
        persona=persona,
        scopes=granted_scopes,
        roles=granted_roles,
    )


def validate_access_profile(access_token: str, access: Role) -> ValidatedAccessProfile:
    """Validate a token and require the persona expected by the CLI test."""
    profile = validate_signed_in_user(access_token)
    if profile.persona != access:
        expected_role = {"operator": "Task.Operator", "approver": "Task.Approver", "reader": "Reader-only role assignment"}[access]
        raise AccessProfileError(
            f"The signed-in account resolves to {profile.persona}; "
            f"the {access} test requires {expected_role}"
        )
    return profile