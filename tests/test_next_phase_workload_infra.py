from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest


sys.dont_write_bytecode = True
ROOT = Path(__file__).parents[1]
PHASE_DIR = ROOT / "infra" / "next-phase"
CONFIG = PHASE_DIR / "environments" / "dev.workloads.config.json"
sys.path.insert(0, str(PHASE_DIR))

import workload_parameters
import check_workload_cleanup
import entra_workload_parameters
import preflight_workloads
import record_entra_workloads
import record_workloads
import runtime_parameters
import verify_workloads
import workload_contract
import workload_release


def load_hyphenated_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_workloads = load_hyphenated_module(
    "validate_workloads",
    PHASE_DIR / "validate-workloads.py",
)


def auth_document() -> dict[str, object]:
    return {
        "tenantId": "11111111-1111-4111-8111-111111111111",
        "controlCallerApplicationId": "22222222-2222-4222-8222-222222222222",
        "controlCallerPrincipalId": "33333333-3333-4333-8333-333333333333",
        "serviceApplicationIds": {
            "diagnostic": "44444444-4444-4444-8444-444444444444",
            "queryRunner": "55555555-5555-4555-8555-555555555555",
            "remediation": "66666666-6666-4666-8666-666666666666",
            "verifier": "77777777-7777-4777-8777-777777777777",
        },
    }


def service_app() -> dict[str, object]:
    return {
        "type": "Microsoft.App/containerApps",
        "name": "app",
        "identity": {
            "type": "UserAssigned",
            "userAssignedIdentities": {"[parameters('identityResourceId')]": {}},
        },
        "properties": {
            "environmentId": "[parameters('environmentId')]",
            "workloadProfileName": "Consumption",
            "configuration": {
                "activeRevisionsMode": "Single",
                "maxInactiveRevisions": 1,
                "identitySettings": [
                    {"identity": "[parameters('identityResourceId')]", "lifecycle": "Main"}
                ],
                "ingress": {
                    "allowInsecure": False,
                    "clientCertificateMode": "ignore",
                    "external": True,
                    "targetPort": 8080,
                    "transport": "auto",
                },
                "registries": [
                    {
                        "server": "[parameters('registryServer')]",
                        "identity": "[parameters('identityResourceId')]",
                    }
                ],
                "secrets": [],
            },
            "template": {
                "containers": [
                    {
                        "image": "[parameters('imageReference')]",
                        "env": [
                            {
                                "name": "LEARNINGNEMO_SERVICE_MODE",
                                "value": "[parameters('serviceMode')]",
                            },
                            {
                                "name": "LEARNINGNEMO_ALLOWED_CALLER_IDS",
                                "value": "[parameters('controlCallerPrincipalId')]",
                            },
                            {
                                "name": "LEARNINGNEMO_SQL_SERVER",
                                "value": "[parameters('sqlServerHostname')]",
                            },
                            {
                                "name": "LEARNINGNEMO_SQL_DATABASE",
                                "value": "[parameters('sqlDatabaseName')]",
                            },
                            {
                                "name": "AZURE_CLIENT_ID",
                                "value": "[parameters('identityClientId')]",
                            },
                        ],
                        "probes": [
                            {"type": "Startup", "httpGet": {"path": "/healthz"}},
                            {"type": "Liveness", "httpGet": {"path": "/healthz"}},
                            {"type": "Readiness", "httpGet": {"path": "/readyz"}},
                        ],
                        "resources": {"cpu": 0.25, "memory": "0.5Gi"},
                    }
                ],
                "scale": {
                    "minReplicas": 0,
                    "maxReplicas": 1,
                    "rules": [
                        {"http": {"metadata": {"concurrentRequests": "1"}}}
                    ],
                },
            },
        },
    }


def auth_config() -> dict[str, object]:
    return {
        "type": "Microsoft.App/containerApps/authConfigs",
        "name": "app/current",
        "properties": {
            "globalValidation": {
                "excludedPaths": ["/healthz", "/readyz"],
                "unauthenticatedClientAction": "Return401",
            },
            "httpSettings": {"requireHttps": True},
            "identityProviders": {
                "azureActiveDirectory": {
                    "enabled": True,
                    "registration": {
                        "clientId": "[parameters('serviceApplicationId')]",
                        "openIdIssuer": "[uri(environment().authentication.loginEndpoint, parameters('tenantId'))]",
                    },
                    "validation": {
                        "allowedAudiences": ["[parameters('serviceApplicationId')]"],
                        "defaultAuthorizationPolicy": {
                            "allowedApplications": ["[parameters('controlCallerApplicationId')]"],
                            "allowedPrincipals": {
                                "identities": ["[parameters('controlCallerPrincipalId')]"],
                            },
                        },
                        "jwtClaimChecks": {
                            "allowedClientApplications": ["[parameters('controlCallerApplicationId')]"],
                        },
                    },
                }
            },
            "login": {"tokenStore": {"enabled": False}},
            "platform": {"enabled": True},
        },
    }


def safe_template() -> dict[str, object]:
    resources: list[dict[str, object]] = []
    for mode in sorted(validate_workloads.EXPECTED_MODES):
        audience_key = "queryRunner" if mode == "query-runner" else mode
        resources.extend(
            [
                {
                    "type": "Microsoft.Resources/deployments",
                    "properties": {
                        "parameters": {
                            "serviceMode": {"value": mode},
                            "identityResourceId": {"value": f"identity-{mode}"},
                            "serviceApplicationId": {
                                "value": f"[parameters('serviceApplicationIds').{audience_key}]"
                            },
                        }
                    },
                },
                service_app(),
                auth_config(),
            ]
        )
    return {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "variables": {
            "tags": "scale-to-zero-trusted-workers wp2b-trusted-workers expiresAt imageDigest"
        },
        "resources": resources,
    }


def materialized_inputs(tmp_path: Path, expires_at: str = "2099-01-01T08:00:00Z") -> tuple[Path, Path, Path]:
    auth_path = tmp_path / "auth.json"
    image_path = tmp_path / "image.txt"
    parameters_path = tmp_path / "workloads.parameters.json"
    auth_path.write_text(json.dumps(auth_document()), encoding="utf-8")
    image_path.write_text(
        f"example.azurecr.io/learningnemo/trusted-runtime@sha256:{'a' * 64}\n",
        encoding="utf-8",
    )
    database_state, artifact_state = connectivity_inputs(tmp_path)
    workload_parameters.materialize(
        CONFIG,
        auth_path,
        image_path,
        parameters_path,
        expires_at,
        database_state,
        artifact_state,
    )
    return auth_path, image_path, parameters_path


def connectivity_inputs(
    tmp_path: Path,
    artifact_expires_at: str = "2099-01-01T08:00:00Z",
) -> tuple[Path, Path]:
    database_state = tmp_path / "database.state.json"
    database_state.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "resourceGroupName": "rg-learningnemo-data-dev",
                "serverName": "sql-learningnemo-generated",
                "databaseName": "learningnemo",
                "sqlAdminIdentityName": "id-learningnemo-sql-admin-dev",
            }
        ),
        encoding="utf-8",
    )
    artifact_state = tmp_path / "artifacts.state.json"
    artifact_state.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "resourceGroupName": "rg-learningnemo-artifacts-dev",
                "registryName": "example",
                "loginServer": "example.azurecr.io",
                "expiresAt": artifact_expires_at,
            }
        ),
        encoding="utf-8",
    )
    return database_state, artifact_state


def release_inputs(tmp_path: Path, image_reference: str) -> tuple[Path, dict[str, Path]]:
    artifacts = {
        "sbom": tmp_path / "sbom.json",
        "vulnerabilityReport": tmp_path / "scan.json",
        "signatureVerification": tmp_path / "signature.json",
        "approvalSchema": tmp_path / "approval.sql",
    }
    for name, path in artifacts.items():
        path.write_text(f"evidence:{name}\n", encoding="utf-8")
    release = {
        "schemaVersion": 1,
        "imageReference": image_reference,
        "sourceRevision": "b" * 40,
        "builtAt": "2020-01-01T00:00:00Z",
        "sbomSha256": workload_release.sha256(artifacts["sbom"]),
        "vulnerabilityScanSha256": workload_release.sha256(artifacts["vulnerabilityReport"]),
        "signatureVerificationSha256": workload_release.sha256(artifacts["signatureVerification"]),
        "signatureVerified": True,
        "criticalVulnerabilities": 0,
        "highVulnerabilities": 0,
        "approvalAuthority": "azure-sql",
        "approvalSchemaSha256": workload_release.sha256(artifacts["approvalSchema"]),
    }
    attestation = tmp_path / "release.json"
    attestation.write_text(json.dumps(release), encoding="utf-8")
    return attestation, artifacts


def live_app(values: dict[str, object], mode: str) -> tuple[dict[str, object], str, str, str]:
    subscription = "subscription"
    resource_group = values["platformResourceGroupName"]
    environment_id = (
        f"/subscriptions/{subscription}/resourceGroups/{resource_group}/providers/"
        f"Microsoft.App/managedEnvironments/{values['containerAppsEnvironmentName']}"
    )
    identity_id = (
        f"/subscriptions/{subscription}/resourceGroups/{resource_group}/providers/"
        "Microsoft.ManagedIdentity/userAssignedIdentities/"
        f"{workload_contract.identity_name(values, mode)}"
    )
    client_id = "88888888-8888-4888-8888-888888888888"
    app = service_app()
    app["name"] = workload_contract.app_name(values, mode)
    app["location"] = values["location"]
    app["tags"] = workload_contract.workload_resource_tags(values, mode)
    app["identity"]["userAssignedIdentities"] = {identity_id: {}}
    properties = app["properties"]
    properties["provisioningState"] = "Succeeded"
    properties["environmentId"] = environment_id
    properties["configuration"]["identitySettings"] = [
        {"identity": identity_id, "lifecycle": "Main"}
    ]
    properties["configuration"]["registries"] = [
        {"server": values["registryServer"], "identity": identity_id}
    ]
    properties["configuration"]["ingress"]["fqdn"] = "worker.invalid"
    container = properties["template"]["containers"][0]
    container["name"] = "worker"
    container["image"] = values["imageReference"]
    container["args"] = ["--port", "8080"]
    container["env"][0]["value"] = mode
    container["env"][1]["value"] = values["controlCallerPrincipalId"]
    container["env"][2]["value"] = values["sqlServerHostname"]
    container["env"][3]["value"] = values["databaseName"]
    container["env"][4]["value"] = client_id
    return app, environment_id, identity_id, client_id


def live_auth(values: dict[str, object], mode: str) -> dict[str, object]:
    auth = auth_config()
    application_id = values["serviceApplicationIds"][workload_contract.SERVICE_KEYS[mode]]
    aad = auth["properties"]["identityProviders"]["azureActiveDirectory"]
    aad["registration"] = {
        "clientId": application_id,
        "openIdIssuer": f"https://login.microsoftonline.com/{values['tenantId']}/v2.0",
    }
    aad["validation"]["allowedAudiences"] = [f"api://{application_id}"]
    aad["validation"]["defaultAuthorizationPolicy"]["allowedApplications"] = [
        values["controlCallerApplicationId"]
    ]
    aad["validation"]["defaultAuthorizationPolicy"]["allowedPrincipals"]["identities"] = [
        values["controlCallerPrincipalId"]
    ]
    aad["validation"]["jwtClaimChecks"]["allowedClientApplications"] = [
        values["controlCallerApplicationId"]
    ]
    return auth


def test_workload_config_contains_no_image_or_identity_values() -> None:
    config = workload_parameters.load_config(CONFIG)
    serialized = json.dumps(config).casefold()

    assert "sha256:" not in serialized
    assert not any(name.casefold().endswith("id") for name in config)
    assert "@" not in serialized


def test_workload_parameters_materialize_private_digest_and_auth(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    image_path = tmp_path / "image.txt"
    output = tmp_path / "workloads.parameters.json"
    auth_path.write_text(json.dumps(auth_document()), encoding="utf-8")
    image_path.write_text(
        f"example.azurecr.io/learningnemo/trusted-runtime@sha256:{'a' * 64}\n",
        encoding="utf-8",
    )
    database_state, artifact_state = connectivity_inputs(tmp_path)

    workload_parameters.materialize(
        CONFIG,
        auth_path,
        image_path,
        output,
        "2099-01-01T08:00:00Z",
        database_state,
        artifact_state,
    )

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["parameters"]["imageReference"]["value"].endswith("a" * 64)
    assert document["parameters"]["serviceApplicationIds"]["value"] == auth_document()["serviceApplicationIds"]
    assert document["parameters"]["registryServer"]["value"] == "example.azurecr.io"
    assert document["parameters"]["sqlServerHostname"]["value"].endswith(".database.windows.net")
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_workload_parameters_reject_image_tag_and_shared_audience(tmp_path: Path) -> None:
    auth = auth_document()
    auth["serviceApplicationIds"]["verifier"] = auth["serviceApplicationIds"]["diagnostic"]
    auth_path = tmp_path / "auth.json"
    image_path = tmp_path / "image.txt"
    auth_path.write_text(json.dumps(auth), encoding="utf-8")
    image_path.write_text("ghcr.io/example/learningnemo:latest\n", encoding="utf-8")

    with pytest.raises(workload_parameters.WorkloadParameterError):
        workload_parameters.load_auth(auth_path)
    with pytest.raises(workload_parameters.WorkloadParameterError):
        workload_parameters.validate_image_reference(image_path.read_text().strip())


def test_compiled_workload_policy_accepts_secure_scale_to_zero_shape() -> None:
    assert validate_workloads.validate_template(safe_template()) == []


def test_compiled_workload_policy_rejects_anonymous_or_always_on_app() -> None:
    template = safe_template()
    app = next(resource for resource in template["resources"] if resource["type"] == "Microsoft.App/containerApps")
    app["properties"]["template"]["scale"]["minReplicas"] = 1
    auth = next(
        resource
        for resource in template["resources"]
        if resource["type"] == "Microsoft.App/containerApps/authConfigs"
    )
    auth["properties"]["globalValidation"]["unauthenticatedClientAction"] = "AllowAnonymous"

    failures = validate_workloads.validate_template(template)

    assert any("scale between zero and one" in failure for failure in failures)
    assert any("return 401" in failure for failure in failures)


def test_materialized_parameters_round_trip_and_local_ttl(tmp_path: Path) -> None:
    _, _, parameters = materialized_inputs(tmp_path)
    values = workload_parameters.parameter_values(parameters)

    assert values["serviceApplicationIds"] == auth_document()["serviceApplicationIds"]
    combined, runtime = preflight_workloads.validate_local_inputs(
        CONFIG,
        parameters,
        PHASE_DIR / "environments" / "dev.runtime.parameters.json",
        now=dt.datetime(2099, 1, 1, 7, tzinfo=dt.UTC),
    )
    assert combined["expiresAt"] == "2099-01-01T08:00:00Z"
    assert runtime["monthlyCostCeiling"] == 50


def test_local_preflight_rejects_lifetime_over_24_hours(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    image_path = tmp_path / "image.txt"
    parameters = tmp_path / "workloads.parameters.json"
    auth_path.write_text(json.dumps(auth_document()), encoding="utf-8")
    image_path.write_text(
        f"example.azurecr.io/learningnemo/trusted-runtime@sha256:{'a' * 64}\n",
        encoding="utf-8",
    )
    database_state, artifact_state = connectivity_inputs(
        tmp_path,
        artifact_expires_at="2099-01-03T08:00:00Z",
    )
    workload_parameters.materialize(
        CONFIG,
        auth_path,
        image_path,
        parameters,
        "2099-01-02T08:00:01Z",
        database_state,
        artifact_state,
    )

    with pytest.raises(preflight_workloads.WorkloadPreflightError, match="exceeds 24 hours"):
        preflight_workloads.validate_local_inputs(
            CONFIG,
            parameters,
            PHASE_DIR / "environments" / "dev.runtime.parameters.json",
            now=dt.datetime(2099, 1, 1, 8, tzinfo=dt.UTC),
        )


def test_workload_expiration_cannot_exceed_artifact_or_network_overlay(
    tmp_path: Path,
) -> None:
    _, _, parameters = materialized_inputs(tmp_path, "2099-01-01T08:00:00Z")
    values = workload_contract.combined_values(
        workload_parameters.load_config(CONFIG),
        workload_parameters.parameter_values(parameters),
    )
    artifact_parameters = tmp_path / "artifacts.parameters.json"
    artifact_parameters.write_text(
        json.dumps(
            {
                "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
                "contentVersion": "1.0.0.0",
                "parameters": {
                    name: {"value": value}
                    for name, value in {
                        "location": "eastus",
                        "environment": "dev",
                        "projectName": "learningnemo",
                        "artifactResourceGroupName": "rg-learningnemo-artifacts-dev",
                        "platformResourceGroupName": "rg-learningnemo-platform-dev",
                        "databaseResourceGroupName": "rg-learningnemo-data-dev",
                        "ownerTag": "learningnemo-portfolio",
                        "monthlyCostCeiling": 50,
                        "additionalTags": {
                            "dataClassification": "synthetic",
                            "workload": "agent-security-lab",
                        },
                        "expiresAt": "2099-01-01T07:59:59Z",
                    }.items()
                },
            }
        ),
        encoding="utf-8",
    )
    network_parameters = tmp_path / "network.parameters.json"
    network_parameters.write_text(
        json.dumps(
            {
                "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
                "contentVersion": "1.0.0.0",
                "parameters": {
                    name: {"value": value}
                    for name, value in {
                        "location": "eastus",
                        "environment": "dev",
                        "projectName": "learningnemo",
                        "databaseNetworkResourceGroupName": "rg-learningnemo-data-network-dev",
                        "databaseResourceGroupName": "rg-learningnemo-data-dev",
                        "sqlServerName": "sql-learningnemo-generated",
                        "platformResourceGroupName": "rg-learningnemo-platform-dev",
                        "platformVnetName": "vnet-learningnemo-platform-dev",
                        "privateEndpointSubnetName": "snet-private-endpoints",
                        "expiresAt": "2099-01-01T09:00:00Z",
                        "ownerTag": "learningnemo-portfolio",
                        "additionalTags": {
                            "dataClassification": "synthetic",
                            "workload": "agent-security-lab",
                        },
                    }.items()
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(preflight_workloads.WorkloadPreflightError, match="artifact registry expiration"):
        preflight_workloads.validate_dependency_expirations(
            values,
            artifact_parameters,
            network_parameters,
        )


def test_release_evidence_rehashes_every_artifact(tmp_path: Path) -> None:
    _, image_file, _ = materialized_inputs(tmp_path)
    image_reference = image_file.read_text(encoding="utf-8").strip()
    attestation, artifacts = release_inputs(tmp_path, image_reference)

    document = workload_release.load_release(
        attestation,
        image_reference,
        artifacts=artifacts,
        expected_source_revision="b" * 40,
        now=dt.datetime(2099, 1, 1, 7, 1, tzinfo=dt.UTC),
    )
    assert document["signatureVerified"] is True
    with pytest.raises(workload_release.ReleaseEvidenceError, match="current trusted image source"):
        workload_release.load_release(
            attestation,
            image_reference,
            artifacts=artifacts,
            expected_source_revision="c" * 40,
            now=dt.datetime(2099, 1, 1, 7, 1, tzinfo=dt.UTC),
        )
    artifacts["vulnerabilityReport"].write_text("tampered\n", encoding="utf-8")
    with pytest.raises(workload_release.ReleaseEvidenceError, match="artifact hash differs"):
        workload_release.load_release(
            attestation,
            image_reference,
            artifacts=artifacts,
            now=dt.datetime(2099, 1, 1, 7, 1, tzinfo=dt.UTC),
        )


def test_service_audience_must_be_credentialless_tenant_only_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application_id = auth_document()["serviceApplicationIds"]["diagnostic"]

    def fake_az_json(arguments: list[str], *, timeout: int = 60):
        del timeout
        if arguments[:3] == ["ad", "app", "show"]:
            return {
                "appId": application_id,
                "signInAudience": "AzureADMyOrg",
                "identifierUris": [f"api://{application_id}"],
                "api": {"requestedAccessTokenVersion": 2},
                "passwordCredentials": [],
                "keyCredentials": [],
                "publicClient": {"redirectUris": []},
                "spa": {"redirectUris": []},
                "web": {"redirectUris": []},
            }
        return {"appId": application_id, "accountEnabled": True}

    monkeypatch.setattr(preflight_workloads, "az_json", fake_az_json)
    preflight_workloads.validate_audience_application(application_id)


def test_live_verifier_rejects_secrets_and_widened_auth(tmp_path: Path) -> None:
    _, _, parameters = materialized_inputs(tmp_path)
    values = workload_contract.combined_values(
        workload_parameters.load_config(CONFIG),
        workload_parameters.parameter_values(parameters),
    )
    app, environment_id, identity_id, client_id = live_app(values, "diagnostic")
    auth = live_auth(values, "diagnostic")

    assert verify_workloads.validate_app_document(
        app, values, "diagnostic", environment_id, identity_id, client_id
    ) == "worker.invalid"
    verify_workloads.validate_auth_document(auth, values, "diagnostic")
    app["properties"]["configuration"]["secrets"] = [{"name": "unexpected"}]
    auth["properties"]["globalValidation"]["unauthenticatedClientAction"] = "AllowAnonymous"
    with pytest.raises(verify_workloads.VerificationError, match="secrets"):
        verify_workloads.validate_app_document(
            app, values, "diagnostic", environment_id, identity_id, client_id
        )
    with pytest.raises(verify_workloads.VerificationError, match="anonymous"):
        verify_workloads.validate_auth_document(auth, values, "diagnostic")


def test_cleanup_accepts_owned_subset_and_rejects_foreign_app(tmp_path: Path) -> None:
    _, _, parameters = materialized_inputs(tmp_path)
    config = workload_parameters.load_config(CONFIG)
    values = workload_contract.combined_values(config, workload_parameters.parameter_values(parameters))
    app, _, _, _ = live_app(values, "verifier")

    assert check_workload_cleanup.validate_cleanup_inventory(config, [app], values) == {"verifier"}
    foreign = copy.deepcopy(app)
    foreign["name"] = "ca-foreign"
    with pytest.raises(check_workload_cleanup.CleanupError, match="outside"):
        check_workload_cleanup.validate_cleanup_inventory(config, [foreign], values)


def test_workload_manifest_is_sanitized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    auth_file, image_file, parameters = materialized_inputs(tmp_path)
    image_reference = image_file.read_text(encoding="utf-8").strip()
    attestation, artifacts = release_inputs(tmp_path, image_reference)
    values = workload_parameters.parameter_values(parameters)
    deployment = tmp_path / "deployment.json"
    deployment.write_text(
        json.dumps(
            {
                "name": "learningnemo-trusted-workers-dev",
                "properties": {
                    "provisioningState": "Succeeded",
                    "outputs": {
                        "serviceNames": {
                            "value": [
                                workload_contract.app_name(values, mode)
                                for mode in workload_contract.SERVICE_KEYS
                            ]
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    template = tmp_path / "template.json"
    template.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "manifest.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "record_workloads.py",
            "--deployment-result",
            str(deployment),
            "--template",
            str(template),
            "--parameters",
            str(parameters),
            "--config",
            str(CONFIG),
            "--auth-file",
            str(auth_file),
            "--image-reference-file",
            str(image_file),
            "--release-attestation",
            str(attestation),
            "--sbom",
            str(artifacts["sbom"]),
            "--vulnerability-report",
            str(artifacts["vulnerabilityReport"]),
            "--signature-verification",
            str(artifacts["signatureVerification"]),
            "--approval-schema",
            str(artifacts["approvalSchema"]),
            "--output",
            str(output),
        ],
    )

    assert record_workloads.main() == 0
    serialized = output.read_text(encoding="utf-8").casefold()
    assert "11111111-1111-4111-8111-111111111111" not in serialized
    assert "controlcaller" not in serialized
    assert image_reference not in serialized
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_workload_mutations_require_subscription_evidence_and_acknowledgement() -> None:
    deploy = (PHASE_DIR / "deploy-workloads.sh").read_text(encoding="utf-8")
    remove = (PHASE_DIR / "remove-workloads.sh").read_text(encoding="utf-8")

    assert 'mode="what-if"' in deploy
    assert "AZURE_SUBSCRIPTION_ID" in deploy and "AZURE_SUBSCRIPTION_ID" in remove
    assert "LEARNINGNEMO_AZURE_APPLY" in deploy
    assert "LEARNINGNEMO_AZURE_DELETE" in remove
    assert deploy.count("require_private_file") >= 8
    assert "--require-budget" in deploy
    assert "summarize_what_if.py" in deploy
    assert "workload_release.py" in deploy
    assert "preflight_workloads.py" in deploy
    assert "verify_workloads.py" in deploy
    assert "check_workload_cleanup.py" in remove
    assert "snapshot_foundation.py" in remove
    assert "--expired-only" in remove


def graph_application(config: dict[str, object], mode: str, application_id: str) -> dict[str, object]:
    name = entra_workload_parameters.unique_name(config, mode)
    return {
        "appId": application_id,
        "uniqueName": name,
        "displayName": name,
        "tags": ["learningnemo", "wp2b-trusted-worker", mode, config["environment"]],
        "signInAudience": "AzureADMyOrg",
        "identifierUris": [f"api://{application_id}"],
        "api": {"requestedAccessTokenVersion": 2},
        "passwordCredentials": [],
        "keyCredentials": [],
    }


def test_entra_materializer_keeps_generated_ids_private(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = workload_parameters.load_config(CONFIG)
    ids = list(auth_document()["serviceApplicationIds"].values())
    applications = {
        mode: graph_application(config, mode, application_id)
        for mode, application_id in zip(workload_contract.SERVICE_KEYS, ids, strict=True)
    }

    def fake_az_json(arguments: list[str], *, timeout: int = 45):
        del timeout
        if arguments[:2] == ["account", "show"]:
            return {"id": "subscription", "tenantId": auth_document()["tenantId"]}
        if arguments[:2] == ["identity", "show"]:
            return {
                "clientId": auth_document()["controlCallerApplicationId"],
                "principalId": auth_document()["controlCallerPrincipalId"],
            }
        if arguments[0] == "rest":
            url = arguments[arguments.index("--url") + 1]
            match = next(mode for mode in workload_contract.SERVICE_KEYS if mode in url)
            return {"value": [applications[match]]}
        raise AssertionError(f"unexpected Azure query: {arguments}")

    monkeypatch.setattr(entra_workload_parameters, "az_json", fake_az_json)
    auth_output = tmp_path / "private-auth.json"
    parameters_output = tmp_path / "private-parameters.json"
    entra_workload_parameters.materialize(CONFIG, auth_output, parameters_output)

    assert workload_parameters.load_auth(auth_output) == auth_document()
    assert stat.S_IMODE(auth_output.stat().st_mode) == 0o600
    assert stat.S_IMODE(parameters_output.stat().st_mode) == 0o600
    assert json.loads(parameters_output.read_text())["parameters"]["serviceApplicationIds"]["value"] == auth_document()["serviceApplicationIds"]


def test_entra_discovery_rejects_partial_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = workload_parameters.load_config(CONFIG)
    application_id = auth_document()["serviceApplicationIds"]["diagnostic"]

    def fake_az_json(arguments: list[str], *, timeout: int = 45):
        del timeout
        url = arguments[arguments.index("--url") + 1]
        return {
            "value": [graph_application(config, "diagnostic", application_id)]
            if "diagnostic" in url
            else []
        }

    monkeypatch.setattr(entra_workload_parameters, "az_json", fake_az_json)
    with pytest.raises(entra_workload_parameters.EntraParameterError, match="partial"):
        entra_workload_parameters.discover(config)


def test_entra_manifest_omits_generated_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps(auth_document()), encoding="utf-8")
    registrations = tmp_path / "registrations.json"
    audiences = tmp_path / "audiences.json"
    registrations.write_text("{}\n", encoding="utf-8")
    audiences.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "manifest.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "record_entra_workloads.py",
            "--config",
            str(CONFIG),
            "--auth-file",
            str(auth_file),
            "--registrations-template",
            str(registrations),
            "--audiences-template",
            str(audiences),
            "--output",
            str(output),
        ],
    )

    assert record_entra_workloads.main() == 0
    serialized = output.read_text(encoding="utf-8")
    assert not any(identifier in serialized for identifier in ids_from_auth(auth_document()))
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def ids_from_auth(auth: dict[str, object]) -> list[str]:
    return [
        auth["tenantId"],
        auth["controlCallerApplicationId"],
        auth["controlCallerPrincipalId"],
        *auth["serviceApplicationIds"].values(),
    ]


def test_entra_mutation_requires_subscription_and_specific_acknowledgement() -> None:
    deploy = (PHASE_DIR / "deploy-entra-workloads.sh").read_text(encoding="utf-8")

    assert 'mode="what-if"' in deploy
    assert "AZURE_SUBSCRIPTION_ID" in deploy
    assert "LEARNINGNEMO_AZURE_APPLY" in deploy
    assert "trusted-workload-audiences" in deploy
    assert deploy.count("az deployment group what-if") == 2
    assert "validate-entra-workloads.py" in deploy
    assert "record_entra_workloads.py" in deploy