# NVIDIA NeMo Agent Toolkit

The [NeMo Agent Toolkit](https://docs.nvidia.com/nemo/agent-toolkit/latest/)
(NAT, package `nvidia-nat`) is the agent framework. Every agent in this
repository is a NAT workflow defined in YAML: an LLM, a set of registered
functions (tools), and middleware around them. NAT runs the tool-calling loop;
this repository supplies the tools, the security middleware, and the
boundaries around the process.

| | |
| --- | --- |
| Version | `nvidia-nat[guardrails,langchain]~=1.9` ([`pyproject.toml`](../../pyproject.toml)) |
| Plugin entry point | `nat.components` → [`task_agent/register.py`](../../src/task_agent/register.py) |
| Workflow type | `tool_calling_agent` for every agent |
| Model | `gpt-4o-mini`, always through an owned APIM gateway, never a provider key in the agent |

## The two agents

| | Task agent | Invoice agent |
| --- | --- | --- |
| Purpose | Local and cloud authorization exercise: list, execute, and reset demo tasks | The website agent: diagnose and repair an invoice incident |
| Config | [`configs/agent.yml`](../../configs/agent.yml) | [`configs/invoice-planning.yml`](../../configs/invoice-planning.yml), [`configs/invoice-execution.yml`](../../configs/invoice-execution.yml) |
| Runs | `nat serve` (FastAPI front end) locally ([`scripts/run-agent.sh`](../../scripts/run-agent.sh)) and in Container Apps ([`scripts/run-cloud-agent.py`](../../scripts/run-cloud-agent.py)) | `WorkflowBuilder` inside an OpenShell sandbox, one run per sandbox ([`control/invoice_agent.py`](../../src/task_agent/control/invoice_agent.py)) |
| Caller identity | Entra JWT verified by NAT's front end and the repo's middleware | None inside the sandbox; a short-lived run capability authorizes the gateway calls |
| Tools | `current_datetime`, `list_tasks`, `execute_task`, `reset_tasks` | Planning: `invoice_summary`, `invoice_batches`, `publish_decision`. Execution: `execute_step` |
| Iteration cap | 2 | Planning 8, Execution 6 |

## What the repository registers with NAT

| Extension | Where | What it does |
| --- | --- | --- |
| Functions (`@register_function`) | [`tasks/tools.py`](../../src/task_agent/tasks/tools.py), [`control/invoice_agent.py`](../../src/task_agent/control/invoice_agent.py) | Tools with strict Pydantic input schemas. Invoice tools call only the run's fixed gateway; the agent cannot supply a target, SQL, or URL. |
| `entra_authentication` middleware | [`security/authorization.py`](../../src/task_agent/security/authorization.py) | Verifies one Entra JWT per request: issuer, audience, client, and token age. |
| `require_access` middleware | same file | Per-tool scope and app-role check (for example `tasks.execute` + `Task.Operator`). |
| `semantic_guardrail` middleware | [`security/semantic_guardrail.py`](../../src/task_agent/security/semantic_guardrail.py) | Fail-closed APIM classifier call on sanitized input. |
| NAT `guardrails` middleware (built in) | [`configs/agent.yml`](../../configs/agent.yml) | Hosts NeMo Guardrails rails on the workflow and on each tool; see [NeMo Guardrails](nemo-guardrails.md). |

Task-agent request order: Entra authentication → PII masking input rail →
semantic classifier → model → (per tool) access check → tool execution rails →
tool → output rails on the final answer.

## What NAT does not do here

These controls come from elsewhere, by design:

- **Authority to change data.** No tool can mutate the invoice database
  directly. `execute_step` reaches a broker that enforces the exact approved
  plan; see [Architecture](../architecture.md).
- **Isolation.** The invoice agent's process runs in an OpenShell MicroVM
  sandbox with a deny-by-default network policy; see [OpenShell](openshell.md).
- **Guarding the invoice model call.** The invoice agent's model endpoint is
  the trusted invoice gateway, which runs NeMo Guardrails outside the sandbox,
  so a compromised agent cannot skip them.

## Where to look next

- Run the task agent locally: [Local authorization walkthrough](../guides/local-authorization.md)
- Code tour: [`src/task_agent/README.md`](../../src/task_agent/README.md)
