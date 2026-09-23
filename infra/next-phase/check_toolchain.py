#!/usr/bin/env python3
"""Enforce minimum CLI versions for reproducible next-phase deployments."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


VERSION_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class ToolchainError(RuntimeError):
    pass


def version_tuple(value: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.search(value)
    if match is None:
        raise ToolchainError(f"unable to parse version from {value!r}")
    return tuple(int(part) for part in match.groups())


def run(arguments: list[str], timeout: int = 20) -> str:
    try:
        result = subprocess.run(arguments, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ToolchainError(f"unable to run {arguments[0]}") from error
    if result.returncode != 0:
        raise ToolchainError(f"{arguments[0]} version check failed")
    return result.stdout.strip()


def load_contract(path: Path) -> dict[str, Any]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ToolchainError(f"unable to read toolchain contract: {error}") from error
    if contract.get("schemaVersion") != 1:
        raise ToolchainError("unsupported toolchain schemaVersion")
    for section in ("minimum", "tested"):
        values = contract.get(section)
        if not isinstance(values, dict) or set(values) != {"azureCli", "bicep", "python", "bash", "uv"}:
            raise ToolchainError(f"toolchain {section} section is incomplete")
        for value in values.values():
            if not isinstance(value, str):
                raise ToolchainError(f"toolchain {section} versions must be strings")
            version_tuple(value)
    return contract


def current_versions() -> dict[str, str]:
    try:
        azure_cli = json.loads(run(["az", "version", "--output", "json"]))["azure-cli"]
    except (json.JSONDecodeError, KeyError) as error:
        raise ToolchainError("Azure CLI returned an unreadable version") from error
    uv_candidate = os.getenv("UV_BIN") or shutil.which("uv")
    if uv_candidate is None:
        user_local_uv = Path.home() / ".local" / "bin" / "uv"
        uv_candidate = str(user_local_uv) if user_local_uv.is_file() else None
    if uv_candidate is None:
        raise ToolchainError("unable to locate uv; set UV_BIN or install it in PATH")
    return {
        "azureCli": str(azure_cli),
        "bicep": run(["az", "bicep", "version"]),
        "python": ".".join(str(part) for part in sys.version_info[:3]),
        "bash": run(["bash", "--version"]).splitlines()[0],
        "uv": run([uv_candidate, "--version"]),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path(__file__).with_name("toolchain.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if not sys.platform.startswith("linux"):
            raise ToolchainError("run next-phase infrastructure from Linux or WSL2")
        contract = load_contract(args.contract)
        current = current_versions()
        for tool, minimum in contract["minimum"].items():
            if version_tuple(current[tool]) < version_tuple(minimum):
                raise ToolchainError(f"{tool} {current[tool]!r} is older than required {minimum}")
        print("PASS infrastructure toolchain meets all minimum versions")
        for tool in ("azureCli", "bicep", "python", "bash", "uv"):
            parsed = ".".join(str(part) for part in version_tuple(current[tool]))
            tested = contract["tested"][tool]
            suffix = "tested" if version_tuple(parsed) == version_tuple(tested) else f"tested baseline {tested}"
            print(f"INFO {tool}={parsed} ({suffix})")
        return 0
    except ToolchainError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())