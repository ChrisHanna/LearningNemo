# Guided Security Demonstration

> **Archived dated record.** This describes the state on the date it was recorded, not current status. See [Status](../status.md) and the [archive index](README.md).

## Current Workflow

The deployed screen is **Analyze -> Propose -> Approve -> Execute -> Verify**.
The private Execution API and its dashboard connection are now deployed. The
older "Execution service not connected" message was caused by a missing service
deployment and an empty dashboard execution origin, not by the approval decision.

After approval, sign in as the original Operator, choose **Continue to execution**,
then **Check execution availability**. **Execute approved plan** sends only the
registered, approved change. Inspect the independent verification results, then
choose **Acknowledge completion**. Navigation and approval do not execute a change.
An uncertain response requires reading persisted status and reconciliation;
reconciliation never repeats the remediation write.

Existing plans and approvals are preserved. Expired authority is not extended by
deployment or sign-in. If an unexecuted plan has expired, run a fresh analysis,
create and submit its draft, and obtain a new independent approval. The original
Operator must execute; the Approver cannot execute or approve their own proposal.

Analysis reads diagnostic data and writes audit metadata only. It cannot start or
cancel queries, kill processes, or change database configuration. An active query
blocks proposal creation. Incident has five fixed SQL procedure grants; its two
legacy initiation grants and query-runner access have been revoked. Review and
Execution use separate identities with two and five fixed procedure grants.
Direct table reads, approval issuance through the execution identity, and direct
remediation procedure access from these API identities are denied.

Planning isolation is optional under **Security evidence** and has a separate
workspace lease. It is not a prerequisite for database analysis. Diagnostic reads
are currently a deterministic trusted-service operation, not a model executing
inside Planning. No complete sandbox AgentRunner integration is claimed.

Verification: 634 Python tests, six focused Node workflow tests, additive SQL
migration receipts verified across connections, and a rollback-only SQL workflow
rehearsal passed. All three private APIs passed live identity, SQL permission,
readiness, and worker-boundary checks. Local intercepted browser tests verified
approved-plan execution/completion and expired-plan analysis/proposal/submission;
all fixtures were removed. These checks did not execute the user's real plan or
impersonate an Approver. Fresh human sign-in and the real execution remain user
actions. Service leases are bounded; consult live status rather than old times.

## Historical Six-Step Release

The following notes describe previous releases, not the current workflow or
current service availability. The current workflow above supersedes them.

The default dashboard is one screen, not five separate demonstrations.
Sponsor, Contain, Investigate, Review, Execute, and Verify share a boundary
diagram and a receipt inspector. One workflow action changes with the actual
state: Sign in, Check workspace readiness, Run containment proof, then Continue.
Future steps remain locked until their prerequisites are present. Selecting an
available step is navigation only; it does not execute a request or mark it
complete. Architecture, historical records, build decisions, and the separate
task-agent diagnostic are in the Technical evidence drawer.

## Operator Readiness

The Investigate step now offers **Start investigation** when the private Incident
API advertises initiation. The service verifies the Operator's Entra identity and
`tasks.execute` scope, admits one bounded incident, starts and observes its owned
controlled query, cancels that query, and persists a draft for the same sponsor.
Inspect the draft and explicitly submit it before switching to the Approver.
The containment proof does not create a plan automatically.

This producer is `trusted-fixed-investigation-v1`: a deterministic trusted-service
workflow, not a model running inside Planning or a completed AgentRunner delegation
integration. Its proposed operation is fixed to `cycle-safe-v1`. No approval or
remediation happens during initiation. Execute and Verify remain unconnected.

The request ID is retained after an uncertain response. Repeating it cannot start
another query; the durable admission returns the recorded plan or reports that the
request is in progress and needs reconciliation. A shared-lab lock rejects another
unexpired investigation or active query before resetting the demo query version.
The admission window is at most 30 minutes and cannot exceed the API lease.

The deployed additive migration is `human-004_incident_initiation.sql`; the earlier
migration hashes are preserved and execution migration 003 is still unapplied.
Incident has exactly four EXECUTE grants (begin, record, list, submit), with no
table SELECT, approval, or remediation permission. Only diagnostic and query-runner
accept its managed identity; remediation and verifier authorization are unchanged.
The migration job is retained with no identity, environment, registry credentials,
or runnable migration command. The SQL administrator's Regional scope is restored.

Live checks verified SQL admission and duplicate blocking within rolled-back
transactions, exact SQL privileges, and managed-identity diagnostic reads. The
browser fixture verified one initiation request, draft display, explicit submission,
and review gating; its routes were removed afterward. These are not a real-user
incident rehearsal: the shared Entra session expired and a fresh sign-in is required.

Runtime recovery on 2026-09-15 restored the same private SAW VM from a repaired
copy of its OS disk. The original disk and rollback snapshot are retained. The
host is running with runtime NAT restored, all eight runtime NSG rules restored,
and a verified stop-only expiry timer through 2026-09-16 00:49:39 UTC. All three
original sandbox IDs were restored through normal OpenShell startup. Planning,
Execution, and Probe now have live boundary proofs; these are not a full incident.
The earlier UI checks below are historical observations, not current VM status.

Planning proof `8bc8b6980ae04d92aa81b54976171cf5` completed at 23:12:31 UTC:
UID 998, diagnostic GET 401, forbidden POST 403, privilege escalation denied.
Boundary proof `646b7ef7847f460c82fc13be11e95948` verified all three original
IDs Ready, Execution GET 403 and POST 401, and Probe GET/POST CONNECT 403.
Both additional sandboxes ran as UID 998 with escalation denied and Azure DNS
configured. Probe intentionally did not reach an upstream endpoint. No workload
credential, model invocation, SQL remediation, or approval was part of these checks.

Initial allowed requests timed out before subsequent requests succeeded. The
Planning proof now retries only its credential-free GET once after a transport
failure, within the existing overall timeout. POST is never retried. Both read
attempts are logged, and two failures still fail the proof. A unit test executes
the generated guest script against transient and persistent timeout fixtures.
The controller change was built, digest-pinned, deployed, and independently
verified. Existing agent and human-service image pins were preserved.
The shared browser session was expired; a fresh real Operator sign-in is required
to verify the browser's complete authenticated path.

The 2026-09-15 17:45 UTC live read-only check returned a stopped SAW VM,
expired lease, intact runtime rules, and `readyForProbe: false`. This is an
environment blocker, not evidence that an Operator role was rejected. The
dashboard previously offered Run before checking this state. It now shows a
specific stopped-workspace message and only offers Recheck, never execution.
That UI-only change did not restore the VM; the later runtime recovery described
above did. The preservation and credential lifecycle requirements still apply.

The 18:00:43 UTC read-only check confirmed the same stopped VM and expired
15:02:18 UTC lease. Observation freshness is now separate from lease expiry:
a successful recent check displays **Fresh Azure observation** even when the
lease is expired. After 60 seconds it becomes **Observation out of date** and
the last-known blocker remains explicitly historical in the next-action text.
Rechecking does not renew the lease or start the VM. This display correction
was verified against live Azure state, covered by 12 combined Node observation
and workflow tests, and deployed with cloud-release verification. No VM start,
sandbox execution, or resource cleanup was performed.

An already signed-in Operator opens at the workspace step. The old automatic
demo-task walkthrough no longer sends background requests or overwrites the
guided instructions. After a successful proof, a missing investigation is
offered through the explicit initiation action when configured; the page cannot
advance to Review without a submitted plan.
The task-agent diagnostic is intentionally not part of incident progression.

Verification of this change: 7 Node workflow tests and all 565 Python tests
passed. An isolated browser test verified one action, zero proof calls while
stopped, exactly one proof call after successful readiness, and no progression
past a missing investigation. All simulated API calls were intercepted and the
fixture was removed afterward; these results are UI tests, not a live incident.
The console-only release was deployed and its cloud identity, image, HTTPS,
and anonymous-access checks passed. Existing agent and human-service images
were preserved; no resource teardown or VM restart ran.

## Presentation Sequence

1. Sponsor: sign in with a real assigned account. The account bar stays visible.
2. Contain: inspect cloud state, then run the fixed Planning proof. The same
   diagnostic endpoint should return GET 401 and POST 403, with UID 998 and
   privilege escalation denied. The receipt is a containment result, not an
   incident investigation or remediation.
3. Investigate: choose Start investigation, then inspect the sponsor's persisted
   draft and exact plan. Confirm Submit for review to request an independent decision.
   The separate agent connection check has a fixed task-list prompt and
   request-scoped read-only authorization enforced in the agent middleware.
   Even an Operator diagnostic cannot call task mutation tools.
4. Review: switch to the actual independent Approver account. Review-specific
   authorization, the exact plan, confirmation, and decision stay on this screen.
5. Execute and Verify: currently unavailable. They must not be presented as
   completed or enabled until the sandbox AgentRunner, coordinator, persisted
   execution reconciliation, and independent verification are integrated.

The central presentation is the boundary decision plus its evidence, not an
animation of unobserved activity. The SAW diagram identifies the private host;
OpenShell identifies the constrained process and method/path policy. Approval,
SQL credentials, and the execution broker remain outside agent authority.

## Approver Consent

On 2026-09-16, Entra sign-in logs confirmed AADSTS90094 for the dedicated
Approver's review-only flow. Initial sign-in succeeded; the request for
`agent.invoke` and `plans.review` required admin consent even after the personal
grant contained both scopes. The per-user repair alone did not resolve the prompt.

The API's existing preauthorization for client
`6693c101-6f5c-4039-87da-d36bed79f9f6` now also includes only the missing
`plans.review` permission. This is client-specific consent, not per-user consent:
users of this client can request that scope without the consent prompt. Actual
review still requires the independently assigned `Task.Approver` role, and the
server rejects self-approval. No roles, directory permissions, execution scope,
other preauthorized clients, or client requested-permission declarations changed.
The previous configuration was backed up and the Graph read-back verified.

The opt-in configuration command is `infra/configure-human-consent.py
--preauthorize-review --apply`, with
`LEARNINGNEMO_AZURE_APPLY=review-client-preauthorization` and the pinned
subscription. The default command continues to manage the personal grant only.
Do not grant all permissions listed on the app registration. Its existing
display name, LearningNeMo Local Client, is also used by the cloud dashboard.

After changing consent, close the failed Microsoft request and start a fresh
sign-in as `learningnemo-approver@christopherghannagmail.onmicrosoft.com`.
An actual Approver session subsequently authenticated and read the SQL-backed
review queue successfully, confirming the consent repair.

The console now requests `plans.review` alongside the existing task scopes in
the initial sign-in when Review is configured. A new Approver session therefore
does not need a second Authorize review access login. The account's verified
roles still decide authority: requesting a scope never assigns Task.Approver or
Task.Operator. The older incremental-review endpoint remains for pre-update
sessions missing the scope and does nothing when the scope is already present.

Pending sign-in displays an enabled Continue sign-in action that reopens the
same device code and polling loop. Cancellation also clears server state after
a page reload. Expired device requests cannot authenticate from a late response.
Local browser tests verified one start, no second review login, cancellation,
and desktop/mobile layout. The deployed cloud request verified all four initial
scopes, an enabled Continue sign-in, code reuse, and cancellation. A completed
human sign-in under the new single-flow release still needs user interaction.

All 630 Python tests passed, with two upstream deprecation warnings. The console
image now imports both cloud entry points at build time: its execution wire
contracts no longer import the deliberately excluded trusted control package.
The corrected cloud release passed image, identity, and anonymous-denial checks.
Incident and Review were renewed with their existing images through
2026-09-16 03:26:10 UTC; no execution service rollout or resource deletion was
part of this login fix.

## Agent 422 Root Cause

A real local Operator request reproduced HTTP 422 on 2026-09-15 at 15:32 UTC,
request ID `d08ffcdcbc474122a6adb820f9334abc` in the agent log. Authentication and
local PII masking passed. The APIM semantic classifier returned HTTP 401, which
NeMo wrapped as a workflow 422.

The Key Vault credential worked in a direct APIM probe. Loading the config and
calling the middleware directly also worked. NeMo 1.9.0's FastAPI launcher calls
`full_config.model_dump(mode="json", by_alias=True, round_trip=True)` before
handing configuration to its worker. This redacts Pydantic SecretStr values.
The worker therefore received a masked classifier credential.

The configuration now carries `api_key_env: OPENAI_API_KEY` instead of a secret
value. The middleware resolves that environment reference inside the worker.
Serialized configuration contains no credential. Missing, unresolved, and
redacted values fail before HTTP. The actual JSON handoff is regression-tested;
a live APIM probe using the round-tripped worker config returned a safe verdict.
The original human session expired before the full-agent retest; that retest
requires a fresh sign-in and is not claimed by the isolated classifier check.

## Resources Are Held

The owner requested no deletion until testing is complete. No resource teardown
is authorized by this runbook. A private `testing-retention.json` hold is active;
the workspace teardown and runtime-NAT cleanup paths refuse cleanup while it
exists. Resource retention is separate from lease validity or running state.

The installed expiry handler now stops expired workloads without deleting
sandbox disks or powering off the host. Before boot, a private maintenance VM
repaired a snapshot-backed disk copy and disabled the expired persistent timer.
A disk-bound receipt gates startup; renewal installed and verified the new timer
before updating the lease tag. The original VM name, network, and identity remain.
The original OS disk, snapshot, repaired disk, and deallocated maintenance VM are
retained. The maintenance NAT, public IP, and disks still incur standing charges.
The original disk retains the old handler and must not be booted unmodified.

The current cloud services were renewed under the existing bounded lease
contract. Expiry does not delete their Azure resources, and standing charges
can continue. A retained resource does not imply an indefinitely available API.

## Runtime Recovery

The retained Execution instance failed supervisor authentication after restart.
OpenShell 0.0.116 reuses the original token in its persisted driver request;
refreshing the supervisor's in-memory token does not update that bootstrap
credential. See upstream [issue 1603](https://github.com/NVIDIA/OpenShell/issues/1603)
and [PR 1721](https://github.com/NVIDIA/OpenShell/pull/1721).

The upstream local-driver resolution uses non-expiring supervisor JWTs
(`ttl_secs = 0`) for single-user local gateways. It is not human token renewal
and has no individual-token revocation. This demo's explicit 900-second setting
was not replaced with that policy. The host-admin recovery now verifies each
original token signature, issuer, audience, sandbox ID, and 900-second lifetime
before issuing a fresh 900-second bootstrap credential with the existing gateway
key. It backs up the original driver records and markers, repairs the stopped
overlay resolver to Azure DNS, and preserves sandbox IDs. This is an explicit
administrator repair, not acceptance of expired tokens or a sandbox capability.
The repair-only protobuf archive is checksummed and staged without new egress.
OpenShell's canonical Ed25519 PKCS#8 v2 key encoding has a strict compatibility
loader for the older host crypto library. A bounded mTLS listener check prevents
querying the gateway before it is ready after systemd startup.

The gateway persists Error independently of the stopped VM driver state. Version
0.0.116 rejects both stop and start from Error. Recovery therefore backs up the
gateway SQLite database, verifies the exact three stopped records and retained
markers, and transactionally reconciles Error to Stopped while preserving opaque
spec/policy bytes and policy versions. Stale process exit state is cleared and
resource versions advance. Ready is never assigned by the repair; normal startup,
policy-load acknowledgement, and live probes must establish it. The backup and
original failure evidence are retained. Ready/deleting candidates fail closed.

The current repair is an explicit administrative procedure, not an automatic
OpenShell restart fix. A later stop/start after bootstrap expiry requires fresh
rebootstrap. Do not substitute an unrestricted restart or a non-expiring JWT.

From the repository in WSL, the bounded procedure is:

```bash
export AZURE_SUBSCRIPTION_ID=40dbf703-f68a-4ca1-b315-c37f3308c38b
LEARNINGNEMO_AZURE_APPLY=stage-rebootstrap-dependency \
   /home/aygul/.venvs/nemo-agents/bin/python infra/next-phase/stage_rebootstrap_dependency.py
LEARNINGNEMO_AZURE_APPLY=resume-owned-sandboxes \
   /home/aygul/.venvs/nemo-agents/bin/python infra/next-phase/resume_workspace_sandboxes.py --rebootstrap
/home/aygul/.venvs/nemo-agents/bin/python infra/next-phase/run_workspace_proof.py
/home/aygul/.venvs/nemo-agents/bin/python infra/next-phase/run_retained_boundary_proofs.py
```

The dependency staging step is only needed on a host without the verified archive.
The VM/runtime dependency leases must be valid first. Do not run rebootstrap
against actively executing workloads: the host command rejects open overlay disks.
Each attempt keeps owner-only evidence under the local LearningNeMo state directory.
Only the attempt-owned registry exception and hosts pin are removed after startup;
the VM, sandboxes, NAT, disks, and rollback resources remain retained.

## Verification

- All 606 repository tests and 13 Node workflow/observation tests passed, with
   two upstream Python deprecation warnings.
- Live classifier with JSON-round-tripped worker config succeeded.
- Browser checks cover one-screen navigation, no execution on step selection,
  anonymous controls, keyboard focus, Escape dismissal, and 390/1440px layout.
- Live cloud checks verify image pins, HTTPS, private service placement, managed
  identities, anonymous denial, and private Incident/Review SQL privilege denials.
- All three retained sandboxes passed live boundary checks. This is not yet a
   complete incident workflow or a fresh human Operator browser rehearsal.

Latest deployment verification passed after renewing the cloud environment window
to 2026-09-16 00:54:09 UTC. Incident/Review revision 8 passed live SQL readiness
and privilege-denial checks. Their own admission deadlines remain separate from
resource retention; no indefinite API availability is promised by the hold.
The final cloud page loaded in Azure mode, showed one sign-in workflow action,
had no horizontal overflow, and denied anonymous workspace access with HTTP 401.
Pointer/keyboard and responsive checks passed locally; pointer actions in the
hidden shared cloud-browser tab timed out before dispatch, so they are not
claimed as passing cloud interaction tests.

Updated local services remain running at `http://127.0.0.1:8768` (console) and
`http://127.0.0.1:8001` (NeMo). The original local console was not replaced.