#!/usr/bin/env python3
"""Verify the final trusted image contains the complete SQL ELF dependency closure."""

from __future__ import annotations

import argparse
import shutil
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from pathlib import PurePosixPath


ROOT_ELFS = {
    "mssql_py_core.cpython-314-x86_64-linux-gnu.so",
    "ddbc_bindings.cp314-x86_64.so",
    "libz.so.1",
}
ODBC_DRIVER = "libmsodbcsql-18.6.so.2.1"
ODBC_DRIVER_SUFFIX = (
    "/mssql_python_odbc/libs/linux/debian_ubuntu/x86_64/lib/"
    + ODBC_DRIVER
)
NEEDED = re.compile(r"\(NEEDED\).*\[([^\]]+)\]")


class ElfVerificationError(RuntimeError):
    pass


def needed_libraries(path: Path) -> set[str]:
    result = subprocess.run(
        ["readelf", "-d", str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise ElfVerificationError(f"unable to inspect ELF: {path.name}")
    return set(NEEDED.findall(result.stdout))


def archive_destination(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ElfVerificationError("trusted image archive contains an unsafe path")
    parts = tuple(part for part in relative.parts if part not in {"", "."})
    if not parts:
        raise ElfVerificationError("trusted image archive contains an empty path")
    return root.joinpath(*parts)


def extract_inventory(
    archive_path: Path,
    root: Path,
) -> tuple[dict[str, list[Path]], dict[str, list[str]]]:
    files: dict[str, list[Path]] = {}
    aliases: dict[str, list[str]] = {}
    try:
        with tarfile.open(archive_path, "r:*") as archive:
            for member in archive:
                destination = archive_destination(root, member.name)
                if member.isfile():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise ElfVerificationError("trusted image archive file is unreadable")
                    with source, destination.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    files.setdefault(destination.name, []).append(destination)
                elif member.issym() or member.islnk():
                    target_name = PurePosixPath(member.linkname).name
                    if not target_name:
                        raise ElfVerificationError("trusted image archive link target is empty")
                    aliases.setdefault(destination.name, []).append(target_name)
    except (OSError, tarfile.TarError) as error:
        raise ElfVerificationError("trusted image archive is unreadable") from error
    return files, aliases


def resolve_name(
    name: str,
    files: dict[str, list[Path]],
    aliases: dict[str, list[str]],
    resolving: frozenset[str] = frozenset(),
) -> Path | None:
    candidates = files.get(name, [])
    if candidates:
        return candidates[0]
    if name in resolving:
        return None
    for target_name in aliases.get(name, []):
        target = resolve_name(target_name, files, aliases, resolving | {name})
        if target is not None:
            return target
    return None


def verify_archive(archive_path: Path) -> tuple[int, int]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        files, aliases = extract_inventory(archive_path, root)
        if "libz.so.1.3.2" in files or "zlib-1.3.2-r5.spdx.json" in files:
            raise ElfVerificationError("trusted image contains superseded zlib artifacts")
        missing_roots = ROOT_ELFS - (set(files) | set(aliases))
        if missing_roots:
            raise ElfVerificationError(
                "trusted image SQL ELF inventory is missing: " + ", ".join(sorted(missing_roots))
            )
        queue: list[Path] = []
        for name in ROOT_ELFS:
            target = resolve_name(name, files, aliases)
            if target is None:
                raise ElfVerificationError(f"trusted image SQL ELF target is absent: {name}")
            queue.append(target)
        driver_candidates = [
            path
            for path in files.get(ODBC_DRIVER, [])
            if path.as_posix().endswith(ODBC_DRIVER_SUFFIX)
        ]
        if len(driver_candidates) != 1:
            raise ElfVerificationError("trusted image selected ODBC driver inventory differs")
        unused_driver_candidates = [
            path
            for path in files.get(ODBC_DRIVER, [])
            if path not in driver_candidates
        ]
        if unused_driver_candidates:
            raise ElfVerificationError("trusted image contains unused ODBC driver variants")
        queue.extend(driver_candidates)
        inspected: set[Path] = set()
        dependencies: set[str] = set()
        while queue:
            path = queue.pop()
            if path in inspected:
                continue
            inspected.add(path)
            for name in needed_libraries(path):
                dependencies.add(name)
                if name not in files and name not in aliases:
                    raise ElfVerificationError(f"trusted image ELF dependency is missing: {name}")
                target = resolve_name(name, files, aliases)
                if target is None:
                    raise ElfVerificationError(f"trusted image ELF dependency target is absent: {name}")
                queue.append(target)
        return len(inspected), len(dependencies)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    try:
        inspected, dependencies = verify_archive(args.archive)
        print(f"PASS trusted image SQL ELF closure contains {inspected} files and {dependencies} dependencies")
        return 0
    except ElfVerificationError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())