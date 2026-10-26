# Guest execution rollout and acceptance handoff

## Live full-cycle acceptance passed

On **2026-10-25 21:37:51–21:41:26 UTC**, actual Edge browser automation on
`https://learningnemo.ai/` completed a fresh disposable invoice workflow in about
3 minutes 35 seconds. No API responses were mocked and no previous request was
replayed. Separate guest Operator and Approver browser contexts completed
Planning, submission, approval, Execution, independent verification and completion.

- Scenario: `0f2b67fb2d6e4711a0e6959808c86226`.
- Plan: `f4d9a730ffd34d41a5dd05a69965b0eb`.
- Approved/persisted hash: `00ce08058a9e7c43074e7ccbc274b56b12c55d741afdb07f5e784984a6ef978d`.
- Planning job: `a72a7b075a654e49a17f66f93ff926b7`.
- Execution job: `dcb5f1d9ca9a4d0381957a5c5859df53`.
- Planning sandbox: `0d4803f0a78f44c2985b2c0a1376943e`.
- Execution sandbox: `e1aad9df9b1c4d41b84a80d6f85c4bc1`.

Execution recorded exactly three tool requests, three tool returns and three step
receipts, followed by `agent-finished`, `verification-passed`, authority revocation
and confirmed sandbox stop. All five independent checks were true:
`no_duplicates`, `legitimate_invoices_preserved`, `total_reconciles`,
`idempotent_version_active`, and `replay_created_no_invoices`.

The browser acknowledged completion, then read back the persisted `completed`
state and exact hash. A separate subsequent live GET independently confirmed
that state/hash binding. Private evidence, including guest cookies, is retained
under local application data as `LearningNeMo/guest-cycle-budget-20261025.json`;
do not commit or publish it. All five invoice services were subsequently observed
Running with latest revisions equal to ready revisions.

This is measured evidence of one complete live cycle, not a guarantee against
future provider/network failures. Earlier failed attempts below remain retained.
The existing zero-retry overnight rehearsal is left scheduled as an additional
check, not counted as another success before it runs.

## Structured agent finalization correction

The quota-window rehearsal `f4ac8a3f88534c989aaee3669bc2cd22` read both diagnostic
tools and published a decision, then failed on an extra model-generated final
narrative. The Planning gateway recorded `output-guardrail-denied` and HTTP 403.
The guardrail was enforcing its policy, not evidence that the structured
decision or its diagnostic reads had failed.

The supported toolkit terminal-tool mechanism is now configured:

- Planning returns directly from `publish_decision`; the decision arguments
  still pass output/tool rails and typed validation before publication.
- Execution returns directly from the local `finish_execution` tool. It accepts
  no arguments, has no broker/SQL route, and refuses to succeed until every
  exact approved receipt is present. It cannot independently verify or close
  the plan; those remain trusted controller/verifier and Operator operations.
- Signed Execution delegations and gateway role allowlists explicitly include
  this local terminal operation. Mutation scope remains only `execute_step`.
- Real toolkit tests refuse any extra model generation after terminal tools,
  and prove that all three approved Execution calls run exactly once, in order,
  before local termination. Premature and repeated completion are rejected.

The agent and signer are deployed on ready `terminal1025` revisions; both
gateways are now ready on the follow-up `budget1025` revisions. Host admission
remains enabled. Exact current image pins:

- Agent: `sha256:2d1e7588a7157ccf4d6ffb080fe7f8ef5c730b6df4f2fc8a8021f00113fae1f4`.
- Operator: `sha256:77430ecf7d4958998ef74ad910a13a84fa9d86e3f85382696f789ff958d223c8`.
- Planning/Execution gateways: `sha256:a9c4a7ac5dac41d2dc31c4909c2db8b7cdea4ac6dc40031b325274a702163c44`.

343 regression tests passed, including the follow-up classifier budget tests.
Both deployed gateways also passed actual NeMo
three-turn and poisoned-middle-result checks, plus terminal-tool role separation
and absence of an HTTP terminal-tool route, without provider calls. These checks
are not proof of a complete live workflow.

A new real-browser attempt, Planning job
`38cc2e544e624e8ab7d4c5f1c3a0095d`, prepared the new filesystem and started its
agent, but stopped on a NeMo input-classifier `ReadTimeout` before any diagnostic
tool result. Authority revocation and sandbox stop were recorded; the run was
not replayed. Private evidence is retained under local application data as
`LearningNeMo/guest-cycle-terminal-20261025.json`.

Five subsequent read-only classifications of the actual Planning prompt passed
in 0.85–1.62 seconds. This confirms intermittent classifier transport failure,
not its underlying network/provider cause. A gateway-only follow-up raises the
classifier attempt timeout from 10 to 15 seconds while retaining exactly one
read-only retry and bounding each semantic rail to 40 seconds. Tool-result and
tool-call rails keep their existing limits, and mutations and agent generation
are never automatically retried. The exhausted classifier still fails closed.
The ready follow-up deployment passed five more actual prompt classifications
in 0.87–1.48 seconds and both roles' local-only terminal-tool/history diagnostics.

Fresh full-browser Planning job `a72a7b075a654e49a17f66f93ff926b7` completed the
follow-up's full acceptance described above. Earlier failed attempts are retained,
not replayed.

## Updated demo quota after explicit owner authorization

The live quota procedure now allows 100 sandbox launches per UTC day with no
per-guest hourly cap, through 2026-10-27 04:00 UTC. It automatically restores
the usual 3/hour and 25/day limits after that window. Existing ledger records
and permissions were preserved; no counters were reset.

The isolated migration rehearsal verified four launches for the same guest,
duplicate-job accounting, rejection of launch 101, and expiry fallback, all
inside a rolled-back transaction. The committed migration receipt was verified
on an independent connection and its temporary administrator job was removed.
The real-browser rehearsal has been resumed with new request IDs now that two
launches fit in the authorized budget. The historical quota blocker below
describes the earlier handoff, not the current live limit.

## Deployed capability

Public guest Operators can now follow the disposable invoice workflow through
Planning, submission, guest Approver review, Execution, independent verification
and completion. The original sponsoring Operator must execute the exact approved
plan hash before approval expires. A separate guest Approver role does not gain
execution scope. This is demonstration identity separation, not production human
identity assurance.

The guest-route rollout did not expand SQL grants or table schema; the quota
procedure change is separately described above. Guest execution is admitted only through
the durable, quota-counted job route. Direct synchronous execution, demo
administration, execution probes and same-sandbox follow-up tests remain blocked.
SQL ownership, ordered-step hashes, active authority, expiry, no-replay and
independent verification remain authoritative.

## Earlier rollout verification

- 337 invoice, guest authorization, model-rail, packaging and safety tests passed.
- 59 focused JavaScript workflow/view tests passed.
- Actual Edge browser rendered the deployed guest Execution and completion
  controls using an explicitly synthetic plan fixture.
- The actual backend rejected that unowned synthetic execution with HTTP 409,
  before quota admission or agent launch. No synthetic status was used as proof
  of a real execution.
- Both deployed gateways passed real three-turn NeMo rail diagnostics and
  blocked a poisoned historical middle result without provider calls.
- In-container direct HTTP, async model and NeMo input-rail probes passed.

## Earlier failed acceptance attempts — retained history

The first real-browser rehearsal created a new disposable scenario and launched
Planning, but failed on an existing classifier transport timeout before review
or Execution. Its job `47ab67c3ab1a434cab1491c3537f823a` remains retained and was
not replayed. Private evidence is in the current user's local application data
under `LearningNeMo/guest-cycle-20261025.json`.

The classifier client allows one bounded retry for read-only classification
only (the follow-up budget above supersedes the original 10-second attempts). SDK/NeMo
regressions verify recovery from one timeout and fail-closed behavior after two.
Execution tools, broker mutations and uncertain requests are never automatically
retried.

The real rehearsal's quota receipt reported 24 of 25 shared launches used on
October 25. A fresh Planning-plus-Execution cycle needs two slots. At that point,
the daily cap was **not** increased, reset or bypassed while the owner was
unavailable. The subsequent explicitly authorized quota window and completed
acceptance above supersede that historical blocker.

## One-shot scheduled rehearsal

A limited, interactive-user Windows task named
`LearningNeMo-GuestCycle-20261026` is registered for **2026-10-26 00:05 UTC**
(2026-10-25 20:05 in the current machine's UTC-04 local zone). It has zero
automatic retries, ignores duplicate instances, and has a 20-minute limit.
Its trigger expires at 04:05 UTC, preventing a much later catch-up execution if
the machine remains unavailable overnight.

The machine must remain powered on or able to wake, connected to the internet,
and the same Windows user must remain logged in. It can run while the session
is locked; it does not request administrator privileges or store a Windows
password. This is a local scheduled task, not a guaranteed cloud execution.

The wrapper is [run-guest-demo-rehearsal.ps1](../../scripts/run-guest-demo-rehearsal.ps1).
It uses the installed Edge browser and existing Playwright package. It refuses
to launch if an evidence file already exists, preventing replay after response
loss or interruption.

Private results will be written under the current user's local application data:

- `LearningNeMo/guest-cycle-after-reset-20261026.json`
- `LearningNeMo/guest-cycle-after-reset-20261026.json.log`

The evidence includes guest cookies for read-only reconciliation. Keep it private;
do not commit or publish it. Success requires `state: completed`, all five actual
verification checks true, a persisted matching plan hash, distinct Planning and
Execution sandboxes, and confirmed agent completion and sandbox stop. Inspect
the result before claiming demo readiness. A scheduled task in `Ready` state is
not evidence that the workflow passed.

If the task fails, inspect the retained request IDs and receipts; do not rerun
the same evidence path or replay an uncertain execution. The base daily launch
limit naturally resets at UTC midnight; all sandbox launches share that budget.