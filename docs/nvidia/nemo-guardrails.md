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
| **Input** | `self check input` on user messages, tool results, and the approved plan's free text, then the topical rail `check invoice topic` on the same content | Presidio PII masking, then the repo's APIM semantic classifier (a NAT middleware, not a NeMo rail) |
| **Output** | Regex secret detection, then `self check output` on model text and on `publish_decision` diagnosis, rationale, and risks | Regex secret detection and Presidio PII masking on the final answer |
| **Execution: tool calls** (`tool_output`) | One call per response, only the role's tools, arguments valid against the `InvoiceStep` / `PlanningDecision` contracts, step targets bound to the admitted scenario, no secrets | Tool arguments checked for shell and SQL syntax before the tool runs |
| **Execution: tool results** (`tool_input`) | Only results from the role's tools, bounded JSON, no secrets | Secret detection and PII masking before the model reads the result |
| **Retrieval** (`retrieval`) | `check invoice retrieval` (`InvoiceModelGateway.check_retrieval`) screens RAG runbook and postmortem chunks for indirect prompt injection and secrets before context assembly | Not used |
| **Dialog** | The topical rail (`check invoice topic`) runs as an input rail rather than an open-chat Colang dialog flow because the agent executes bounded workflows | Not used |

## Keeping the agent on topic

The topical rail `check invoice topic` blocks untrusted content that falls
outside the invoice-incident scope: general questions, creative writing, coding
help, other systems, or attempts to change the agent's role. Content that is
partly in scope but also asks for something else is blocked.

- **NemoGuard format.** The check prompt follows the contract of the
  NemoGuard Topic Control NIM (`llama-3.1-nemoguard-8b-topic-control`) and
  NeMo's built-in `topic safety check input` flow
  (`nemoguardrails/library/topic_safety`): numbered rules in a system prompt
  and a one-word `on-topic` / `off-topic` verdict. The dedicated NIM can
  replace the check model without changing the rules.
- **Fails closed.** Only an exact `on-topic` verdict passes. NeMo's built-in
  topic-safety action treats any reply other than `off-topic` as a pass; this
  rail does the opposite, so an empty, truncated, or unexpected reply blocks.
- **Actions stay structural.** The topical rail limits what the agent reads.
  What it can *do* is already limited by its toolset, the execution rails, and
  the broker.

Rules: `TOPIC_CONTROL_PROMPT` in
[`control/invoice_rails.py`](../../src/task_agent/control/invoice_rails.py).

## Evaluation

[`evals/guardrails/cases.yml`](../../evals/guardrails/cases.yml) holds labeled
allow and block cases for every surface the gateway screens: benign incident
content, prompt injection, off-topic requests, secret leaks, unsafe model
narrative, off-contract tool calls, and bad tool results. Each block case names
the rail expected to catch it.

```bash
.venv/bin/python scripts/evaluate-guardrails.py          # offline, no model calls
.venv/bin/python scripts/evaluate-guardrails.py --live   # all cases, real check model
```

| Mode | Scores | Runs |
| --- | --- | --- |
| Offline | 40 deterministic cases (secret detection, tool-call contracts, tool-result checks, RAG retrieval poisoning checks, and benign cases on those surfaces; Precision 100%, Recall 100%, F1 100%, p95 latency < 8 ms); 26 model-backed cases are stubbed to pass | Every pull request in CI |
| Live | All 66 cases, including `self check input`, the topical rail, and `self check output`, with precision, recall, F1, false positives/negatives, and p50/p95 latency per surface | On demand; needs `OPENAI_GUARDRAIL_BASE_URL` and `GUARDRAIL_EVAL_API_KEY` |

The script prints a Markdown report and exits 1 on any misclassified case. The
same cases can score NemoGuard NIMs against the current self-checks.

## Where the rails run

**Invoice agent: in the trusted invoice gateway, outside the sandbox.** The
sandboxed agent's model endpoint is the gateway
([`console/invoice_gateway.py`](../../src/task_agent/console/invoice_gateway.py)),
so a compromised agent cannot skip the rails. For each model call the gateway:

1. runs input rails on untrusted messages and execution rails on tool results;
2. calls the model and **buffers the whole response**;
3. runs execution rails on proposed tool calls and output rails on model text;
4. returns the response only if every rail passed, otherwise HTTP 403.

Decisions are logged and forwarded as structured gateway events: `guardrail-denied`,
`tool-result-guardrail-denied`, `output-guardrail-denied`, and the matching
`-passed` events, which render in the console activity feed and `Model + Guardrails`
component inspector ([`static/invoice-view.js`](../../src/task_agent/console/static/invoice-view.js))
while rejecting any unverified guardrail claim from `agent-runtime`. Configuration
and custom actions: [`control/invoice_model.py`](../../src/task_agent/control/invoice_model.py)
and [`control/invoice_rails.py`](../../src/task_agent/control/invoice_rails.py).

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
- The topical rail adds one check-model call per model call, alongside
  `self check input`.
- A rail error or timeout fails closed (HTTP 500 or 403); nothing reaches the
  agent.

## Gaps

- **Live model-backed run not yet recorded.** The
  [evaluation set](#evaluation) (66 labeled cases across all five surfaces:
  `input`, `output`, `tool_call`, `tool_result`, and `retrieval`)
  scores 40 deterministic cases on every pull request in CI. Running `--live`
  against the APIM guardrail route to record the 26 model-backed cases
  ([specification §18.5](../specification/next-phase-saw-openshell-spec.md#185-evaluation))
  remains an on-demand operator step.
- **Same-model self-checks in default cloud config.** Default cloud checks use
  `gpt-4o-mini` via APIM; [`control/invoice_rails.py`](../../src/task_agent/control/invoice_rails.py)
  also includes `parse_nemoguard_safety_verdict` and `TOPIC_CONTROL_PROMPT`
  for drop-in replacement with `nvidia/llama-3.1-nemoguard-8b-content-safety`
  and `nvidia/llama-3.1-nemoguard-8b-topic-control` NIMs.

Tests: [`tests/test_invoice_model.py`](../../tests/test_invoice_model.py),
[`tests/test_invoice_gateway.py`](../../tests/test_invoice_gateway.py),
[`tests/test_output_execution_rails.py`](../../tests/test_output_execution_rails.py),
[`tests/test_guardrail_eval.py`](../../tests/test_guardrail_eval.py),
[`tests/test_hybrid_guardrails.py`](../../tests/test_hybrid_guardrails.py).
