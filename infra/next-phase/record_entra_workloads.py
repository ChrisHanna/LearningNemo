#!/usr/bin/env python3
"""Write a sanitized manifest for verified WP2b Entra audiences."""

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
from workload_parameters import WorkloadParameterError
from workload_parameters import load_auth
from workload_parameters import load_config


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
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--auth-file", type=Path, required=True)
    parser.add_argument("--registrations-template", type=Path, required=True)
    parser.add_argument("--audiences-template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.config)
        load_auth(args.auth_file)
        names = sorted(
            f"{config['projectName']}-{mode}-{config['environment']}"
            for mode in SERVICE_KEYS
        )
        record = {
            "schemaVersion": 1,
            "verifiedAt": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
            "configuration": config,
            "applicationUniqueNames": names,
            "servicePrincipalCount": 4,
            "credentialCount": 0,
            "templateSha256": {
                "registrations": sha256(args.registrations_template),
                "audiences": sha256(args.audiences_template),
            },
            "privateAuthStateSha256": sha256(args.auth_file),
            "verificationState": "passed",
        }
        serialized = json.dumps(record, indent=2, sort_keys=True) + "\n"
        if UUID_SEARCH.search(serialized) or "/subscriptions/" in serialized.casefold():
            raise RecordError("sanitized Entra manifest contains an Azure identifier")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(args.output)
        print(f"PASS wrote sanitized Entra audience manifest to {args.output}")
        return 0
    except (OSError, RecordError, WorkloadParameterError) as error:
        print(f"FAIL unable to record Entra audience evidence: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())