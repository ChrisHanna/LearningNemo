#!/usr/bin/env python3
"""Validate the compiled scale-to-zero trusted-worker template."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ALLOWED_RESOURCE_TYPES = {
    "Microsoft.App/containerApps",
    "Microsoft.App/containerApps/authConfigs",
    "Microsoft.Resources/deployments",
}
EXPECTED_MODES = {"diagnostic", "query-runner", "remediation", "verifier"}


def resources_in(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        resources = value.get("resources")
        if isinstance(resources, list):
            found.extend(item for item in resources if isinstance(item, dict))
        for child in value.values():
            found.extend(resources_in(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(resources_in(child))
    return found


def env_values(app: dict[str, Any]) -> dict[str, str]:
    containers = (((app.get("properties") or {}).get("template") or {}).get("containers") or [])
    if len(containers) != 1 or not isinstance(containers[0], dict):
        return {}
    return {
        str(item.get("name", "")): str(item.get("value", ""))
        for item in containers[0].get("env") or []
        if isinstance(item, dict)
    }


def validate_app(app: dict[str, Any], failures: list[str]) -> None:
    name = str(app.get("name", "unnamed"))
    identity = app.get("identity") or {}
    user_identities = identity.get("userAssignedIdentities") or {}
    if identity.get("type") != "UserAssigned" or len(user_identities) != 1:
        failures.append(f"{name} must have exactly one user-assigned identity")
    properties = app.get("properties") or {}
    configuration = properties.get("configuration") or {}
    ingress = configuration.get("ingress") or {}
    if configuration.get("activeRevisionsMode") != "Single":
        failures.append(f"{name} must use single revision mode")
    if configuration.get("maxInactiveRevisions") != 1:
        failures.append(f"{name} must retain at most one inactive revision")
    if configuration.get("secrets") != []:
        failures.append(f"{name} must not contain application secrets")
    registries = configuration.get("registries") or []
    if len(registries) != 1:
        failures.append(f"{name} must contain one managed-identity registry binding")
    else:
        registry = registries[0]
        if (
            "registryserver" not in str(registry.get("server", "")).casefold()
            or "identityresourceid" not in str(registry.get("identity", "")).casefold()
            or "username" in registry
            or "passwordSecretRef" in registry
        ):
            failures.append(f"{name} registry authentication must use its managed identity")
    identity_settings = configuration.get("identitySettings") or []
    if len(identity_settings) != 1 or identity_settings[0].get("lifecycle") != "Main":
        failures.append(f"{name} identity must be available to main containers only")
    expected_ingress = {
        "allowInsecure": False,
        "clientCertificateMode": "ignore",
        "external": True,
        "targetPort": 8080,
        "transport": "auto",
    }
    if any(ingress.get(key) != value for key, value in expected_ingress.items()):
        failures.append(f"{name} HTTPS ingress contract differs")
    if properties.get("workloadProfileName") != "Consumption":
        failures.append(f"{name} must use the Consumption profile")
    template = properties.get("template") or {}
    scale = template.get("scale") or {}
    if scale.get("minReplicas") != 0 or scale.get("maxReplicas") != 1:
        failures.append(f"{name} must scale between zero and one replica")
    rules = scale.get("rules") or []
    if len(rules) != 1 or ((rules[0].get("http") or {}).get("metadata") or {}).get("concurrentRequests") != "1":
        failures.append(f"{name} must allow one concurrent request per replica")
    containers = template.get("containers") or []
    if len(containers) != 1:
        failures.append(f"{name} must contain exactly one worker container")
        return
    container = containers[0]
    if "imagereference" not in str(container.get("image", "")).casefold():
        failures.append(f"{name} image must come from the digest-validated parameter")
    resources = container.get("resources") or {}
    cpu = resources.get("cpu")
    if cpu not in {0.25, "[json('0.25')]"} or resources.get("memory") != "0.5Gi":
        failures.append(f"{name} CPU or memory limit differs")
    probes = container.get("probes") or []
    paths = {(probe.get("type"), (probe.get("httpGet") or {}).get("path")) for probe in probes}
    if paths != {("Startup", "/healthz"), ("Liveness", "/healthz"), ("Readiness", "/readyz")}:
        failures.append(f"{name} health probe contract differs")
    environment = env_values(app)
    if "controlcallerprincipalid" not in environment.get("LEARNINGNEMO_ALLOWED_CALLER_IDS", "").casefold():
        failures.append(f"{name} must receive only the private caller allowlist parameter")
    if "servicemode" not in environment.get("LEARNINGNEMO_SERVICE_MODE", "").casefold():
        failures.append(f"{name} service mode must come from its module parameter")
    for environment_name, parameter_name in (
        ("LEARNINGNEMO_SQL_SERVER", "sqlserverhostname"),
        ("LEARNINGNEMO_SQL_DATABASE", "sqldatabasename"),
        ("AZURE_CLIENT_ID", "identityclientid"),
    ):
        if parameter_name not in environment.get(environment_name, "").casefold():
            failures.append(f"{name} SQL setting differs: {environment_name}")


def validate_auth(auth: dict[str, Any], failures: list[str]) -> None:
    name = str(auth.get("name", "unnamed"))
    properties = auth.get("properties") or {}
    validation = properties.get("globalValidation") or {}
    if validation.get("unauthenticatedClientAction") != "Return401":
        failures.append(f"{name} must return 401 for unauthenticated requests")
    if set(validation.get("excludedPaths") or []) != {"/healthz", "/readyz"}:
        failures.append(f"{name} anonymous path allowlist differs")
    if (properties.get("httpSettings") or {}).get("requireHttps") is not True:
        failures.append(f"{name} must require HTTPS")
    if (properties.get("platform") or {}).get("enabled") is not True:
        failures.append(f"{name} platform authentication is disabled")
    if ((properties.get("login") or {}).get("tokenStore") or {}).get("enabled") is not False:
        failures.append(f"{name} token store must remain disabled")
    aad = ((properties.get("identityProviders") or {}).get("azureActiveDirectory") or {})
    if aad.get("enabled") is not True:
        failures.append(f"{name} Entra authentication is disabled")
    registration = aad.get("registration") or {}
    if "serviceapplicationid" not in str(registration.get("clientId", "")).casefold():
        failures.append(f"{name} service audience must come from a secure parameter")
    if "environment().authentication.loginendpoint" not in str(registration.get("openIdIssuer", "")).casefold():
        failures.append(f"{name} issuer must use the active cloud environment")
    aad_validation = aad.get("validation") or {}
    serialized = json.dumps(aad_validation, sort_keys=True).casefold()
    for required in ("allowedapplications", "allowedprincipals", "controlcallerapplicationid", "controlcallerprincipalid"):
        if required not in serialized:
            failures.append(f"{name} caller policy is missing: {required}")


def validate_template(template: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    expected_schema = "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#"
    if template.get("$schema") != expected_schema:
        failures.append("compiled workloads template uses an unexpected resource-group schema")
    resources = resources_in(template)
    resource_types = {str(resource.get("type", "")) for resource in resources}
    disallowed = sorted(resource_types - ALLOWED_RESOURCE_TYPES)
    if disallowed:
        failures.append(f"unapproved workload resource types: {', '.join(disallowed)}")
    apps = [resource for resource in resources if resource.get("type") == "Microsoft.App/containerApps"]
    auth_configs = [
        resource
        for resource in resources
        if resource.get("type") == "Microsoft.App/containerApps/authConfigs"
    ]
    if len(apps) != 4 or len(auth_configs) != 4:
        failures.append(f"expected four apps and four auth configs, found {len(apps)} and {len(auth_configs)}")
    for app in apps:
        validate_app(app, failures)
    modes = {
        str((((resource.get("properties") or {}).get("parameters") or {}).get("serviceMode") or {}).get("value", ""))
        for resource in resources
        if resource.get("type") == "Microsoft.Resources/deployments"
        and "serviceMode" in ((resource.get("properties") or {}).get("parameters") or {})
    }
    if modes != EXPECTED_MODES:
        failures.append("trusted worker mode inventory differs")
    bindings = [
        (resource.get("properties") or {}).get("parameters") or {}
        for resource in resources
        if resource.get("type") == "Microsoft.Resources/deployments"
        and "serviceMode" in ((resource.get("properties") or {}).get("parameters") or {})
    ]
    identity_bindings = {
        str((binding.get("identityResourceId") or {}).get("value", ""))
        for binding in bindings
    }
    audience_bindings = {
        str((binding.get("serviceApplicationId") or {}).get("value", ""))
        for binding in bindings
    }
    if len(bindings) != 4 or len(identity_bindings) != 4:
        failures.append("each trusted worker must bind a distinct managed identity")
    if len(audience_bindings) != 4:
        failures.append("each trusted worker must bind a distinct application audience")
    for mode in EXPECTED_MODES:
        normalized = mode.replace("-", "").casefold()
        if not any(normalized in value.replace("-", "").casefold() for value in identity_bindings):
            failures.append(f"managed identity binding is missing: {mode}")
        audience_key = "queryrunner" if mode == "query-runner" else normalized
        if not any(audience_key in value.casefold() for value in audience_bindings):
            failures.append(f"application audience binding is missing: {mode}")
    for auth in auth_configs:
        validate_auth(auth, failures)
    serialized = json.dumps(template, sort_keys=True).casefold()
    for required in (
        "scale-to-zero-trusted-workers",
        "wp2b-trusted-workers",
        "expiresat",
        "imagedigest",
    ):
        if required not in serialized:
            failures.append(f"workload evidence tag contract is missing: {required}")
    for prohibited in ("passwordsecretref", '"value": "bearer ', "microsoft.app/jobs"):
        if prohibited in serialized:
            failures.append(f"prohibited workload configuration detected: {prohibited}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    args = parser.parse_args()
    try:
        template = json.loads(args.template.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL unable to read compiled workload template: {error}", file=sys.stderr)
        return 1
    failures = validate_template(template)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print("PASS four least-authority trusted worker apps use distinct modes and identities")
    print("PASS every app scales to zero, caps at one replica, and has bounded resources")
    print("PASS Entra platform auth, caller allowlists, HTTPS, probes, and no-secret configuration match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())