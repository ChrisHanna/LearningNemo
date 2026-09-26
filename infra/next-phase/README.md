# LearningNeMo Next-Phase Infrastructure

This directory implements the Azure infrastructure from the
[next-phase specification](../../docs/next-phase-saw-openshell-spec.md):

- WP0 preflight and the WP1 Azure network foundation;
- the WP2a trusted platform base and WP2b Entra audiences and least-authority
  workers;
- the WP3 private SQL control plane with a disposable end-to-end incident
  cycle;
- the private workspace VM, its OpenShell bootstrap, and its staged network
  egress controls (the SAW layer); and
- the cloud dashboard, human-handoff services, and invoice services that drive
  the website agent.

Sections below describe WP0-WP3 in detail. The later layers are listed under
[Workspace, Cloud Demo, and Invoice Layers](#workspace-cloud-demo-and-invoice-layers)
and documented in the [build guide](../../docs/build-and-reproduce.md),
[cloud demo guide](../../docs/cloud-demo.md), and
[invoice demo guide](../../docs/invoice-demo.md).

## Current Claim

This is a **live-verified identity, private SQL, and least-authority agent
control-plane POC**. The repository can reproducibly deploy the VNet-integrated
runtime, a signed immutable image, four credentialless audiences and workers,
six hash-bound SQL migrations, and a one-shot approval-bound incident cycle.
Migration and control jobs are removed after execution; workers and supporting
runtime resources remain TTL-bound. On 2026-09-14, a private SAW VM completed
OpenShell bootstrap with Planning, Execution, and Probe MicroVMs and policy
checks. On 2026-09-15 the runtime-lock DNS rule was repaired, independent host
verification passed, an owned runtime NAT restored approved egress, and a fixed
Planning route proof passed under lockdown. On 2026-09-16 the invoice workflow
ran Planning and Execution agents through OpenShell to broker receipts and
independent SQL verification. Clean-host reproducibility remains pending. This
is not yet a complete reference-aligned SAW; recorded results are not current
health.

The [build guide](../../docs/build-and-reproduce.md) connects these infrastructure
phases to local setup, image evidence, SAW deployment, and cleanup. The
[capability runbook](../../docs/capability-demo.md) describes what to show live,
what to present as recorded evidence, and which claims remain pending.

## What These Slices Create

The WP1 subscription-scoped template creates seven resources:

| Resource | Count | Metered in this slice |
| --- | ---: | --- |
| Resource groups | 2 | No |
| Virtual networks | 2 | No |
| Network security groups | 2 | No |
| Route tables | 1 | No |

WP2a is split at the cost boundary:

| Slice | Resource | Count | Lifecycle |
| --- | --- | ---: | --- |
| WP2a-I | User-assigned managed identities | 6 | Persistent, no compute |
| WP2a-R | Container Apps managed environment | 1 | Optional 1-24 hour runtime envelope |

WP2a-I creates no application, compute, network, database, or logging resource.
WP2a-R is an explicitly deployed, TTL-bound runtime. It
attaches one empty environment to the WP1 Container Apps subnet because an
environment's network type cannot be changed after creation. This preserves the
future path to SQL, Key Vault, and ACR private endpoints.

WP2b is split into persistent identity policy and ephemeral compute:

| Slice | Resource | Count | Lifecycle |
| --- | --- | ---: | --- |
| WP2b-I | Credentialless tenant-only Entra applications | 4 | Persistent, no compute |
| WP2b-I | Entra service principals | 4 | Persistent, no compute |
| WP2b-R | Digest-pinned Container Apps | 4 | Optional 1-24 hour, scale to zero |

WP2b-I uses the pinned Microsoft Graph Bicep extension. Because Entra generates
application IDs, its wrapper creates resources by immutable `uniqueName`,
resolves those IDs into owner-only state, then converges exact `api://` audience
URIs and service principals in a second Bicep deployment. Neither template has
identity outputs. WP2b-R binds one existing managed identity and one audience
to each worker, caps every app at one replica, and persists no secrets or token
store.

Azure creates a separate managed infrastructure resource group for the
VNet-integrated environment. For this external workload-profile environment,
Azure documents one load balancer plus ingress and egress public IP resources.
Those service-managed resources incur standing charges and are not shown as
direct resources in the one-create WP2a-R what-if summary.

Azure may charge for traffic or services added in later work packages. The
compiled-template validator fails if a metered resource type is accidentally
added to WP1 or if a workload or deferred dependency is added to WP2a. WP2a has
no application-compute or log-ingestion charges while no app exists, but the
managed network resources are billable. Azure pricing remains authoritative.

The canonical development parameters record a `50` monthly ceiling in the
subscription billing currency. The separate zero-cost subscription budget is
deployed and verified. Its checked-in
configuration contains no recipient; the deployment wrapper derives the
signed-in user's email at runtime or accepts
`LEARNINGNEMO_BUDGET_CONTACT_EMAIL`, passes it as a secure ARM parameter, and
does not write it to the local manifest. The platform preflight blocks apply
unless an active notified subscription budget is at or below the recorded
ceiling. Azure budgets send alerts; they do not stop, scale down, or delete
resources.

## Files

| File | Purpose |
| --- | --- |
| `environments/dev.parameters.json` | Canonical non-secret development topology and tags |
| `foundation.bicep` | Creates trusted-platform and disposable-SAW resource groups and invokes both network modules |
| `modules/platform-network.bicep` | Reserves delegated Container Apps and private-endpoint subnets |
| `modules/saw-network.bicep` | Creates the untrusted workspace subnet, firewall reservation, NSG, and route table |
| `toolchain.json` / `check_toolchain.py` | Record tested versions and enforce minimum deployment-tool versions |
| `provider-phases.json` / `register-providers.sh` | Define and optionally register providers one work package at a time |
| `environments/dev.budget.config.json` | Canonical recipient-free monthly budget name and amount |
| `budget.bicep` / `deploy-budget.sh` | Create the subscription budget with a secure runtime recipient |
| `remove-budget.sh` | Verify and optionally remove only the subscription budget |
| `budget_parameters.py` / `validate-budget.py` / `verify_budget.py` | Materialize, validate, and verify the budget without exposing its recipient |
| `record_budget.py` | Write a recipient-free local budget manifest |
| `summarize_what_if.py` | Print change types and safe resource names without account IDs |
| `test-budget.sh` | Run the local or Azure budget validation gate |
| `foundation_parameters.py` | Validate and materialize the non-secret ARM parameter contract |
| `preflight.py` | Performs offline CIDR checks and read-only Azure readiness checks |
| `validate-foundation.py` | Rejects unapproved or metered resource types in the compiled WP1 template |
| `deploy-foundation.sh` | Runs preflight, compile, policy validation, Azure validation, and what-if before optional apply |
| `record_foundation.py` | Writes a non-secret local manifest with resolved parameters and template hashes |
| `snapshot_foundation.py` | Captures a minimal non-secret inventory before guarded resource-group deletion |
| `verify_foundation.py` | Verifies exact deployed resources, tags, topology, links, and deny rules |
| `check_foundation_cleanup.py` / `remove-foundation.sh` | Remove the groups only while exact WP1 names, tags, and expiration state match |
| `test-foundation.sh` | Runs the reproducible local gate and optional read-only Azure gate |
| `environments/dev.platform.parameters.json` | Canonical non-secret persistent identity parameters |
| `modules/platform-identities.bicep` / `identities.bicep` | Define and deploy the six persistent identities once |
| `deploy-identities.sh` / `remove-identities.sh` | Guard identity-only deployment and removal |
| `preflight_identities.py` / `validate-identities.py` / `verify_identities.py` | Validate identity configuration, template, and live state |
| `record_identities.py` / `test-identities.sh` | Record safe identity evidence and run local/Azure gates |
| `environments/dev.runtime.parameters.json` | Canonical runtime parameters with a materialized expiration placeholder |
| `platform.bicep` | Creates only the VNet-integrated external Consumption runtime envelope |
| `platform_contract.py` | Centralizes exact WP1/WP2a names, tags, and inventories |
| `platform_parameters.py` / `runtime_parameters.py` | Validate persistent and expiring parameter contracts |
| `preflight_platform.py` | Performs read-only runtime readiness and budget checks |
| `validate-platform.py` | Requires the WP1 delegated subnet and rejects apps, jobs, logging, and deferred dependencies |
| `deploy-platform.sh` | Runs the what-if-first, hourly runtime-envelope workflow |
| `record_platform.py` / `verify_platform.py` | Record sanitized evidence and verify live runtime controls |
| `check_platform_cleanup.py` / `remove-platform.sh` | Preserve identities while removing only the runtime envelope |
| `test-platform.sh` | Runs the reproducible local or read-only Azure runtime gate |
| `bicepconfig.json` / `entra-*.bicep` | Pin Graph Bicep and define two-stage credentialless audiences |
| `entra_workload_parameters.py` | Resolve generated IDs and the control identity into owner-only state |
| `validate-entra-workloads.py` / `test-entra-workloads.sh` | Enforce and test Graph audience policy without mutation |
| `deploy-entra-workloads.sh` / `record_entra_workloads.py` | What-if, deploy, verify, and record sanitized Entra evidence |
| `environments/dev.workloads.config.json` | Identifier-free worker topology and tags |
| `modules/trusted-service-app.bicep` / `workloads.bicep` | Define four isolated, scale-to-zero worker apps |
| `workload_parameters.py` / `workload_contract.py` | Validate private inputs and centralize exact worker names/tags |
| `preflight_workloads.py` / `validate-workloads.py` | Check live dependencies and compiled worker security policy |
| `workload_release.py` | Rehash immutable image, SBOM, scan, signature, and approval-schema evidence |
| `deploy-workloads.sh` / `verify_workloads.py` | What-if, deploy, and verify ARM plus anonymous HTTP denial |
| `record_workloads.py` | Write an identifier-free evidence manifest after verified apply |
| `check_workload_cleanup.py` / `remove-workloads.sh` | Remove only owned workers while preserving WP1/WP2a |
| `test-workloads.sh` | Run the complete local WP2b worker/runtime/workflow gate |
| `database-stack.bicep` / `database.bicep` | Define the Entra-only free-limit SQL base |
| `database-network-stack.bicep` / `database-network.bicep` | Define the expiring private endpoint and private DNS overlay |
| `deploy-database*.sh` / `verify_database*.py` | Reconcile and independently verify SQL base and network layers |
| `artifact-registry-stack.bicep` / `artifact-registry.bicep` | Define one expiring Basic ACR and five exact `AcrPull` bindings |
| `deploy-artifacts.sh` / `verify_artifacts.py` | Reconcile and verify the passwordless artifact layer |
| `scripts/install-supply-chain-tools.sh` / `scripts/build-trusted-image.sh` | Install pinned tools and remotely build, scan, sign, and attest the image |
| `migration-stack.bicep` / `migration-job.bicep` | Define one manual, zero-retry, bounded SQL migration job |
| `sql_migrations.py` / `scripts/apply-sql-migrations.py` | Render identity-bound SQL and verify six exact migration receipts |
| `deploy-migration.sh` / `verify_migration.py` | Apply and verify migrations through private VNet-connected compute |
| `control-cycle-stack.bicep` / `control-cycle-job.bicep` | Define one expiring, no-ingress live control job with split runtime/image-pull identities |
| `control_cycle_parameters.py` / `preflight_control_cycle.py` | Bind the signed release, worker audiences, private SQL, subject hashes, and dependency lifetimes |
| `deploy-control-cycle.sh` / `verify_control_cycle.py` | Preview, deploy, and verify the exact least-authority live control job |
| `summarize_control_cycle.py` / `verify_control_cycle_evidence.py` | Preserve and independently verify identifier-free eight-stage source-bound evidence |
| `remove-control-cycle.sh` | Remove only disposable control compute while preserving workers, identities, and grant separation |
| `test-control-cycle.sh` | Run the local control-cycle, SQL adapter, cancellation, and IaC policy gates |
| `reconcile-wp3.sh` / `verify-wp3.sh` | Compose all guarded layers into one repeatable preview/apply/verification path |

### Workspace, Cloud Demo, and Invoice Layers

These later layers are not part of the WP0-WP3 gates above. Each has its own
preview, validation, and verification scripts.

| Area | Main files | Purpose |
| --- | --- | --- |
| Workspace VM | `environments/dev.workspace.config.json`, `workspace.bicep`, `workspace_parameters.py`, `preflight_workspace.py`, `deploy-workspace.sh`, `verify_workspace.py`, `remove-workspace.sh`, `test-workspace.sh` | Private, no-public-IP Trusted Launch VM (`Standard_D2s_v5`, pinned Ubuntu 24.04 image) whose runtime identity has no Azure RBAC |
| OpenShell bootstrap | `workspace-openshell-bootstrap.bicep`, `../../scripts/bootstrap-saw-openshell.sh`, `validate-workspace-openshell-bootstrap.py` | Install pinned OpenShell `0.0.116`, a loopback-only mTLS gateway with 15-minute JWTs, the MicroVM driver (1 vCPU / 1 GiB per sandbox), and the Planning, Execution, and Probe sandboxes |
| Sandbox policies | `openshell/*.yaml` | Fixed-proof Planning, Execution, and Probe policies (hash-pinned, unchanged) and hardened invoice Planning/Execution policies (Landlock `hard_requirement`, Python-only egress); invoice policy changes take effect for new runs after the invoice services image is redeployed |
| Workspace egress | `workspace-bootstrap-egress.bicep`, `workspace-registry-bootstrap.bicep`, `workspace-subnet-egress.bicep`, `workspace-runtime-lock.bicep`, `workspace-runtime-egress.bicep`, `deploy_runtime_egress.py` and their validators | Staged network controls described under [Security Properties](#security-properties) |
| Workspace operations | `renew_preserved_workspace.py`, `repair_retained_workspace.py`, `resume_workspace_sandboxes.py`, `run_workspace_proof.py`, `run_retained_boundary_proofs.py`, `capture_workspace_diagnostics.py`, `collect_runtime_diagnostics.py` | Lease renewal, recovery, fixed proofs, and sanitized diagnostics |
| Cloud dashboard | `cloud-demo.bicep`, `modules/cloud-demo-*.bicep`, `deploy-cloud-demo.sh`, `validate_cloud_demo.py`, `verify-cloud-demo.py`, `remove-cloud-demo.py` | Dashboard, internal workspace controller, and task API in Container Apps |
| Human-handoff services | `human-services.bicep`, `human-*-job.bicep`, `deploy_human_services.py`, `verify_human_services.py`, `review-service/001`-`007` SQL | Approver review, incident handoff, and execution coordination |
| Invoice services | `invoice-services.bicep`, `invoice-package-maintenance.bicep`, `deploy_invoice_services.py`, `verify_invoice_services.py`, `rehearse_invoice_*.py`, `review-service/008`-`016` SQL | Diagnostic and execution gateways used by the invoice agent sandboxes |
| Connectivity probe | `connectivity-probe-*.bicep`, `deploy-connectivity-probe.sh`, `verify_connectivity_probe.py` | Bounded private SQL connectivity checks |

## Reproducibility Contract

The checked-in parameter, provider, and toolchain files are the source of truth.
The scripts do not depend on values from this conversation or hidden local
configuration.

The deployment was tested with:

| Tool | Minimum | Tested |
| --- | ---: | ---: |
| Azure CLI | 2.76.0 | 2.90.0 |
| Bicep CLI | 0.35.0 | 0.47.16 |
| Python | 3.11.0 | 3.12.3 |
| Bash | 4.4.0 | 5.2.21 |
| uv | 0.8.0 | 0.12.13 |

Run all infrastructure commands from Linux or Ubuntu WSL2. Native Windows
PowerShell does not satisfy the Bash and GNU `date` assumptions in these
scripts.

The stable topology is stored in `environments/dev.parameters.json`. WP1
materializes a date from a bounded one-to-seven-day TTL. WP2a-R separately
materializes a whole-second UTC timestamp from a bounded 1-24 hour TTL; the
default is eight hours.

After apply, `${XDG_STATE_HOME:-$HOME/.local/state}/learningnemo` contains:

```text
foundation-dev.parameters.json  exact resolved non-secret parameters
foundation-dev.manifest.json    template/parameter hashes and deployment outputs
budget-dev.manifest.json        recipient-free budget amount, start date, and hashes
identities-dev.parameters.json  exact non-secret identity parameters
identities-dev.manifest.json    identity template/parameter hashes and safe-name outputs
runtime-dev.parameters.json     exact runtime parameters including expiration
runtime-dev.manifest.json       runtime template/parameter hashes and safe-name outputs
workloads-dev.auth.json         private tenant, caller, principal, and audience IDs
workloads-dev.entra-manifest.json  identifier-free Graph names and evidence hashes
workloads-dev.parameters.json   private digest, expiry, and workload identity parameters
workloads-dev.manifest.json     identifier-free release/deployment evidence hashes
database-dev.state.json         private generated SQL endpoint state
database-network-dev.parameters.json  private endpoint parameters and expiration
artifacts-dev.state.json        private generated ACR endpoint state
trusted-runtime-dev.image.txt   private digest-pinned image reference
trusted-runtime-dev.sbom.json   observed CycloneDX image SBOM
trusted-runtime-dev.scan.json   observed Grype vulnerability report
trusted-runtime-dev.signature.json  verified detached Cosign bundle and binding hashes
trusted-runtime-dev.release.json    source-, scan-, and signature-bound release evidence
migration-dev.parameters.json   private SQL bundle, image, endpoint, and job parameters
migration-dev.manifest.json     identifier-free six-receipt migration evidence
control-cycle-dev.result.json   identifier-free eight-stage live-cycle evidence
```

The Linux state directory preserves restrictive file permissions, unlike the
default Windows-mounted workspace under WSL. Set `LEARNINGNEMO_INFRA_STATE_DIR`
to use another path; `.infra-state` remains gitignored as an optional local
override. Azure deployment history remains the cloud-side record of the
submitted template and parameters.

No parameter or tag key may contain a secret-like name. Additional tags cannot
override managed security, cost, ownership, environment, or expiration tags.

## Clean-Room Bootstrap

From a new Ubuntu or WSL2 environment:

```bash
cd LearningNemo  # repository root

UV_PROJECT_ENVIRONMENT="$HOME/.venvs/nemo-agents" \
  uv sync --locked

az login
az account set --subscription "<expected-subscription-id>"
export AZURE_SUBSCRIPTION_ID="<expected-subscription-id>"
```

`AZURE_SUBSCRIPTION_ID` is not a secret, but it is intentionally not committed.
Every mutation verifies it against the active Azure CLI subscription.

Run the complete local gate:

```bash
bash infra/next-phase/test-foundation.sh
bash infra/next-phase/test-budget.sh
bash infra/next-phase/test-identities.sh
bash infra/next-phase/test-platform.sh
bash infra/next-phase/test-entra-workloads.sh
bash infra/next-phase/test-workloads.sh
bash infra/next-phase/test-control-cycle.sh
```

The local gate verifies tool versions, shell syntax, Python syntax, parameter
contracts, CIDRs, Bicep compilation, the no-metered-resource allowlist, NSG rule
semantics, deployment manifests, and the post-deploy verification contract.

Add read-only Azure readiness, provider, deployment-validation, and what-if
checks:

```bash
bash infra/next-phase/test-foundation.sh --azure
bash infra/next-phase/test-budget.sh --azure
bash infra/next-phase/test-identities.sh --azure
bash infra/next-phase/test-platform.sh --azure
```

None of these commands creates or changes Azure resources.

## Provider Registration

Provider registration is subscription state, so it is separate from template
deployment. Preview the WP1 provider state:

```bash
bash infra/next-phase/register-providers.sh --phase foundation
```

Register only missing WP1 providers after review:

```bash
LEARNINGNEMO_AZURE_APPLY=provider-registration \
  bash infra/next-phase/register-providers.sh --phase foundation --apply
```

The budget stack uses `--phase cost`. WP2a-I uses `--phase identities`, with
only `Microsoft.ManagedIdentity` and `Microsoft.Resources`. WP2a-R uses
`--phase platform`, with only `Microsoft.App` and `Microsoft.Resources`. Later shared services
use `--phase platform-services`; Azure SQL and the SAW use `--phase database`
and `--phase workspace`. `--phase all` exists for clean-room automation but
SHOULD NOT be used before reviewing each work package. Provider registration
itself does not deploy billable service instances.

## Security Properties

The workspace subnet's network controls are applied in stages. All stages use
the same network security group (NSG), `nsg-vnet-learningnemo-saw-dev-workspace`.

**1. Foundation (WP1, `modules/saw-network.bicep`).** Always present:

| Priority | Direction | Rule |
| ---: | --- | --- |
| 100 | Inbound | Deny all inbound traffic |
| 100 | Outbound | Deny the trusted platform VNet |
| 110 | Outbound | Deny east-west traffic inside the SAW VNet |
| 120 | Outbound | Deny the Azure `Sql` service tag on every port |

The workspace route table disables BGP route propagation. With only these
rules, other internet egress is still possible through Azure's default route.

**2. Bootstrap (temporary).** `workspace-bootstrap-egress.bicep` attaches a
temporary NAT gateway so the VM can install pinned packages, and
`workspace-registry-bootstrap.bicep` adds rule 124, allowing HTTPS only to the
pinned sandbox image registry addresses. Both are removed before runtime
proofs.

**3. Runtime lock (`workspace-runtime-lock.bicep`).** Applied after bootstrap:

| Priority | Direction | Rule |
| ---: | --- | --- |
| 121 | Outbound | Deny `AzurePlatformIMDS` (instance metadata) |
| 122 | Outbound | Allow DNS to `168.63.129.16:53` |
| 125 | Outbound | Allow HTTPS to the `AzureCloud` service tag |
| 130 | Outbound | Deny `Internet` |

**4. Runtime egress (`workspace-runtime-egress.bicep`,
`deploy_runtime_egress.py`).** Attaches an owned NAT gateway for outbound
connections without changing the NSG rules above.

The IMDS deny (121) also blocks the host's own cloud-init. Starting the VM
therefore uses a guarded maintenance path that temporarily removes only that
rule on a deallocated VM and restores it before sandbox admission (see
[runtime diagnosis](../../docs/diagnose-runtime.md)).

What this does and does not provide:

- General internet egress is denied at the NSG layer once the runtime lock is
  applied.
- `AzureCloud` covers every public Azure IP address, including resources owned
  by other Azure customers. The NSG cannot restrict traffic to this project's
  own endpoints; that restriction comes only from OpenShell's per-sandbox
  network policy.
- The `/26` Azure Firewall subnet is reserved, but no firewall is deployed.
  Hostname-based egress filtering and the NVIDIA reference design's Azure
  Firewall Premium perimeter are therefore not provided. This is a lower-cost
  substitute, not a reference-aligned SAW perimeter.

### Provider-mediated run capability (opt-in)

By default each invoice run's capability is delivered to the sandbox agent.
With provider mode, the VM host mints it into an OpenShell provider bound by
`openshell/invoice-<kind>-provider-profile.yaml` to that role's gateway
routes. The agent sees only an `openshell:resolve:env:` placeholder, and only
the capability's SHA-256 hash leaves the VM. Stopping the run expires the
credential.

To enable it, rebuild and redeploy the invoice services image (it now carries
the two profiles), then run the services deployment with the mode set:

```bash
LEARNINGNEMO_INVOICE_CREDENTIAL_MODE=provider LEARNINGNEMO_AZURE_APPLY=invoice-services \
  python3 infra/next-phase/deploy_invoice_services.py services
```

The first prepared run imports each profile into the gateway once and refuses
to continue if an existing profile's credential binding has drifted. Verify
one Planning and Execution cycle and the boundary challenges before keeping
it on; to roll back, redeploy without the variable (the default is
`manifest`). Providers of deleted sandboxes are not yet garbage-collected;
their credentials are already expired.

The Container Apps subnet uses the current workload-profile requirements:

- dedicated to Container Apps;
- delegated to `Microsoft.App/environments`; and
- `/27`, the documented minimum size.

## Read-Only Preflight

Run from Ubuntu WSL:

```bash
cd LearningNemo  # repository root
python3 infra/next-phase/preflight.py \
  --parameters infra/next-phase/environments/dev.parameters.json
```

The preflight:

- validates canonical private CIDRs and subnet containment;
- rejects overlapping platform and SAW address spaces;
- checks Container Apps and Azure Firewall minimum subnet sizes;
- checks the active Azure cloud and region;
- checks providers needed by this slice;
- reports future provider registration as a warning;
- verifies that at least one approved low-cost SAW VM SKU is unrestricted; and
- rejects overlap with existing Azure VNets.
- reports absent, empty, partial, complete, or unexpected WP1 resource-group
  state before a rerun;

Use `--offline` to run only local configuration checks.

The persistent identity and runtime preflights require a healthy WP1 platform
group:

```bash
python3 infra/next-phase/preflight_identities.py \
  --parameters infra/next-phase/environments/dev.platform.parameters.json

python3 infra/next-phase/preflight_platform.py \
  --parameters infra/next-phase/environments/dev.runtime.parameters.json
```

The identity preflight checks only Managed Identity readiness, exact WP1
dependencies, and absent/partial/complete identity state. The runtime preflight
checks budget readiness, Container Apps regional availability, exact WP1 and
identity dependencies, the managed infrastructure group, and runtime state.
ARM deployment validation remains the authoritative permission and
service-quota gate.

## What-If First

The deployment wrapper defaults to a non-mutating subscription what-if:

```bash
bash infra/next-phase/deploy-foundation.sh
```

This command performs:

1. read-only Azure preflight;
2. Bicep compilation;
3. compiled-template resource allowlist validation;
4. Azure subscription deployment validation; and
5. `ResourceIdOnly` what-if.

No resource is changed by the default command.

Both WP2a wrappers have a non-mutating default:

```bash
bash infra/next-phase/deploy-identities.sh
bash infra/next-phase/deploy-platform.sh
```

The identity policy permits exactly six regional identities and no runtime
resource. The runtime policy permits exactly one VNet-integrated managed
environment and no identity mutation. After WP2a-I deployment, the runtime
what-if must show one create, eight ignores, and no modifications or deletes.
The service-managed
resource group, load balancer, and public IP resources are lifecycle-verified
after deployment even though they are not direct what-if entries.

Azure may label the already-deployed identities as `Deploy` in the identity
stack what-if because they are emitted through a nested ARM deployment. That is
what-if noise, not sufficient drift evidence. `verify_identities.py` is the
authoritative exact live-state check for identity names, tags, location, and
regional isolation.

WP2b and WP3 also default to no mutation. After prerequisite owner-only state
exists, each command performs preflight and what-if only:

```bash
bash infra/next-phase/deploy-entra-workloads.sh
bash infra/next-phase/deploy-database.sh --what-if
bash infra/next-phase/deploy-database-network.sh --what-if
bash infra/next-phase/deploy-artifacts.sh --what-if
bash infra/next-phase/deploy-migration.sh --what-if
bash infra/next-phase/deploy-workloads.sh --what-if
bash infra/next-phase/deploy-control-cycle.sh --what-if
bash infra/next-phase/reconcile-wp3.sh
```

All private files must be outside the repository and mode `0600`. The initial
Graph what-if can plan registration creation but must defer its audience stage
until Entra has generated the app IDs. A later run can plan the exact audience
convergence. Migration and workload commands additionally require a live
runtime, unexpired SQL network and artifact layers, a matching control
identity, four configured audiences, and complete observed release evidence.
Generated endpoints and IDs are read only from mode-`0600` state and never
source-controlled.

Preview the prerequisite subscription budget separately:

```bash
bash infra/next-phase/deploy-budget.sh
```

Its sanitized what-if must show exactly one
`Microsoft.Consumption/budgets` create or modify. It never prints the runtime
recipient or subscription ID.

## Apply

Review the what-if result first. To create the foundation, use an explicit,
command-scoped acknowledgement and expected subscription:

```bash
export AZURE_SUBSCRIPTION_ID="<expected-subscription-id>"

LEARNINGNEMO_AZURE_APPLY=network-foundation \
  bash infra/next-phase/deploy-foundation.sh \
  --parameters infra/next-phase/environments/dev.parameters.json \
  --ttl-days 7 \
  --apply
```

For another environment, copy and review the complete parameter contract:

```bash
cp infra/next-phase/environments/dev.parameters.json \
  infra/next-phase/environments/test.parameters.json
```

Change the environment, names, and CIDRs in that file, then pass it with
`--parameters`. Avoid ad hoc shell overrides because they are harder to review
and reproduce. `FOUNDATION_PARAMETERS_FILE` and `SAW_FOUNDATION_TTL_DAYS` remain
available for non-interactive automation.

Do not place credentials, user identifiers, email addresses, or sensitive data
in resource tags.

Apply automatically writes a local non-secret manifest and then runs
`verify_foundation.py`. An apply is not successful until live Azure state
matches the expected inventory, tags, subnets, links, deny rules, no-peering
condition, and empty route table.

Re-run live verification independently with the resolved state file:

```bash
python3 infra/next-phase/verify_foundation.py \
  --parameters "$HOME/.local/state/learningnemo/foundation-dev.parameters.json"
```

After reviewing the budget what-if, create the zero-cost alerting control first:

```bash
export AZURE_SUBSCRIPTION_ID="<expected-subscription-id>"

LEARNINGNEMO_AZURE_APPLY=cost-budget \
  bash infra/next-phase/deploy-budget.sh --apply
```

The wrapper derives the signed-in user's email without printing it. For
automation, set `LEARNINGNEMO_BUDGET_CONTACT_EMAIL` for that command only. The
budget sends actual-cost alerts at 50% and 80%, plus a forecasted alert at 100%.
Its recipient-free manifest records the amount, effective start month, and
source hashes.

Budget removal is independently guarded and never affects application or
infrastructure resources:

```bash
LEARNINGNEMO_AZURE_DELETE=cost-budget \
  bash infra/next-phase/remove-budget.sh --apply
```

The default invocation is a dry run and verifies the live budget without
printing its recipient.

After budget verification, reviewing the separate WP2a what-if, and reviewing
current Azure pricing, deploy persistent identities independently:

```bash
export AZURE_SUBSCRIPTION_ID="<expected-subscription-id>"

LEARNINGNEMO_AZURE_APPLY=platform-identities \
  bash infra/next-phase/deploy-identities.sh --apply
```

WP2a-I is currently deployed. Re-running this command is an idempotent identity
reconciliation; it cannot create the runtime environment.

Deploy the optional runtime envelope only for a bounded integration window:

```bash
export AZURE_SUBSCRIPTION_ID="<expected-subscription-id>"

LEARNINGNEMO_AZURE_APPLY=platform-runtime-envelope \
  bash infra/next-phase/deploy-platform.sh \
  --parameters infra/next-phase/environments/dev.runtime.parameters.json \
  --ttl-hours 8 \
  --apply
```

Apply records a sanitized manifest and runs `verify_platform.py`. The verifier
checks the exact two WP1 resources, six persistent identities, and one runtime
resource; all identity purpose tags; the exact delegated subnet; peer mTLS; peer traffic
encryption, the external Consumption-only profile, absence of persistent
logging, and the managed load-balancer/public-IP footprint. It never prints
client, principal, tenant, or subscription identifiers.

The environment has `publicNetworkAccess` enabled and `internal: false` so the
first AgentRunner-facing APIs can use the specification's public HTTPS pattern.
No app or ingress endpoint exists in WP2a. The environment's VNet integration
is permanent; a later private-ingress phase may disable public network access
without changing that network type. Do not deploy an application until its
ingress, audience, calling-client, and network controls have their own reviewed
work package.

After separate review, persistent WP2b audiences are applied with:

```bash
LEARNINGNEMO_AZURE_APPLY=trusted-workload-audiences \
  bash infra/next-phase/deploy-entra-workloads.sh --apply
```

The complete reviewed WP3 dependency order can be reconciled with one explicit
acknowledgement. Every infrastructure phase still runs its own Bicep validation,
what-if, apply, recorder, and live verifier:

```bash
export AZURE_SUBSCRIPTION_ID="<expected-subscription-id>"

LEARNINGNEMO_AZURE_APPLY=wp3-reproducible \
  bash infra/next-phase/reconcile-wp3.sh --ttl-hours 8 --apply
```

The command reconciles SQL base, private networking, ACR, Container Apps
runtime, image evidence, Entra audiences, one-shot migrations, and four
SQL-backed workers in dependency order. Re-running it updates bounded
expiration tags but requires all stable ownership, cost, identity, digest, and
security contracts to match. `verify-wp3.sh` independently verifies recorded
live state. Do not create placeholder evidence to bypass a gate.

The incident cycle remains a separate explicit mutation after WP3 verification.
Preview it first, then apply with a command-scoped acknowledgement:

```bash
bash infra/next-phase/deploy-control-cycle.sh --what-if --ttl-hours 1

LEARNINGNEMO_AZURE_APPLY=wp3-control-cycle \
  bash infra/next-phase/deploy-control-cycle.sh --apply --ttl-hours 1
```

The job uses the control identity for SQL and worker tokens and reuses the
diagnostic identity only for image pull; it creates no RBAC assignment, secret,
or ingress. Success requires all eight workflow stages and the final SQL
summary. The wrapper writes `control-cycle-dev.result.json`, deletes the
deployment stack and resource group, and verifies that workers, identities,
and registry grant separation remain unchanged.

## Remove

Preview cleanup:

```bash
bash infra/next-phase/remove-foundation.sh
```

Delete both groups only while they contain foundation-approved resource types:

```bash
export AZURE_SUBSCRIPTION_ID="<expected-subscription-id>"

LEARNINGNEMO_AZURE_DELETE=network-foundation \
  bash infra/next-phase/remove-foundation.sh --apply
```

To remove only an expired disposable SAW foundation while preserving the
stable platform group:

```bash
LEARNINGNEMO_AZURE_DELETE=network-foundation \
  bash infra/next-phase/remove-foundation.sh --expired-only --apply
```

The expiry path fails closed when the `expiresOn` tag is missing, malformed, or
still in the future. Cleanup requires exact WP1 resource names and ownership
tags before deletion. Every deletion first writes a minimal inventory under
the configured Linux state directory; the snapshot excludes Azure resource
IDs, arbitrary tags, and resource properties.

The removal script fails closed after later work packages add databases,
Container Apps, VMs, firewalls, or other resource types. Those phases require
their own scoped teardown procedures.

Remove only the runtime envelope while retaining identities and WP1:

```bash
export AZURE_SUBSCRIPTION_ID="<expected-subscription-id>"

LEARNINGNEMO_AZURE_DELETE=platform-runtime-envelope \
  bash infra/next-phase/remove-platform.sh --apply
```

The default invocation is a dry run. Cleanup verifies exact resource names,
ownership tags, identity-purpose tags, and the absence of later-phase resources
before taking minimal snapshots of both platform groups. It deletes the managed
environment, waits for Azure to delete its managed infrastructure group, and
confirms that the six identities and WP1 network remain. Use `--expired-only`
for unattended eligibility checks. The expiration tag does not schedule its
own deletion; an operator or trusted scheduler must invoke the guarded removal
command with its subscription and deletion acknowledgement.

Remove only the four ephemeral workers before removing the runtime envelope:

```bash
LEARNINGNEMO_AZURE_DELETE=trusted-workloads \
  bash infra/next-phase/remove-workloads.sh --expired-only --apply
```

The command validates full ownership, expiration, environment, and managed
identity bindings before deleting exact app names. It preserves the runtime,
six managed identities, network foundation, persistent Entra audiences, and
sanitized evidence manifest. Entra audiences are intentionally persistent
identity policy and do not have an automatic teardown path in this slice.

An interrupted control-cycle run can be removed independently:

```bash
LEARNINGNEMO_AZURE_DELETE=wp3-control-cycle \
  bash infra/next-phase/remove-control-cycle.sh --apply
```

Cleanup fails closed unless the owning deployment stack exists, then confirms
the disposable group is absent, all four workers and both reused identities
remain, control still has no ACR grant, and diagnostic still has exactly one.

Persistent identities have their own removal path and cannot be removed while
the runtime environment exists:

```bash
LEARNINGNEMO_AZURE_DELETE=platform-identities \
  bash infra/next-phase/remove-identities.sh --apply
```

## Gate Before The Next Work Package

Do not start WP2 until:

- the default what-if shows only the expected seven creates;
- CIDR overlap checks pass;
- the two resource groups are clearly tagged;
- the SAW group has a tested expiration/cleanup process; and
- the POC versus reference-perimeter limitation is accepted.

Before WP2a-R apply, additionally require:

- WP2a-I live verification proves all six identities;
- the runtime what-if shows exactly one managed-environment create and eight
  existing resources ignored;
- there are no modify or delete operations;
- an active subscription budget has at least one enabled notification;
- that budget amount is no greater than the checked-in `50` development
  ceiling;
- the external-ingress security boundary is accepted; and
- managed load-balancer/public-IP pricing and cleanup ownership are reviewed.

Before any WP2b-R apply, additionally require all local WP2b gates to pass, a
real digest-pinned image plus rehashed SBOM/scan/signature evidence, four live
credentialless audiences, a workload expiration no later than the runtime
expiration, and an implemented Azure SQL `ApprovalAuthority` with a reviewed
schema hash. ACR, Key Vault, Log Analytics, SQL infrastructure, Azure Firewall,
and the SAW VM remain separate, explicit security and cost gates.