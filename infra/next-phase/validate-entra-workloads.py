#!/usr/bin/env python3
"""Validate compiled Microsoft Graph Bicep policy for WP2b audiences."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


EXPECTED_MODES = {"diagnostic", "query-runner", "remediation", "verifier"}
UUID_SEARCH = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)


def load_template(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("unable to read compiled Graph template") from error
    if not isinstance(document, dict):
        raise ValueError("compiled Graph template must be an object")
    return document


def validate_application(resource: dict[str, Any], *, configured: bool, failures: list[str]) -> None:
    properties = resource.get("properties") or {}
    if resource.get("import") != "microsoftGraphV1" or resource.get("type") != "Microsoft.Graph/applications@v1.0":
        failures.append("application must use the pinned Microsoft Graph v1 extension")
    if (resource.get("copy") or {}).get("count") != "[length(variables('services'))]":
        failures.append("application inventory must be generated from the four-service contract")
    if properties.get("signInAudience") != "AzureADMyOrg":
        failures.append("applications must be tenant-only")
    if properties.get("isFallbackPublicClient") is not False:
        failures.append("applications must not be public clients")
    api = properties.get("api") or {}
    if api.get("requestedAccessTokenVersion") != 2:
        failures.append("applications must issue v2 access tokens")
    for name in ("oauth2PermissionScopes", "preAuthorizedApplications"):
        if api.get(name) != []:
            failures.append(f"application API {name} must be empty")
    for name in ("appRoles", "keyCredentials", "passwordCredentials", "requiredResourceAccess"):
        if properties.get(name) != []:
            failures.append(f"application {name} must be empty")
    for name in ("publicClient", "spa", "web"):
        if (properties.get(name) or {}).get("redirectUris") != []:
            failures.append(f"application {name} redirect URIs must be empty")
    implicit = (properties.get("web") or {}).get("implicitGrantSettings") or {}
    if implicit != {"enableAccessTokenIssuance": False, "enableIdTokenIssuance": False}:
        failures.append("application implicit token issuance must be disabled")
    identifiers = properties.get("identifierUris")
    if configured:
        if (
            not isinstance(identifiers, list)
            or len(identifiers) != 1
            or "serviceApplicationIds" not in str(identifiers[0])
            or not str(identifiers[0]).startswith("[format('api://")
        ):
            failures.append("configured applications must bind one private app ID URI")
    elif identifiers is not None:
        failures.append("registration-stage applications must defer generated app ID URIs")


def validate_template(template: dict[str, Any], *, configured: bool) -> list[str]:
    failures: list[str] = []
    graph_import = (template.get("imports") or {}).get("microsoftGraphV1") or {}
    if graph_import != {"provider": "MicrosoftGraph", "version": "1.0.0"}:
        failures.append("Microsoft Graph extension version differs")
    if template.get("outputs"):
        failures.append("Graph templates must not persist identity outputs")
    services = template.get("variables", {}).get("services")
    if configured:
        modes = {item.get("mode") for item in services or [] if isinstance(item, dict)}
    else:
        modes = set(services or [])
    if modes != EXPECTED_MODES:
        failures.append("Graph service inventory differs")
    resources = template.get("resources") or {}
    application = resources.get("applications") if isinstance(resources, dict) else None
    if not isinstance(application, dict):
        failures.append("Graph application resource is missing")
    else:
        validate_application(application, configured=configured, failures=failures)
    principal = resources.get("servicePrincipals") if isinstance(resources, dict) else None
    if configured:
        if not isinstance(principal, dict):
            failures.append("configured Graph service principals are missing")
        else:
            properties = principal.get("properties") or {}
            if principal.get("import") != "microsoftGraphV1" or principal.get("type") != "Microsoft.Graph/servicePrincipals@v1.0":
                failures.append("service principals must use the pinned Graph v1 extension")
            if properties.get("accountEnabled") is not True:
                failures.append("service principals must be enabled")
            if properties.get("keyCredentials") != [] or properties.get("passwordCredentials") != []:
                failures.append("service principals must not hold credentials")
            if "serviceApplicationIds" not in str(properties.get("appId", "")):
                failures.append("service principals must bind private generated app IDs")
    elif principal is not None:
        failures.append("registration stage must not create service principals")
    parameters = template.get("parameters") or {}
    service_ids = parameters.get("serviceApplicationIds")
    if configured and (not isinstance(service_ids, dict) or service_ids.get("type") != "secureObject"):
        failures.append("configured Graph app IDs must use a secure object parameter")
    serialized = json.dumps(template, sort_keys=True)
    if UUID_SEARCH.search(serialized):
        failures.append("compiled Graph template contains a hard-coded identifier")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registrations", type=Path)
    parser.add_argument("audiences", type=Path)
    args = parser.parse_args()
    try:
        failures = [
            *validate_template(load_template(args.registrations), configured=False),
            *validate_template(load_template(args.audiences), configured=True),
        ]
    except ValueError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS four Graph applications use stable unique names and tenant-only v2 audiences")
    print("PASS applications and service principals have no credentials, redirects, roles, or scopes")
    print("PASS generated application identifiers remain secure deployment inputs and never outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())