# Build and Reproduce LearningNeMo

This is the operator build guide. For the presentation, use the
[capability demonstration runbook](capability-demo.md). For design intent, use
the [SAW specification](../specification/next-phase-saw-openshell-spec.md); it includes planned
work and is not a completion report.

## Status and Acceptance

Recorded on 2026-09-14:

| Layer | Established result | Remaining qualification |
| --- | --- | --- |
| Local NeMo agent and console | Reader/Operator authorization walkthroughs were live-verified | Recheck sign-in, API, and gateway before presenting |
| Trusted SQL control plane | Eight-stage incident cycle was independently verified | Its evidence is separate from sandbox execution |
| SAW and OpenShell | Private VM bootstrap, three MicroVMs, and policy/boundary probes succeeded | Success came from a preserved-VM retry |
| Post-bootstrap network | Temporary NAT was removed | Azure rejected part of the runtime-lock deployment |
| Complete SAW demo | Not yet established | Repair the lock, pass final verification, and prove a clean build and sandbox-to-incident integration |

Later records supersede parts of this table. On 2026-09-15 the runtime lock was
repaired, an owned runtime NAT gateway was attached for approved egress, and a
fixed Planning proof passed under lockdown
([runtime diagnosis](../archive/diagnose-runtime.md)). On 2026-09-16 the invoice workflow
ran real Planning and Execution agents in separate OpenShell MicroVMs through
approval, broker receipts, and independent SQL verification
([invoice demo](invoice-demo.md#observed-acceptance)). A clean-host
reproduction is still outstanding.

These are historical results, not current Azure health. Do not bypass a gate to
turn this table green. Local compilation alone cannot establish Azure service
tag compatibility or a functioning sandbox.

## 1. Prepare the Workstation

Run Linux commands below from the repository root in Ubuntu 24.04 WSL2.
Use PowerShell only for commands explicitly marked as PowerShell. This project
uses a Linux Python environment; a Windows virtual environment is not equivalent.

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/nemo-agents"
"$HOME/.local/bin/uv" sync --frozen
python3 infra/next-phase/check_toolchain.py
bash infra/test-all-local.sh
```

Install `uv`, Azure CLI, and Bicep before these commands. The authoritative
minimums and tested versions are in [toolchain.json](../../infra/next-phase/toolchain.json).
The last deployment used Python 3.12, Azure CLI 2.90.0, Bicep 0.47.16, and Bash
5.2. These are recorded versions, not instructions to ignore the toolchain gate.

Authenticate interactively and pin the subscription intentionally:

```bash
az login
export AZURE_SUBSCRIPTION_ID="<expected-subscription-id>"
test "$(az account show --query id --output tsv)" = "$AZURE_SUBSCRIPTION_ID"
```

Use an account with the required Azure deployment and Entra administration
permissions. Do not substitute the active subscription automatically for the
expected one. Regional capacity, provider registration, budget, and policy checks
must also pass. No command in this guide creates a new subscription or tenant.

## 2. Build the Local Agent Experience

Follow the local walkthrough's [Entra configuration](local-authorization.md#configure-entra) and
[LLM gateway deployment](local-authorization.md#deploy-the-llm-gateway). They establish the
public test client, two account role assignments, APIM operations, and Key Vault
configuration. Use the checked-in preview and apply workflows, not portal edits.

The provider API key is entered directly in the terminal or supplied through an
owner-only file outside the repository. Never place it in Markdown, a command
transcript, browser state, or a review archive.

In one WSL terminal:

```bash
bash scripts/run-agent.sh
```

In a second WSL terminal at the repository root:

```bash
"$HOME/.venvs/nemo-agents/bin/learningnemo"
```

Expected local addresses are `http://127.0.0.1:8001` for the API and
`http://127.0.0.1:8765` for the console. Do not expose either listener publicly.
Sign in through the console. Reader must be unable to write; Operator must
execute and then read back the state. These task operations are not the SQL
incident workflow and are not evidence that the agent runs inside OpenShell.

## 3. Establish the Azure Dependencies

Review the [infrastructure operator guide](../../infra/next-phase/README.md) for
configuration, resource inventory, permissions, and per-layer cleanup.
The initial dependency order is:

| Stage | Entry point | Expected outcome |
| --- | --- | --- |
| Network foundation | [deploy-foundation.sh](../../infra/next-phase/deploy-foundation.sh) | Tagged platform/SAW networking with no VM |
| Budget | [deploy-budget.sh](../../infra/next-phase/deploy-budget.sh) | Notified subscription budget within the configured ceiling |
| Persistent identities | [deploy-identities.sh](../../infra/next-phase/deploy-identities.sh) | Six distinct regional service identities |
| Trusted services | [reconcile-wp3.sh](../../infra/next-phase/reconcile-wp3.sh) | SQL, private network, ACR, runtime, signed image, audiences, migrations, workers |

Preview and review the foundation, budget, and identity phases using their
operator instructions. Their apply acknowledgements are respectively
`network-foundation`, `cost-budget`, and `platform-identities`. Verify their
results before advancing to WP3.

The image build needs the checksum-verified tools installed by
[install-supply-chain-tools.sh](../../scripts/install-supply-chain-tools.sh).
For a full trusted-service build, after those prerequisites:

```bash
bash scripts/install-supply-chain-tools.sh
bash infra/next-phase/reconcile-wp3.sh --ttl-hours 8
LEARNINGNEMO_AZURE_APPLY=wp3-reproducible \
  bash infra/next-phase/reconcile-wp3.sh --ttl-hours 8 --apply
bash infra/next-phase/verify-wp3.sh
```

The first reconciliation command performs local gates and available previews.
Initial previews can be unavailable until earlier phases create private state.
The apply command is a real deployment: it also builds an image, reconciles
Entra policy, and runs migrations. It is not a lightweight lease renewal.

### What the Image Build Proves

[build-trusted-image.sh](../../scripts/build-trusted-image.sh) builds remotely in
ACR from digest-pinned bases and a hashed source context. It resolves an immutable
image digest, generates a CycloneDX SBOM and vulnerability report, checks native
ELF dependencies, signs and verifies a detached image-reference payload, and
binds the evidence to the approval schema and source revision.

The release is accepted by [workload_release.py](../../infra/next-phase/workload_release.py)
before deployment. A valid signature does not prove absence of vulnerabilities;
the scan gate is a separate check. Do not invent attestations, replace a partial
signing trust root, or call a mutable tag reproducible. If the source changes,
regenerate and validate its release evidence.

### Leave Enough Time for the Workspace

The WP3 reconciler currently deploys workers with a one-hour lease even when its
supporting resources get eight hours. Before a longer SAW rehearsal, renew the
workers within the remaining supporting-service window:

```bash
state="$HOME/.local/state/learningnemo"
bash infra/next-phase/deploy-workloads.sh --what-if \
  --runtime-parameters "$state/runtime-dev.parameters.json" --ttl-hours 5
LEARNINGNEMO_AZURE_APPLY=trusted-workloads \
  bash infra/next-phase/deploy-workloads.sh --apply \
  --runtime-parameters "$state/runtime-dev.parameters.json" --ttl-hours 5
```

Five hours is an example, not a permitted extension beyond dependency expiry.
If dependencies have expired or lack headroom, renew them through their scoped
deployment scripts first. The worker and workspace preflights must accept all
expirations; editing state files to defeat those checks is not a renewal.

## 4. Build the SAW and OpenShell Layer

Source ownership is deliberately small:

| File | Responsibility |
| --- | --- |
| [dev.workspace.config.json](../../infra/next-phase/environments/dev.workspace.config.json) | VM/image/version/hash/resource bounds |
| [workspace.bicep](../../infra/next-phase/workspace.bicep) | Private Trusted Launch VM, NIC, no-RBAC identity |
| [workspace-openshell-bootstrap.bicep](../../infra/next-phase/workspace-openshell-bootstrap.bicep) | Render policy hosts and the bounded run command |
| [bootstrap-saw-openshell.sh](../../scripts/bootstrap-saw-openshell.sh) | Verify package, configure mTLS, create three sandboxes, test boundaries, arm expiry |
| [workspace-runtime-lock.bicep](../../infra/next-phase/workspace-runtime-lock.bicep) | Post-bootstrap host network restrictions |
| [verify_workspace.py](../../infra/next-phase/verify_workspace.py) | Independent infrastructure and runtime evidence validation |

The recorded configuration pins OpenShell `0.0.116`, the Debian package hash,
both OCI image digests, Ubuntu image version, and `Standard_D2s_v5`. The host has
no public IP. Sandboxes use the image-native non-root account. Package download
and image pulls use a temporary bootstrap NAT and registry rule, which must be
removed before completion. Runtime egress then uses a separate owned NAT gateway
(`workspace-runtime-egress.bicep`) under the runtime-lock NSG rules. The
network stages are described in the
[infrastructure README](../../infra/next-phase/README.md#security-properties).

**Status (updated 2026-09-15):** the runtime-lock rule was repaired and
independent host verification passed. Approved runtime connectivity was then
restored with the owned runtime NAT gateway, guest DNS was pinned to
`168.63.129.16`, and the fixed Planning route proof passed under lockdown. Do
not reopen unrestricted egress or reuse the pre-lockdown bootstrap result as
current route verification.

Two operational caveats from that recovery:

- The runtime `AzurePlatformIMDS` deny also blocks the host's cloud-init. VM
  start uses a guarded maintenance path that temporarily removes only that
  rule on a deallocated VM, then restores and compares all rules; sandbox
  admission stays blocked while it is absent.
- This OpenShell version revalidates the sandbox image registry on start, so
  stop/start must happen within a pinned-registry window.

Run local checks first; the clean deployment still has to be reproduced on a
new host:

```bash
bash infra/next-phase/test-workspace.sh
bash infra/next-phase/deploy-workspace.sh --what-if --ttl-hours 4
LEARNINGNEMO_AZURE_APPLY=wp5-wp6-saw-openshell \
  bash infra/next-phase/deploy-workspace.sh --apply --ttl-hours 4
```

The clean workflow requires the SAW resource group in exact network-foundation
state, not a preserved VM. Do not delete a diagnostic VM simply to pass that
preflight. Inspect and preserve its evidence first. There is currently no
general `--resume` flag. The bounded retry performed in this investigation is
documented in the [diagnostic record](../archive/openshell-bootstrap-diagnostic-record.md).

A complete build requires all of the following, not just exit zero from a CLI:

- Gateway JSON reports the intended endpoint, `connected`, and authenticated mTLS.
- Three sandboxes exist and the route and boundary checks pass.
- The temporary bootstrap NAT/PIP and registry rule are absent; only the owned
  runtime NAT gateway is associated with the subnet.
- The runtime-lock stack and exact NSG rules verify.
- Host Internet and IMDS probes fail as intended; the expiry timer is armed.
- Independent verification writes a current, matching workspace result.
- A new host without the diagnostic image cache reproduces the result.

The deploy wrapper invokes the verifier with its freshly compiled templates.
Do not reuse an old result file as proof of a later attempt. Sandbox bootstrap
`readiness.json` precedes host lockdown and is not the final workspace result.

## 5. Evidence, Failure Handling, and Cleanup

Generated parameters, identities, signing material, diagnostics, and evidence
live under `~/.local/state/learningnemo` with owner-only permissions. Keep that
directory outside the repository. The main evidence families are:

| Evidence | Meaning |
| --- | --- |
| Trusted runtime release/SBOM/scan/signature files | Build and release binding |
| `control-cycle-dev.result.json` | Recorded trusted-service incident cycle |
| Timestamped workspace diagnostics | Captured state of a particular attempt |
| `workspace-dev.result.json` | Final workspace verification, only if that run succeeded |

Before restart, deletion, or another bootstrap attempt, capture a new file:

```bash
python3 infra/next-phase/capture_workspace_diagnostics.py \
  --resource-group rg-learningnemo-saw-dev --vm-name vm-learningnemo-saw-dev \
  --deployment-prefix learningnemo-openshell-bootstrap-dev \
  --output "$HOME/.local/state/learningnemo/workspace-dev.$(date -u +%Y%m%dT%H%M%SZ).diagnostics.json"
```

If capture fails, diagnose that failure before destructive recovery. Inspect
partial stack/rule state after a failed deployment. A CLI exit code alone is not
a readiness decision. In the pinned release, avoid gateway restart as a demo
reset: restoring older sandboxes can reuse expired JWTs. Use the recorded
diagnostic procedure rather than repeated restarts.

When the demonstration window is over, preview then explicitly remove only the
workspace, preserving the foundation:

```bash
bash infra/next-phase/remove-workspace.sh
LEARNINGNEMO_AZURE_DELETE=wp5-wp6-saw-openshell \
  bash infra/next-phase/remove-workspace.sh --apply
```

The guest timer powers off the host and attempts sandbox deletion. It does not
delete Azure resources or guarantee Azure deallocation. Tags and budget alerts
are not cleanup schedulers or hard spending caps. Use the separate scoped
removal procedures for workers, runtime, registry, and private networking, and
check their final inventories. Keep evidence after removing compute.

For an interview handoff, create the allowlisted review archive from PowerShell:

```powershell
.\scripts\create-review-archive.ps1
```

Do not ZIP the workspace or private state. Include the build guide, demo runbook,
source, and tests; review any separately shared evidence for identifiers and
sensitive content first.