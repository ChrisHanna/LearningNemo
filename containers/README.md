# Trusted Runtime Image

`trusted-runtime.Dockerfile` packages only the deterministic `task_agent.control`
service, the fixed migration entrypoint, and 27 hash-locked dependencies. It
does not include NeMo, model weights, a package manager, or a shell in the
Azure Linux distroless final image.

All base-image arguments MUST be immutable digest references:

```text
BUILD_BASE_IMAGE=python:3.14.7-slim-trixie@sha256:<64 lowercase hex characters>
NATIVE_BUILD_BASE_IMAGE=cgr.dev/chainguard/gcc-glibc:latest-dev@sha256:<64 lowercase hex characters>
RUNTIME_BASE_IMAGE=mcr.microsoft.com/azurelinux/distroless/base:3.0@sha256:<64 lowercase hex characters>
```

The image is not deployment-eligible until a trusted build environment:

1. resolves and reviews both base-image digests;
2. builds for `linux/amd64`;
3. produces an SBOM and vulnerability report;
4. signs or attests the resulting image; and
5. records the final application reference as `repository@sha256:<digest>`.

The isolated trusted-worker image uses CPython 3.14.7 independently of the
NeMo development environment. Its 27-distribution lock is resolved and import
tested specifically for CPython 3.14 on Linux amd64. A separate digest-pinned
compiler stage builds checksum-pinned zlib-ng 2.3.3 in zlib-compatible mode and
runs its tests. The final Azure Linux distroless runtime copies the pinned
CPython installation, replaces zlib, supplies only the native ODBC and C++
dependency closure with package provenance, and returns to numeric UID 65532
without a shell, package manager, pip, or compiler toolchain. A final-image
offline token-provider probe must reach `SQLDriverConnect`, and the image must
scan with zero High or Critical findings.

`infra/next-phase/deploy-workloads.sh` rehashes the actual SBOM,
vulnerability report, signature-verification report, and approval-schema
artifact. Its private release attestation must bind those hashes to the exact
image digest and source revision, report zero high or critical findings, and
name `azure-sql` as the authoritative approval store. An asserted hash without
its artifact does not pass the gate.

No local container engine is required. Run
`scripts/install-supply-chain-tools.sh` to install checksum-pinned Syft, Grype,
Cosign, and Crane in the user-scoped LearningNeMo tool directory. Then run
`scripts/build-trusted-image.sh`; Azure performs the build in the IaC-managed
ACR, while the script resolves the final digest, creates an SBOM, rejects any
High or Critical finding, creates and verifies a detached Cosign bundle, and
writes owner-only release evidence. There is intentionally no sample release
attestation: every attestation must be generated from observed artifacts.