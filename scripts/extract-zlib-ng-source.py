#!/usr/bin/env python3
"""Download and safely extract the checksum-pinned zlib-ng source tree."""

from __future__ import annotations

import hashlib
import shutil
import tarfile
import urllib.request
from pathlib import Path
from pathlib import PurePosixPath


COMMIT = "12731092979c6d07f42da27da673a9f6c7b13586"
URL = f"https://codeload.github.com/zlib-ng/zlib-ng/tar.gz/{COMMIT}"
SHA256 = "a0d2a5d122c84b56a793a1553a9c3327fb2eb7469bf7a86b79e3c7be5d92e8d6"
REQUIRED_FILES = {"configure", "Makefile.in", "LICENSE.md", "zlib.h.in"}


def main() -> None:
    archive_path = Path("/tmp/zlib-ng.tar.gz")
    urllib.request.urlretrieve(URL, archive_path)
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError("zlib-ng source archive hash differs")
    extraction_root = Path("/opt/zlib-ng-extract")
    destination = Path("/opt/zlib-ng-source")
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        roots = {
            PurePosixPath(member.name).parts[0]
            for member in members
            if PurePosixPath(member.name).parts
        }
        if len(roots) != 1:
            raise RuntimeError("zlib-ng source archive root differs")
        archive.extractall(extraction_root, filter="data")
    source = extraction_root / roots.pop()
    if not source.is_dir() or not REQUIRED_FILES <= {path.name for path in source.iterdir()}:
        raise RuntimeError("zlib-ng source archive inventory differs")
    if destination.exists():
        shutil.rmtree(destination)
    source.rename(destination)
    shutil.rmtree(extraction_root)
    archive_path.unlink()


if __name__ == "__main__":
    main()