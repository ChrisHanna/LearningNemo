# ADR 0001: OpenShell MicroVM Driver for the SAW POC

## Status

Accepted for the single-user OpenShell runtime portfolio POC.

## Decision

Run OpenShell `0.0.116` on an exact Ubuntu 24.04 Gen2 image inside one
no-public-IP Azure VM. Use OpenShell's bundled MicroVM driver, KVM, and one
fixed 1-vCPU/1-GiB MicroVM allocation per sandbox.

The Azure VM is the single-user workspace VM inside the Secure Agent Workspace
(SAW) envelope. In NVIDIA's reference design the SAW is the managed envelope
around that VM (lifecycle, brokered access, perimeter, governed connectors,
and audit), not the VM itself. The VM hosts the website's agent workload,
not an interactive user desktop; "single-user" means one trust domain with one
accountable owner. The
Planning, Execution, and Probe sandboxes each receive a distinct immutable
OpenShell policy and their own MicroVM. The gateway listens only on loopback,
requires its package-managed mTLS client bundle, uses a 15-minute sandbox JWT,
and exposes no Docker or Podman socket to a sandbox.

## Rationale

OpenShell supports rootless Podman 5.x, but Ubuntu 24.04 does not currently
offer that version from its stable archive and the available third-party feed
is explicitly unstable. Docker is supported but introduces a rootful daemon.
The bundled MicroVM driver avoids both supply-chain deviations and provides a
stronger inner isolation boundary when Azure nested virtualization is present.

## Consequences

- The Azure VM SKU must support nested virtualization and `/dev/kvm`.
- Per-sandbox `--cpu` and `--memory` flags are not enforced by the MicroVM
  driver, so sizing is fixed in the gateway's operator-owned configuration.
- This is an OpenShell runtime portfolio POC, not a production SAW fleet.
- Brokered interactive SSO and runtime credential mediation remain required
  before claiming the specification's complete SAW Phase I or Phase II level.
- The lower-cost Azure perimeter uses NSGs plus OpenShell policy. It does not
  claim the NVIDIA reference architecture's Azure Firewall Premium perimeter.
- The current slice proves sandbox lifecycle and denial controls. It does not
  yet supply reusable model or broker credentials to AgentRunner.
- NVIDIA's reference design requires kernel-level runtime sandboxing, not
  MicroVMs. Per-sandbox MicroVMs are an additional layer chosen here.
- NVIDIA's signed-policy governance layer is not implemented: OpenShell
  policies and per-engagement delegation records are not signed or attested.
  Only container release artifacts are signed. This remains required before
  claiming reference-aligned SAW governance.
