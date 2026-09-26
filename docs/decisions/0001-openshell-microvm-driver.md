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
Planning, Execution, and Probe sandboxes each receive a distinct OpenShell
policy and their own MicroVM. Filesystem, Landlock, and process sections are
fixed at sandbox creation; network sections can be replaced on a running
sandbox only by the operator through the gateway. The gateway listens only on loopback,
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
  Only the trusted-worker container image is signed. This remains required before
  claiming reference-aligned SAW governance.
- The invoice Planning and Execution policies follow OpenShell v0.0.116
  security guidance: Landlock `hard_requirement` (the sandbox refuses to start
  rather than run without filesystem restrictions), only
  `/usr/local/bin/python3.12` may reach the gateway routes, and REST endpoints
  use `enforcement: enforce` with exact method/path rules. The invoice agent
  image build fails if any policy path is missing.
- The fixed-proof Planning, Execution, and Probe policies are hash-pinned by
  the retained-workspace scripts and are intentionally left unchanged to match
  their recorded evidence. They still use Landlock `best_effort` and carry
  `tls: terminate`, which OpenShell v0.0.116 accepts but treats as a deprecated
  no-op (TLS is auto-detected and terminated). Apply the invoice hardening to
  them only together with a full rebootstrap.
- Per-run capability mediation through OpenShell providers is implemented as an
  opt-in mode (`credentialMode=provider` in `invoice-services.bicep`, set by
  `LEARNINGNEMO_INVOICE_CREDENTIAL_MODE=provider` when running
  `deploy_invoice_services.py`). The VM host mints the capability, stores it in
  a per-run provider (`invoice-run-<run id>`) whose profile
  (`openshell/invoice-<kind>-provider-profile.yaml`) binds it to the role's
  gateway routes, attaches it at sandbox creation, and returns only its SHA-256
  hash for SQL admission. The raw value never passes through Run Command
  output, the controller, or the sandbox; the agent receives an
  `openshell:resolve:env:` placeholder and refuses to run with anything else.
  Stopping the run sets the credential's expiry in the past, so a retained
  sandbox cannot reuse it. The default remains `manifest` (capability delivered
  to the agent) until one live Planning and Execution cycle verifies provider
  mode on the pinned OpenShell release. Providers of deleted sandboxes are not
  yet garbage-collected; their credentials are already expired.
