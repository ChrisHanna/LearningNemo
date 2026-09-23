# LearningNeMo

A NVIDIA NeMo Agent Toolkit task agent with hybrid input guardrails, Microsoft
Entra authorization, per-tool scope and app-role checks, and a Key Vault-backed
Azure API Management LLM gateway.

For a reviewer-oriented reading order and package boundaries, start with the
[`src/task_agent` source tour](src/task_agent/README.md).

For setup, deployment gates, evidence, and cleanup, use the
[build and reproduction guide](docs/build-and-reproduce.md). For an eight-minute
interview walkthrough with expected outcomes and honest capability boundaries,
use the [capability demonstration runbook](docs/capability-demo.md).

The [cloud demo guide](docs/cloud-demo.md) covers the Azure-hosted dashboard,
private controller and NeMo API, managed identities, deployment verification,
and scheduled cleanup. Cloud hosting and the full sandbox incident integration
are separate acceptance results.

The proposed Azure SQL, Secure Agent Workspace, OpenShell, identity, approval,
and incident-response phase is defined in the
[`next-phase SAW and OpenShell specification`](docs/next-phase-saw-openshell-spec.md).
The [next-phase infrastructure](infra/next-phase/README.md) implements and has
live-verified WP1 networking, six persistent WP2a identities, the
VNet-integrated runtime, four credentialless WP2b audiences and workers, and a
private Azure SQL control plane. Its reproducible incident cycle diagnoses and
contains an owned unsafe query, persists a separate approval, performs bounded
remediation, verifies recovery, records source-bound evidence, and removes its
one-shot control compute. Runtime resources remain TTL-bound. On 2026-09-14,
OpenShell bootstrap and the three sandbox policy probes succeeded on a private
SAW VM. On 2026-09-15 the Azure runtime-lock rule was repaired and independent
host verification passed. A new live Planning probe returned UID 998 and denied
privilege escalation, but API requests timed out after NAT removal. Protected
route connectivity, clean-host reproducibility, and the complete incident cycle
through OpenShell remain unfinished.

The whole agent is defined in `configs/agent.yml`:

1. Azure API Management routes model requests and authenticates internal callers.
2. Azure Key Vault stores the OpenAI and internal gateway keys.
3. Microsoft Entra validates the caller and supplies delegated scopes plus assigned app roles.
4. NeMo Guardrails masks PII locally, then runs a semantic self-check through a
	dedicated APIM operation before the agent runs.
5. Every tool enforces both its required scope and user role before executing.

## Permissions

| Capability | Required scope | Required app role |
| --- | --- | --- |
| Invoke the agent and read the current time | `agent.invoke` | `Task.Reader` |
| List tasks | `tasks.read` | `Task.Reader` |
| Execute a task or reset demo state | `tasks.execute` | `Task.Operator` |

A [dedicated Approver account](docs/approver-account.md) now has only
`Task.Approver`. It can sign into the common Demo session view without Reader or
Operator permissions. The deployed private review service supports exact-plan
decisions after explicit `plans.review` authorization. It cannot execute tasks
or operate the workspace. See the [live checkpoint](docs/human-handoff-live-checkpoint.md)
for the scheduled window and outstanding full-incident rehearsal.

### One Client, Two Test Accounts

Both users authenticate through one public client. The client receives the
same delegated permissions for either user; API app-role assignments determine
what each person may do:

```text
LearningNeMo Local Client
|-- Reader account   -> roles: Task.Reader
`-- Operator account -> roles: Task.Reader, Task.Operator

Both tokens -> scp: agent.invoke tasks.read tasks.execute
```

The API requires both claims. For example, `execute_task` requires
`tasks.execute` in `scp` **and** `Task.Operator` in `roles`. LearningNeMo uses a
salted, process-local fingerprint to identify the current session without
sending the underlying account ID or token to the browser. Direct user
assignments keep the demo understandable. Production deployments often assign
the same roles through governed Entra groups and Conditional Access.

The public test client uses device-code authentication and never uses a client
secret. The API validates token signature, issuer, audience, lifetime, scope,
and role through Microsoft Entra JWKS plus the verified token payload.

## Local Setup

Open Ubuntu and install the locked dependencies into the Linux environment:

```bash
cd /mnt/c/Users/aygul/Desktop/agents
UV_PROJECT_ENVIRONMENT=/home/aygul/.venvs/nemo-agents ~/.local/bin/uv sync
```

The locked environment includes Presidio and spaCy for local PII masking. It
does not install GPT-2, Transformers, PyTorch, CUDA, or a separate guardrail
model server.

## Configure Entra

Azure CLI must be signed in to the target tenant. The script is idempotent and
preserves existing scopes and Microsoft Graph permissions.

```bash
export ENTRA_TENANT_ID="<tenant-id>"
export ENTRA_CLIENT_ID="<agent-api-client-id>"
export ENTRA_PUBLIC_CLIENT_ID="<learningnemo-client-id>"

bash infra/configure-entra.sh
bash infra/configure-entra.sh --apply
```

It exposes `agent.invoke`, `tasks.read`, and `tasks.execute`, defines
`Task.Reader` and `Task.Operator`, and configures one public client. After
`--apply`, the script writes these non-secret IDs to the gitignored
`.nemo-test-client.json` file used by the test client.

After creating two tenant users, assign their API roles:

```bash
export ENTRA_READER_USER="reader-test@contoso.onmicrosoft.com"
export ENTRA_OPERATOR_USER="operator-test@contoso.onmicrosoft.com"

bash infra/assign-entra-test-users.sh
bash infra/assign-entra-test-users.sh --apply
```

The assignment script fails if both identifiers resolve to the same user. It
assigns `Task.Reader` to the Reader account, both roles to the Operator account,
and then enables assignment-required on the API service principal.

## Deploy The LLM Gateway

The gateway uses the APIM Consumption tier. APIM's managed identity reads the
real OpenAI key from Key Vault. The agent receives a separate generated gateway
credential and cannot read the OpenAI key.

```bash
bash infra/test-gateway-iac.sh
bash infra/deploy-gateway.sh --what-if
```

The deployment wrapper defaults to ARM validation and identifier-safe what-if.
It reuses the existing APIM and OpenAI backend, adding a distinct
`/guardrails/chat/completions` operation rather than another Azure service.
That operation inherits gateway authentication, forces `gpt-4o-mini`,
temperature `0`, a three-token response, and non-streaming mode, and removes
tool/function fields before forwarding.

After reviewing what-if, apply through Bicep with a command-scoped subscription
and acknowledgement:

```bash
export AZURE_SUBSCRIPTION_ID="<expected-subscription-id>"
LEARNINGNEMO_AZURE_APPLY=llm-gateway \
	bash infra/deploy-gateway.sh --apply
```

On first deployment only, the script prompts for the OpenAI key without echoing
it. Automation can pass `--openai-api-key-file` with an owner-only mode `0600`
file outside the repository. Apply stores provider and internal gateway keys in
Key Vault, deploys APIM declaratively, and smoke-tests both operations. Load the
resulting endpoints and internal client credential into the current shell:

```bash
source scripts/load-gateway-env.sh
```

## Run The API

```bash
bash scripts/run-agent.sh
```

The launcher reads the non-secret Entra IDs from `.nemo-test-client.json`,
loads only the internal APIM credential from Key Vault, disables telemetry,
and binds the local API to `127.0.0.1:8001`. The workflow fails at startup when
gateway configuration is absent, preventing an accidental direct OpenAI call.

## Create A Review Archive

Do not ZIP the working directory: it may contain virtual environments, caches,
or local settings. Create an allowlisted, secret-scanned archive instead:

```powershell
.\scripts\create-review-archive.ps1
```

The archive includes only source, tests, infrastructure, configuration,
documentation, the lockfile, and vendored licenses. It excludes `.env`, local
Entra settings, virtual environments, caches, and build output.

## Test Authorization

### Visual Console

With the agent API running, launch the local browser workspace:

```bash
~/.venvs/nemo-agents/bin/learningnemo
```

It opens `http://127.0.0.1:8765` and shows:

- a default Pattern view with a five-boundary authority flow, SAW/OpenShell
	containment, and target persona permissions; diagram selections explain the
	design and do not change the signed-in identity or execute actions;
- a Live proof view with explicit configuration, timestamped cloud checks,
	and an identity-to-Planning proof action; cloud transport is disabled unless
	started with `--workspace-subscription <expected-subscription-id>`;
- a Recorded evidence view with an interactive SAW/OpenShell topology, selectable
	Planning/Execution/Probe policies, expected-versus-observed route results,
	and exportable dated evidence summaries;
- a Build & Decisions view with design rationale, tradeoffs, release contracts,
	and an in-app reader for the build guide, presenter runbook, and diagnostic record;
- one Demo session for all accounts: Reader denial test, Operator mutation
	test, or Approver service availability, with technical tools in expandable sections;
- shared account controls with real sign-in switching, next-action status,
	read-only step previews, and separate running, verified, denied, and failed states;
- live health and the secured request path from Entra through APIM;
- a System view with active controls, hybrid Guardrails, scoped tools, and the
	complete model/tool execution loop;
- the current account's granted scopes, assigned roles, and detected persona;
- a role-derived Reader or Operator walkthrough with exact access decisions;
- Guardrail probe prompts for semantic injection detection and local PII masking;
- a manual prompt workspace that follows the current user, request activity, and an
	evidence panel that distinguishes observed behavior from configured policy.

Tokens remain in memory in the localhost console process and are never sent to
the browser. The browser receives only role status, scope names, the temporary
device code, timings, and agent responses.

The Recorded evidence tab is a curated engineering summary dated 2026-09-14, not a live
Azure monitor or imported raw trace. It explicitly reports current cloud health
as unknown, preserves the recorded runtime-lock failure, and keeps the separate
incident cycle and pending sandbox integration distinct. Selecting a sandbox or
story step never starts a cloud operation. The local agent health indicator is
independent of those historical results.

Each browser receives an isolated server-side session through an opaque,
HttpOnly, SameSite cookie. Unsafe console requests require both an exact
loopback Origin and a per-session CSRF token. The console serves all JavaScript
locally, uses system fonts, and runs under a restrictive Content Security
Policy.

The walkthrough requires one sign-in. LearningNeMo derives the persona from the
verified `roles` claim:

- A `Task.Reader` account gets three steps: capture state, attempt a write, and
	prove the denied write made no state change.
- A `Task.Operator` account gets four steps: reset state, confirm pending,
	execute a task, and verify completion.

Walkthrough step IDs are resolved on the localhost server. The browser cannot
choose a role or prompt. The server rejects a step belonging to another persona
and returns the evaluated required, granted, and missing scopes and roles.

### Command-Line Checks

The CLI remains useful for scripting and CI. The guided scenario reads the
non-secret Entra IDs created during configuration, opens the Microsoft sign-in
page, copies the device code to the Windows clipboard, and uses one token for
the role-derived path:

```bash
~/.venvs/nemo-agents/bin/python scripts/test_client.py \
	--access reader \
	--scenario authorization
```

Use `--access operator` with the Operator test account to run its mutation path.
Each invocation requires only the account being demonstrated.

For exploratory testing, omit `--prompt` to keep one authenticated session
open. Enter `/quit` to exit:

```bash
~/.venvs/nemo-agents/bin/python scripts/test_client.py --access reader
```

One-shot calls remain available for scripting.

Reader access can list tasks but cannot execute them:

```bash
~/.venvs/nemo-agents/bin/python scripts/test_client.py \
	--access reader \
	--prompt "List the pending tasks."
```

Operator access can execute a task:

```bash
~/.venvs/nemo-agents/bin/python scripts/test_client.py \
	--access operator \
	--prompt "Execute task-1."
```

The test client prints a Microsoft device-login URL and code. It never prints
or persists the access token. Pass `--no-browser` when automatic browser and
clipboard integration is not wanted.

## Test Locally

Run authorization tests and validate the complete workflow without calling the
model:

```bash
~/.venvs/nemo-agents/bin/pytest -q
~/.venvs/nemo-agents/bin/python scripts/validate-agent-config.py
bash infra/test-all-local.sh
```

The solution-level infrastructure gate compiles and policy-checks the APIM,
network, budget, identity, Container Apps, Entra audience, and worker templates.
It makes no Azure calls. `nat validate` is interactive in the pinned toolkit
version, so the checked-in Python validator is the automation-safe config gate.

## Current Limits

- The task store is in memory and is for authorization testing only.
- APIM currently has one OpenAI backend; additional model backends and failover
	can be added to turn it into a multi-provider router.
- The APIM Consumption tier does not provide per-caller rate limiting in this
	demo.
- Scope-based tool hiding is not implemented. Every tool still enforces its
	scope and app role at execution time, which is the authoritative boundary.
- Access tokens older than 15 minutes are rejected to bound role-revocation
	latency. Continuous Access Evaluation is not implemented for this custom API.
- Authorization decisions are emitted as structured pseudonymous logs. A
	production deployment must route them to a durable, access-controlled audit
	sink.
- Raw NeMo Agent Toolkit workflow failures use HTTP `422`. LearningNeMo disables
	model narration of tool errors and maps expected policy denials to structured
	HTTP `403` responses.
- Replace WSL with a Linux container deployment before production.

## Credential Rotation

LearningNeMo no longer uses an application client secret, and local `.env`
files are not part of the runtime. If an OpenAI provider key has ever been
shared outside Key Vault, rotate it in the OpenAI dashboard and rerun
`infra/deploy-gateway.sh`; the repository cannot create or revoke provider keys.

PII masking uses Presidio and spaCy locally for `PERSON`, `EMAIL_ADDRESS`, and
`PHONE_NUMBER`. A strict NAT middleware sends only the sanitized user text to
the dedicated APIM operation. It accepts exactly `No` as safe, blocks on `Yes`,
and stops the workflow on unrecognized verdicts or transport failures.
Guardrails remain defense in depth; Entra, tool schemas, app roles, approval,
and runtime containment remain authoritative.