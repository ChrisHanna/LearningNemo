#!/usr/bin/env python3
"""Prove the final runtime can load its resolver-selected SQL driver."""

from __future__ import annotations

import ctypes
import os
import ssl
import zlib
from pathlib import Path
from types import SimpleNamespace

import mssql_python
import mssql_python_odbc
from azure.identity import ManagedIdentityCredential
from mssql_python import ddbc_bindings


def selected_linux_family() -> str:
    if Path("/etc/alpine-release").exists():
        return "alpine"
    if Path("/etc/redhat-release").exists() or Path("/etc/centos-release").exists():
        return "rhel"
    if Path("/etc/SuSE-release").exists() or Path("/etc/SUSE-brand").exists():
        return "suse"
    return "debian_ubuntu"


def main() -> None:
    if ManagedIdentityCredential.__name__ != "ManagedIdentityCredential":
        raise RuntimeError("trusted runtime managed identity client differs")
    expected_ca_file = Path("/etc/pki/tls/cert.pem")
    default_paths = ssl.get_default_verify_paths()
    if (
        os.environ.get("SSL_CERT_FILE") != str(expected_ca_file)
        or default_paths.cafile != str(expected_ca_file)
        or Path("/usr/lib/ssl/cert.pem").resolve() != expected_ca_file
    ):
        raise RuntimeError("trusted runtime OpenSSL trust path differs")
    if ssl.create_default_context().cert_store_stats().get("x509_ca", 0) < 1:
        raise RuntimeError("trusted runtime OpenSSL trust store is empty")
    if not zlib.ZLIB_RUNTIME_VERSION.endswith(".zlib-ng"):
        raise RuntimeError("trusted runtime did not load the zlib-ng compatibility library")
    family = selected_linux_family()
    if family != "debian_ubuntu":
        raise RuntimeError(f"trusted runtime selects unexpected ODBC family: {family}")
    module_dir = Path(mssql_python_odbc.__file__).resolve().parent
    driver = Path(
        ddbc_bindings._get_odbc_driver_path(str(module_dir), "msodbcsql18")
    ).resolve()
    expected_parent = module_dir / "libs" / "linux" / family / "x86_64" / "lib"
    if driver.parent != expected_parent or not driver.is_file():
        raise RuntimeError("trusted runtime ODBC driver path differs")

    class ProbeTokenProvider:
        def get_token(self, _scope: str) -> SimpleNamespace:
            return SimpleNamespace(token="offline-build-probe", expires_on=4102444800)

    try:
        connection = mssql_python.connect(
            "Server=tcp:127.0.0.1,1;Database=master;Encrypt=strict;"
            "TrustServerCertificate=no;ConnectRetryCount=0;",
            token_provider=ProbeTokenProvider(),
            timeout=1,
        )
    except Exception as error:
        detail = str(getattr(error, "ddbc_error", "")).casefold()
        if any(
            marker in detail
            for marker in (
                "failed to set attribute 1256 before connect",
                "failed to load the driver",
                "failed to allocate environment handle",
                "failed to set environment attributes",
                "connection handle not allocated",
            )
        ):
            raise RuntimeError("trusted runtime ODBC token pre-connect probe failed") from error
        if not any(marker in detail for marker in ("08001", "connection refused", "tcp provider")):
            raise RuntimeError("trusted runtime ODBC probe did not reach SQLDriverConnect") from error
    else:
        connection.close()
        raise RuntimeError("trusted runtime ODBC offline probe unexpectedly connected")
    ctypes.CDLL(str(driver), mode=os.RTLD_NOW | os.RTLD_LOCAL)
    forbidden = (
        "/bin/bash",
        "/bin/sh",
        "/usr/bin/apk",
        "/usr/bin/apt-get",
        "/usr/bin/dpkg",
        "/usr/bin/gcc",
        "/usr/bin/make",
        "/usr/bin/rpm",
        "/usr/bin/sh",
        "/usr/bin/tdnf",
        "/usr/local/bin/pip",
        "/usr/local/lib/python3.14/ensurepip",
    )
    if any(Path(path).exists() or Path(path).is_symlink() for path in forbidden):
        raise RuntimeError("trusted runtime contains forbidden execution tooling")
    print(f"PASS final runtime loaded {driver.name} from {family}")


if __name__ == "__main__":
    main()