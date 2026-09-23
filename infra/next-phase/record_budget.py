#!/usr/bin/env python3
"""Write a recipient-free local manifest after budget deployment."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

from budget_parameters import BudgetParameterError
from budget_parameters import load_config
from budget_parameters import validate_start_date


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
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--resolved-parameters", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        try:
            deployment = json.loads(args.deployment_result.read_text(encoding="utf-8-sig"))
            config = load_config(args.config)
            resolved = json.loads(args.resolved_parameters.read_text(encoding="utf-8-sig"))
            start_date = validate_start_date(resolved["parameters"]["startDate"]["value"])
        except (OSError, json.JSONDecodeError, KeyError, BudgetParameterError) as error:
            raise RecordError("unable to read budget deployment evidence") from error
        properties = deployment.get("properties") or {}
        if properties.get("provisioningState") != "Succeeded":
            raise RecordError("Azure budget deployment did not report Succeeded")
        record = {
            "schemaVersion": 1,
            "deploymentName": deployment.get("name"),
            "appliedAt": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
            "configSource": args.config.name,
            "templateSha256": sha256(args.template),
            "configSha256": sha256(args.config),
            "budgetName": config["budgetName"],
            "monthlyAmount": config["monthlyAmount"],
            "startDate": start_date,
            "recipientStored": False,
            "provisioningState": "Succeeded",
        }
        serialized = json.dumps(record, indent=2, sort_keys=True) + "\n"
        if "@" in serialized or "/subscriptions/" in serialized.casefold():
            raise RecordError("budget manifest contains a prohibited recipient or account identifier")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(args.output)
        print(f"PASS wrote recipient-free budget manifest to {args.output}")
        return 0
    except RecordError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())