# Documentation

## Start here

| If you want to… | Read |
| --- | --- |
| Understand the design in ten minutes | [Architecture](architecture.md), then [Status](status.md) |
| See how each NVIDIA component is used | The four [NVIDIA component pages](#nvidia-components) |
| Run something on your own machine | [Local authorization walkthrough](guides/local-authorization.md) |
| Build or operate the Azure environment | [Build and reproduce](guides/build-and-reproduce.md), then the [infrastructure README](../infra/next-phase/README.md) |
| Present the project | [Presenter runbook](guides/capability-demo.md) and the [invoice demo](guides/invoice-demo.md) |
| Review the code | [Source tour](../src/task_agent/README.md) |

## NVIDIA components

Each page covers what NVIDIA says the component is, exactly how this
repository uses it (with file links), and what is not done yet.

| Component | Role |
| --- | --- |
| [NeMo Agent Toolkit](nvidia/nemo-agent-toolkit.md) | Agent framework: every agent is a NAT workflow |
| [NeMo Guardrails](nvidia/nemo-guardrails.md) | Input, output, and execution rails around every model call |
| [OpenShell](nvidia/openshell.md) | MicroVM sandbox runtime with deny-by-default policy |
| [Secure Agent Workspace](nvidia/secure-agent-workspace.md) | Reference design for the workspace envelope around the runtime |

## Guides

| Guide | Covers |
| --- | --- |
| [Local authorization walkthrough](guides/local-authorization.md) | Install, configure Entra and the model gateway, run the task agent as Reader and Operator |
| [Build and reproduce](guides/build-and-reproduce.md) | Operator build of the Azure environment, workspace VM, and OpenShell |
| [Cloud demo](guides/cloud-demo.md) | Dashboard, workspace controller, and task API in Container Apps |
| [Invoice demo](guides/invoice-demo.md) | The website agent workflow: Planning, approval, Execution, verification |
| [Presenter runbook](guides/capability-demo.md) | Eight-minute demonstration script and claim boundaries |
| [Approver account](guides/approver-account.md) | Provisioning the dedicated Approver identity |
| [Human handoff deployment](guides/human-handoff-deployment.md) | Incident, review, and approval service contract |

## Reference

| Document | Covers |
| --- | --- |
| [Architecture](architecture.md) | Components, trust boundaries, end-to-end flow |
| [Status](status.md) | What is verified, merged but not deployed, and open |
| [ADR 0001: OpenShell MicroVM driver](decisions/0001-openshell-microvm-driver.md) | Why MicroVMs, and the recorded constraints |
| [Specification](specification/next-phase-saw-openshell-spec.md) | Target-state requirements and claim levels (includes planned work) |
| [Archive](archive/README.md) | Dated deployment, diagnosis, and recovery records |

## Reading evidence

Design intent, an implemented control, a configured policy, a dated recorded
probe, current live health, and a complete human-plus-agent run are different
claims. Each guide states its own dates; a recorded result is not current
health.
