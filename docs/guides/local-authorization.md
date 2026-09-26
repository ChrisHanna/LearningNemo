# Local Task Authorization Walkthrough

This walkthrough runs the **task agent**, a small NeMo Agent Toolkit workflow,
on your machine and lets two Microsoft Entra users try it with different
permissions: a Reader can inspect tasks, while an Operator can execute them. It
exercises Entra roles and scopes, the NAT security middleware, and the NeMo
Guardrails rails around the agent. It is not the OpenShell sandbox
demonstration; for that, see the [invoice demo](invoice-demo.md) and the
[build guide](build-and-reproduce.md).

> **Choose the path that matches your goal**
>
> - **Offline/local validation** installs the locked dependencies and checks
>   configuration and infrastructure templates. It makes no model calls or Azure
>   requests.
> - **The local agent and console** run on your machine, but they are *not*
>   offline: sign-in needs Entra, and agent/guardrail model requests need the
>   configured APIM gateway and model-provider access.
> - **Cloud, SAW, and OpenShell work** is where the sandbox architecture
>   actually runs. See the [cloud demo guide](cloud-demo.md) and
>   [build and reproduction guide](build-and-reproduce.md). It requires
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
[`infra/next-phase/toolchain.json`](../../infra/next-phase/toolchain.json). Cloud
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
[the Approver account guide](approver-account.md).

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

[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) runs on every pull
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
[build and reproduction guide](build-and-reproduce.md#5-evidence-failure-handling-and-cleanup)
and the [cloud demo guide](cloud-demo.md).

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
[NeMo Guardrails](../nvidia/nemo-guardrails.md)). Task state is
in-memory and intended only for this authorization demonstration.

For the rest of the documentation, start at the [docs index](../README.md).
