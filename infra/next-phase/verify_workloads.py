#!/usr/bin/env python3
"""Verify deployed WP2b controls without printing Azure identifiers."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from workload_contract import SERVICE_KEYS
from workload_contract import app_name
from workload_contract import combined_values
from workload_contract import identity_name
from workload_contract import workload_resource_tags
from workload_parameters import WorkloadParameterError
from workload_parameters import load_config
from workload_parameters import parameter_values


APP_API_VERSION = "2025-01-01"
AUTH_API_VERSION = "2025-01-01"
PROTECTED_PATHS = {
    "diagnostic": ("GET", "/v1/diagnostics/current"),
    "query-runner": ("POST", "/v1/query-runs/cancel"),
    "remediation": ("POST", "/v1/remediations/execute"),
    "verifier": ("POST", "/v1/verifications/cycle-recovery"),
}


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def az_json(arguments: list[str], *, timeout: int = 60) -> Any:
    try:
        result = subprocess.run(
            ["az", *arguments, "--output", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VerificationError("Azure verification query failed or timed out") from error
    if result.returncode != 0:
        raise VerificationError("Azure verification query failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise VerificationError("Azure verification query returned unreadable JSON") from error


def resource_show(resource_group: str, name: str, resource_type: str, api_version: str) -> dict[str, Any]:
    value = az_json(
        [
            "resource",
            "show",
            "--resource-group",
            resource_group,
            "--name",
            name,
            "--resource-type",
            resource_type,
            "--api-version",
            api_version,
        ]
    )
    require(isinstance(value, dict), "Azure resource query returned an unexpected shape")
    return value


def auth_config_show(subscription_id: str, resource_group: str, app: str) -> dict[str, Any]:
    value = az_json(
        [
            "rest",
            "--method",
            "get",
            "--url",
            f"https://management.azure.com/subscriptions/{subscription_id}/resourceGroups/{resource_group}/"
            f"providers/Microsoft.App/containerApps/{app}/authConfigs/current?api-version={AUTH_API_VERSION}",
        ]
    )
    require(isinstance(value, dict), "Azure auth-config query returned an unexpected shape")
    return value


def normalized_id(value: Any) -> str:
    return str(value).rstrip("/").casefold()


def normalized_location(value: Any) -> str:
    return "".join(str(value).split()).casefold()


def tags_match(actual: Any, expected: dict[str, str]) -> bool:
    return isinstance(actual, dict) and all(actual.get(key) == value for key, value in expected.items())


def validate_app_document(
    app: dict[str, Any],
    values: dict[str, Any],
    mode: str,
    expected_environment_id: str,
    expected_identity_id: str,
    expected_client_id: str,
) -> str:
    require(
        normalized_location(app.get("location")) == normalized_location(values["location"]),
        f"{mode} app location differs",
    )
    require(tags_match(app.get("tags"), workload_resource_tags(values, mode)), f"{mode} app tags differ")
    identity = app.get("identity") or {}
    require(identity.get("type") == "UserAssigned", f"{mode} app identity type differs")
    identities = identity.get("userAssignedIdentities") or {}
    require(
        {normalized_id(name) for name in identities} == {normalized_id(expected_identity_id)},
        f"{mode} app must use exactly its worker identity",
    )
    properties = app.get("properties") or {}
    require(properties.get("provisioningState") == "Succeeded", f"{mode} app is not provisioned")
    require(
        normalized_id(properties.get("environmentId")) == normalized_id(expected_environment_id),
        f"{mode} app environment differs",
    )
    require(properties.get("workloadProfileName") == "Consumption", f"{mode} workload profile differs")
    configuration = properties.get("configuration") or {}
    require(configuration.get("activeRevisionsMode") == "Single", f"{mode} revision mode differs")
    require(configuration.get("maxInactiveRevisions") == 1, f"{mode} inactive revision cap differs")
    require(configuration.get("secrets") in (None, []), f"{mode} app secrets are configured")
    registries = configuration.get("registries") or []
    require(
        len(registries) == 1
        and registries[0].get("server") == values["registryServer"]
        and normalized_id(registries[0].get("identity")) == normalized_id(expected_identity_id)
        and not registries[0].get("username")
        and not registries[0].get("passwordSecretRef"),
        f"{mode} managed-identity registry binding differs",
    )
    identity_settings = configuration.get("identitySettings") or []
    require(
        len(identity_settings) == 1
        and normalized_id(identity_settings[0].get("identity")) == normalized_id(expected_identity_id)
        and identity_settings[0].get("lifecycle") == "Main",
        f"{mode} identity lifecycle differs",
    )
    ingress = configuration.get("ingress") or {}
    require(
        ingress.get("external") is True
        and ingress.get("allowInsecure") is False
        and ingress.get("targetPort") == 8080,
        f"{mode} ingress controls differ",
    )
    containers = ((properties.get("template") or {}).get("containers") or [])
    require(len(containers) == 1, f"{mode} must have exactly one container")
    container = containers[0]
    require(container.get("name") == "worker", f"{mode} container name differs")
    require(container.get("image") == values["imageReference"], f"{mode} image digest differs")
    require(container.get("args") == ["--port", "8080"], f"{mode} process arguments differ")
    environment = {
        item.get("name"): item.get("value")
        for item in container.get("env") or []
        if isinstance(item, dict) and not item.get("secretRef")
    }
    require(
        environment
        == {
            "LEARNINGNEMO_SERVICE_MODE": mode,
            "LEARNINGNEMO_ALLOWED_CALLER_IDS": values["controlCallerPrincipalId"],
            "LEARNINGNEMO_SQL_SERVER": values["sqlServerHostname"],
            "LEARNINGNEMO_SQL_DATABASE": values["databaseName"],
            "AZURE_CLIENT_ID": expected_client_id,
        },
        f"{mode} runtime environment differs",
    )
    probes = {
        probe.get("type"): (probe.get("httpGet") or {}).get("path")
        for probe in container.get("probes") or []
    }
    require(
        probes == {"Startup": "/healthz", "Liveness": "/healthz", "Readiness": "/readyz"},
        f"{mode} health probes differ",
    )
    limits = container.get("resources") or {}
    require(limits.get("cpu") == 0.25 and limits.get("memory") == "0.5Gi", f"{mode} limits differ")
    scale = (properties.get("template") or {}).get("scale") or {}
    require(scale.get("minReplicas") == 0 and scale.get("maxReplicas") == 1, f"{mode} scale differs")
    rules = scale.get("rules") or []
    require(
        len(rules) == 1
        and ((rules[0].get("http") or {}).get("metadata") or {}).get("concurrentRequests") == "1",
        f"{mode} concurrency rule differs",
    )
    fqdn = ingress.get("fqdn")
    require(isinstance(fqdn, str) and fqdn, f"{mode} ingress endpoint is absent")
    return fqdn


def validate_auth_document(auth: dict[str, Any], values: dict[str, Any], mode: str) -> None:
    properties = auth.get("properties") or {}
    global_validation = properties.get("globalValidation") or {}
    require(
        global_validation.get("unauthenticatedClientAction") == "Return401",
        f"{mode} anonymous policy differs",
    )
    require(
        set(global_validation.get("excludedPaths") or []) == {"/healthz", "/readyz"},
        f"{mode} auth exclusions differ",
    )
    require((properties.get("httpSettings") or {}).get("requireHttps") is True, f"{mode} HTTPS policy differs")
    require((properties.get("login") or {}).get("tokenStore", {}).get("enabled") is False, f"{mode} token store differs")
    require((properties.get("platform") or {}).get("enabled") is True, f"{mode} auth platform is disabled")
    aad = ((properties.get("identityProviders") or {}).get("azureActiveDirectory") or {})
    require(aad.get("enabled") is True, f"{mode} Entra authentication is disabled")
    application_id = values["serviceApplicationIds"][SERVICE_KEYS[mode]]
    registration = aad.get("registration") or {}
    require(registration.get("clientId") == application_id, f"{mode} audience application differs")
    expected_issuer = f"https://login.microsoftonline.com/{values['tenantId']}/v2.0"
    require(str(registration.get("openIdIssuer", "")).rstrip("/") == expected_issuer, f"{mode} token issuer differs")
    validation = aad.get("validation") or {}
    require(set(validation.get("allowedAudiences") or []) == {f"api://{application_id}"}, f"{mode} audience differs")
    policy = validation.get("defaultAuthorizationPolicy") or {}
    require(
        set(policy.get("allowedApplications") or []) == {values["controlCallerApplicationId"]},
        f"{mode} caller application allowlist differs",
    )
    require(
        set((policy.get("allowedPrincipals") or {}).get("identities") or [])
        == {values["controlCallerPrincipalId"]},
        f"{mode} caller principal allowlist differs",
    )
    require(
        set((validation.get("jwtClaimChecks") or {}).get("allowedClientApplications") or [])
        == {values["controlCallerApplicationId"]},
        f"{mode} JWT client allowlist differs",
    )


def probe(mode: str, fqdn: str) -> None:
    for path, expected in (("/healthz", "healthy"), ("/readyz", "ready")):
        request = urllib.request.Request(f"https://{fqdn}{path}", method="GET")
        for attempt in range(5):
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    body = json.loads(response.read().decode("utf-8"))
                    require(response.status == 200 and body.get("status") == expected, f"{mode} {path} probe differs")
                    break
            except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
                if attempt == 4:
                    raise VerificationError(f"{mode} {path} probe failed") from error
                time.sleep(3)
    method, path = PROTECTED_PATHS[mode]
    request = urllib.request.Request(
        f"https://{fqdn}{path}",
        data=b"{}" if method == "POST" else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        urllib.request.urlopen(request, timeout=15)
    except urllib.error.HTTPError as error:
        require(error.code == 401, f"{mode} anonymous protected-route response differs")
    except OSError as error:
        raise VerificationError(f"{mode} anonymous auth probe failed") from error
    else:
        raise VerificationError(f"{mode} protected route allowed an anonymous request")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--skip-http", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        try:
            config = load_config(args.config)
            values = combined_values(config, parameter_values(args.parameters))
        except (WorkloadParameterError, ValueError) as error:
            raise VerificationError(str(error)) from error
        account = az_json(["account", "show"], timeout=20)
        expected_subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
        if expected_subscription:
            require(account.get("id") == expected_subscription, "active subscription mismatch")
        require(account.get("tenantId") == values["tenantId"], "active tenant mismatch")
        subscription_id = account.get("id")
        require(isinstance(subscription_id, str) and subscription_id, "active subscription is unavailable")
        resource_group = values["platformResourceGroupName"]
        environment_id = (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/"
            f"Microsoft.App/managedEnvironments/{values['containerAppsEnvironmentName']}"
        )
        endpoints: dict[str, str] = {}
        for mode in SERVICE_KEYS:
            worker_identity_id = (
                f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/"
                f"Microsoft.ManagedIdentity/userAssignedIdentities/{identity_name(values, mode)}"
            )
            worker_identity = resource_show(
                resource_group,
                identity_name(values, mode),
                "Microsoft.ManagedIdentity/userAssignedIdentities",
                "2024-11-30",
            )
            expected_client_id = str((worker_identity.get("properties") or {}).get("clientId", ""))
            require(expected_client_id != "", f"{mode} managed identity client ID is absent")
            app = resource_show(
                resource_group,
                app_name(values, mode),
                "Microsoft.App/containerApps",
                APP_API_VERSION,
            )
            endpoints[mode] = validate_app_document(
                app,
                values,
                mode,
                environment_id,
                worker_identity_id,
                expected_client_id,
            )
            auth = auth_config_show(subscription_id, resource_group, app_name(values, mode))
            validate_auth_document(auth, values, mode)
        print("PASS four apps use exact digest, distinct identities, bounded scale, and no secrets")
        print("PASS four auth policies require HTTPS and exact Entra caller/audience allowlists")
        if not args.skip_http:
            for mode, fqdn in endpoints.items():
                probe(mode, fqdn)
            print("PASS public health routes respond and protected routes reject anonymous callers")
        print("PASS deployed WP2b verification complete")
        return 0
    except VerificationError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())