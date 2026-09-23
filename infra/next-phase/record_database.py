#!/usr/bin/env python3
"""Write sanitized and owner-only evidence for a WP3 database deployment."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from database_parameters import DatabaseParameterError
from database_parameters import parameter_values


ALLOWED_OUTPUTS = {
    "databaseResourceGroupName",
    "databaseName",
    "sqlAdminIdentityName",
    "costProfile",
    "platformResourceGroupName",
}
SERVER_NAME = re.compile(r"^[a-z0-9-]{1,63}$")


class RecordError(RuntimeError):
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
    parser.add_argument("--server-name", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--private-state", type=Path, required=True)
    args = parser.parse_args()
    try:
        if SERVER_NAME.fullmatch(args.server_name) is None:
            raise RecordError("SQL server name is invalid")
        try:
            deployment = json.loads(args.deployment_result.read_text(encoding="utf-8"))
            values = parameter_values(args.parameters)
        except (OSError, json.JSONDecodeError, DatabaseParameterError) as error:
            raise RecordError("unable to read database deployment evidence") from error
        properties = deployment.get("properties") or {}
        if properties.get("provisioningState") != "Succeeded":
            raise RecordError("database deployment did not report Succeeded")
        outputs = properties.get("outputs") or {}
        if set(outputs) != ALLOWED_OUTPUTS:
            raise RecordError("database deployment outputs differ")
        safe_outputs = {name: item.get("value") for name, item in outputs.items()}
        record = {
            "schemaVersion": 1,
            "appliedAt": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
            "parameters": values,
            "outputs": safe_outputs,
            "templateSha256": sha256(args.template),
            "parametersSha256": sha256(args.parameters),
            "provisioningState": "Succeeded",
        }
        serialized = json.dumps(record, indent=2, sort_keys=True) + "\n"
        if args.server_name in serialized or "/subscriptions/" in serialized.casefold():
            raise RecordError("sanitized database manifest contains a prohibited identifier")
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(serialized, encoding="utf-8")
        os.chmod(args.manifest, 0o600)
        private = {
            "schemaVersion": 1,
            "resourceGroupName": values["databaseResourceGroupName"],
            "serverName": args.server_name,
            "databaseName": values["databaseName"],
            "sqlAdminIdentityName": safe_outputs["sqlAdminIdentityName"],
        }
        args.private_state.write_text(json.dumps(private, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(args.private_state, 0o600)
        print("PASS wrote sanitized WP3 manifest and owner-only SQL endpoint state")
        return 0
    except (OSError, RecordError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())