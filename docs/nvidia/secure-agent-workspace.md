# NVIDIA Secure Agent Workspace

The [Secure Agent Workspace (SAW) reference design](https://docs.nvidia.com/enterprise-reference-architectures/secure-agent-workspace-reference-design/latest/what-is-secure-agent-workspace.html)
is NVIDIA's reference architecture for running autonomous agents safely. It is
not a product and not a single VM. A SAW is the managed envelope around a
single-user workspace VM: approved provisioning and lifecycle, brokered access,
a runtime enforcement layer ([OpenShell](openshell.md) is NVIDIA's reference
implementation), governed connectors with human review for sensitive writes,
and audit. Containment nests from the inside out:

```text
agent loop -> OpenShell sandbox -> single-user workspace VM -> SAW envelope
```

NVIDIA groups SAW controls into three layers ([Figure 7: Security Envelope](https://docs.nvidia.com/enterprise-reference-architectures/secure-agent-workspace-reference-design/latest/security-and-governance-model.html)):
baseline managed-workspace controls (Phase I perimeter), runtime sandbox
controls (Phase II in-VM enforcement), and signed-policy governance,
decomposed across **two structural control points** (*Workspace control* and
*Runtime control*, where enforcement physically happens) and **four
cross-cutting contract surfaces** (*Signed-policy*, *Credential mediation*,
*Audit / revocation*, and *Identity / delegation*). This repository implements
a single-user Phase II runtime proof of concept, not a production SAW fleet.

## Control Surface & Residual Controls under Guest Compromise

Per NVIDIA's [Security and Governance Model (Tables 6 & 7)](https://docs.nvidia.com/enterprise-reference-architectures/secure-agent-workspace-reference-design/latest/security-and-governance-model.html),
controls inside the guest VM do not survive a root-on-OS or guest-kernel
compromise—only controls enforced **outside** the guest remain load-bearing:

| Compromise Level | In-VM / Runtime Controls (OpenShell) | External Controls That Survive Guest Root Compromise |
| --- | --- | --- |
| **1. Agent-level compromise** (prompt injection, scope creep, tool misuse) | OpenShell L7 route allowlist, Landlock `hard_requirement`, Python binary check, provider credential injection | 5-surface NeMo Guardrails in the external gateway; human `Approver` sign-off; exact-plan SQL broker; independent SQL verifier |
| **2. Unprivileged process exploit** inside the MicroVM | KVM MicroVM boundary (no guest network device, `vsock`-only to host supervisor), non-root UID 998, seccomp | Host supervisor holds provider credentials outside the MicroVM; external gateway + SQL broker |
| **3. Root-on-OS inside the Workspace VM** | *Impaired* (in-guest policy verification and provider store can be bypassed by root on the VM) | **External controls remain intact:** Untrust VNet NSG (denies inbound, east-west, and direct SQL 1433); external Invoice Gateways hold the model API key; SQL Broker enforces the approved `plan_hash` using its own Container Apps Managed Identity; control-plane VM stop & SQL capability revocation (`control.usp_revoke_invoice_run`) |

## How this repository maps to the reference design

| SAW element | This repository | Status |
| --- | --- | --- |
| Single-user workspace VM, approved image, managed lifecycle | Dedicated private Trusted Launch Azure VM for the website agent, pinned Ubuntu image, control-plane-owned start/stop. The invoice deployment runs it in `operator-managed` mode (no VM lease timer); per-run watchdogs and capabilities still expire | Implemented |
| Runtime sandbox layer | OpenShell sandboxes, each in its own MicroVM, with distinct policies per role; see [OpenShell](openshell.md) | Implemented; boundary probes recorded |
| Separate sponsor, workspace, logical-agent, and runtime identities | Entra human roles, AgentRunner logical identity (`agentrunner-invoice-planning`, `agentrunner-invoice-execution`), per-run sandbox capability, and Ed25519-signed `DelegationRecord` ([`control/invoice_governance.py`](../../src/task_agent/control/invoice_governance.py)) | Implemented in code; verified in CI |
| Governed connectors with human review for sensitive writes | Capability-authenticated invoice gateways, a SQL broker that enforces the exact approved plan, an independent verifier, and a separate Approver | Implemented; sandbox Planning → approval → sandbox Execution → verification recorded 2026-09-16 (synthetic reviewer), Approver UI validated 2026-09-19 |
| Brokered interactive access (enterprise SSO) | Not implemented | Gap |
| Runtime credential mediation | Model key held by a trusted gateway. Opt-in OpenShell provider mode keeps the run capability out of the sandbox and garbage-collects providers on sandbox deletion | Partial; provider mode not yet verified live |
| Workspace perimeter | NSGs plus OpenShell egress policy; see [Network perimeter](#network-perimeter) | Lower-cost substitute for the reference Azure Firewall Premium perimeter |
| Signed-policy governance and delegation record | Checked-in OpenShell policies verified against Ed25519 signatures ([`policy-signatures.json`](../../infra/next-phase/openshell/policy-signatures.json)) in controller and on VM host; per-run `SignedDelegation` verified before sandbox execution | Implemented in code; verified in CI |
| Kill switch outside the agent's control | Control-plane VM stop, per-run capability and delegation revocation, sandbox stop, and a root-owned admission gate (`/etc/learningnemo/invoice-availability.json`) | Implemented; bound to signed delegation hash |

## Who the workspace is for

NVIDIA describes a SAW as a *single-user* workspace. This repository applies
that to a **website-driven agent**, not a person working inside the VM:

- The website (dashboard, sign-in, approval, and trusted APIs) runs outside the
  workspace in Azure Container Apps. Per the
  [specification](../specification/next-phase-saw-openshell-spec.md#62-do-not-use-saw-as-a-service-hosting-platform),
  the SAW never hosts the website.
- The workspace VM hosts only the agent runtime: the OpenShell gateway and the
  sandboxes the website asks it to run.
- "Single-user" means **one trust domain with one accountable owner**, the
  operator who owns the engagement. Website users never receive a shell,
  credentials, or network access to the VM.
- Runs from different website users share the VM, so isolation *between runs*
  comes from separate, short-lived sandboxes and per-run capabilities. One
  sandbox is active at a time and up to 24 stopped sandboxes are retained for
  evidence. Hard isolation between users or tenants needs a separate
  workspace VM each.

## Network perimeter

The workspace subnet's NSG is applied in stages; full rule tables are in the
[infrastructure README](../../infra/next-phase/README.md#security-properties).

| Stage | Effect |
| --- | --- |
| Foundation | Deny all inbound, the trusted platform network, east-west traffic, and direct Azure SQL |
| Bootstrap (temporary) | NAT and a registry rule for package and image installation, removed afterwards |
| Runtime lock | Deny instance metadata, allow Azure DNS, allow HTTPS to `AzureCloud`, deny `Internet` |
| Runtime egress | Owned NAT gateway for outbound connections under those rules |

`AzureCloud` covers every Azure customer's addresses, so the NSG alone cannot
restrict traffic to this project's endpoints; that restriction comes from the
OpenShell sandbox policy. Making the gateways internal-only behind a private
endpoint, or adding an Azure Firewall with hostname rules, would close it.

## Where to look next

- Rationale and constraints: [ADR 0001](../decisions/0001-openshell-microvm-driver.md)
- Target design and claim levels: [Specification](../specification/next-phase-saw-openshell-spec.md)
- Current status: [Status](../status.md)
