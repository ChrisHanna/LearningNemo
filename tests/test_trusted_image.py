from __future__ import annotations

import re
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
DOCKERFILE = ROOT / "containers" / "trusted-runtime.Dockerfile"
LOCKFILE = ROOT / "containers" / "trusted-runtime.requirements.lock"
BASE_IMAGES = ROOT / "containers" / "trusted-runtime.base-images.json"
PREPARE_SCRIPT = ROOT / "scripts" / "prepare-trusted-runtime.py"
ZLIB_SOURCE_SCRIPT = ROOT / "scripts" / "extract-zlib-ng-source.py"


def test_runtime_lock_pins_and_hashes_every_distribution() -> None:
    content = LOCKFILE.read_text(encoding="utf-8")
    entries = re.findall(r"(?m)^([a-z0-9-]+)==([^\s\\]+)", content)

    assert {"fastapi", "mssql-python", "uvicorn"} <= {name for name, _version in entries}
    assert len(entries) == 27
    assert len({name for name, _version in entries}) == 27
    for index, (name, _version) in enumerate(entries):
        start = content.index(f"{name}==")
        end = content.find("\n" + entries[index + 1][0] + "==", start) if index + 1 < len(entries) else len(content)
        hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", content[start:end])
        assert hashes, f"{name} is not hash locked"


def test_runtime_dockerfile_requires_pinned_minimal_non_root_build() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")
    preparation = PREPARE_SCRIPT.read_text(encoding="utf-8")
    zlib_source = ZLIB_SOURCE_SCRIPT.read_text(encoding="utf-8")

    assert "ARG BUILD_BASE_IMAGE" in content
    assert "ARG NATIVE_BUILD_BASE_IMAGE" in content
    assert "ARG RUNTIME_BASE_IMAGE" in content
    assert "FROM ${BUILD_BASE_IMAGE} AS build" in content
    assert "FROM ${NATIVE_BUILD_BASE_IMAGE} AS native-build" in content
    assert "FROM ${RUNTIME_BASE_IMAGE}" in content
    assert "--require-hashes" in content
    assert "COPY src/task_agent/control" in content
    assert "COPY scripts/apply-sql-migrations.py" in content
    assert "COPY scripts/extract-runtime-library.py" in content
    assert "COPY scripts/extract-zlib-ng-source.py" in content
    assert "COPY scripts/prepare-trusted-runtime.py" in content
    assert "COPY scripts/probe-sql-connectivity.py" in content
    assert "COPY scripts/probe-sql-odbc.py" in content
    assert "COPY scripts/run-live-cycle-workflow.py" in content
    assert "COPY scripts/verify-runtime-native.py" in content
    assert "RUN python /tmp/extract-runtime-library.py" in content
    assert "COPY --from=build /usr/local /usr/local" in content
    assert "COPY --from=build /opt/runtime-libs/usr/lib/libltdl.so.7.3.2" in content
    assert "dpkg-query --status libffi8" in content
    assert "dpkg-query --status libssl3t64" in content
    assert "dpkg-query --status libzstd1" in content
    assert "COPY --from=build /usr/lib/x86_64-linux-gnu/libffi.so.8.1.4" in content
    assert "COPY --from=build /usr/lib/x86_64-linux-gnu/libcrypto.so.3 /usr/local/lib/libcrypto.so.3" in content
    assert "COPY --from=build /usr/lib/x86_64-linux-gnu/libssl.so.3 /usr/local/lib/libssl.so.3" in content
    assert "COPY --from=build /usr/lib/x86_64-linux-gnu/libzstd.so.1.5.7 /usr/local/lib/libzstd.so.1" in content
    assert "/var/lib/dpkg/status.d/libffi8" in content
    assert "/var/lib/dpkg/status.d/libssl3t64" in content
    assert "/var/lib/dpkg/status.d/libzstd1" in content
    assert "LD_LIBRARY_PATH=/usr/local/lib:/usr/lib" in content
    assert "OPENSSL_CONF=/etc/pki/tls/openssl.cnf" in content
    assert "SSL_CERT_FILE=/etc/pki/tls/cert.pem" in content
    assert "a0d2a5d122c84b56a793a1553a9c3327fb2eb7469bf7a86b79e3c7be5d92e8d6" in zlib_source
    assert "12731092979c6d07f42da27da673a9f6c7b13586" in zlib_source
    assert "--zlib-compat --without-optimizations" in content
    assert "make test" in content
    assert "libz.so.1.3.1.zlib-ng" in content
    assert "zlib-ng-2.3.3.spdx.json" in content
    assert "libgssapi_krb5.so.2.2" in content
    assert "libkrb5.so.3.3" in content
    assert "keyutils-libs-1.6.3-r40.spdx.json" in content
    assert "krb5-libs-1.22.2-r3.spdx.json" in content
    assert "libgcc-16.2.0-r1.spdx.json" in content
    assert "libstdc++-16.2.0-r1.spdx.json" in content
    assert 'RUN ["python3", "/tmp/prepare-trusted-runtime.py"]' in content
    assert "/opt/learningnemo/mssql_python_odbc/libs/linux/alpine" in preparation
    assert "/opt/learningnemo/mssql_python_odbc/libs/linux/rhel" in preparation
    assert "/opt/learningnemo/mssql_python_odbc/libs/linux/suse" in preparation
    assert 'RUN ["python3", "/opt/learningnemo/scripts/verify-runtime-native.py"]' in content
    assert "from azure.identity import ManagedIdentityCredential" in (
        ROOT / "scripts" / "verify-runtime-native.py"
    ).read_text(encoding="utf-8")
    assert "/usr/bin/apk" in preparation
    assert "/usr/bin/sh" in preparation
    assert "/usr/bin/pip3.14" in preparation
    assert 'TEMP_DIRECTORY = Path("/tmp")' in preparation
    assert "TEMP_DIRECTORY.chmod(0o1777)" in preparation
    assert 'OPENSSL_DEFAULT_DIRECTORY = Path("/usr/lib/ssl")' in preparation
    assert '"cert.pem": Path("/etc/pki/tls/cert.pem")' in preparation
    assert "/usr/local/lib/python3.14/ensurepip" in preparation
    assert "COPY src/task_agent/tasks" not in content
    assert "USER 65532:65532" in content
    assert 'ENTRYPOINT ["/usr/local/bin/python3", "-m", "task_agent.control.runtime"]' in content
    assert "apt-get" not in content
    assert "apk add" not in content


def test_docker_build_context_excludes_unrelated_application_and_secrets() -> None:
    content = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert content[0] == "*"
    assert "!src/task_agent/control/**" in content
    assert "!containers/trusted-runtime.requirements.lock" in content
    assert "!containers/zlib-ng-2.3.3.spdx.json" in content
    assert "!scripts/apply-sql-migrations.py" in content
    assert "!scripts/extract-runtime-library.py" in content
    assert "!scripts/extract-zlib-ng-source.py" in content
    assert "!scripts/prepare-trusted-runtime.py" in content
    assert "!scripts/probe-sql-connectivity.py" in content
    assert "!scripts/probe-sql-odbc.py" in content
    assert "!scripts/run-live-cycle-workflow.py" in content
    assert "!scripts/verify-runtime-native.py" in content
    assert not any(".env" in line or ".nemo-test-client" in line for line in content[1:])


def test_base_images_are_versioned_and_digest_pinned() -> None:
    document = json.loads(BASE_IMAGES.read_text(encoding="utf-8"))
    assert set(document) == {"schemaVersion", "build", "nativeBuild", "runtime"}
    assert document["schemaVersion"] == 1
    pattern = re.compile(r"^[a-z0-9./:_-]+@sha256:[0-9a-f]{64}$")
    assert pattern.fullmatch(document["build"])
    assert pattern.fullmatch(document["nativeBuild"])
    assert pattern.fullmatch(document["runtime"])
    assert ":3.14.7-slim-trixie@" in document["build"]
    assert "chainguard/gcc-glibc:latest-dev@" in document["nativeBuild"]
    assert "mcr.microsoft.com/azurelinux/distroless/base:3.0@" in document["runtime"]