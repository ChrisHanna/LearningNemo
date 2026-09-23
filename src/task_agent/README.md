# Source Tour

This package keeps domain behavior, security policy, framework integration,
and local verification in separate directories. A reviewer can read the
runtime without first understanding the browser console.

## Recommended Reading Order

1. [`tasks/models.py`](tasks/models.py) defines the task vocabulary and tool
   inputs.
2. [`tasks/store.py`](tasks/store.py) owns all mutable task state and
   transitions.
3. [`tasks/tools.py`](tasks/tools.py) adapts store operations to NeMo Agent
   Toolkit functions.
4. [`security/authorization.py`](security/authorization.py) validates one
   Entra token per request and enforces scopes plus app roles before each tool
   call.
5. [`security/semantic_guardrail.py`](security/semantic_guardrail.py) sends
   locally sanitized user text to the bounded APIM classifier operation and
   fails closed on anything except an explicit safe verdict.
6. [`register.py`](register.py) is the small plugin entry point that imports
   the registered components.
7. [`console/app.py`](console/app.py) composes the optional local verification
   UI after the agent runtime is understood.
8. [`control/models.py`](control/models.py) defines strict incident, plan,
   approval, execution, verification, and evidence records.
9. [`control/service.py`](control/service.py) enforces the deterministic
   containment-to-completion lifecycle.
10. [`control/approval.py`](control/approval.py) defines authoritative,
   one-time approval consumption. The deployed default is unavailable and
   denies remediation.
11. [`control/runtime.py`](control/runtime.py) exposes one least-authority HTTP
    surface per worker mode.

## Package Map

```text
task_agent/
|-- register.py
|-- security/
|   |-- authorization.py
|   `-- jwt_claims.py
|-- tasks/
|   |-- models.py
|   |-- store.py
|   `-- tools.py
`-- console/
   |-- capabilities.py
   |-- browser_sessions.py
    |-- identity.py
    |-- sessions.py
    |-- agent_client.py
   |-- walkthrough.py
    |-- app.py
    `-- static/
```

| Area | Owns | Does not own |
| --- | --- | --- |
| `tasks/models.py` | Task records and validated inputs | State or framework registration |
| `tasks/store.py` | Locking and state transitions | JSON or NeMo decorators |
| `tasks/tools.py` | NeMo registration and response serialization | Authorization decisions |
| `security/authorization.py` | Verified request identity, scope, and app-role checks | Task behavior |
| `security/jwt_claims.py` | Minimal parsing after trusted JWT verification | Signature validation |
| `security/policies.py` | Canonical tool requirements and access explanations | Request identity |
| `security/semantic_guardrail.py` | Strict APIM classifier call and fail-closed verdict parsing | Authorization decisions |
| `console/capabilities.py` | Review-safe system map and evidence labels | Secrets or runtime state |
| `console/browser_sessions.py` | Opaque cookies, CSRF tokens, and per-browser auth isolation | Entra role policy |
| `console/identity.py` | Local test-client settings and token-profile checks | Runtime API authentication |
| `console/sessions.py` | In-memory device-flow sessions | Browser rendering |
| `console/agent_client.py` | Typed calls to the NeMo HTTP API | FastAPI responses |
| `console/walkthrough.py` | Fixed step IDs, prompts, and persona bindings | Browser state |
| `console/app.py` | Routes, dependency composition, and process startup | Domain state |
| `control/operations.py` | Closed operation catalog and deterministic executor | Arbitrary SQL or commands |
| `control/service.py` | Versioned workflow, exact approval binding, and completion authority | HTTP or cloud persistence |
| `control/approval.py` | Atomic lookup and consumption contract for authoritative approvals | Azure SQL implementation |
| `control/runtime.py` | Diagnostic, query-runner, remediation, or verifier HTTP boundary | Multi-worker authority |
| `control/evidence.py` | Append-only hash-chained evidence records | External durable storage |

## Request Flow

```text
Browser console
  -> console/app.py
  -> console/agent_client.py
  -> NeMo FastAPI frontend
  -> security/authorization.py
   -> local PII Guardrails middleware
   -> security/semantic_guardrail.py
  -> tasks/tools.py
  -> tasks/store.py
```

The access token remains in `console/sessions.py` process memory. The browser
receives only device-flow status, granted scope and app-role names, timings,
and agent responses. Each session holds one account. Its verified app roles
select either the Reader or Operator walkthrough; the browser cannot select a
persona for a step.

## Intentional Demo Limits

- `InMemoryTaskStore` is reset when the agent process restarts. A production
  implementation would inject a durable repository.
- `reset_tasks` exists only to make the authorization walkthrough repeatable
  and requires the same `tasks.execute` scope as task mutation.
- NeMo currently maps workflow authorization exceptions to its generic HTTP
  error response; tool execution still fails closed in middleware.
- The four trusted-worker modes currently use the deterministic in-memory
   incident backend. They do not share state across processes.
- `InMemoryApprovalAuthority` exists only for deterministic tests. A
   remediation runtime created without an injected authority rejects every
   execution, even when a caller presents a correctly hashed approval receipt.
- Azure SQL approval persistence, the controlled recursive-query backend, and
   NeMo/UI integration are not implemented or deployed yet.