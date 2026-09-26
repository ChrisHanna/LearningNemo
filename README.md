# LearningNeMo — OpenShell Sandboxes and MicroVM Isolation

LearningNeMo explores how to run NVIDIA NeMo agents inside constrained,
disposable environments using **NVIDIA OpenShell**, **per-sandbox MicroVM
isolation**, and an Azure-hosted **Secure Agent Workspace (SAW)**. NeMo
supplies the agent framework; OpenShell and the workspace architecture define
where agent code runs and what it is permitted to do. The dashboard, Entra
authorization exercises, and trusted SQL services exist to support that
sandbox demonstration, not the other way around.

The central question is not only "will the model follow instructions?" but
"what can the agent actually reach or change — even when it does not follow
instructions?"

## Why OpenShell and MicroVMs Matter

In NVIDIA's
[Secure Agent Workspace reference design](https://docs.nvidia.com/enterprise-reference-architectures/secure-agent-workspace-reference-design/latest/what-is-secure-agent-workspace.html),
a SAW is not a single VM or product. It is the managed envelope around a
single-user workspace VM: approved provisioning and lifecycle, brokered access,
a runtime enforcement layer (OpenShell is NVIDIA's reference implementation),
governed connectors with human review for sensitive writes, and audit.
Containment nests from the inside out:

```text
agent loop -> OpenShell sandbox -> single-user workspace VM -> SAW envelope
```

NVIDIA groups SAW controls into three layers: baseline managed-workspace
controls (the perimeter), runtime sandbox controls, and signed-policy
governance (including a signed per-engagement delegation record). This
repository implements parts of the first two layers; see
[How this repository maps to the SAW reference design](#how-this-repository-maps-to-the-saw-reference-design).
See [ADR 0001](docs/decisions/0001-openshell-microvm-driver.md) for the runtime
rationale and its recorded constraints.

- **The workspace VM.** A private, no-public-IP Azure VM is the dedicated
  workspace VM inside the SAW envelope. Here it hosts the website's agent
  workload rather than a person's desktop (see
  [Who the workspace is for](#who-the-workspace-is-for)). It has its own bounded
  lease/lifecycle, owned by the trusted control plane and independent of any
  sandbox running inside it. The VM alone is not the SAW; the lifecycle
  control plane, network perimeter, trusted services, and audit around it are
  part of the envelope.
- **OpenShell policy boundaries.** Planning, Execution, and Probe each run
  under a distinct OpenShell policy that constrains their permitted
  operations and network routes. Filesystem, Landlock, and process rules are
  fixed when a sandbox is created; network rules can be changed on a running
  sandbox, but only by the operator through the mTLS-authenticated gateway,
  which sandboxes cannot reach. The invoice agent's policies require Landlock
  (`hard_requirement`) and allow only the agent's Python interpreter to reach
  its exact, enforced gateway routes. Planning and Execution are
  deliberately kept separate so that a planning step cannot itself execute a
  mutation.
- **Per-sandbox MicroVMs.** This repository selects OpenShell's bundled
  KVM-backed MicroVM driver with a fixed 1-vCPU/1-GiB allocation per sandbox,
  giving each sandbox its own virtual-machine boundary inside the workspace VM
  rather than relying on container isolation alone. NVIDIA's reference design
  requires kernel-level runtime sandboxing, not MicroVMs specifically; the
  MicroVM layer is an addition chosen here. This choice depends on
  Azure nested virtualization and `/dev/kvm`; it is a decision made for this
  repository's proof of concept, not a universal claim about how every
  OpenShell deployment must be configured.
- **Trusted services authorize mutations, not the sandbox.** A sandbox, a
  model response, or mere possession of a running MicroVM does not itself
  authorize a protected change. Approval and database authority stay with
  separately identified, trusted services; Probe sandboxes exist to test
  policy boundaries, not to perform authorized work.

This is a single-user runtime portfolio proof of concept, not a hardened,
multi-tenant production SAW fleet. See the architecture and evidence links
below for what is designed, what is implemented, what is configured policy,
what has been probed and recorded on a given date, and what remains
unverified end-to-end.

### How this repository maps to the SAW reference design

| SAW reference-design element | This repository | Status |
| --- | --- | --- |
| Single-user workspace VM, approved image, managed lifecycle | Dedicated private Trusted Launch Azure VM for the website agent, pinned Ubuntu image, control-plane-owned start/stop. The invoice deployment runs it in `operator-managed` availability mode (no VM lease timer); per-run watchdogs and capabilities still expire | Implemented; see dated evidence |
| Runtime sandbox layer (OpenShell) | Planning, Execution, and Probe sandboxes with distinct policies (static sections fixed at creation, network rules operator-controlled), each in its own MicroVM | Implemented; policy/boundary probes recorded |
| Separate sponsor, workspace, logical-agent, and runtime-agent identities | Entra human roles, AgentRunner logical identity, per-sandbox runtime capability | Designed; AgentRunner delegation integration partially pending |
| Governed connectors with human review for sensitive writes | Capability-authenticated invoice tool gateways, SQL broker enforcing the exact approved plan, independent verifier, separate Approver | Implemented; sandbox Planning -> approval -> sandbox Execution -> verification recorded on 2026-09-16 with a synthetic reviewer, and the Approver UI validated on 2026-09-19 ([invoice demo](docs/invoice-demo.md)) |
| Brokered interactive access (enterprise SSO) | Not yet implemented | Gap |
| Runtime credential mediation | Model credential held by a trusted gateway; general OpenShell credential mediation not yet complete | Partial |
| Workspace perimeter | NSGs plus OpenShell egress policy | Lower-cost substitute; not the reference design's Azure Firewall Premium perimeter |
| Signed-policy governance and signed delegation record | The trusted-worker image is signed and attested; other images are digest-pinned only; sandbox policies and engagement delegations are not signed | Gap |
| Kill switch outside the agent's control | Control-plane VM stop, per-run capability revocation, sandbox stop, and a root-owned admission gate (`/etc/learningnemo/invoice-availability.json`) | Partial; revocation is not yet bound to a signed delegation |

Treat the "Gap" and "Partial" rows as reasons this repository claims a
single-user OpenShell runtime POC rather than a complete SAW.

### Who the workspace is for

NVIDIA's reference design describes a SAW as a *single-user* workspace. This
repository applies that to a **website-driven agent**, not to a person working
inside the VM:

- The website (dashboard, sign-in, approval, and trusted APIs) runs outside
  the workspace in Azure Container Apps. Per the
  [specification](docs/next-phase-saw-openshell-spec.md#62-do-not-use-saw-as-a-service-hosting-platform),
  the SAW never hosts the website itself.
- The workspace VM hosts only the agent runtime: the OpenShell gateway and the
  Planning, Execution, and Probe sandboxes the website asks it to run.
- "Single-user" is read as **one trust domain with one accountable owner**:
  the operator who owns the engagement and its lease. Website users never
  receive a shell, credentials, or network access to the VM; they reach the
  agent only through authenticated website actions.
- Because several website users can trigger runs in the same workspace VM,
  isolation *between their runs* comes from separate, short-lived OpenShell
  sandboxes and per-run capabilities, not from the VM. The invoice deployment
  admits one active sandbox at a time and retains up to 24 stopped sandboxes
  for evidence. Anything that requires
  hard isolation between users or tenants needs a separate workspace VM
  (see the specification's cross-user isolation claim level).

### NeMo Guardrails coverage

[NeMo Guardrails](https://docs.nvidia.com/nemo/guardrails/latest/about-nemo-guardrails-library/rail-types)
defines five rail types. Guardrails screen content; they are not an
authorization boundary. Entra roles, OpenShell policy, and the SQL broker
enforce what an agent can actually do.

| Rail type | Invoice agent (website agent) | Task agent (local exercise) |
| --- | --- | --- |
| Input | `self check input` on user messages, tool results, and the approved plan's free text | Presidio PII masking, then a custom APIM semantic classifier |
| Output | Regex secret detection, then `self check output` on the model's text and on `publish_decision` diagnosis, rationale, and risks | Regex secret detection and Presidio PII masking on the final response |
| Execution: tool calls (`tool_output`) | One call per response, only the role's tools, arguments valid against the `InvoiceStep` / `PlanningDecision` contracts, step targets bound to the run's scenario, no secrets | Tool arguments checked for shell and SQL metacharacters before the tool runs |
| Execution: tool results (`tool_input`) | Only results from the role's tools, bounded JSON, no secrets | Regex secret detection and Presidio PII masking before the model sees the result |
| Dialog, retrieval | Not used | Not used |

The invoice rails run in the trusted invoice gateway, outside the sandbox. The
gateway buffers each model response and returns nothing until the output and
execution rails pass, so a blocked response never reaches the agent; blocks
are recorded as `tool-result-guardrail-denied` or `output-guardrail-denied`
gateway events. `self check output` is an extra model call on responses that
contain text. Contract validation, the exact-plan broker check, and SQL
verification remain the authoritative controls. See
[`control/invoice_rails.py`](src/task_agent/control/invoice_rails.py) and
[`configs/agent.yml`](configs/agent.yml).

## Architecture and Sandbox Documentation

- [SAW and OpenShell specification](docs/next-phase-saw-openshell-spec.md) —
  design intent and phased acceptance criteria for the full workspace.
- [ADR 0001: OpenShell MicroVM driver](docs/decisions/0001-openshell-microvm-driver.md) —
  why this repository selects OpenShell's bundled MicroVM driver, KVM, and
  fixed per-sandbox sizing.
- [Build and reproduce the workspace](docs/build-and-reproduce.md) — operator
  build guide for the SAW/OpenShell infrastructure.
- [Capability demonstration runbook](docs/capability-demo.md) — the
  sandbox-focused demonstration story and its evidence.
- [OpenShell bootstrap diagnostic record](docs/openshell-bootstrap-diagnostic-record.md) —
  a dated, recorded live diagnosis of the OpenShell bootstrap, not a live
  health status.
- [Governed invoice incident demo](docs/invoice-demo.md) — the current,
  dated workflow guide for the Planning/Execution agents that run inside
  separate OpenShell MicroVMs, with its own explicit evidence boundaries.

**Evidence boundary:** design intent, an implemented control, a configured
policy, a dated recorded probe, present live health, and a fully accepted
human/agent end-to-end run are different claims. A recorded probe from an
earlier date is not current live health, and a running dashboard is not proof
that the sandboxed workflow itself succeeded. Consult the linked documents for
their own dated evidence and acceptance criteria rather than assuming this
README's summary is the latest status.

## Supporting Exercise: Local Task Authorization

The walkthrough below is a **local, offline-friendly authorization exercise**
that predates and supports the sandbox work above — it is not the OpenShell
sandbox demonstration itself. It lets two Microsoft Entra users try the same
task agent with different permissions: a Reader can inspect tasks, while an
Operator can execute them. Use it to validate Entra roles and the local
agent/console before moving on to the sandbox architecture and cloud demo.

> **Choose the path that matches your goal**
>
> - **Offline/local validation** installs the locked dependencies and checks
>   configuration and infrastructure templates. It makes no model calls or Azure
>   requests.
> - **The local agent and console** run on your machine, but they are *not*
>   offline: sign-in needs Entra, and agent/guardrail model requests need the
>   configured APIM gateway and model-provider access.
> - **Cloud, SAW, and OpenShell work** is where the sandbox architecture
>   actually runs. See the [cloud demo guide](docs/cloud-demo.md) and
>   [build and reproduction guide](docs/build-and-reproduce.md). It requires
>   Azure resources and is not needed to complete the local authorization
>   exercise below.

## Before you start

Run the commands from the repository root in Linux or Ubuntu WSL2 (the recorded
environment is Ubuntu 24.04 WSL2). Use a Linux Python environment, not a
Windows virtual environment.

| For | You need |
| --- | --- |
| Local checks | Bash 4.4+, Python 3.11–3.13, and `uv` 0.8+ |
| Infrastructure checks | Azure CLI 2.76+, Bicep 0.35+, and the local-check tools above |
| Live demo | An Azure tenant and subscription, Azure CLI signed into the intended tenant/subscription, rights to administer the existing Entra applications and role assignments, access to deploy/use the APIM and Key Vault resources, model-provider access, and two distinct Entra test accounts |

The tested tool versions are recorded in
[`infra/next-phase/toolchain.json`](infra/next-phase/toolchain.json). Cloud
deployment also depends on subscription policy, capacity, provider registration,
and the relevant Azure/Entra permissions; this repository does not create a
tenant or subscription for you.

## 1. Clone and install the locked environment

```bash
git clone https://github.com/ChrisHanna/LearningNemo.git
cd LearningNemo

export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/nemo-agents"
"$HOME/.local/bin/uv" sync --frozen
```

`uv sync --frozen` installs the versions in `uv.lock`, including the local PII
masking dependencies. The `$HOME` path is portable and avoids putting a virtual
environment in the repository. If your `uv` executable is elsewhere, use that
path instead.

For a safe first check, run:

```bash
"$UV_PROJECT_ENVIRONMENT/bin/python" scripts/validate-agent-config.py
```

Expected result: two `PASS` lines confirming that the configuration loads with
placeholder values. This does not authenticate, contact Azure, or call a model.

<a id="configure-entra"></a>

## 2. Configure Azure and Entra (one time)

The following setup creates or reconciles application configuration and test-user
role assignments. It is required before the live demo, but not for the offline
check above.

Sign in and deliberately verify the target subscription:

```bash
az login
export AZURE_SUBSCRIPTION_ID="<expected-subscription-id>"
test "$(az account show --query id --output tsv)" = "$AZURE_SUBSCRIPTION_ID"

export ENTRA_TENANT_ID="<tenant-id>"
export ENTRA_CLIENT_ID="<agent-api-application-client-id>"
export ENTRA_PUBLIC_CLIENT_ID="<learningnemo-public-client-id>"
```

Replace each `<...>` value with an ID from the intended tenant. The Entra scripts
also reject a signed-in Azure CLI tenant that does not match `ENTRA_TENANT_ID`.

First preview the Entra changes, then explicitly apply them:

```bash
bash infra/configure-entra.sh
bash infra/configure-entra.sh --apply
```

The preview reports the planned scopes and roles. Applying configures
`agent.invoke`, `tasks.read`, and `tasks.execute`; `Task.Reader` and
`Task.Operator`; and the public client. It writes the non-secret IDs to the
gitignored `.nemo-test-client.json` file used by the console and test client.

Create or identify **two different** tenant users, then assign the demo roles:

```bash
export ENTRA_READER_USER="reader-test@contoso.onmicrosoft.com"
export ENTRA_OPERATOR_USER="operator-test@contoso.onmicrosoft.com"

bash infra/assign-entra-test-users.sh
bash infra/assign-entra-test-users.sh --apply
```

The Reader receives `Task.Reader`; the Operator receives both `Task.Reader` and
`Task.Operator`. The script refuses to use the same user for both accounts and,
when applied, makes assignment required on the API service principal.

<a id="deploy-the-llm-gateway"></a>

## 3. Preview and deploy the LLM gateway (one time)

The local agent deliberately fails closed without gateway settings: it does not
fall back to a direct provider call. The gateway uses APIM and Key Vault; APIM
uses a provider key while the agent gets only a separate internal gateway
credential.

Run local gateway-template checks, then review the non-mutating deployment
preview:

```bash
export LEARNINGNEMO_PYTHON="$UV_PROJECT_ENVIRONMENT/bin/python"
bash infra/test-gateway-iac.sh
bash infra/deploy-gateway.sh --what-if
```

`--what-if` validates and previews Azure changes, but still queries the selected
Azure environment. It may report that a gateway preview is deferred until the
platform resources exist.

Applying can create billable APIM/Key Vault resources and may prompt, without
echoing, for the provider API key on the first deployment. Review the preview
first. To apply, retain the verified subscription value and use the explicit
command-scoped acknowledgement:

```bash
LEARNINGNEMO_AZURE_APPLY=llm-gateway \
  bash infra/deploy-gateway.sh --apply
```

The script checks that the active subscription equals
`AZURE_SUBSCRIPTION_ID`, deploys the gateway, and smoke-tests both APIM
operations. Do not put provider keys, tokens, or credentials in the repository.
For unattended first setup, its `--openai-api-key-file` option requires an
owner-only (`0600`) file outside the repository.

## 4. Start the local API and console

These are separate terminals. Both commands below start from the repository
root. In a fresh terminal, restore the environment variables needed by the
launchers.

**Terminal 1 — API**

```bash
cd /path/to/LearningNemo
export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/nemo-agents"
export NAT_BIN="$UV_PROJECT_ENVIRONMENT/bin/nat"
bash scripts/run-agent.sh
```

The launcher reads `.nemo-test-client.json`, loads the internal gateway
credential with Azure CLI/Key Vault, disables telemetry, and starts the API on
`http://127.0.0.1:8001`. A missing settings file, Entra ID, or gateway
configuration is an intentional startup failure.

**Terminal 2 — console**

```bash
cd /path/to/LearningNemo
export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/nemo-agents"
"$UV_PROJECT_ENVIRONMENT/bin/learningnemo"
```

Open `http://127.0.0.1:8765`. Both listeners are loopback-only; do not expose
them publicly. The console uses Entra device-code sign-in and keeps tokens in
its local server-side process, not in the browser.

### Returning-user startup

After the one-time Entra and gateway setup, repeat only the two terminal
commands above. You may source the gateway script in a shell to inspect whether
Azure access still works, but it is not needed before `run-agent.sh`:

```bash
source scripts/load-gateway-env.sh
```

Sourcing matters: running that script directly intentionally exits because its
environment variables would not persist.

## 5. Try the authorization demo

In the console, sign in first as the **Reader** account:

1. List the pending tasks.
2. Attempt the offered write/mutation step.
3. Confirm the task state did not change after the expected denial.

Then use **Switch account**, sign in as the **Operator**, and follow its
walkthrough to reset the disposable state, execute `task-1`, and read back the
completed state. The role comes from the verified Entra token; selecting a
persona in the interface or passing a CLI flag cannot grant a role.

For the same guided paths from a terminal, with the API running:

```bash
"$UV_PROJECT_ENVIRONMENT/bin/python" scripts/test_client.py \
  --access reader --scenario authorization

"$UV_PROJECT_ENVIRONMENT/bin/python" scripts/test_client.py \
  --access operator --scenario authorization
```

Sign in with the matching account when prompted. The client validates the
expected role and uses device-code authentication. For a manual one-shot check:

```bash
"$UV_PROJECT_ENVIRONMENT/bin/python" scripts/test_client.py \
  --access reader --prompt "List the pending tasks."
```

A Reader mutation returning HTTP 403 is the expected security result, not a
failure to bypass. The optional Approver path is separate; see
[the Approver account guide](docs/approver-account.md).

## Validate without deploying

Run these from the repository root with `UV_PROJECT_ENVIRONMENT` exported:

```bash
"$UV_PROJECT_ENVIRONMENT/bin/python" -m pytest -q
"$UV_PROJECT_ENVIRONMENT/bin/python" scripts/validate-agent-config.py
bash infra/test-all-local.sh
```

- `pytest` and `validate-agent-config.py` are local checks; the latter uses
  placeholder credentials and makes no network calls.
- `infra/test-all-local.sh` compiles and policy-checks infrastructure templates
  without querying or changing Azure state.
- The live console/client exercises Entra, APIM/Key Vault, and model calls.
- `infra/deploy-gateway.sh --what-if` makes read-only Azure validation/preview
  requests; `--apply` is mutating and has the explicit acknowledgement above.

### Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every pull
request and on pushes to `main`: the agent configuration check, the full
`pytest` suite, the local infrastructure gates (`infra/test-all-local.sh`,
with the Azure CLI and the Bicep version from `toolchain.json`), and the
browser JavaScript tests. It uses no Azure credentials and does not query or
change Azure state. Deployment remains a manual, acknowledged operator step.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Missing `.nemo-test-client.json` or an Entra ID | Complete `configure-entra.sh --apply`; it writes the non-secret settings file. |
| “signed in to a different tenant” or subscription mismatch | Run `az login`, export the intended IDs, and compare `az account show` to them before applying. |
| `nat`, `pytest`, or `learningnemo` is not found | Re-export `UV_PROJECT_ENVIRONMENT`; set `NAT_BIN="$UV_PROJECT_ENVIRONMENT/bin/nat"` for the API; rerun `uv sync --frozen` if needed. |
| Gateway settings or Key Vault lookup fail | Check Azure CLI access to the intended resources and complete/review the gateway setup. Do not replace the fail-closed gateway with a direct provider key. |
| Reader gets HTTP 403 for a mutation | This is expected. Verify the task state remains unchanged, then sign in with the separately assigned Operator account for execution. |

## Stop, cleanup, and architecture notes

Use `Ctrl-C` in each terminal to stop the local API and console. This does
**not** delete or deallocate cloud resources and does not stop billing. Before
any cloud cleanup, use the scoped preview/apply procedures in the
[build and reproduction guide](docs/build-and-reproduce.md#5-evidence-failure-handling-and-cleanup)
and the [cloud demo guide](docs/cloud-demo.md).

| Capability | Required delegated scope | Required app role |
| --- | --- | --- |
| Invoke agent / read time | `agent.invoke` | `Task.Reader` |
| List tasks | `tasks.read` | `Task.Reader` |
| Execute or reset demo tasks | `tasks.execute` | `Task.Operator` |

Every tool requires both its scope **and** role. The public client has no client
secret. Local PII masking runs before the APIM semantic guardrail; unrecognized
guardrail verdicts and transport failures stop the workflow. Each tool also
passes through execution rails after its authorization check, and the final
response passes through output rails (see
[NeMo Guardrails coverage](#nemo-guardrails-coverage)). Task state is
in-memory and intended only for this authorization demonstration.

The source and deeper operational material remain available:

- [Source tour](src/task_agent/README.md)
- [Build and reproduction guide](docs/build-and-reproduce.md)
- [Capability demonstration runbook](docs/capability-demo.md)
- [Cloud demo guide](docs/cloud-demo.md)
- [Governed invoice incident demo](docs/invoice-demo.md)
- [Approver account guide](docs/approver-account.md)
- [Human handoff checkpoint](docs/human-handoff-live-checkpoint.md)
- [Next-phase SAW/OpenShell specification](docs/next-phase-saw-openshell-spec.md)
- [SAW/OpenShell diagnostic record](docs/openshell-bootstrap-diagnostic-record.md)

Historical SAW/OpenShell evidence is not current cloud health, and the status
of one workflow does not carry over to another. The bootstrap diagnostic
record's earlier connectivity findings do not automatically apply to later,
separately dated work such as the invoice demo above, and a later dated record
does not retroactively resolve a different, still-open gap elsewhere. Each
linked guide states its own dated evidence boundaries; check the specific guide
for the workflow you care about rather than assuming one status applies to all
of them.
