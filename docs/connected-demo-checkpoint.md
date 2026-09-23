# Connected Demo Implementation Checkpoint

Status update: the SQL schema, private Incident/Review services, dedicated consent,
and dashboard integration are now deployed and verified at the service level.
See the [live checkpoint](human-handoff-live-checkpoint.md) for exact revisions,
real SQL checks, rollout corrections, and the scheduled availability window.
The full sandbox incident and two-human rehearsal remain incomplete. The
implementation notes below describe the earlier staging work and its contracts.

## Intended Demonstration

One Operator-sponsored SQL incident should produce an AgentRunner investigation
inside OpenShell, a persisted registered plan, an independent Approver decision,
bounded broker execution, and independent verification. Reader is an optional
permission comparison, not a required stage. Each transition needs evidence with
the same incident, engagement, workspace, logical-agent, and plan identifiers.

The existing role-based task tests, fixed sandbox probe, and scripted SQL cycle
remain separate. The review UI does not turn those into a connected incident.

## Implemented Locally

- Operator-to-Approver handoff through separate private APIs. A recorded draft
   is visible only to its sponsor and enters the review queue only after exact
   hash/version submission. Reader and Approver cannot submit investigations.
- TrustedInvestigationRecorder validates matching incident, engagement,
   workspace, agent, query-run, fresh diagnosis, and successful containment
   results. It retains canonical evidence documents and server-computed hashes.
   It is a producer-side API, not an endpoint accessible to browser users.
- Staged incident SQL records drafts and evidence atomically, reads only the
   sponsor's investigations, and uses locked version/state checks for submission.
   It neither issues approval receipts nor executes remediation.
- A dedicated non-root human-services image was built and digest-pinned in ACR,
   including import checks for both APIs and the SQL driver. Its dependencies
   are hash-locked. The OS packages installed during build are not version-pinned;
   the resulting runtime image digest is pinned, not a bit-identical rebuild claim.
- Private incident/review deployment template with separate identities,
   identities-only default, and fail-closed image/lease/ingress validation.
   Dashboard origins remain opt-in and restricted to the named private services.

- Real-service queue and exact-plan approve/reject routes through the console.
  The browser sends only decision, plan hash, and version. Identity remains
  derived from verified Entra claims, never a browser-provided actor field.
- One selected-plan inspector with explicit confirmation, registered operation,
  target, parameters, rollback, author binding, and deadline. Submitted-plan,
  human-decision, and broker-consumption states are distinct; consumption is not
  presented as verified recovery.
- Expired, self-authored, changed, unavailable, and empty-queue states. Controls
  clear on account changes and after uncertain decisions. There is no automatic
  decision retry or automatic remediation execution.
- Strict queue validation including content/hash equality, allowed operation
  and target, exact parameters, typed timestamps, and bounded plan count.
- Opt-in `plans.review` consent when a private review origin is configured.
  Configuring the URL does not register the scope or grant tenant consent.
- Stable human identity binding:
  `SHA256("entra-tenant-oid-v1:<tenant UUID>:<object UUID>")`, using signed `tid`
  and `oid` claims. Legacy scripted actor hashes are not equivalent identities.
- A private service entry point with managed-identity SQL access, process health,
  repository readiness, and an explicit admission lease of at most two hours.
- Safe cloud task diagnostics: request correlation and validation/workflow error
  categories without raw upstream prompts, tokens, or exception response bodies.

The staged review SQL adds nullable `CreatedByScheme` to ResolutionPlans.
Review reads and decisions require `entra-tenant-oid-v1`; existing rows are not
backfilled. A real human-bound incident producer must set this field when it
creates the plan, atomically with the corresponding verified author identity.
Do not mark scripted plans as human-authored to populate the queue.

## Verification Performed

The full repository suite passed 457 tests. Two upstream deprecation
warnings remain. Browser fixtures passed exact-plan confirmation, approve and
reject request shape, conflict handling, expired/self-review states, missing
service states, and responsive layout checks at 1440, 390, and 360 pixels.
The new two-person API and browser handoff checks preserved the same plan ID
and hash from Operator submission to Approver decision and back to Operator
status, without sending an execution request. They used an injected SQL client
or intercepted API responses, not a real SQL transaction or human login.
These fixtures were isolated from the cloud and sent no Azure commands.
Fixture routes were removed and the browser reloaded afterward; no test plan is
left displayed as a live record. A fresh local preview runs the current code at
`http://127.0.0.1:8767/`; without configured incident/review services it correctly shows
the unconnected state.

The existing cloud deployment verifier previously passed without any new service
deployment. This increment built a human-services image in ACR but applied no
new live SQL migration, managed-identity grant, consent, SAW start, or network
change.

SQL tests use an injected procedure client and a T-SQL parser. They do not prove
database permissions, live rollback behavior, concurrent decisions, or replay
handling across real processes. These remain release gates.

## Release Gates

1. Reproduce one authenticated Operator read through the current cloud agent.
   Capture its request ID and the same revision's logs before replacing it.
   An agent health response or direct model call is not equivalent evidence.
2. Restore approved sandbox connectivity under the runtime network rules. Pair
   a successful permitted request with a denied control. An HTTP timeout does
   not establish policy enforcement. Renew/start only a bounded owned SAW.
3. Connect a real sponsor-bound incident producer and AgentRunner. Persist its
   task/workspace/agent context and the canonical human author binding. No
   in-memory reference workflow or fabricated database row may stand in for it.
4. Use the separately built human-services image and the
   [handoff deployment contract](human-handoff-deployment.md). The dashboard
   image deliberately has no SQL runtime. Verify actual private-service
   readiness, least-authority database grants, and native SQL connectivity;
   build-time import checks do not establish those properties.
5. Version and apply the staged SQL through the reviewed migration mechanism.
   Use a dedicated contained database principal with EXECUTE on only
   `control.usp_list_review_plans` and `control.usp_decide_review_plan`. Do not
   grant direct table DML, `db_owner`, or direct `control.usp_issue_approval`
   execution to the reviewer service.
6. Register/consent `plans.review` for the intended client and configure private
   ingress plus the exact internal review origin. Do not add Task.Operator to
   the Approver account. Confirm readiness against the actual SQL schema.
7. Prove two independently signed-in people can submit and review the same plan.
   Test self-review, hash/version changes, expiry, concurrent decisions, unknown
   commit outcomes, and replay against real SQL, not just mocks.
8. Connect Operator resumption through the registered broker operation and
   independent verification. Display completion only from matching receipts.
9. Rehearse the complete cloud journey, capture a dated fallback, verify cleanup,
   and only then use the connected incident as the default interview screen.

## Review Runtime Configuration

Required environment: `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`,
`ENTRA_PUBLIC_CLIENT_ID`, `AZURE_CLIENT_ID`, `LEARNINGNEMO_SQL_SERVER`,
`LEARNINGNEMO_SQL_DATABASE`, and `LEARNINGNEMO_REVIEW_EXPIRES_AT` (timezone-aware,
future, at most two hours from startup).

The entry point is `python -m task_agent.console.review_service`; it requires
the managed-identity SQL driver and private SQL resolution. `/healthz` means
process liveness only; `/readyz` reads the review repository and returns no plans.
Admission after lease expiry is denied. The lease does not delete or deallocate
resources; scheduled cleanup is a separate obligation.

Only after the release gates should the dashboard be configured with
`LEARNINGNEMO_REVIEW_ORIGIN`, an exact internal Container Apps HTTPS origin.
Do not set that variable on the running demo merely to remove the unavailable
message. Without an enabled service, the deployed Approver remains status-only.