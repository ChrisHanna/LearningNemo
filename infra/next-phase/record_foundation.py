#!/usr/bin/env python3
"""Write a non-secret local deployment manifest after a foundation apply."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

from foundation_parameters import ParameterError
from foundation_parameters import parameter_values


ALLOWED_OUTPUTS = {
    "costProfile",
    "platformResourceGroupName",
    "sawResourceGroupName",
    "platformVnetName",
    "sawVnetName",
    "perimeterClaim",
}


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
    parser.add_argument("--parameter-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        try:
            deployment = json.loads(args.deployment_result.read_text(encoding="utf-8"))
            values = parameter_values(args.parameters, allow_runtime_expiry=False)
        except (OSError, json.JSONDecodeError, ParameterError) as error:
            raise RecordError(f"unable to read deployment inputs: {error}") from error
        properties = deployment.get("properties") or {}
        if properties.get("provisioningState") != "Succeeded":
            raise RecordError("Azure deployment did not report Succeeded")
        output_values = {
            name: item.get("value")
            for name, item in (properties.get("outputs") or {}).items()
            if name in ALLOWED_OUTPUTS and isinstance(item, dict)
        }
        record = {
            "schemaVersion": 1,
            "deploymentName": deployment.get("name"),
            "appliedAt": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
            "parameterSource": args.parameter_source.name,
            "templateSha256": sha256(args.template),
            "resolvedParametersSha256": sha256(args.parameters),
            "resolvedParameters": values,
            "outputs": output_values,
            "provisioningState": "Succeeded",
        }
        serialized = json.dumps(record, indent=2, sort_keys=True) + "\n"
        for prohibited in ("/subscriptions/", "tenantid", "clientid", "principalid"):
            if prohibited in serialized.casefold():
                raise RecordError("deployment manifest contains a prohibited account identifier")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(args.output)
        print(f"PASS wrote non-secret deployment manifest to {args.output}")
        return 0
    except RecordError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())