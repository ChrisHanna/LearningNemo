# Enterprise ISV Solutions Architecture Blueprint

This blueprint maps LearningNeMo's architecture and interactive demo to the
business problems solved when partnering with **enterprise cybersecurity, AI
safety, and infrastructure protection ISVs**: moving autonomous tool-using
agents from experimental prototypes to governed, auditable production systems.

## 1. Business Problem: Autonomous Triage & Remediation Without Over-Privileging

Security Operations Center (SOC) and enterprise operations ISVs want AI agents
to **triage alerts, translate analyst questions into diagnostic queries
(NL-to-Query), correlate retrieved runbooks (RAG), and execute containment or
remediation**—without introducing catastrophic blast radius if a model
hallucinates or encounters an adversarial payload.

| ISV Customer Requirement | LearningNeMo Pattern | Implementation & Evidence |
| --- | --- | --- |
| **Threat triage & investigation agent** (read-only analysis + correlation) | `Planning` NAT workflow in an isolated OpenShell MicroVM with read-only diagnostic tools (`invoice_summary`, `invoice_batches`) | [`src/task_agent/control/invoice_agent.py`](../../src/task_agent/control/invoice_agent.py), [`configs/invoice-planning.yml`](../../configs/invoice-planning.yml) |
| **Indirect prompt injection in untrusted telemetry / RAG** | `Poisoned batch telemetry` demo scenario + 5-surface NeMo Guardrails in the trusted gateway outside the sandbox | [`src/task_agent/control/invoice_rails.py`](../../src/task_agent/control/invoice_rails.py), [`evals/guardrails/cases.yml`](../../evals/guardrails/cases.yml) |
| **Natural-language-to-query (NL-to-SQL) safety** | Bounded `query-draft` (`SELECT`-only) and `sql-denied` (port 1433 blocked by OpenShell + NSG) sandbox challenges with OCSF denial telemetry | [`src/task_agent/console/invoice_probes.py`](../../src/task_agent/console/invoice_probes.py), [`src/task_agent/console/static/invoice-view.js`](../../src/task_agent/console/static/invoice-view.js) |
| **Human-in-the-loop approval & separation of duties** | Separate Entra `Task.Operator` and `Task.Approver` personas; an Operator cannot approve their own plan | [`src/task_agent/console/invoice_service.py`](../../src/task_agent/console/invoice_service.py), [`docs/guides/approver-account.md`](../guides/approver-account.md) |
| **Remediation workflow & failure containment** | Fresh `Execution` MicroVM bound to a single-use capability and Ed25519 `SignedDelegation`; SQL broker executes only registered stored procedures matching the exact approved `plan_hash` | [`src/task_agent/control/invoice_controller.py`](../../src/task_agent/control/invoice_controller.py), [`src/task_agent/control/invoice_governance.py`](../../src/task_agent/control/invoice_governance.py) |
| **PII handling & content safety** | Presidio PII masking (`PERSON`, `EMAIL_ADDRESS`, `PHONE_NUMBER`), regex secret blocking, and NemoGuard Content Safety / Topic Control contracts | [`configs/agent.yml`](../../configs/agent.yml), [`src/task_agent/control/invoice_rails.py`](../../src/task_agent/control/invoice_rails.py) |
| **Confidential Computing & partner KMS** | NRAS Entity Attestation Token (`GH100`/`GB200` `cc_mode=on` + RIM match + `amd-sev-snp`/`intel-tdx`) gating KMS Secure Key Release (SKR) | [`docs/nvidia/confidential-computing.md`](confidential-computing.md), [`src/task_agent/control/confidential_compute.py`](../../src/task_agent/control/confidential_compute.py) |

## 2. Partner Engagement Maturity Matrix (PoC → Production → Air-Gapped)

When guiding an ISV partner from discovery through production scale, controls
graduate across three deployment profiles without changing the agent workflow
code:

| Layer | Phase I: Rapid Partner PoC | Phase II: Cloud Production (Current Repo) | Phase III: Confidential & Air-Gapped Enterprise |
| --- | --- | --- | --- |
| **Model & Safety NIMs** | Hosted API (`gpt-4o-mini` via APIM guardrail route) | Buffered trusted gateway running all 5 NeMo Guardrails surfaces | Self-hosted **NemoGuard Content Safety & Topic Control NIMs** inside the private enclave |
| **Runtime Containment** | Local OpenShell container/VM | Private Trusted Launch SAW VM + per-run **OpenShell MicroVMs** (`hard_requirement` Landlock, L7 proxy) | **Confidential VMs (AMD SEV-SNP / Intel TDX)** + **H100/B200 CC-On** verified via **NVIDIA NRAS / nvtrust** |
| **Credential & Key Custody** | Manifest-delivered run capability | **OpenShell Provider** credential mediation (`openshell:resolve:env:`) + Ed25519 `SignedDelegation` | **Partner KMS / HSM Secure Key Release (SKR)** bound to `attestation_hash` + `delegation_hash` |
| **Audit & SIEM Integration** | Local JSONL transcripts | Correlated SQL activity ledger + OpenShell **OCSF** network denial receipts + NAT latency spans | Signed OCSF telemetry streamed to customer SIEM / SOAR |

## 3. Latency, Throughput, and Safety Trade-Offs

A key Solutions Architecture deliverable is quantifying the latency and safety
budget of each control layer:

1. **Deterministic Rails (`< 8 ms` p95)**:
   - Secret regex checks, Pydantic tool-call contract validation, target
     scenario binding, tool-result JSON bounds, and RAG retrieval injection
     checks run in-process in **2.7–7.8 ms p95** with zero model cost.
2. **Model-Backed Guardrails (`self check input`, `check invoice topic`, `self check output`)**:
   - In the trusted invoice gateway, responses are **buffered** so output and
     `tool_output` rails complete before any byte reaches the sandboxed agent.
   - Replacing general-purpose LLMs with dedicated **8B NemoGuard NIMs**
     (`nvidia/llama-3.1-nemoguard-8b-content-safety` and
     `nvidia/llama-3.1-nemoguard-8b-topic-control`) deployed on local TensorRT-LLM
     reduces guardrail check latency while eliminating external API egress in
     air-gapped environments.
