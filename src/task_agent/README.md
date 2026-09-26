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

The package has three layers. `tasks/` and `security/` are the NeMo agent used
in the local authorization exercise. `control/` is the trusted, deterministic
domain for incidents, approval, execution, and the invoice agent. `console/`
is the local and cloud website that drives both.

```text
task_agent/
|-- register.py          NeMo plugin entry point
|-- tasks/               Task records, store, and NeMo tools
|-- security/            Entra authorization, JWT claims, policies, semantic guardrail
|-- control/             Trusted incident, approval, execution, and invoice-agent domain
`-- console/             Local console, cloud dashboard, and private service APIs
    `-- static/          Browser code (no secrets or authority)
```

### Agent and security

| Module | Owns | Does not own |
| --- | --- | --- |
| `tasks/models.py` | Task records and validated inputs | State or framework registration |
| `tasks/store.py` | Locking and state transitions | JSON or NeMo decorators |
| `tasks/tools.py` | NeMo registration and response serialization | Authorization decisions |
| `security/authorization.py` | Verified request identity, scope, and app-role checks | Task behavior |
| `security/jwt_claims.py` | Minimal parsing after trusted JWT verification | Signature validation |
| `security/policies.py` | Canonical tool requirements and access explanations | Request identity |
| `security/semantic_guardrail.py` | Strict APIM classifier call and fail-closed verdict parsing | Authorization decisions |

### Trusted control domain (`control/`)

| Module | Owns |
| --- | --- |
| `models.py`, `canonical.py` | Strict incident, plan, approval, execution, verification, and evidence records; canonical hashing |
| `service.py` | Versioned workflow, exact approval binding, replay, and completion rules |
| `approval.py` | Approval lookup and one-time consumption contract (`UnavailableApprovalAuthority` denies by default) |
| `operations.py` | Closed operation catalog and the deterministic in-memory backend used locally and in tests |
| `workers.py`, `runtime.py` | One least-authority HTTP worker per mode: diagnostic, query runner, remediation, verifier |
| `sql_backend.py`, `mssql_client.py`, `mssql_owned_query.py` | Azure SQL stored-procedure backends (including `AzureSqlRemediationAuthority`) over a managed-identity client with a closed procedure catalog |
| `evidence.py` | Append-only, hash-chained evidence records |
| `initiation.py`, `incident.py`, `investigation_transport.py`, `database_analysis.py` | Sponsor-bound incident initiation and read-only investigation |
| `review.py` | Human review of SQL-persisted plans |
| `execution.py`, `execution_transport.py` | Durable approved execution; ambiguous writes are never replayed |
| `invoice_agent.py` | NeMo tools and sandbox entry point for the invoice agent |
| `invoice_controller.py`, `invoice_sandbox.py` | Sponsor-bound orchestration and host-side OpenShell lifecycle; Planning and Execution never share capabilities |
| `invoice_contract.py`, `invoice_catalog.py`, `invoice_repository.py`, `invoice_jobs.py` | Closed invoice contracts, fixed SQL calls and grants, and at-most-once job claims |
| `invoice_model.py` | Trusted APIM inference transport; runs NeMo input rails, buffers each response, and runs output and execution rails before returning it |
| `invoice_rails.py` | NeMo output and execution (`tool_output` / `tool_input`) rail flows, secret patterns, and contract checks for the invoice agent |
| `invoice_activity.py`, `invoice_challenges.py`, `invoice_retention.py` | Activity metadata, fixed boundary probes, and retained-sandbox evidence checks |
| `execution_sql_probe.py`, `invoice_sql_probe.py` | Rolled-back SQL rehearsals; never human evidence |

### Website (`console/`)

| Module group | Owns |
| --- | --- |
| `app.py`, `sessions.py`, `browser_sessions.py`, `identity.py`, `hosting.py` | Local console: device-flow sessions, per-browser cookies and CSRF, loopback/HTTPS origin rules |
| `agent_client.py`, `walkthrough.py`, `capabilities.py`, `showcase.py` | NeMo API client, role-selected walkthroughs, and review-safe descriptions of the system and dated results |
| `cloud.py`, `cloud_auth.py` | Cloud dashboard entry point and Entra JWT validation |
| `cloud_controller.py`, `remote_workspace.py`, `live_workspace.py` | Private workspace controller (fixed operations via Azure Run Command) and its dashboard proxy |
| `incident_service.py`, `review_service.py`, `execution_service.py` and `*_contract.py` | Private sponsor, reviewer, and operator APIs with strict browser input contracts |
| `remote_incident.py`, `remote_review.py`, `remote_execution.py`, `remote_invoice.py` | Fixed-route dashboard clients for those private services |
| `invoice_service.py`, `invoice_gateway.py`, `invoice_deployed.py`, `invoice_observations.py`, `invoice_availability.py` | Invoice workflow API, capability-authenticated agent tool gateway, and deployment composition |
| `invoice_remote_runtime.py`, `invoice_probes.py`, `invoice_retention_host.py`, `invoice_retention_worker.py` | Fixed Run Command bridge to the workspace VM, sandbox network probes, and stopped-run retention |
| `local_invoice.py`, `local_invoice_demo.py`, `local_demo_auth.py`, `invoice_demo_session.py` | Deterministic, loopback-only invoice demo |

## Request Flow

```text
Browser console
  -> console/app.py
  -> console/agent_client.py
  -> NeMo FastAPI frontend
  -> security/authorization.py
   -> local PII Guardrails middleware
   -> security/semantic_guardrail.py
   -> response output rails (on the final answer)
  -> tasks/tools.py (behind authorization, then tool execution rails)
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
- Trusted workers use the deterministic in-memory backend locally and in tests,
  and Azure SQL backends (`control/sql_backend.py`) when deployed. The
  in-memory backend does not share state across processes.
- `InMemoryApprovalAuthority` exists only for deterministic tests. A
  remediation runtime created without an injected authority rejects every
  execution, even when a caller presents a correctly hashed approval receipt.
  Deployed remediation uses `AzureSqlRemediationAuthority`.
- The task-agent authorization exercise (`tasks/`) is separate from the
  incident and invoice workflows and keeps its in-memory state.
