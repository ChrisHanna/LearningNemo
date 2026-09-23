#!/usr/bin/env python3
"""Download and extract the checksum-pinned Wolfi libltdl runtime package."""

from __future__ import annotations

import hashlib
import tarfile
import urllib.request
from pathlib import Path


URL = "https://packages.wolfi.dev/os/x86_64/libltdl-2.4.7-r7.apk"
SHA256 = "7f985f2a48b0d60cb3a19d698e6dd44fe4426814d043699bdfdfdff3686651fe"


def main() -> None:
    package = Path("/tmp/libltdl.apk")
    urllib.request.urlretrieve(URL, package)
    if hashlib.sha256(package.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError("libltdl package hash differs")
    destination = Path("/opt/runtime-libs")
    with tarfile.open(package, "r:*") as archive:
        members = [
            member
            for member in archive.getmembers()
            if Path(member.name).name.startswith("libltdl.so.7")
        ]
        if len(members) != 2:
            raise RuntimeError("libltdl package inventory differs")
        archive.extractall(destination, members=members, filter="data")


if __name__ == "__main__":
    main()