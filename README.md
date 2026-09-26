# LearningNeMo — Governed AI Agents on the NVIDIA Agent Stack

LearningNeMo runs NVIDIA NeMo agents against a production-like Azure system
and shows what it takes to let an agent do useful work **without giving it the
authority to cause harm**. The central question is not only "will the model
follow instructions?" but "what can the agent actually reach or change, even
when it does not follow instructions?"

The example workload is an invoice incident: a Planning agent investigates
and proposes a repair, a human approves the exact plan, an Execution agent
carries it out in a separate sandbox, and an independent verifier checks the
database. Each step is bounded by a different control.

## The NVIDIA stack

| Component | What it does here | Details |
| --- | --- | --- |
| **NeMo Agent Toolkit** | Every agent is a NAT workflow: an LLM, registered tools, and security middleware defined in YAML | [NeMo Agent Toolkit](docs/nvidia/nemo-agent-toolkit.md) |
| **NeMo Guardrails** | Input, output, and execution rails on every model call, run in a trusted gateway the agent cannot bypass | [NeMo Guardrails](docs/nvidia/nemo-guardrails.md) |
| **OpenShell** | Each agent run gets its own MicroVM sandbox with deny-by-default filesystem, process, and network policy | [OpenShell](docs/nvidia/openshell.md) |
| **Secure Agent Workspace** | NVIDIA's reference design for the private, single-owner workspace around the runtime | [Secure Agent Workspace](docs/nvidia/secure-agent-workspace.md) |

```text
agent loop (NeMo Agent Toolkit)
  -> screened by NeMo Guardrails in a trusted gateway
  -> contained by an OpenShell MicroVM sandbox
  -> inside a private workspace VM (Secure Agent Workspace)
  -> changes only through a broker that enforces the human-approved plan
```

See [Architecture](docs/architecture.md) for the full design and trust
boundaries.

## Status

This is a single-user proof of concept, not a production deployment. The
invoice workflow has run end to end with real Planning and Execution agents in
separate MicroVMs; several hardening changes are merged but not yet deployed,
and some reference-design layers are still gaps. [Status](docs/status.md) is
the single summary.

## Where to start

| Goal | Start with |
| --- | --- |
| Understand the design | [Architecture](docs/architecture.md), then the [NVIDIA component pages](docs/README.md#nvidia-components) |
| Run the task agent on your machine | [Local authorization walkthrough](docs/guides/local-authorization.md) |
| Build or operate the Azure environment | [Build and reproduce](docs/guides/build-and-reproduce.md) |
| Present it | [Presenter runbook](docs/guides/capability-demo.md) |
| Everything else | [Documentation index](docs/README.md) |

## Repository layout

| Path | Contents |
| --- | --- |
| [`src/task_agent/`](src/task_agent/README.md) | Agents, tools, security middleware, trusted services, and the dashboard |
| [`configs/`](configs) | NeMo Agent Toolkit workflow definitions |
| [`infra/next-phase/`](infra/next-phase/README.md) | Azure infrastructure, workspace VM, OpenShell policies and provider profiles |
| [`containers/`](containers/README.md) | Container images and their supply-chain evidence |
| [`docs/`](docs/README.md) | Architecture, NVIDIA component pages, guides, decisions, and dated records |
| [`tests/`](tests) | Python and browser tests |

## Validate locally

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/nemo-agents"
uv sync --frozen
"$UV_PROJECT_ENVIRONMENT/bin/python" -m pytest -q
"$UV_PROJECT_ENVIRONMENT/bin/python" scripts/validate-agent-config.py
bash infra/test-all-local.sh
```

These make no model calls and do not query or change Azure. The same checks,
plus the browser tests, run on every pull request in
[CI](.github/workflows/ci.yml).
