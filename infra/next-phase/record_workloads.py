#!/usr/bin/env python3
"""Write a sanitized evidence manifest after a WP2b deployment."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from workload_contract import SERVICE_KEYS
from workload_contract import app_name
from workload_parameters import WorkloadParameterError
from workload_parameters import load_config
from workload_parameters import parameter_values
from workload_release import ReleaseEvidenceError
from workload_release import load_release


ALLOWED_OUTPUTS = {"serviceNames"}
UUID_SEARCH = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)


class RecordError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-result", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--auth-file", type=Path, required=True)
    parser.add_argument("--image-reference-file", type=Path, required=True)
    parser.add_argument("--release-attestation", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--vulnerability-report", type=Path, required=True)
    parser.add_argument("--signature-verification", type=Path, required=True)
    parser.add_argument("--approval-schema", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        try:
            deployment = json.loads(args.deployment_result.read_text(encoding="utf-8"))
            config = load_config(args.config)
            values = parameter_values(args.parameters)
            release = load_release(
                args.release_attestation,
                values["imageReference"],
                artifacts={
                    "sbom": args.sbom,
                    "vulnerabilityReport": args.vulnerability_report,
                    "signatureVerification": args.signature_verification,
                    "approvalSchema": args.approval_schema,
                },
            )
        except (
            OSError,
            json.JSONDecodeError,
            WorkloadParameterError,
            ReleaseEvidenceError,
        ) as error:
            raise RecordError("unable to validate workload deployment evidence") from error
        properties = deployment.get("properties") or {}
        if properties.get("provisioningState") != "Succeeded":
            raise RecordError("Azure deployment did not report Succeeded")
        raw_outputs = properties.get("outputs") or {}
        if set(raw_outputs) != ALLOWED_OUTPUTS:
            raise RecordError("deployment outputs differ from the WP2b contract")
        outputs = {
            name: item.get("value")
            for name, item in raw_outputs.items()
            if isinstance(item, dict) and set(item) >= {"value"}
        }
        expected_names = sorted(app_name(values, mode) for mode in SERVICE_KEYS)
        if (
            set(outputs) != ALLOWED_OUTPUTS
            or sorted(outputs["serviceNames"]) != expected_names
        ):
            raise RecordError("deployment outputs do not match the requested workload")
        record = {
            "schemaVersion": 1,
            "deploymentName": deployment.get("name"),
            "appliedAt": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
            "configuration": config,
            "expiresAt": values["expiresAt"],
            "imageDigest": values["imageReference"].rsplit("@sha256:", 1)[1],
            "serviceNames": expected_names,
            "sourceRevision": release["sourceRevision"],
            "evidenceSha256": {
                "compiledTemplate": sha256(args.template),
                "privateParameters": sha256(args.parameters),
                "privateAuthInput": sha256(args.auth_file),
                "privateImageReference": sha256(args.image_reference_file),
                "releaseAttestation": sha256(args.release_attestation),
                "sbom": release["sbomSha256"],
                "vulnerabilityScan": release["vulnerabilityScanSha256"],
                "signatureVerification": release["signatureVerificationSha256"],
                "approvalSchema": release["approvalSchemaSha256"],
            },
            "approvalAuthority": "azure-sql",
            "provisioningState": "Succeeded",
        }
        serialized = json.dumps(record, indent=2, sort_keys=True) + "\n"
        if UUID_SEARCH.search(serialized) or "/subscriptions/" in serialized.casefold():
            raise RecordError("sanitized workload manifest contains an Azure identifier")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(args.output)
        print(f"PASS wrote sanitized WP2b evidence manifest to {args.output}")
        return 0
    except RecordError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())