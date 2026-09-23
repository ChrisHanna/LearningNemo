#!/usr/bin/env python3
"""Write sanitized evidence for the WP3 SQL private-network overlay."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

from database_network_parameters import DatabaseNetworkParameterError
from database_network_parameters import load_config
from database_network_parameters import parameter_values


ALLOWED_OUTPUTS = {"databaseNetworkResourceGroupName", "expiresAt", "privateEndpointName"}


class RecordDatabaseNetworkError(RuntimeError):
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
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        try:
            deployment = json.loads(args.deployment_result.read_text(encoding="utf-8"))
            values = parameter_values(args.parameters)
            config = load_config(args.config)
        except (OSError, json.JSONDecodeError, DatabaseNetworkParameterError) as error:
            raise RecordDatabaseNetworkError("unable to read database network evidence") from error
        properties = deployment.get("properties") or {}
        outputs = properties.get("outputs") or {}
        if properties.get("provisioningState") != "Succeeded" or set(outputs) != ALLOWED_OUTPUTS:
            raise RecordDatabaseNetworkError("database network deployment result differs")
        record = {
            "schemaVersion": 1,
            "appliedAt": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
            "config": config,
            "expiresAt": values["expiresAt"],
            "outputs": {name: item.get("value") for name, item in outputs.items()},
            "templateSha256": sha256(args.template),
            "parametersSha256": sha256(args.parameters),
            "provisioningState": "Succeeded",
        }
        serialized = json.dumps(record, indent=2, sort_keys=True) + "\n"
        if values["sqlServerName"] in serialized or "/subscriptions/" in serialized.casefold():
            raise RecordDatabaseNetworkError("sanitized network evidence contains a prohibited identifier")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
        os.chmod(args.output, 0o600)
        print("PASS wrote sanitized WP3 database network evidence")
        return 0
    except (OSError, RecordDatabaseNetworkError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())