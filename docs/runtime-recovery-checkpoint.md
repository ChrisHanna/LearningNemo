# Runtime Recovery and Execution Checkpoint

For subsequent recovery results, including the live passing Planning proof at
2026-09-15 14:52:54 UTC and verified session cleanup, see
[Runtime Diagnosis and Logs](diagnose-runtime.md). The sections below are the
earlier checkpoint; their pending GHCR/Planning proof statements are historical.

Recorded 2026-09-15. The complete incident demonstration remains unfinished.
This checkpoint supersedes earlier claims that deployed handoff services alone
make the sandbox incident journey operational.

## Live Recovery Results

The trusted workers' existing signed image was retained. Their allocation lease
was renewed to 07:04:09 UTC using an expiry-only template and pre/post snapshots
of identity, runtime configuration, and image. Current modified source was not
published as that signed release.

The preserved SAW VM and disk were not replaced. Recovery captured their prior
state and copied the host's previous shutdown timer and readiness evidence.
The real shutdown timer was rearmed and its next firing time verified before
the VM's expiry tag was changed to match it. OpenShell 0.0.116-1 and its
authenticated gateway were healthy. The previous expired session had removed
the sandboxes, leaving zero before restoration was attempted.

A separately named runtime NAT was attached without changing the NSG rules or
route-table association. The host then reached the intended Azure diagnostic
API over verified TLS and received 401 without credentials. This established
host-to-protected-API reachability, not a successful sandbox or agent request.

The proof gate now validates all eight NSG rules, including their actual
priorities, directions, protocol, source and destination fields. An owned,
unexpired runtime NAT can be distinguished from an unexpected or bootstrap NAT.
These changes are local; the cloud proof controller was not updated in this
recovery session.

## Remaining Provisioning Failure

Sandbox provisioning still tried to contact GHCR for the digest-pinned image.
That bootstrap-only destination is denied under runtime egress policy.
Temporary exceptions were restricted to public IPv4 /32 addresses for the two
fixed registry hosts and outbound TCP 443, and removed after every attempt.

The recorded address exception was `140.82.112.34/32`, while GHCR subsequently
resolved to `140.82.114.33` inside the VM. The host registry control timed out
before sandbox creation. Even using a prior VM DNS lookup was insufficient
because the lookup changed between commands.

The staged fix pins the selected verified address to `ghcr.io` for the bounded
provisioning command, preserves the prior hosts file, and removes only its own
uniquely marked entry afterward. It rejects existing overrides. This fix passed
rendered-script and address-validation tests but has NOT been exercised live;
the remaining lease was too short to safely retry provisioning and proof.
The separate registry content host must also be checked if redirects fail.

Deployed policy files use CRLF. Their bytes are now preserved while computing
expected hashes, matching Bicep's original byte representation. The deployed
policy files were not modified to make the hashes pass.

Only newly failed Planning provisioning instances were removed, after checking
OpenShell's `phase` field was `Error` and retaining their inventory records.
No running sandbox was replaced. The final host registry check failed before
any additional sandbox was created. No fresh Planning UID/HTTP proof passed.

## Cleanup Verified

The recovery session was closed. The SAW VM and disk remain preserved; the VM
was deallocated. The runtime NAT and its public IP were removed, and the
temporary registry rule is absent. NSG rules and subnet security associations
were unchanged; default outbound access remains disabled. The new runtime NAT
does not remain as an untracked ongoing cost.

Owner-only evidence includes `workspace-renewal.verified.json`,
`workspace-runtime-egress.verified.json`,
`workspace-resume-sandboxes.result.json`, and
`workspace-runtime-egress.rollback-verified.json` under the WSL state directory.
Keep their timestamps: the earlier NAT-present observation is superseded by
the later rollback record, not silently rewritten as current health.

## Execution Code Added, Not Activated

- An approved-execution coordinator claims a plan once, checks its approval
  binding, and requires a valid broker result before independent verification.
- The staged third SQL migration adds unique execution claims, persisted broker
  and verification receipts, and explicit Operator completion. It does not
  issue approvals or invoke the remediation procedure itself.
- The fixed managed-identity transport calls only the existing broker and
  verifier HTTPS routes. It does not redirect or automatically retry an
  ambiguous execution write. Azure tokens are not placed in request bodies.
- A separate Operator-authenticated execution API rejects Reader/Approver
  execution, extra fields, browser-supplied actor hashes, and expired leases.
- The shared procedure client now lets stored procedures own their transactions
  using driver autocommit mode, reflecting the durable-commit fix established
  during the prior live migration. This client change is NOT deployed yet.

These components are not wired into the public dashboard, given live grants,
or deployed. The third migration is deliberately NOT appended to the already
applied two-migration handoff inventory. It requires an additive migration
release, dedicated coordinator identity/grants, real SQL concurrency tests, and
a read-only reconciliation path before any production activation.

The transport is a trusted control-plane call, not the AgentRunner's bounded
sandbox capability or credential mediator. It must not be presented as proof
that the model executed inside OpenShell. No fake incident was inserted and no
successful execution or recovery record was manufactured.

## Verification and Next Gates

Full repository tests: 521 passed, two upstream deprecation warnings. Dedicated
process tasks are required in this environment because the shared interactive
terminal repeatedly injected Ctrl+C. Interrupted commands are not passing tests.

Outstanding gates, in order:

1. Renew validated infrastructure leases for a complete rehearsal window.
2. Validate the registry pin live, recreate pinned sandboxes, remove bootstrap
   access, and pass both permitted and denied runtime-route controls.
3. Connect real human-sponsored incident initiation, task-bound AgentRunner
   identity, out-of-sandbox credential mediation, and the model investigation.
4. Integrate and deploy the execution coordinator and reconciliation path with
   durable receipts and separate permissions. Do not add its grants to Review.
5. Verify the entire start/investigate/submit/review/execute/verify/complete
   sequence with real accounts, including wrong-owner, stale-plan, replay,
   timeout, expiry and concurrent-decision behavior.

Until these gates pass, the deployed cloud dashboard is a partial handoff demo,
not the requested fully implemented production incident workflow.