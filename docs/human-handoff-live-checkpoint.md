# Live Human Handoff Checkpoint

Later recovery work and final SAW/NAT cleanup are recorded in the
[runtime recovery checkpoint](runtime-recovery-checkpoint.md). Its staged
execution coordinator is not part of the deployed release described here.

Verified 2026-09-15 at 05:48 UTC. This is a dated deployment checkpoint, not a
claim of a complete production-ready incident workflow.

Dashboard: https://ca-learningnemo-dashboard-dev.jollybeach-503c7ed1.eastus.azurecontainerapps.io/

## Deployed and Verified

- Dashboard/controller revision `0000008` connects to the exact internal
  Incident and Review endpoints. The cloud task-agent revision remains `0000002`.
- Incident and Review revision `0000001` run in `rg-learningnemo-human-dev` with
  separate managed identities, internal-only HTTPS ingress, and pinned images.
- Both APIs returned HTTP 200 from SQL-backed readiness over the internal HTTPS
  routes. Anonymous data requests returned 401; execution endpoints returned 404.
- The two additive migrations and SQL users were verified from a separate
  database connection after commit. The original six migration receipts remain
  unchanged. The database has eight migration receipts in total.
- Each API identity has exactly two direct procedure EXECUTE grants, plus the
  normal database CONNECT permission. Real service-identity checks confirmed
  no direct plan-table SELECT, direct approval issuing, or remediation execution.
- Real missing/unowned-plan procedure calls were denied and left `@@TRANCOUNT`
  at zero after repeated calls. No incident records were created for these tests.
- ACR has exactly ten intended pull principals: the original five, three cloud
  demo identities, and two human-service identities. Admin and anonymous access
  remain disabled. Each new API identity has only registry-scoped AcrPull in
  Azure RBAC, not workspace or SQL-administrator Azure roles.
- `plans.review` is registered and consented only to the ownership-verified
  dedicated Approver account. Reader/Operator assignments were not changed.
- The one-shot migration job is deleted and the SQL bootstrap identity's
  `Regional` isolation scope is restored. Its original registry pull grant is
  preserved; no additional migration-only grant remains.
- Full repository tests: 470 passed, with two upstream deprecation warnings.
  The public browser loaded the new assets without page exceptions and denied
  anonymous incident/review requests. A real human sign-in was not available.
- The final authorization-state browser fixture verified the explicit review
  scope transition without a decision request. An event-order bug in the shared
  account bar was corrected and published in revision `0000008`; the fixture
  routes were removed afterward.

## Sign-In Behavior

Normal sign-in requests the existing task scopes. After signing in as Approver,
choose **Authorize review access** to request only `agent.invoke` and
`plans.review`. Finish Microsoft sign-in with the Approver account. This avoids
forcing Reader/Operator accounts to request an admin-only review scope.

Signing out resets the requested scopes to the ordinary task flow. Tokens stay
in server memory and never enter the browser or sandbox. A dashboard revision
change requires signing in again.

The queues contain only actual persisted records. An empty queue does not mean
an incident was started or completed. The investigator still needs to call the
trusted recorder with real sponsor-bound sandbox and worker results.

## Deployment Corrections

The live rollout found issues that local mocks did not expose:

1. The first job name exceeded Azure's 32-character limit; the bounded name is
   now checked by a compiled-template test.
2. A paused serverless database rejected the first connection while resuming.
   That attempt performed no SQL migration.
3. SQL external users receive a normal CONNECT grant; verification now allows
   only that permission plus the exact two procedure grants.
4. Azure SQL service-principal SIDs use the managed identity client ID, matching
   the existing worker setup. Object IDs remain the identifiers for Azure RBAC.
5. Mixing driver-managed and explicit SQL transactions caused nested transaction
   state and a misleading pre-persistence success. The migration now owns one
   explicit transaction with autocommit enabled, explicitly commits, closes the
   connection, and verifies receipts/users from a fresh connection before success.
6. Container Apps logs wrapped embedded JSON without escaping quotes. Verification
   now decodes the structured receipt inside that wrapper and checks its hashes.
7. Azure's identity-attachment index lagged job deletion. Cleanup records remained
   pending until regional isolation was successfully restored; services were not
   enabled through that gate while administrator cleanup was incomplete.

Earlier migration job success statuses without independent persistence proof
are superseded by the final verified receipt. They must not be cited as proof
that the schema had already committed.

## Remaining Acceptance Gates

- One actual Operator request through the cloud task agent, with correlated
  logs from the unchanged agent revision, to resolve the previously observed 422.
- Real sponsor-bound AgentRunner execution in the SAW/OpenShell workspace and
  approved runtime API connectivity. The stopped SAW was not started by this rollout.
- A real investigation creating a persisted draft, Operator submission, and a
  different human's approval/rejection. Browser/API fixtures are not that rehearsal.
- Successful real SQL concurrent-decision/replay tests against a legitimate test
  plan. The live SQL checks so far cover connection, permissions and denied-plan
  rollback; successful two-person transitions were tested with injected stores.
- Broker execution, independent recovery verification, and correlated completion.
- Always-on production session persistence, availability, monitoring, lifecycle
  automation, and reviewed runtime grants. The current single-replica in-memory
  browser sessions and scheduled leases remain prototype constraints.

## Lifetime and Verification

The Incident and Review admission leases expire at `2026-09-15T07:04:09Z`.
The dashboard/environment window ends at `2026-09-15T07:05:08Z`. Expiry denies
new private API business requests; it does not delete or deallocate resources.
The private SQL network was renewed through its validated Bicep deployment,
not by editing expiry evidence. Cleanup/renewal remains an operator obligation.

Repeat the deployment checks from WSL:

```bash
export AZURE_SUBSCRIPTION_ID="<expected-subscription-id>"
python3 infra/next-phase/verify_human_services.py
python3 infra/next-phase/verify-cloud-demo.py \
  --parameters "$HOME/.local/state/learningnemo/cloud-demo.parameters.json" \
  --output "$HOME/.local/state/learningnemo/cloud-demo.result.json"
python3 infra/next-phase/verify_artifacts.py \
  --parameters "$HOME/.local/state/learningnemo/artifacts-dev.parameters.json" \
  --allow-cloud-demo --allow-human-services
```

Owner-only records include `human-services.verified.json`,
`human-migration.verified.json`, `human-migration.cleanup-completed.json`,
`human-services.live.json`, `human-consent.verified.json`, and image/build records
under `~/.local/state/learningnemo`. Never publish that entire state directory.