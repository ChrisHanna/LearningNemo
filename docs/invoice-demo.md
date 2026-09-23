# Governed Invoice Incident Demo

Live console:
https://ca-learningnemo-dashboard-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io/

The first screen is the invoice workflow. Planning and Execution are real NeMo
agents running in different OpenShell MicroVMs. The tool gateway uses NeMo
Guardrails and APIM; the SQL broker, not model text, enforces approved mutations.

## Explicit Demo Sessions

Published September 19, 2026. All five invoice services are on revision 0000026.
Live verification passed all six managed-identity SQL grant checks, workload
identity and private connectivity. It also confirmed an idle Operator session,
one replica per service, and only the new revision active: the old always-on
monitor revision is inactive. The console was built, deployed and independently
verified for pinned images, exact private origins, HTTPS, identity boundaries
and anonymous denial. A fresh human sign-in is required after deployment.

The updated console initially reads only SQL-free demo status after sign-in.
An Operator with execution scope explicitly chooses Start demo to enable SQL
access and existing active-demo monitoring. Starting a session does not create
a scenario, run an agent, approve a plan, establish SQL readiness, or change
billing. The Approver can observe the session but cannot start or end it.

End demo closes admission, lets accepted Operator requests, background jobs,
and remote Review requests finish, then attempts one final bounded cleanup
cycle. The UI shows ending until those requests have drained. Cleanup still
requires verified successful stopped sandboxes, archival evidence and exact
inventory readback. A failed final cleanup reports unconfirmed and stops;
idle timers do not repeat it or continue querying SQL.

An abandoned session closes admission after four hours, without extending
scenario, plan, approval, token or per-run deadlines. A process restart starts
idle, never resumes agent jobs, and does not replay cleanup. Independent host
expiry watchdogs remain active. Session status and health reads do not use SQL
or renew the session. During an active session, completion notifications,
one-minute fallback checks and pre-run capacity checks remain enabled.

Start and End request IDs are recorded before POST. Lost responses are reconciled
through SQL-free status reads, never automatic POST retries. An unmatched request
remains unconfirmed. A lost remote Review lease or completion acknowledgement
can leave the session ending; inspect it rather than cancelling work, guessing
completion, replaying a decision, or restarting services to clear the state.
The gate is process-local and depends on the existing single Operator replica.

Idle means this workflow has stopped automatic SQL activity, not that Azure has
already paused the database. The configured 60-minute auto-pause delay and other
database clients still matter. The explicit deployment SQL probes also count as
database activity. Local fixtures cover Start, End, draining, idle status-only
reads, lost responses, role controls and responsive layout; they do not establish
an authenticated human Start/End or full agent workflow acceptance test.

## Paid Serverless Continuation

At the user's explicit request on September 19, 2026, the retained database was
changed once from free-limit `AutoPause` to `BillOverUsage`. Live readback confirmed
`useFreeLimit=true`, General Purpose serverless Gen5, 0.5 minimum and 2 maximum
vCores, 32 GiB maximum storage, and a 60-minute idle auto-pause delay are preserved.
Actual service SQL connections subsequently passed; monthly quota exhaustion
no longer blocks this release.

This change is irreversible for the free offer: monthly free-limit auto-pause
cannot be restored. The monthly free allowance remains, with usage above it
billed. Idle auto-pause is a separate setting and remains enabled. Budget alerts
are notifications, not spending caps; the existing 50-unit budget is not a limit
on paid SQL usage or the other retained Azure resources.

The guarded transition is `infra/next-phase/configure_sql_paid_overage.py`. It
requires the exact subscription and explicit `sql-paid-overage-irreversible`
acknowledgement for its single mutation, saves an immutable before/request receipt,
and independently checks preserved settings afterward. Running it without
`--apply` verifies the existing paid state. An uncertain mutation is never replayed.
Owner-only receipts are `sql-paid-overage.request.json` and
`sql-paid-overage.verified.json` in the WSL LearningNeMo state directory. The legacy
free-only base deployment refuses to run when the transition receipt exists;
its default remains free-only for a fresh environment. Existing descriptive
cost tags are historical labels, not current billing authority.

## Doubled Review and Approval Windows

Migration execution `caj-learningnemo-human-mig-dev-ol0iw7r` succeeded on
September 19, 2026 at 09:42 UTC. Its separate-connection readback verified
180-minute review and 30-minute approval windows, all nine invoice migration
hashes, and preservation of nine existing review deadlines and five existing
approval deadlines. The receipt was saved and temporary administrator authority
was removed; the disabled migration job remains retained.

The matching services were deployed as revision 0000025. Operator/Simulator SQL
and private-connectivity checks passed, but the subsequent Review SQL connection
reported that September 2026's monthly free allowance was exhausted. Azure SQL
is paused for the remainder of the month, with renewal at October 1, 2026,
00:00 UTC. Final all-service verification and live invoice operations are blocked.
No billing settings were changed; paid continuation requires explicit approval.
The verified migration was not replayed. The console was not redeployed.

Migration 016 changes new proposals to a 180-minute review window from creation
and new independent approval decisions to a 30-minute execution approval window.
Existing stored review and approval deadlines are preserved, including expired
ones. Signing in again does not reset either deadline. Exact plan hashes,
independent reviewers, one-time execution claims, and per-run capabilities remain
unchanged. The approval contract accepts at most 30 minutes.

The separate scenario lifetime remains two hours from scenario creation. Execution
therefore still requires both a live scenario and a live approval, with the existing
two-minute preparation margin. The longer review window does not extend test-data
life or guarantee three hours of end-to-end availability. The 15-minute human
token-age limit is also unchanged.

Validation passed 855 Python tests. Deployment readback checks the 180-minute
SQL default and the 30-minute decision procedure across separate connections,
plus preservation of previously stored review and approval deadlines. No human
approval, new scenario, or agent run is required or started by the migration.

## Sandbox Inventory and Pressure Cleanup

The proactive monitor is deployed on invoice service revision 0000024. Live
verification passed all six SQL identity/grant checks, private connectivity,
and the deployed completion-notification, one-minute fallback, challenge preflight,
and reserved-slot wiring. Automatic status records at 09:13:33, 09:14:46, and
09:16:00 UTC confirmed repeated checks. The minute is a delay between checks,
not an exact wall-clock interval; host operations take additional time. A concurrent
inventory read caused one monitor attempt to report ResourceExistsError and skip
the pass without deletion. At 09:17 UTC, the actual signed-in Operator inventory
read returned 18 stopped sandboxes, six free slots, and zero eligible entries.
The session was preserved; the optional revised console wording for the final-slot
guard is built and tested but not published during this authenticated test.

Published and verified September 19, 2026 at 08:37 UTC. All five invoice services
are on revision 0000023. Migration 015 was installed by `enai70g`; SELECT-only
readback `ywo7ndm` verified all eight migration hashes, six exact identity/grant
sets, and unchanged review deadlines. Temporary migration authority was removed.
Live service and console verification passed, including anonymous inventory
denial. The public page requires fresh sign-in after deployment.

The version-2 policy is enabled. Six verified completed sandboxes were archived
and deleted oldest-first in bounded passes, reducing the inventory from 24 to
18 and restoring six slots. All nine archives, including three from prior cleanup,
passed hash and deletion-receipt verification. An independent baseline comparison
confirmed that only the six previewed IDs disappeared and all other 18 UUID,
name, and stopped-state bindings were unchanged. Free space was 7.49 GiB.
The remaining 18 lack the required successful-run evidence and stay protected;
the 13-sandbox target is not a guarantee when protected entries exceed it.

The Operator console shows the retained sandbox count, hard limit, available
slots, free disk space, and an observed inventory. Each row shows the sandbox ID,
state, and deletion eligibility. Refresh is read-only. The trash control requires
confirmation of the exact ID and Operator execution scope; only an owned,
verified completed run may be deleted manually. SQL plans and receipts remain.
Audience and Approver views do not expose deletion controls. A pending deletion
is saved before POST and never automatically retried after a lost response.

Version 2 of the root-owned retention policy starts pressure cleanup at 14
retained sandboxes and targets 13. It selects the oldest eligible stopped runs
first, independently of the 24-hour TTL, with at most two deletions per pass.
The existing TTL still applies below the pressure threshold. The monitor wakes
immediately after a job's final result is persisted, checks every minute while
idle, and checks before Planning, Execution, and standalone challenge creation.
It drains up to six confirmed two-deletion batches in one cycle, stopping at the
target, no progress, or an uncertain result. Admission waits for monitor-held
maintenance rather than treating it as another agent run. Inventory GETs never
trigger cleanup. If cleanup cannot get the count below 23, new sandbox work is
refused before the final slot is consumed. The host hard cap remains 24 and the
free-space floor remains 4 GiB. Protected evidence can prevent reaching the
target; no fallback deletes arbitrary sandboxes to meet a count.

The proactive-monitoring regression suite passed 853 Python and 82 Node tests,
including completion-triggered wakeup, challenge admission, confirmed batch
progress, no replay after lost deletion evidence, and waiting through maintenance.

All deletion paths share the controller/lifecycle locks, require an entirely
stopped host inventory, recheck exact UUID/name immediately before deletion,
archive and verify evidence first, and compare the entire inventory afterward.
Failed, uncertain, active, unbound, or evidence-incomplete runs stay protected.
Unknown deletion outcomes require inspection and are never replayed. Migration
015 expands only the controller's existing readback to recent successful runs
and includes owner binding; agent and reviewer permissions do not change.

Local validation passed 843 Python and 81 Node tests. Browser fixtures verified
one exact confirmed delete POST, count refresh, protected controls, read-only
Audience, desktop/mobile fit, and preservation of the inventory's horizontal
scroll position. No fixture was installed on the production page.

The first read-only host preview found six eligible successful runs among 24
retained sandboxes. The other 18 were protected by the evidence gates; the target
is not permission to delete them. Inventory receipts are compressed and bounded
to fit Azure Run Command's output limit, with no change to the archive evidence.

## Planning Capacity Failure

Reporting fixes published September 19, 2026 at 06:36 UTC. All five invoice
services are on revision 0000021; live verification passed six exact SQL identity
grants, workload identity, and private connectivity. Console image/security checks
and public bundle checks passed. The page was left signed out after deployment;
fresh human sign-in is required. No SQL migration or resource-limit change was made.

On September 19, the user's Planning run `41e37768a2324de79dbc55f6a3c07c16`
for scenario `9397b7e5456c4242afcfd2085a01daee` failed during sandbox
preparation. Host inspection confirmed 24 retained sandboxes at the 24-slot limit,
all stopped, with 7.49 GiB free. There was no sandbox matching the attempt and no
launch receipt. The host preparation command failed at its inventory assertion;
no agent ran and no proposal was produced. Earlier service retention output also
reported zero eligible deletions. This was an admission failure, not a delayed
transition to Propose.

The reporting update emits a run-bound capacity rejection before creating host
run artifacts. Jobs retain the specific reason without replay, and Analyze shows
progress while running or becomes unavailable for the same uncertain attempt.
Next-action text no longer replaces failure with Ready to analyze. Existing failed
records are not retroactively rewritten. A recorded successful proposal still
opens Propose automatically.

Validation passed 832 Python and 79 Node tests. Local browser fixtures confirmed
capacity feedback, disabled repeat analysis, no premature Propose, GET-only
reconnection, and layout fit at 1440, 768, 390, and 360 pixels. Fixtures were removed.
The retained limit remains 24, the free-space floor 4 GiB, and concurrency one.
No sandbox was deleted or restarted, no agent was launched, and no failed job was
replayed during diagnosis. New Planning remains blocked until approved capacity
changes or eligible policy-driven cleanup frees a slot.

## Same-Sandbox Test Release

Published September 19, 2026. Invoice services revision 0000020 and the public
console now support Test this sandbox on the selected active agent run. Request
the test before the run closes; it executes in that same sandbox after successful
agent completion and authority revocation, before normal cleanup. It does not
create or restart a sandbox. The separate-new-sandbox test is secondary.

Migration 014 was applied by execution `caj-learningnemo-human-mig-dev-441m7jl`.
SELECT-only readback `caj-learningnemo-human-mig-dev-9jrvx88` passed at 05:17 UTC:
seven exact migration hashes, six identity/grant inventories, the 90-minute review
default, and seven unchanged legacy deadlines across separate SQL connections.
Temporary authority was removed and the disabled migration job retained. Live
service and console verifiers passed exact identity, SQL privilege, private-route,
image-pin, operator-managed availability, and anonymous-denial checks.

All 826 Python and 76 Node tests pass. Browser fixtures cover confirmation, one
bound request, proof matching, read-only Audience view, responsive layouts, and
scroll preservation. A live same-agent-sandbox probe has not yet been exercised;
it requires fresh Operator sign-in and an explicit test request during a new
authorized run. No real scenario, agent run, approval, or repair was started by
this release. Earlier release records below are historical.

## Recovery and Review Update

Published and independently verified September 18, 2026 at 20:17 UTC. Invoice
services are on revision 0000017. All 748 Python and 55 JavaScript tests pass.
Local browser fixtures verified lost-response recovery with one POST, independent
history errors, Approver evidence, keyboard inspection, and desktop/mobile fit.
Live checks verified migration hashes across connections, temporary-authority
cleanup, six exact SQL identity grants, private connectivity, pinned console
assets, and anonymous status denial. No real scenario, Planning run, approval,
Execution run, or challenge was requested during this release. The Operator
sign-in and authenticated walkthrough remain the next acceptance gate.

The September 18 recovery update adds an owner-bound scenario receipt atomically
with fixture creation. The browser stores the request ID before the POST and keeps
an uncertain request across reload and sign-in using a stable, namespaced storage
key. This key is not authorization: status reads still require the signed-in
Operator and verified SQL ownership. Check creation status is GET-only and never
replays creation. An absent record remains unconfirmed, not permission to retry.
Older scenarios without a creation owner can be read only if an existing owned
Planning run establishes their ownership; no legacy ownership is fabricated.

History loading has its own progress/error state and does not lock navigation or
scenario setup. Confirmed submission, review, completion, and reconciliation
receipts survive a failed list refresh. Reconnect observations reads the selected
run, refreshes cached history, and timestamps its own observation. Read clients
use bounded pre-connection recovery only; procedure execution is never retried.

Scenario status returns its two-hour expiry and a Planning preparation cutoff.
The controller checks ownership and eligibility before allocating a sandbox.
Plan lists return SQL-derived submission/review and Execution preparation
deadlines. Expiry updates action availability without a manual refresh; no
approval, capability, scenario, or run lifetime has been extended.

The Approver queue includes the persisted diagnostic summary bound to the exact
plan's evidence hash. The service validates that binding before returning it;
the UI shows baseline metrics and an immutable snapshot before confirmation.
Approvers gain no live diagnostic, scenario, job, or execution route. Quarantined
history no longer marks the retained active invoice as a duplicate. The component
inspector supports arrows, Home, End, roving focus, and named tab panels.

The additive migration is `011_invoice_recovery.sql`; installed migrations
008-010 remain unchanged. It adds one scoped Operator read, replaces the
simulator's unowned create grant with its owner-bound wrapper, and retains the
existing two-procedure review grant. Migration authority is temporary and the
disabled migration job is retained after cleanup. No user scenario or agent run
is required for release verification; the real Operator walkthrough is a separate
acceptance gate after sign-in.

## Fresh Operator Walkthrough: September 19

At 03:02 UTC the real Operator completed a new scenario, Planning, and submission
through the production UI. No approval or repair was performed.

- Runtime check returned 200; the read-only host check found 21 stopped sandboxes, three free slots, and 7.49 GiB free disk.
- Exactly one scenario POST returned 201: `4997b4d774f74fb2a631bc06126548ca`, created at 02:59:39 UTC and expiring at 04:59:39 UTC.
- Exactly one Planning POST returned 202: run `25cd03c86b0e4ce7a69ac8b2cacfa478`, sandbox `ac1dff3489884eddbb1fbbc3cf9d0b5d`.
- The real agent produced the three scoped operations. Independent diagnostics reported 12 orders, 24 active invoices, 12 duplicates, and 295600 cents versus an expected 147800 cents.
- Authority revocation at 03:01:14 UTC and sandbox stop at 03:01:28 UTC were recorded. The UI automatically selected Propose.
- Plan `c23b870d3fd4443991b35e5fa7df50d5` has hash `82de5e95633ef4e8bba8ddc282ea8b51eef8a49dbf60023a21ff29ac45d14e57`. Its SQL deadline is 04:31:13 UTC, confirming the new 90-minute window.
- Read database again returned 200 with the same revision 1, 24 unquarantined rows, and no execution receipts. The panel showed one recorded reading, the 1478.00 discrepancy, and Repair not started.
- Exactly one submission POST returned 200, not the earlier false 503. Independent GET confirmed the same hash in submitted state with no execution run. The UI selected Approve and retained its success acknowledgment.

The next acceptance gate is the separately signed-in Approver's review and
decision, then an explicit Operator execution. Do not repeat creation, Planning,
or submission for these identifiers. Older expired proposals remain unchanged.

## Operator Walkthrough: September 18

The real Operator created scenario `d592a9a48bde4a8eb6da5f2b1c61f48a` once
at 23:51:16 UTC. Creation returned 201; reload confirmed the same owned scenario
with GET 200 and no additional POST. The initial history read completed without
blocking scenario setup. Scenario expiry is September 19 at 01:51:16 UTC.

Planning run `7c2c56b27f9b4c9496c0a0e23a4bd4eb` was admitted once at
23:52:53 UTC. Actual agent diagnostics found 12 orders, 24 active invoices,
12 duplicates, expected total 147800 cents, and reported total 295600 cents.
The run published plan `69a01eecae304b9bbc9614b60f4ba002`; authority revocation
and sandbox stop were recorded before completion. The UI advanced to Propose.
An independent evidence refresh confirmed revision 1, no quarantine, and no
execution receipts. No repair has occurred.

Submission at 23:55:43 UTC returned a false 503 after SQL committed. An explicit
GET established that the exact plan was submitted; the POST was never repeated.
The new receipt validator compared the SQL client's tuple to a list. Both submit
and decision now validate one exact row independent of the sequence container;
33 focused repository/service tests pass, including tuple-backed API responses
and rejected missing, extra, or mismatched receipts. Approval acknowledgment is
not yet tested with the real Approver. Review expires September 19 at
00:24:19 UTC. Plan hash:
`c8c320718ea64e43a48176740ec3e149bd880ff6874331ce241170072b714d84`.

The acknowledgment hotfix is deployed on invoice service revision 0000018.
Live image, six SQL identity/grant, workload identity, and private-connectivity
verification passed. No schema or console deployment was needed, and the
submitted plan was not replayed or changed. The Operator session had expired by
September 19 at 00:18 UTC; the final authenticated read correctly returned 401.
Independent review now requires the actual Approver to sign in. No approval or
Execution has been attempted. The deadline above is unchanged.

Observed presentation follow-ups, not yet implemented:

- Show request admission immediately, before the first status read completes.
- Replace scenario setup/readiness text with a running-state summary during Planning.
- Label preflight freshness separately from live run status; a stale preflight is not a run failure.
- Audience view should say Planning is running, not Awaiting an investigation.
- Serialize SQL UTC timestamps with an explicit timezone; the current event list interprets naive UTC values as local time.
- Use Latest observation before execution; Observed after currently labels pre-repair reads.
- Preserve the selected artifact while the completion refresh loads, and reconcile a failed action acknowledgment into clear confirmed-state feedback after GET.

## Approver UI Validation: September 19

Read-only validation completed at 00:27 UTC against the real Approver session.
All 55 JavaScript regression tests and 30 focused console/API/receipt tests pass.
This was not an approval or Execution test: no approval, rejection, scenario,
challenge, or agent-start request was sent, and no confirmation was checked.

Verified in the deployed browser:

- The exact submitted plan and immutable diagnostic snapshot were visible to the Approver.
- At 00:24:19 UTC the review window expired; checkbox, Approve, and Reject disabled automatically, without reload or clock changes.
- Layouts at 1440, 768, 390, and 360 pixels had no horizontal overflow or clipped buttons; visible inputs were labelled and tab panels were associated.
- Inspector arrows, Home, End, roving focus, and keyboard disclosure toggles worked. Desktop and mobile screenshots were inspected.
- Approver GETs for Operator job history, live database evidence, and scenario status returned the expected 403.
- Audience view exposed no mutation inputs or commands. Monitored UI navigation sent zero non-GET API requests.
- Refresh plans returned 200 with an empty review queue after expiry. The browser was restored to desktop, Approver view, still authenticated.

Reproduced findings, not fixed by this validation:

1. Expiry labels disagree: the next-action message says expired, but the stage still says Waiting for Approver and the change summary says Awaiting independent review.
2. Missing role-authorized history is presented as absent history: the review sidebar says No events recorded, and Audience view says No agent run requested despite the completed Planning run and its bound proposal.
3. After the expired plan leaves the review queue, the reason disappears. The UI asks the Approver to select a plan from an empty queue and labels disabled Analyze as Current while Approve is selected.
4. Audience view's Back to operator button returns to the same Approver session; it should use a role-neutral label.

The proposal was never approved and its review window is now expired. No deadline
was extended, no submission was replayed, and no fresh investigation was created.
Execution and verification remain untested in this real-human walkthrough.

## Scroll Stability

All scrollable invoice panels now preserve their independent horizontal and
vertical positions during same-context refreshes, in normal and Audience views.
Stable content keys identify event lists, invoice tables, plan artifacts,
diagnostic snapshots, selected-event data, architecture evidence, and challenge
receipts. Open and closed disclosure states are restored before their nested
scroll positions. The page position is preserved around the outer session-bar
update as well as the invoice render. Incoming events never force a jump to
either end. Each refresh captures the latest manual position, including zero.
Different accounts, modes, views, and runs do not inherit another context's state;
intentional navigation such as opening Architecture remains available.

Verification passed: 71 JavaScript tests, 29 focused Python console tests, and
native-timer browser fixtures at 1440/768/390/360 pixels. Multiple panels retained
distinct offsets through new-event polling and the full bootstrap refresh,
including a page at 1,100px, Audience history at 900px, normal challenge receipt
at 240px, and Audience receipt at 320px. The Approver's plan and diagnostic panels
were checked independently. No visible overflow area lacked a key. A simulated
clock harness stalled and was discarded; native refresh tests replaced it and
caught the outer session-bar page shift, which is now covered by a regression.
The original Audience completion and cross-account/run isolation tests still pass.
Fixtures sent no mutations and were removed. Public helper loading order, both
render paths, image pins, identity boundaries, and anonymous denial passed.
No invoice service, agent run, approval, or execution was changed.

## Recorded Invoice Findings

The evidence clarity update replaces SQL snapshot recorded with Recorded invoice
findings. The selected expired proposal had two identical pre-repair reads:
12 orders, 24 active invoices, 12 duplicates, an invoice total of 2,956.00 and
an order total of 1,478.00. These are retained observations, not a repaired result
or a live database monitor. The panel now shows one set of figures before repair,
the source order count, expected order total, discrepancy, explicit repair status,
and the recorded timestamp in UTC. Expired scenarios have a retained-data notice
and cannot trigger Read database again.

A baseline/latest comparison appears only when execution is recorded and the
bound SQL observation has a higher revision than the Planning baseline. Its
labels are Planning baseline and Latest database read, not Observed after.
Repair independently verified still requires all five persisted verifier checks;
neither a new read nor zero observed duplicates substitutes for verification.
The invoice-row detail uses the same recorded-reading language and UTC timestamp.

This console-only release passed 63 JavaScript and 30 focused Python tests,
desktop/mobile browser fixtures at 1440/768/390/360 pixels, and independent cloud
release verification. Mobile Audience comparison amounts were checked to stay on
one line without overflow. Local fixtures were removed. Public assets contain the
new labels; no real invoice mutation, agent run, approval, or execution was made.

## Review Window

Live and independently read back on September 19, 2026. Migrations 012 and 013
were installed by execution `caj-learningnemo-human-mig-dev-pjjgk7y`. Its log
stream became unavailable, so a separate SELECT-only job,
`caj-learningnemo-human-mig-dev-y91487a`, verified exact migration hashes,
the default, legacy deadlines, and all six identity/grant inventories across two
connections. Azure confirmed successful exit of the exact pinned readback image
and `--verify-only` command; the release receipt explicitly records this evidence
source, not an invented stdout receipt or legacy-row count. Temporary SQL
authority was removed and the disabled migration job retained afterward.

New proposals have a 90-minute submission and independent-review window from
creation. Migration 013 stores the deadline as `ReviewExpiresAt`; the queue,
submission, decision procedure, and UI all use that same value. Existing plans
retain their original 30-minute deadlines, including already-expired proposals.
The migration verifies preservation across a separate SQL connection.

This does not extend the scenario's two-hour lifetime, the 15-minute approval
lifetime after a decision, or any capability/run watchdog. A long review does
not guarantee enough remaining scenario lifetime for execution. No existing
approval is renewed and no old action or agent run is replayed.

## Successful Sandbox Retention

Enabled and verified September 19, 2026 on service revision 0000019. The preview
found three eligible old successful runs. One bounded pass at 02:27 UTC archived
and removed runs `0f7c6d5c927b4b81b65cfa49945986d0` and
`0d8bc0d10d1249219b4c72874599dee3`, reducing the inventory from 24 to 22.
Independent archive rehashing and deletion readback passed. Every other sandbox
UUID, name, and stopped state exactly matched the saved baseline. Two slots are
available; reported free disk remains 7.49 GiB. No VM, database, service, invoice
row, approval, or failed/uncertain sandbox was deleted. The remaining eligible
sandbox is left for a later scheduled pass. Private rollout records are
`invoice-retention.preview.json`, `invoice-retention.applied.json`,
`invoice-retention.policy.json`, and `invoice-retention.verified.json` in the
operator's infrastructure state directory.

The last full backend gate passed 801 tests, followed by 15 focused SQL/readback
provenance checks; all 56 JavaScript tests passed. Live six-identity SQL checks,
private connectivity, and exact release metadata passed on revision 0000019.
No user agent run or human approval was requested for this release.

The operator has authorized a narrow exception to the testing-retention hold:
successfully completed invoice sandboxes may be deleted after 24 hours stopped.
Azure VMs, disks, database records, services, original workspace sandboxes,
failed/uncertain runs, and unbound legacy sandboxes remain retained. The limit
stays at 24 sandboxes; one active sandbox and the 4 GiB free-space floor remain.

The managed Operator service checks hourly, including on startup. The SQL read
procedure `control.usp_read_invoice_retention` returns only old finished jobs
with revoked authority and a matching controller stop event. Planning needs a
valid bound diagnostic result; Execution also needs independent verification and
every exact step receipt. No new database mutation grant or public cleanup API
is introduced. The host independently requires stopped state, a matching UUID
and name, a successful agent transcript/exit, and a host stop marker aged 24 hours.

Before deletion, a root-only archive under
`/var/lib/learningnemo-invoice-retention/<run_id>/` stores SQL evidence, plan and
verification data, step receipts, agent events, stderr, launch receipt, configured
and observed policies, and OpenShell console/network logs and metadata. Every
file is hash-verified and flushed to disk first. Archives and original host run
diagnostics are not automatically deleted. They contain diagnostic data and must
not be published as public demo assets. Archive usage still consumes host disk.

A shared lifecycle lock serializes cleanup and fresh sandbox provisioning. Any
active sandbox blocks a cleanup pass. Each pass deletes at most two eligible
sandboxes and verifies that every other sandbox is preserved. A persisted delete
attempt prevents blind replay after a timeout or an uncertain result; manual
inspection is required in that case. Missing evidence or archive failures retain
the sandbox. Old stopped sandboxes without proof of success are not inferred safe.

Deletion is separately gated by root-owned
`/etc/learningnemo/invoice-retention.json` with the exact policy:

```json
{"version":1,"enabled":true,"successful_stopped_ttl_hours":24}
```

The guarded `infra/next-phase/configure_invoice_retention.py` commands are:

- `preview`: read SQL/host evidence and show eligible runs; no archive or deletion.
- `enable --apply`: require a fresh matching preview and enable the root policy.
- `pause --apply`: disable future cleanup without changing sandbox admission.
- `run --apply`: perform one bounded pass using the same worker as the scheduler.
- `verify`: check the enabled policy, archive hashes, deletion readback, and capacity.

All commands require the expected `AZURE_SUBSCRIPTION_ID`; apply commands also
require `LEARNINGNEMO_AZURE_APPLY=invoice-retention`. The global resource retention
marker is not removed. No expiry, approval, capability, or uncertain job is renewed
or replayed by this policy. Pausing the Operator service pauses the hourly check;
it resumes from SQL records at startup, without replaying an uncertain delete.

## Conference View

Operators have two explicit modes: Invoice workflow and Sandbox challenge. The
workflow contains the five-stage plan journey; the challenge mode primarily tests
the selected agent run's existing sandbox. Switching modes preserves the selected
investigation and test receipt and sends no request. The shared one-active-job
limit remains enforced. Architecture and permissions remains a collapsed section.
The secondary separate-policy test never borrows the selected run's evidence.
Architecture opens the component inspector. Signed-out sessions
show a direct sign-in action without empty diagnostic metrics or activity panels.
The header identifies the invoice console, not a legacy endpoint's agent health.

Operators can select Check runtime for an explicit read-only Azure workspace
observation. It reports operator-managed availability and network state and marks
results stale after one minute. There is no application or host admission deadline
in this mode. It does not check the root-owned admission file, SQL
availability, sandbox capacity, or model readiness; sandbox admission still checks
its independent prerequisites. A failed check is not a successful readiness result.
Next-action text distinguishes pending challenges, unconfirmed admission, review,
expired approval, execution receipts, and completion.

Background same-context refreshes preserve an untouched confirmation. Changing
account, mode, plan, stage, scenario, selected challenge, or job state clears it; starting
a request consumes it. No refresh or navigation automatically submits an action.

The five-step screen now includes a selectable architecture: human identity,
harness, SAW workspace, OpenShell sandbox, Planning/Execution agents, mediated
model, diagnostic gateway, SQL broker, and independent verifier. Select a
component to inspect Authority, Boundaries, or Evidence.

The primary workflow leads with recorded invoice findings: observed invoice and
duplicate counts, the source order count, invoice versus order totals, and repair
status. A later database revision can be compared with the Planning baseline after
execution is recorded. Unobserved values are never filled from the plan's desired
result. Proposed and approved changes show
their exact target, revision transition, duplicate-set hash where applicable,
preserved records, risks, and artifact before confirmation. Successful verification
is displayed separately from a SQL observation or an agent-reported response.

Request & proof is a selectable, run-bound event inspector. Each selection shows
the recorded action, capability lifecycle observations up to that event, the
relevant boundary, and available proof. A SQL receipt badge requires a controller
event matching the plan step, target, operation, next revision, and a receipt hash.
Generic tool request/response events do not contain per-request correlation IDs;
the UI explicitly labels these as run context only and never joins them to a nearby
receipt by timestamp. Verification is attributed to the independent SQL verifier.
Selection does not issue requests or change authorization.

Audience view is a read-only presentation of the same selected run, not a second
workflow. It removes setup, account controls, submission, approval, execution, and
challenge-start controls, retaining outcomes, the current observation, selectable
proof, and Back to operator. Existing GET polling continues; entering, leaving,
or selecting evidence sends no mutation request. A request guard also blocks
operator commands while this view is active. Sign-out or account change exits the
view and clears its evidence selection. A stalled current-run observation becomes
Last known state after 15 seconds in both operator and audience views; a browser
clock test confirmed this transition with zero writes. This UI mode is not an additional server
authorization boundary. Outcome values are 44px on desktop and responsive on mobile.

Human authorization shows the current session's roles and scopes separately
from agent-run permissions. API ownership and approval checks remain mandatory;
the diagram does not grant permissions. Boundary examples in the architecture
inspector remain a policy explorer. Test this sandbox requires explicit Operator
confirmation while the selected run is active and its sandbox binding is observed.
Migration 014 persists the exact owned job/sandbox request. It creates no new job,
sandbox, or capability. The request is queued for successful agent completion:
revoke authority, claim the request once, probe the same still-Ready sandbox, stop,
then persist the receipt. Agent failure or uncertain revocation prevents the test;
cleanup still runs. Completed sandboxes are never restarted. Existing watchdogs,
approval deadlines, and separate Planning/Execution sandboxes are unchanged.

The probe is a fixed, credential-free controlled process, not a model request.
The agent previously held its normal capability; no capability is issued or passed
to this probe. Browser pending state is saved before POST; response loss permits
read-only receipt recovery, never automatic replay. Audience view has no commands.
Same-sandbox results require exact run, role, sandbox, and policy binding plus
enforcement and confirmed-stop evidence. Unconfirmed tests remain retained.

Local validation for this update passed 826 Python tests and 76 Node tests.
Browser fixtures verified one bound POST and no job/challenge creation, explicit
confirmation, queued-state suppression, read-only Audience controls, rejection of
foreign-sandbox proof, and receipt scroll preservation. Layouts fit at 1440, 768,
390, and 360 pixels. Fixtures were removed afterward. These checks do not establish
a live same-agent-sandbox denial: that acceptance check requires a new authorized
agent run and an explicit test request while it is active. No human approval or
repair was performed as part of this update.

Planning's probe first reaches its allowed summary route without a credential
(HTTP 401), then attempts the forbidden repair route (HTTP 403). Execution's
probe reaches its allowed execution route, then attempts the forbidden diagnostic
route. A successful denial requires a matching OpenShell OCSF L7 denial record,
not only an HTTP status. DNS failures/timeouts or missing receipts are unconfirmed.
Both legacy separate-policy probes were exercised live as UID 998 and their fresh sandboxes were confirmed
stopped and retained. No model, real plan, approval, or database capability is
given to these probes. They demonstrate route enforcement, not agent intent,
arbitrary prompt-injection handling, or SQL changed-target enforcement.

The collapsed Separate policy test in a new sandbox remains a secondary option.
Its challenge IDs use the same durable SQL job admission and global one-active-job
gate as incident work. Reconnect reads the receipt; it does not repeat a probe.
The challenge panel retains the request ID in account-specific browser storage.
No background challenge resumes automatically after a service restart.

The current observation uses recorded run events and marks stale or disconnected
observations explicitly. Agent-reported activity is distinguished from harness
events and independent verification. New runs record preparation, capability
issuance/revocation, verification, and confirmed sandbox stop. Historical runs
without those events do not acquire synthetic lifecycle evidence.

Planning results include persisted diagnostic counts for the pre-change snapshot.
The work and proof panels precede the architecture. Independently read
SQL step receipts are labelled separately from agent-reported tool responses.
Missing metrics remain Not observed. Readable operation names replace UUIDs and
procedure IDs in the main plan; exact artifacts and hashes remain expandable.

Inspect database evidence is an owner-checked, read-only action. The coordinator
calls a fixed workload-authenticated diagnostic route using its managed identity;
the Planning sandbox cannot call that route. No SQL grants were added. Summary
revision is checked before and after reading bounded invoice rows. The ledger
groups records by order key, preserves quarantined rows, and shows pre-change
versus current counts/totals when both observations exist. A current observation
does not itself establish that a plan passed independent verification.
Stage history reads are owner-scoped and never restart a job. Keyboard navigation
is available on the five-stage tabs; layout is checked at 390, 768, and 1440 pixels.

The conference UI is deployed and verified. The managed-availability release passed 731 Python
tests; this console-only redesign passed 21 Node presentation-model tests and 43
focused Python console tests. Browser fixtures covered explicit
execution/completion, independent Approver controls, disconnected/uncertain runs,
stage history, and no writes from architecture navigation. The public page passed
responsive layout, icon rendering, read-only boundary exploration, and anonymous
invoice API denial checks. Fixtures were removed afterward.

The final audience freshness image was deployed and independently verified on
September 18. The deployed workflow bundle includes the current-run freshness
update. A clock-controlled local browser test observed Live observation becoming
Awaiting a fresh observation / Last known state after 17 seconds of stalled GET
polling, with zero mutation requests. Public verification checked image pins,
operator-managed availability, identity boundaries, and anonymous access denial.

The follow-up UI fixes associate a newly admitted Execution job with its exact
selected plan before the persisted execution run ID is available. Completion
refreshes the plan and selects Verify without a manual refresh; this association
does not grant authority or mark an approval as consumed. SQL evidence selection
prefers the highest matching revision, then the latest observation time, so a
pre-repair inspection cannot override a newer completion snapshot. Snapshots are
labelled as observations, not continuous live reads. Challenge selection survives
architecture navigation, recorded results identify their own role, and keyboard
focus on evidence disclosures survives a same-account rerender. All four audit
reproductions passed with local API fixtures; no real job or approval was replayed.

Submission, review, and execution actions follow the readable exact change review,
and the workflow panels no longer have nested scrollbars. Saved plans open at the
appropriate stage. Optional history reads do not keep primary actions disabled.
SQL already limits draft submission to 30 minutes after plan creation; the UI now
shows that deadline and explains expiry rather than offering a doomed submission.
The Approver retains decision feedback and Switch to Operator after the approved
plan leaves the submitted-only queue. The redesign's isolated browser rehearsal
passed scenario creation, Planning, submit, review, execute, verify, and completion
with exactly six expected POSTs. A separate challenge required exactly one POST;
audience navigation and evidence selection issued none. The test caught and fixed
restored Planning completion incorrectly returning an approved plan to Propose.
The persisted plan state now determines the restored stage. Read-only audience,
receipt binding, session expiry, and 390/768/1440px layouts passed. This is UI fixture
verification, not a new human-authenticated cloud rehearsal.

## Five-Step Presentation

The Investigation selector switches between an account-local draft and saved
investigations. Returning to the draft preserves its selected scenario; it does
not create or reset anything. New investigation explicitly starts an empty draft.
Scenario to create selects the fixture type, while Create scenario performs the
separate confirmed setup action. Draft selection survives page refresh in the
same browser session and is kept separate for each signed-in account.

1. **Analyze:** Sign in as Operator. Open Scenario setup, select the lost
   acknowledgement fixture, confirm, and create it. This alone does not run an
   agent. Start Planning. Activity records the actual agent and diagnostic calls.
2. **Propose:** Inspect the diagnosis, rationale, risks, evidence hash, and exact
   artifact. The expected repair has three steps: quarantine the exact duplicate
   set, rebuild the reported total, and activate idempotent importing. Confirm
   submission within 30 minutes of plan creation. Expired drafts require a fresh
   investigation; the application does not extend their authority. A proposal does
   not change invoice data.
3. **Approve:** Switch to the separately assigned Approver account. Select and
   inspect the submitted plan, then approve or reject. Approval expires after
   fifteen minutes. The sponsor cannot approve their own plan.
4. **Execute:** Switch back to the original Operator and refresh plans. Confirm
   the exact approved artifact and start Execution. This creates a fresh sandbox
   with a different capability and tool policy. Steps run serially through the
   exact-plan broker, with durable receipts.
5. **Verify:** Inspect the independent checks, then acknowledge completion.
   A successful model response is not verification, and approval is not execution.

The healthy scenario is a separate choice. A supported no-change decision is a
valid Planning result and must not be presented as a repair.

## Observed Acceptance

The automated rehearsal used isolated fixtures and explicitly synthetic reviewer
metadata, not a human JWT or a user's plan. Actual human sign-in, approval, and
execution remain the presenter's rehearsal steps.

- Planning run: `0d8bc0d10d1249219b4c72874599dee3`
- Planning sandbox: `6086be65cca94fedbbf66b3d54579184`
- Plan: `ee8f6770156941ce8a02a8d88c1b0b7b`
- Plan hash: `0a1f71d98348ee77bf6833867eabb41f9c5c4d1deaa7b84cfd1325c0180089d1`
- Execution run: `0f7c6d5c927b4b81b65cfa49945986d0`
- Final receipt: 2026-09-16 23:54:50 UTC

The real Planning agent called invoice_summary and invoice_batches and published
three steps. The real Execution agent obtained three broker receipts. Independent
SQL verification passed all five checks: no duplicates, legitimate invoices
preserved, total reconciled, idempotent importer active, and import replay created
no invoices. The test plan remains verified, with explicit completion pending.

The new SQL observation confirmed 24 active invoices with 12 duplicates before
repair, then 12 active invoices with zero duplicates at revision 4. Expected,
actual, and reported totals all read 147800 minor units after repair. The activity
feed recorded three independently read SQL receipts and successful verification,
authority revocation, and sandbox stop. Azure initially throttled the Execution
connection before the worker started; only that saved attempt was resumed after
Retry-After elapsed, without repeating submission or approval.

Live Planning route challenge `952878d0153e40f4b14baba59849164f` and Execution
route challenge `531347cfcc624b9486bf021381c3351f` both returned the expected
401 control response, 403 forbidden response, matching OpenShell OCSF denial,
and independently confirmed stopped sandbox.

In the earlier acceptance cycle, SQL rejected a tampered plan hash and reuse of
the consumed approval. Its three receipts remained unchanged. Those negative
checks were rollback-protected. Real Guardrails probes allowed benign
Planning/approved Execution and blocked policy override, malicious plan rationale,
and broker-bypass instructions. These are separate from the new UI route challenges.

The cloud verifier checked image pins, dedicated identities, six exact SQL grant
sets, private service connectivity, HTTPS, secure cookies/CSP, and anonymous API
denial. Browser checks covered desktop/mobile layout, explicit scenario creation,
healthy decisions, and partial-run receipt inspection. Browser fixtures were
removed. Completed rehearsal and challenge sandboxes are stopped and retained.

## Evidence Boundaries

- `agent-runtime` activity is sandbox-reported metadata, not independent attestation.
- Gateway logs record capability and Guardrails decisions. They are not OpenShell
  denials and are not yet merged into the console's SQL activity feed.
- OpenShell's OCSF records identify its actual network decisions. A DNS-denied
  failed rehearsal was diagnosed from this enforcing-layer log, not agent text.
- SQL receipts and the separate verifier establish database outcomes.
- Strict proposal schemas constrain identifiers/revisions from diagnostic SQL.
  The model selects operations and supplies reasoning; server code does not
  fabricate a successful decision or silently correct a rejected plan.

## Recovery And Availability

Reconnect observations reads the existing job; it does not start it again. An
uncertain run must not be replayed. Inspect execution receipts reports partial
progress and keeps the run halted. Only a complete exact receipt set can trigger
independent verification again. Partial repair needs a new investigation and
human-reviewed plan, not resumption of the consumed approval.

Jobs have durable at-most-once claims. There is no automatic recovery dispatcher
for queued jobs after a process restart; expired queued/running jobs become
uncertain. SQL serverless resume can delay the first connection. Do not repeat a
mutation whose response is unknown; inspect its persisted state first.

Scenario setup has bounded cold-connection recovery: only the dedicated simulator
client can attempt its initial SQL connection up to three times, with a 30-second
connection timeout per attempt and ten seconds between failed attempts. Procedure
execution still has its separate 15-second timeout, and the console allows 150
seconds for the scenario response.
Once a connection opens, the procedure is sent once. Execution, result-read, and
post-execution disconnect failures are never replayed. Other SQL clients retain
their single-attempt default. A prolonged outage can still fail after this budget.

During setup the UI displays Creating scenario and disables duplicate submission.
An unconfirmed response includes the scenario request ID without selecting a
scenario or starting Planning. Runtime logs on September 18 showed a scenario 503
at 11:17 UTC followed by a 201 Created at 11:26 UTC; that successful creation was
not repeated during the repair. The cold-connection recovery passed 739 Python
regressions and isolated browser delayed-success/failure checks with no real writes.

## Operator-Managed Availability

The invoice application remains available until explicit operator intervention.
Application uptime is no longer tied to agent authority lifetimes. Active invoice
dependencies, the public console, and the SAW host are tagged
`availabilityMode=operator-managed` and have no `expiresAt` tag. Invoice services
use `INVOICE_AVAILABILITY_MODE=operator-managed` with no service deadline. The
legacy global `learningnemo-saw-expire.timer` is disabled, not repurposed as a
per-run watchdog. The old timed renewal commands refuse to reinstall this timer.

The following controls are unchanged:

- Human JWT verification, roles, scopes, ownership, and independent approval.
- Thirty-minute draft submission and fifteen-minute approval windows.
- Short-lived per-run capabilities, call budgets, revocation, and single-use claims.
- Provisioning watchdogs, execution timeouts, and sandbox stop in cleanup.
- Exact-plan SQL enforcement, transactional receipts, and independent verification.
- One active sandbox, 24 retained sandbox maximum, and a 4 GiB disk-space floor.
- Network denies, private SQL, fixed model gateways, and NeMo Guardrails.

New sandbox admission requires a root-owned, non-writable-by-agent
`/etc/learningnemo/invoice-availability.json` with admission enabled. A missing or
paused gate fails closed. A running or enabled global timer also blocks managed
admission, preventing a mixed policy from being mistaken for readiness. This
policy does not automatically restart queued or uncertain jobs.

Use these workspace tasks:

1. **LearningNeMo: verify operator-managed availability** checks resource policy,
   the root-owned host gate, disabled global timer, and stopped sandbox inventory.
   Run it when no agent is active.
2. **LearningNeMo: pause managed sandbox admission** prevents new sandboxes.
   It refuses while any sandbox is active; let the bounded run finish or inspect
   its recorded outcome first. The web APIs remain available for review.
3. **LearningNeMo: explicitly shut down managed host** pauses admission and
   deallocates the existing VM, preserving all disks and sandboxes. It does not
   delete NAT, registry, SQL, or container apps.
4. **LearningNeMo: enable operator-managed availability** enables admission on an
   already-running retained host and reconciles the owned resource policy. It
   never starts an agent. After host deallocation, use a reviewed retained-boot
   procedure before running this task; do not boot the original destructive disk.

The transition saves before-state metadata and owner-only receipts under
`~/.local/state/learningnemo`. `invoice-availability.verified.json`,
`invoice-services.verified.json`, and `cloud-demo.verified.json` establish current
policy. Earlier lease reports are historical. The unused legacy human APIs and
repair-environment retention records are not part of this availability policy.

Live verification confirmed 36 resource/group policies, the root-owned enabled
host gate, a disabled global timer, and 21 stopped retained sandboxes. All five
invoice service revisions were checked for managed mode with no service deadline,
along with six exact SQL identity grant sets and private connectivity. The deployed
controller returned `availabilityMode=operator-managed`, `expiresAt=null`, and
verified runtime network policy. No scenario or agent was started to perform this
transition. The public tab remains signed out; a fresh human workflow is a separate
acceptance check, not implied by these infrastructure and authorization checks.

Costs continue while the application is available. Deallocating the host reduces
VM compute charges but does not stop storage, NAT, registry, container-app, or
other charges. The existing budget notifications are not a hard spending cap.
SQL retains its serverless pause/free-limit controls, so cold-start latency and
quota exhaustion can still affect availability. This policy is not an uptime SLA.

The September 18 scenario-creation 503 was traced to the expired
`INVOICE_EXPIRES_AT` lease, despite the container reporting Running. The deployed
middleware's exact lease-expired response was confirmed before route execution;
SQL was also paused and was resumed via a read-only connection check. The console
now preserves that specific reason: service renewal is required and that request
was rejected before execution. Other 503 responses remain unconfirmed and must
not be automatically replayed. Thirteen focused service/proxy/console tests passed.
The user's Operator session expired during recovery, so authenticated scenario
creation after renewal still requires a fresh human sign-in and was not claimed
as verified. No temporary identity or synthetic approval replaced the user.
An older unconfirmed challenge stays unconfirmed: renewal is not a denial receipt.
Reconnect reads that challenge's existing receipt. Deploy service updates with
LearningNeMo: activate invoice services, then deploy invoice console and verify
retained cloud release. A new revision may still be starting when ARM succeeds;
rerun only the read-only verifier, never the agent or an uncertain mutation.

The read-only invoice verifier is `infra/next-phase/verify_invoice_services.py`.
Owner-only receipts and image pins are under the WSL user's
`~/.local/state/learningnemo` directory. Do not read temporary credential files.

The host admits one active sandbox, retains at most twenty-four, and requires
at least 4 GiB free disk. The installed MicroVM driver does not enforce the
per-sandbox CPU/memory CLI flags. Simulator and coordinator have separate SQL
identities but share a process. This is an operator-managed hosted demonstration, not a
claim of unattended production availability or complete security certification.

No existing VM, disk, sandbox, snapshot, NAT, or user record was deleted.