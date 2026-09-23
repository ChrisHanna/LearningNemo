#!/usr/bin/env python3
"""Finalize the shell-free trusted runtime filesystem and zlib provider."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


ZLIB_TARGET = Path("/usr/lib/libz.so.1.3.1.zlib-ng")
ZLIB_LINK = Path("/usr/lib/libz.so.1")
ZLIB_OLD_TARGET = Path("/usr/lib/libz.so.1.3.2")
ZLIB_OLD_SBOM = Path("/var/lib/db/sbom/zlib-1.3.2-r5.spdx.json")
ZLIB_NG_SBOM = Path("/var/lib/db/sbom/zlib-ng-2.3.3.spdx.json")
TEMP_DIRECTORY = Path("/tmp")
OPENSSL_DEFAULT_DIRECTORY = Path("/usr/lib/ssl")
OPENSSL_DEFAULT_LINKS = {
    "cert.pem": Path("/etc/pki/tls/cert.pem"),
    "certs": Path("/etc/pki/tls/certs"),
    "openssl.cnf": Path("/etc/pki/tls/openssl.cnf"),
}
SONAME_LINKS = {
    "libffi.so.8": "libffi.so.8.1.4",
    "libstdc++.so.6": "libstdc++.so.6.0.36",
    "libcom_err.so.2": "libcom_err.so.2.1",
    "libgssapi_krb5.so.2": "libgssapi_krb5.so.2.2",
    "libk5crypto.so.3": "libk5crypto.so.3.1",
    "libkeyutils.so.1": "libkeyutils.so.1.10",
    "libkrb5.so.3": "libkrb5.so.3.3",
    "libkrb5support.so.0": "libkrb5support.so.0.1",
}


def remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def main() -> None:
    if not ZLIB_TARGET.is_file() or not ZLIB_NG_SBOM.is_file():
        raise RuntimeError("zlib-ng runtime artifacts are incomplete")
    sbom = json.loads(ZLIB_NG_SBOM.read_text(encoding="utf-8"))
    packages = sbom.get("packages") if isinstance(sbom, dict) else None
    if not isinstance(packages, list) or not any(
        package.get("name") == "zlib-ng" and package.get("versionInfo") == "2.3.3"
        for package in packages
        if isinstance(package, dict)
    ):
        raise RuntimeError("zlib-ng runtime provenance differs")
    remove(ZLIB_LINK)
    remove(ZLIB_OLD_TARGET)
    remove(ZLIB_OLD_SBOM)
    ZLIB_LINK.symlink_to(ZLIB_TARGET.name)
    if TEMP_DIRECTORY.is_symlink() or (TEMP_DIRECTORY.exists() and not TEMP_DIRECTORY.is_dir()):
        raise RuntimeError("trusted runtime temporary path differs")
    TEMP_DIRECTORY.mkdir(mode=0o1777, exist_ok=True)
    TEMP_DIRECTORY.chmod(0o1777)
    OPENSSL_DEFAULT_DIRECTORY.mkdir(mode=0o755, parents=True, exist_ok=True)
    for link_name, target in OPENSSL_DEFAULT_LINKS.items():
        if not target.exists():
            raise RuntimeError("Azure Linux TLS trust inventory differs")
        link = OPENSSL_DEFAULT_DIRECTORY / link_name
        remove(link)
        link.symlink_to(target)
    for link_name, target_name in SONAME_LINKS.items():
        link = Path("/usr/lib") / link_name
        target = Path("/usr/lib") / target_name
        if not target.is_file():
            raise RuntimeError("Kerberos runtime dependency inventory differs")
        remove(link)
        link.symlink_to(target.name)
    for path in (
        Path("/etc/apk"),
        Path("/etc/busybox-paths.d"),
        Path("/opt/learningnemo/mssql_python_odbc/libs/linux/alpine"),
        Path("/opt/learningnemo/mssql_python_odbc/libs/linux/rhel"),
        Path("/opt/learningnemo/mssql_python_odbc/libs/linux/suse"),
        Path("/usr/bin/apk"),
        Path("/usr/bin/ash"),
        Path("/usr/bin/bash"),
        Path("/usr/bin/busybox"),
        Path("/usr/bin/pip"),
        Path("/usr/bin/pip3"),
        Path("/usr/bin/pip3.14"),
        Path("/usr/bin/sh"),
        Path("/usr/local/bin/2to3"),
        Path("/usr/local/bin/2to3-3.14"),
        Path("/usr/local/bin/idle3"),
        Path("/usr/local/bin/idle3.14"),
        Path("/usr/local/bin/pip"),
        Path("/usr/local/bin/pip3"),
        Path("/usr/local/bin/pip3.14"),
        Path("/usr/local/bin/pydoc3"),
        Path("/usr/local/bin/pydoc3.14"),
        Path("/usr/local/bin/python3-config"),
        Path("/usr/local/bin/python3.14-config"),
        Path("/usr/local/include"),
        Path("/usr/local/lib/libpython3.14.a"),
        Path("/usr/local/lib/pkgconfig"),
        Path("/usr/local/lib/python3.14/config-3.14-x86_64-linux-gnu"),
        Path("/usr/local/lib/python3.14/ensurepip"),
        Path("/usr/local/lib/python3.14/site-packages/pip"),
        Path("/usr/local/lib/python3.14/site-packages/setuptools"),
        Path("/usr/local/share/man"),
        Path("/usr/lib/apk"),
        Path("/usr/lib/python3.14/site-packages/pip"),
        Path("/usr/lib/python3.14/site-packages/setuptools"),
        Path("/usr/share/doc/apk"),
        Path("/var/cache/apk"),
        Path("/var/lib/apk"),
    ):
        remove(path)
    for site_packages in (
        Path("/usr/lib/python3.14/site-packages"),
        Path("/usr/local/lib/python3.14/site-packages"),
    ):
        for pattern in ("pip-*.dist-info", "setuptools-*.dist-info"):
            for path in site_packages.glob(pattern):
                remove(path)
    if ZLIB_LINK.resolve() != ZLIB_TARGET:
        raise RuntimeError("zlib-ng compatibility link differs")
    if TEMP_DIRECTORY.stat().st_mode & 0o7777 != 0o1777:
        raise RuntimeError("trusted runtime temporary directory mode differs")
    if any((OPENSSL_DEFAULT_DIRECTORY / name).resolve() != target for name, target in OPENSSL_DEFAULT_LINKS.items()):
        raise RuntimeError("trusted runtime OpenSSL trust links differ")
    if any((Path("/usr/lib") / link).resolve().name != target for link, target in SONAME_LINKS.items()):
        raise RuntimeError("Kerberos runtime dependency link differs")
    Path(__file__).unlink()


if __name__ == "__main__":
    main()