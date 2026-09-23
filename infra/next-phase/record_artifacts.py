#!/usr/bin/env python3
"""Write sanitized and owner-only evidence for the WP3 artifact registry."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from artifact_parameters import ArtifactParameterError
from artifact_parameters import load_config
from artifact_parameters import parameter_values


SAFE_OUTPUTS = {"artifactResourceGroupName", "pullAssignmentCount", "expiresAt"}
REGISTRY_NAME = re.compile(r"^[a-z0-9]{5,50}$")
LOGIN_SERVER = re.compile(r"^[a-z0-9]{5,50}\.azurecr\.io$")


class ArtifactRecordError(RuntimeError):
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
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--registry-name", required=True)
    parser.add_argument("--login-server", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--private-state", type=Path, required=True)
    args = parser.parse_args()
    try:
        if REGISTRY_NAME.fullmatch(args.registry_name) is None or LOGIN_SERVER.fullmatch(args.login_server) is None:
            raise ArtifactRecordError("artifact registry endpoint is invalid")
        try:
            deployment = json.loads(args.deployment_result.read_text(encoding="utf-8"))
            values = parameter_values(args.parameters)
            config = load_config(args.config)
        except (OSError, json.JSONDecodeError, ArtifactParameterError) as error:
            raise ArtifactRecordError("unable to read artifact deployment evidence") from error
        properties = deployment.get("properties") or {}
        outputs = properties.get("outputs") or {}
        if properties.get("provisioningState") != "Succeeded" or set(outputs) != SAFE_OUTPUTS:
            raise ArtifactRecordError("artifact deployment result differs")
        safe_outputs = {name: item.get("value") for name, item in outputs.items()}
        record = {
            "schemaVersion": 1,
            "appliedAt": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
            "config": config,
            "parameters": values,
            "outputs": safe_outputs,
            "templateSha256": sha256(args.template),
            "parametersSha256": sha256(args.parameters),
            "provisioningState": "Succeeded",
        }
        serialized = json.dumps(record, indent=2, sort_keys=True) + "\n"
        if args.registry_name in serialized or args.login_server in serialized or "/subscriptions/" in serialized.casefold():
            raise ArtifactRecordError("sanitized artifact manifest contains a prohibited identifier")
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(serialized, encoding="utf-8")
        os.chmod(args.manifest, 0o600)
        private = {
            "schemaVersion": 1,
            "resourceGroupName": values["artifactResourceGroupName"],
            "registryName": args.registry_name,
            "loginServer": args.login_server,
            "expiresAt": values["expiresAt"],
        }
        args.private_state.write_text(json.dumps(private, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(args.private_state, 0o600)
        print("PASS wrote sanitized artifact evidence and owner-only registry state")
        return 0
    except (OSError, ArtifactRecordError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())