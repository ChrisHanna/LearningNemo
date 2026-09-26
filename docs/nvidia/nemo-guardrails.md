# NVIDIA NeMo Guardrails

[NeMo Guardrails](https://docs.nvidia.com/nemo/guardrails/latest/about-nemo-guardrails-library/overview)
screens content going into and coming out of a model. This repository uses it
as a **screening layer, not an authorization boundary**: Entra roles, OpenShell
policy, and the SQL broker decide what an agent can actually do. A rail that
misses something still meets those controls.

| | |
| --- | --- |
| Version | `nemoguardrails[sdd]>=0.21,<0.22` ([`pyproject.toml`](../../pyproject.toml)) |
| Colang | 1.0 |
| Check model | `gpt-4o-mini` through a dedicated APIM guardrail route that forces temperature 0, 3 output tokens, no tools, no streaming ([`infra/policies/semantic-guardrail.xml`](../../infra/policies/semantic-guardrail.xml)) |

## Coverage by rail type

NVIDIA defines [five rail types](https://docs.nvidia.com/nemo/guardrails/latest/about-nemo-guardrails-library/rail-types).

| Rail type | Invoice agent (website agent) | Task agent (local and cloud exercise) |
| --- | --- | --- |
| **Input** | `self check input` on user messages, tool results, and the approved plan's free text | Presidio PII masking, then the repo's APIM semantic classifier (a NAT middleware, not a NeMo rail) |
| **Output** | Regex secret detection, then `self check output` on model text and on `publish_decision` diagnosis, rationale, and risks | Regex secret detection and Presidio PII masking on the final answer |
| **Execution: tool calls** (`tool_output`) | One call per response, only the role's tools, arguments valid against the `InvoiceStep` / `PlanningDecision` contracts, step targets bound to the admitted scenario, no secrets | Tool arguments checked for shell and SQL syntax before the tool runs |
| **Execution: tool results** (`tool_input`) | Only results from the role's tools, bounded JSON, no secrets | Secret detection and PII masking before the model reads the result |
| Dialog, retrieval | Not used | Not used |

## Where the rails run

**Invoice agent: in the trusted invoice gateway, outside the sandbox.** The
sandboxed agent's model endpoint is the gateway
([`console/invoice_gateway.py`](../../src/task_agent/console/invoice_gateway.py)),
so a compromised agent cannot skip the rails. For each model call the gateway:

1. runs input rails on untrusted messages and execution rails on tool results;
2. calls the model and **buffers the whole response**;
3. runs execution rails on proposed tool calls and output rails on model text;
4. returns the response only if every rail passed, otherwise HTTP 403.

Decisions are logged as gateway events: `guardrail-denied`,
`tool-result-guardrail-denied`, `output-guardrail-denied`, and the matching
`-passed` events. Configuration and custom actions:
[`control/invoice_model.py`](../../src/task_agent/control/invoice_model.py) and
[`control/invoice_rails.py`](../../src/task_agent/control/invoice_rails.py).

**Task agent: NAT guardrails middleware** in
[`configs/agent.yml`](../../configs/agent.yml):
`local_input_sanitization` and `semantic_input_guardrails` on the workflow
input, `tool_execution_rails` on each tool after its access check, and
`response_output_rails` on the final answer. A refused tool argument replaces
the tool result with a refusal and the tool does not run.

## Trade-offs

- Buffering means the invoice agent receives each model response in one piece;
  its model timeout is 150 seconds to allow for generation plus checks.
- `self check output` adds one model call per response that contains text.
  Tool-call-only responses skip it.
- A rail error or timeout fails closed (HTTP 500 or 403); nothing reaches the
  agent.

## Gaps

- **No evaluation dataset.** [Specification §18.5](../specification/next-phase-saw-openshell-spec.md#185-evaluation)
  requires allowed/denied examples with accuracy, false-positive, and latency
  reporting. Unit tests cover rail behaviour, not detection quality.
- **Rail decisions stay in logs.** They are not yet shown in the console
  activity feed.
- **Same-model self-checks.** Checks use the same model family as the agent.
  NVIDIA's dedicated safety models (NemoGuard) would add model diversity.

Tests: [`tests/test_invoice_model.py`](../../tests/test_invoice_model.py),
[`tests/test_invoice_gateway.py`](../../tests/test_invoice_gateway.py),
[`tests/test_output_execution_rails.py`](../../tests/test_output_execution_rails.py),
[`tests/test_hybrid_guardrails.py`](../../tests/test_hybrid_guardrails.py).
