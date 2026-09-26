# LearningNeMo Capability Demonstration

## The Story

> An AI agent can propose an operation, but it cannot grant itself the authority
> to execute it. LearningNeMo separates model behavior, caller authorization,
> sandbox policy, and approval-bound execution, and makes the results inspectable.

Audience: engineering interviewers evaluating agent security, cloud design,
operational judgment, and reproducibility. Target length: eight minutes, with
an optional deeper source/evidence review. Use the existing console, a terminal,
and this page. No new dashboard or simulated integration is required.

For setup and teardown, use the [build guide](build-and-reproduce.md).

## Start With the Pattern

The site opens on **The pattern**, not the status table. Lead with the use case:
"Resolve an Azure SQL incident without making the agent an administrator."

1. Follow the five boundaries: human identity, AgentRunner delegation,
   SAW/OpenShell, approval/broker, and verified Azure state.
2. Select a boundary. Explain its risk, the design decision, and the specific
   observation that would prove the control works. The panel also states what
   is implemented or still pending.
3. Use the containment diagram to distinguish the SAW envelope (managed
   lifecycle, perimeter, and trusted services around a private workspace VM
   that runs the website's agent) from the inner OpenShell process and route
   boundary. Guardrails inspect
   content; they do not replace either boundary.
4. Compare Reader, Operator, Approver, and AgentRunner in the persona-design
   selector. These are target permissions, not a way to change the logged-in
   user. Approver and AgentRunner integration remain explicitly pending.
5. Choose a proof: live workspace status/probe, the separate task authorization
   walkthrough, or dated sandbox evidence. These buttons navigate only. They
   never sign in, execute a command, or start a walkthrough automatically.

The architecture page uses public design descriptions, not private runtime
data. Solid boundaries denote implemented components; dashed boundaries denote
pending integrations. Arrows describe the target workflow, not a completed
execution trace. Do not claim novelty or full security from the diagram alone.

## Live Identity-to-Workspace Proof

The **Live proof** view is separate from architecture and historical evidence. Start the
local console with explicit operator-transport opt-in:

```bash
"$HOME/.venvs/nemo-agents/bin/learningnemo" --no-browser --port 8766 \
  --workspace-subscription "$AZURE_SUBSCRIPTION_ID"
```

The subscription must be the intended existing demo subscription. Without this
option the live transport is disabled. It never accepts a resource ID, hostname,
shell command, or script from the browser. The local Azure CLI account must have
the deployment-operator permissions for the fixed demo resources. This is not
delegated Azure access using the signed-in browser user's token.

1. **Check cloud status** makes fresh read-only VM, NSG, stack, and subnet queries.
   Each observation has a timestamp and becomes stale after 60 seconds. It does
   not establish guest or sandbox liveness.
2. **Sign in** uses the existing Entra device flow. Keep device codes and tokens
   off screen when recording. Never enter credentials into chat.
3. **Run workspace proof** verifies the token signature, issuer, audience, client,
   subject, lifetime, and maximum age. The fixed demo action requires
   `agent.invoke`, `tasks.execute`, `Task.Reader`, and `Task.Operator`.
   A verified Reader receives a denial before any workspace command runs.
4. An authorized request rechecks the live workspace lease and network boundary,
   then uses Azure Run Command and OpenShell SSH to run one fixed script inside
   `planning-demo`. The script reads its UID, attempts the diagnostic GET and
   forbidden POST, and checks that `setuid(0)` fails. It does not call a model,
   change policy, restart the gateway, create sandboxes, or remediate SQL.
5. The result contains a per-request run ID, identity decision, observed UID and
   HTTP codes, completion time, script hash, and explicit transport provenance.
   A timeout is **unreachable**, not a successful policy denial. Export is a
   receipt of that request, distinct from historical-summary export.

Current qualification from 2026-09-15: the runtime-lock deployment and independent
host verification now pass. A real operator-transport test returned UID 998 and
failed privilege escalation inside Planning. Both API probes returned no HTTP
response after NAT removal. The live proof therefore reports a failed downstream
route, not a complete successful flow. Actual browser-user sign-in is required
to establish the full user-bound proof; backend authorization paths have automated
coverage, but a new human sign-in was not impersonated during development.

Approved runtime API connectivity and the model/approval/SQL incident integration
remain work to complete. Do not weaken egress rules, extend expired leases by
editing evidence, or present the fixed probe as a complete agent incident cycle.

## Interactive Console

The other top-level views are:

- **Demo session:** the same destination for all three accounts. The account
   bar shows the verified role and next action on every view. Reader previews
   three steps, then tests a denied write and unchanged task state. Operator
   previews four steps, then resets disposable tasks, executes task-1, and
   verifies the changed state. Previewing or navigating sends no task requests.
   Approver sees review-service availability, not an invented waiting plan;
   queue and submission are not connected. These are separate account paths,
   not a Reader-to-Operator-to-Approver sequence. Use Switch account to clear
   the demo session, then choose the other account at Microsoft sign-in.
- **Recorded evidence:** select Planning, Execution, or Probe in the topology; select
   a recorded request to compare its expected and observed outcome. Expand
   Tradeoff or Evidence source to inspect the qualification. Export selected
   evidence produces a dated JSON summary, not a raw trace or new test result.
- **Build & decisions:** select an architecture decision to inspect its choice,
   rationale, cost, and supporting evidence. Open the build guide, this runbook,
   or the diagnostic record in the document reader.

Recorded evidence also includes five selectable security-story stages. Previous/next
controls only navigate the explanation; they do not execute an incident.
Keyboard arrows navigate tab groups; Escape closes the document reader.

The showcase data comes from the reviewed, non-secret
[presentation manifest](../src/task_agent/console/showcase.py). It is a curated
summary of the engineering record, not an import of current cloud state. The
read-only endpoint makes no Azure calls and reads no private credential files.
Its recorded failure and pending states remain visible even when the local
agent is online. The normal console server still requires valid local Entra
settings; "read-only" does not mean a separate authentication-free server.

For the interview, establish the pattern first, then use Live proof for actual
status and permission decisions. Use Recorded evidence for historical policy
comparisons and Build & decisions for the build record. Demo session remains a
separate task-agent demonstration, not an OpenShell incident workflow. Do not
describe diagram or persona selections as live commands or identity changes.

The task test displays expected access before execution and observed results
after each request. A failed check stops subsequent steps. Technical claims,
manual prompts, and request history are in expandable sections beneath the test.
Switching accounts clears prior results; switching is disabled while a task or
workspace request is in progress. Browser fixture tests validate these UI states
but do not establish that the cloud agent answered an actual user request.

## Claim Boundaries

The current demonstration has three distinct parts. Do not join their timelines
or imply shared execution that has not been implemented and verified.

| Part | What can be demonstrated | What must not be inferred |
| --- | --- | --- |
| Local console | Entra-derived Reader/Operator behavior, tool authorization, state read-back | The console task agent is running in OpenShell |
| OpenShell | Recorded successful bootstrap of three MicroVMs and route/boundary probes | Full host lockdown or an authenticated SQL remediation from the sandbox |
| Trusted incident cycle | Recorded, independently verified approval-bound SQL workflow | An interactive human approval UI or a sandbox-originated incident cycle |

As of the 2026-09-14 investigation, runtime-lock deployment and final independent
SAW verification remain incomplete. A fresh-host reproduction is also pending.
The next integration milestone is an actual sandbox-to-broker incident journey,
not a visual animation connecting the separate demonstrations.

Use these labels in slides, terminal headings, and any recordings:

- **Live:** executed during this session, with an observed result.
- **Recorded:** a dated previous result, bound to the corresponding release.
- **Configured:** source or policy intent, not execution proof.
- **Pending:** missing integration or failed acceptance gate.

## Before the Interview

1. Choose live or recorded mode for each part and label it before presenting.
2. Check the selected subscription, resource health, and remaining leases. Leave
   enough time for rehearsal and cleanup; never race an expiry timer.
3. Run the local workspace gates and validate the selected incident evidence.
4. Start the local API and console using the build guide. Rehearse both accounts
   in separate browser sessions. Sign in before screen recording begins.
5. Reset only disposable task/incident state using supported workflows. Do not
   recreate sandboxes or restart the gateway as an interview reset.
6. Keep private parameter files, credentials, browser login codes, and raw logs
   off screen. Share selected sanitized proof, not the entire state directory.
7. Record a short fallback of the actual rehearsal. Keep its date and limitations
   visible; never present playback as a live run.

Do not begin a full ACR build or multi-stage Azure deployment in the interview.
Show its recorded evidence, source, and locally runnable gates instead.

## Eight-Minute Presenter Script

### 0:00-0:45: Establish the Boundaries

Say: "There are two different questions: what the model proposes, and what the
system permits. I test the second independently of the first."

Show the three demonstration parts above and the current acceptance status.
Explain that the workspace VM inside the SAW envelope is private and runs only
the website's agent sandboxes, the OpenShell gateway uses mTLS, and each
sandbox has a different policy. Show the
[three policy files](../infra/next-phase/openshell) as configured intent, then
move to behavioral evidence.

### 0:45-2:30: Same Client, Different Authority

Use the existing console at `http://127.0.0.1:8765`:

1. In the Reader session, show the derived role and scopes, then capture task state.
2. Run the built-in denied-write step. Show the authorization decision and
   authoritative unchanged state, not merely the model's refusal text.
3. In the Operator session, run the built-in reset, pending-state, execution,
   and read-back steps. Show the actual state change.

Expected: Reader cannot write even though its token may contain `tasks.execute`;
the API also requires `Task.Operator`. Operator can execute and read back the
result. Never choose a role in a UI dropdown or copy one user's token into the
other session. The console derives the persona from verified claims.

Say: "The same client and delegated permissions do not give both users the
same authority. The tool checks both scope and role."

Optional: run one built-in guardrail probe and inspect its outcome. A model
self-check is probabilistic input screening, not a replacement for authorization.
Do not rely on an improvised prompt producing a specific response.

### 2:30-4:30: A Sandbox Boundary Holds

Show selected, sanitized bootstrap evidence and the matching policy file.
The successful preserved-VM run was recorded on 2026-09-14; its locations are
listed in the [diagnostic record](openshell-bootstrap-diagnostic-record.md).

| Attempt in the bootstrap probes | Expected observation | Interpretation |
| --- | --- | --- |
| Planning GET `/v1/diagnostics/current` | HTTP 401 | Network policy allowed the route; trusted API still requires authentication |
| Planning POST on that route, or GET on an unapproved path | HTTP 403 | The requested method/path was denied |
| Execution POST `/v1/remediations/execute` | HTTP 401 | Permitted broker route was reached without granting anonymous execution |
| Execution GET on that route, or POST on an unapproved path | HTTP 403 | Execution policy remains narrowly scoped |
| Probe Internet, IMDS, and SQL attempts | Probe command completes with denied-access checks passing | No successful access was observed for these tested destinations |
| Probe host paths/sockets, `/etc` write, and `setuid(0)` | Boundary checks pass; process is non-root | Tested host/privilege boundaries hold |

The `401` versus `403` distinction is the central teaching moment. Neither
status proves that an approved SQL operation executed. A network timeout alone
also cannot identify which enforcement layer blocked a request. Pair negative
results with working sandbox execution and the permitted-route control.

Relevant bootstrap milestones are:

```text
PASS openshell_gateway_authenticated
PASS openshell_three_microvm_sandboxes
PASS openshell_planning_execution_separation
PASS openshell_probe_denials
PASS saw_maximum_lifetime_armed
PASS saw_openshell_ready
```

These are bootstrap milestones, not proof that the later host runtime lock
succeeded. Do not rerun the whole bootstrap to produce them during a demo: it
restarts the gateway and replaces the named demo sandboxes.

For a fresh, read-only Azure status check in WSL, inspect only selected fields:

```bash
az vm show --resource-group rg-learningnemo-saw-dev \
  --name vm-learningnemo-saw-dev --show-details \
  --query '[name,provisioningState,powerState,tags.expiresAt]' --output json
az stack group list --resource-group rg-learningnemo-saw-dev \
  --query '[].[name,provisioningState]' --output json
az network vnet subnet show --resource-group rg-learningnemo-saw-dev \
  --vnet-name vnet-learningnemo-saw-dev --name snet-workspace \
  --query 'natGateway != null' --output json
```

VM `running` and NAT association `false` are useful observations, but not a
substitute for the independent workspace verifier. If a stack is failed or a
lease expired, show Recorded mode. Do not silently remove the failed step.

Say: "The sandbox can reach only its intended route, and reaching that route
still does not grant application authority. These are separate checks."

### 4:30-6:30: Verify an Incident Outcome

Switch explicitly to the separate trusted-service incident cycle. Explain that
the current cycle is a scripted control job, not a human approval screen and
not yet an OpenShell-originated workflow.

Validate the preserved result in WSL:

```bash
state="$HOME/.local/state/learningnemo"
python3 infra/next-phase/verify_control_cycle_evidence.py \
  --manifest "$state/control-cycle-dev.result.json" \
  --release-attestation "$state/trusted-runtime-dev.release.json" \
  --image-reference-file "$state/trusted-runtime-dev.image.txt"
```

Expected: both preserved-evidence checks pass. Show the verified eight-stage
summary, recorded timestamp, source revision, and image digest. Walk through
diagnosis, owned-query containment, separately represented approval, bounded
remediation, and verification of the final SQL state. The verifier checks the
manifest and release binding; it does not rerun SQL or rehash unavailable raw
artifacts merely because their hashes appear in the manifest.

If an image rebuild changed the release binding, use the corresponding archived
release bundle or perform a new authorized rehearsal. Do not alter old evidence
to make it match a newer image.

For an optional new rehearsal, only in the disposable demo environment after
dependency verification and explicit acceptance of SQL state changes:

```bash
bash infra/next-phase/verify-wp3.sh
bash infra/next-phase/deploy-control-cycle.sh --what-if --ttl-hours 1
LEARNINGNEMO_AZURE_APPLY=wp3-control-cycle \
  bash infra/next-phase/deploy-control-cycle.sh --apply --ttl-hours 1
```

The apply command exercises the controlled incident, writes evidence, and removes
its one-shot compute on success. It is not a read-only status check. Preserve a
previous rehearsal's matching evidence bundle before replacing current results.

Say: "Completion comes from the deterministic workflow and verified database
state, not from the model saying it is done."

### 6:30-8:00: Show Engineering Depth

Show the [build guide](build-and-reproduce.md) and one short local check:

```bash
bash infra/next-phase/test-workspace.sh
```

Expected: local shell, Python, policy, and Bicep checks pass. Describe these as
local gates; they do not prove cloud runtime correctness.

Show one debugging example from the diagnostic record: `execve` failed because
the non-root process could not traverse an overlay root created with mode
`0700`. A controlled A/B changed traversal permissions and demonstrated the
cause. The reproducible bootstrap fix sets the gateway service umask while
retaining explicit protection for key material.

Close with the remaining work: supported host DNS/runtime-lock rules, independent
SAW verification, clean-host reproducibility, and a verified sandbox-to-broker
incident journey. Naming those boundaries is part of the engineering result.

## Evidence Checklist and Handoff

Prepare a review-safe bundle containing:

- A short recording with dates and Live/Recorded labels.
- This runbook and the build guide.
- Selected policy and denial evidence from the successful sandbox attempt.
- The incident manifest with its corresponding release-binding evidence.
- Local gate results, source revision, and current known limitations.
- A cleanup record after the rehearsal window ends.

Keep signing private keys, passwords, SSH material, generated private parameters,
access tokens, and login codes out. Sanitized diagnostics can still reveal
operational context; inspect excerpts before sharing. Use the allowlisted review
archive procedure in the build guide for source distribution.

## Questions to Be Ready For

| Question | Answer to substantiate |
| --- | --- |
| Why not trust prompt instructions? | Model output cannot confer tool permission; show a denied tool call and unchanged state |
| Why both Entra and OpenShell? | Caller authority and process/network isolation address different boundaries |
| Why is 401 a useful test? | It shows route reachability without bypassing the API's authentication gate |
| Is this production-ready? | No; list the failed/pending acceptance gates and pinned-release recovery limitations |
| Is approval independently human-driven? | The current incident cycle represents separate approval actors in a scripted workflow; interactive human approval is not demonstrated |
| Does expiry stop billing? | Guest poweroff and tags are not Azure resource deletion or guaranteed deallocation; scoped cleanup is required |
| Can another engineer reproduce it? | Show pins, scripts, private-state prerequisites, verifiers, and the still-pending clean-host acceptance result |

If a live step fails, state the failure, retain its evidence, and switch openly
to the dated recording. Do not weaken a policy, expose a public SSH port, or
restart the gateway to keep the presentation moving.