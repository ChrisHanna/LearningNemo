#!/usr/bin/env python3
"""Record a sanitized receipt for one successful hash-bound SQL migration execution."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from migration_contract import job_name
from migration_parameters import MigrationParameterError
from migration_parameters import load_config
from migration_parameters import parameter_values
from workload_release import ReleaseEvidenceError
from workload_release import load_release


ALLOWED_OUTPUTS = {
    "migrationResourceGroupName",
    "migrationJobName",
    "migrationBundleSha256",
    "expiresAt",
}
UUID_SEARCH = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)


class MigrationRecordError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-result", type=Path, required=True)
    parser.add_argument("--execution-result", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--release-attestation", type=Path, required=True)
    parser.add_argument("--image-reference-file", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--vulnerability-report", type=Path, required=True)
    parser.add_argument("--signature-verification", type=Path, required=True)
    parser.add_argument("--approval-schema", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        try:
            deployment = json.loads(args.deployment_result.read_text(encoding="utf-8"))
            execution = json.loads(args.execution_result.read_text(encoding="utf-8"))
            values = parameter_values(args.parameters)
            config = load_config(args.config)
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
            MigrationParameterError,
            ReleaseEvidenceError,
        ) as error:
            raise MigrationRecordError("unable to validate migration evidence") from error
        properties = deployment.get("properties") or {}
        outputs = properties.get("outputs") or {}
        execution_status = (execution.get("properties") or {}).get("status") or execution.get("status")
        if properties.get("provisioningState") != "Succeeded" or set(outputs) != ALLOWED_OUTPUTS:
            raise MigrationRecordError("migration deployment result differs")
        if execution_status != "Succeeded":
            raise MigrationRecordError("migration execution did not report Succeeded")
        safe_outputs = {name: item.get("value") for name, item in outputs.items()}
        if (
            safe_outputs["migrationJobName"] != job_name(values)
            or safe_outputs["migrationBundleSha256"] != values["migrationBundleSha256"]
        ):
            raise MigrationRecordError("migration outputs differ from the requested bundle")
        record = {
            "schemaVersion": 1,
            "appliedAt": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
            "configuration": config,
            "migrationBundleSha256": values["migrationBundleSha256"],
            "imageDigest": values["imageReference"].rsplit("@sha256:", 1)[1],
            "sourceRevision": release["sourceRevision"],
            "executionStatus": "Succeeded",
            "receiptCount": 6,
            "evidenceSha256": {
                "compiledTemplate": sha256(args.template),
                "privateParameters": sha256(args.parameters),
                "releaseAttestation": sha256(args.release_attestation),
                "executionResult": sha256(args.execution_result),
            },
        }
        serialized = json.dumps(record, indent=2, sort_keys=True) + "\n"
        for prohibited in (
            values["sqlServerHostname"],
            values["registryServer"],
            values["imageReference"],
            "/subscriptions/",
        ):
            if prohibited.casefold() in serialized.casefold():
                raise MigrationRecordError("sanitized migration receipt contains a prohibited identifier")
        if UUID_SEARCH.search(serialized):
            raise MigrationRecordError("sanitized migration receipt contains an Azure identifier")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
        os.chmod(args.output, 0o600)
        print("PASS wrote sanitized six-receipt SQL migration evidence")
        return 0
    except (OSError, MigrationRecordError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())