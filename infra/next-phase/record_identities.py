#!/usr/bin/env python3
"""Write a non-secret manifest after the persistent identity deployment."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

from platform_parameters import ParameterError
from platform_parameters import parameter_values


ALLOWED_OUTPUTS = {
    "costProfile",
    "platformResourceGroupName",
    "plannedRuntimeEnvironmentName",
    "monthlyCostCeiling",
    "identityNames",
}


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
    parser.add_argument("--parameter-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        try:
            deployment = json.loads(args.deployment_result.read_text(encoding="utf-8-sig"))
            values = parameter_values(args.parameters)
        except (OSError, json.JSONDecodeError, ParameterError) as error:
            raise RecordError("unable to read identity deployment evidence") from error
        properties = deployment.get("properties") or {}
        if properties.get("provisioningState") != "Succeeded":
            raise RecordError("Azure identity deployment did not report Succeeded")
        raw_outputs = properties.get("outputs") or {}
        if set(raw_outputs) != ALLOWED_OUTPUTS:
            raise RecordError("identity deployment outputs differ from the safe contract")
        outputs = {
            name: item.get("value")
            for name, item in raw_outputs.items()
            if isinstance(item, dict)
        }
        if set(outputs) != ALLOWED_OUTPUTS:
            raise RecordError("one or more identity outputs are malformed")
        record = {
            "schemaVersion": 1,
            "deploymentName": deployment.get("name"),
            "appliedAt": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
            "parameterSource": args.parameter_source.name,
            "templateSha256": sha256(args.template),
            "parametersSha256": sha256(args.parameters),
            "parameters": values,
            "outputs": outputs,
            "provisioningState": "Succeeded",
        }
        serialized = json.dumps(record, indent=2, sort_keys=True) + "\n"
        for prohibited in ("/subscriptions/", "tenantid", "clientid", "principalid"):
            if prohibited in serialized.casefold():
                raise RecordError("identity manifest contains a prohibited account identifier")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(args.output)
        print(f"PASS wrote non-secret identity manifest to {args.output}")
        return 0
    except RecordError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())