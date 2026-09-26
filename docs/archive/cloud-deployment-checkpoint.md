# Cloud Deployment Checkpoint

> **Archived dated record.** This describes the state on the date it was recorded, not current status. See [Status](../status.md) and the [archive index](README.md).

Recorded on 2026-09-15. This is a dated deployment result, not current health.

Dashboard:
https://ca-learningnemo-dashboard-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io/

## Established Results

- The dashboard, private workspace controller, and private NeMo task API are
  deployed as three Container Apps in `rg-learningnemo-demo-dev`.
- All three final revisions were ready and passed the independent cloud
  deployment verifier. The public dashboard uses HTTPS and Secure/HttpOnly
  session cookies, validates Origin plus CSRF, and denies anonymous access to
  protected demo records.
- The dashboard identity has only ACR pull access. The controller uses its own
  managed identity, with fixed read operations and VM-scoped Run Command
  authority. The agent reads only the internal APIM client secret through its
  managed identity; no operator Azure CLI profile was deployed.
- A real Operator session successfully queried cloud workspace status through
  the dashboard and private controller. A separate controller `--check` inside
  its Azure container returned the stopped/expired SAW state using managed
  identity. No sandbox command was executed in either status query.
- The cloud agent initialized JWT authorization and both input guardrail
  stages. Minimal direct requests from its container to both APIM model routes
  returned HTTP 200; internal gateway secret retrieval returned HTTP 200.
- Browser checks at 1440, 1024, 390, and 360 pixels found no horizontal overflow
  or page exceptions. Requests observed during those checks did not reference
  localhost. Signed-out live-action buttons remained disabled.

## Not Established

An authenticated read-only task request on the prior agent revision returned
HTTP 422. The deployment replaced that revision before its failure logs were
retrieved, and the dashboard's in-memory sign-in session ended during the
revision update. A successful model-backed task response has therefore not
been established for the final cloud deployment. Both APIM routes are reachable,
but this does not substitute for the user-bound request and middleware checks.

Next diagnostic action: sign in again with an assigned account, issue one
read-only task request, and capture the same revision's bounded agent logs
before restarting or updating it. The VS Code task `LearningNeMo: cloud agent
logs` opens a dedicated terminal for that purpose. Do not bypass authorization
or infer success from the agent health endpoint.

The SAW is preserved in Azure but stopped after its lease expired at
`2026-09-15T00:40:49Z`. Its earlier Planning proof returned UID 998 and denied
privilege escalation, but its API requests timed out after NAT removal.
An approved runtime-connectivity design and renewed bounded workspace session
are prerequisites for a new sandbox proof. The dashboard does not start or
renew an expired workspace automatically.

The full AgentRunner delegation, human approval, and sandbox-to-broker SQL
incident journey remains unimplemented as a connected browser scenario.
Cloud-hosting the task API is not equivalent to running that agent in OpenShell.

## Evidence and Lifetime

Owner-only build records, deployment parameters, and verifier output are under
`~/.local/state/learningnemo/cloud-demo.*` and the associated cloud image files.
No signing keys, browser tokens, or operator credentials belong in a shared
review archive. The last recorded dashboard deployment expiry was
`2026-09-15T05:38:52Z`, bounded by its hosting dependencies.

Each of these scheduled demo services has one minimum replica. The expiry tag
is not an automatic stop or deletion mechanism. Use the scoped cleanup in the
[cloud demo guide](../guides/cloud-demo.md) when the window ends. Existing workers and the
SAW have separate lifecycle and cleanup obligations.