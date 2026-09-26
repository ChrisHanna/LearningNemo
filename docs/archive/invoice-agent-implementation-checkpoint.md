# Invoice Agent Implementation Checkpoint

> **Archived dated record.** This describes the state on the date it was recorded, not current status. See [Status](../status.md) and the [archive index](README.md).

## Completed Hosted Workflow

The invoice console is now live. The real two-agent rehearsal passed with a
three-step model-authored plan, distinct Planning/Execution MicroVMs, three SQL
receipts, and all five independent verification checks. Actual human approval is
not claimed: the automated rehearsal used labelled synthetic reviewer metadata.
See [invoice-demo.md](../guides/invoice-demo.md) for the current presentation workflow,
observed run IDs, evidence provenance, operating deadlines, and known limits.

All fourteen retained sandboxes were observed stopped. Real SQL tampered-hash
and consumed-approval checks passed without changing receipts. Public invoice
mode, private API connectivity, anonymous denial, and desktop/mobile layout were
verified. The full Python regression suite passed 711 tests with 27 warnings.

The dated implementation notes below are historical and are superseded by the
runbook above, particularly their statements that no valid agent plan or public
invoice workflow had been observed.

Status at 2026-09-16 17:45 UTC: implementation in progress. Five invoice services
are deployed, but invoice mode is not enabled in the public console. Successful
live model-backed Planning and Execution runs are still required.

## Current Verified State

- SQL migrations 008, 009, and 010 are installed. The latest six-identity migration
   execution was `caj-learningnemo-human-mig-dev-tpst79i`; hashes and bindings were
   verified. Applied migrations are immutable. Administrator authority was removed,
   Regional isolation restored, and the migration job retained disabled.
- Five apps use `ca-nemo-invoice-<role>-dev` (Azure's 32-character name limit).
   Roles are operator, review, planning, execution, verifier. Planning/execution
   expose capability-protected HTTPS to the SAW host; the other apps are private.
- Six managed identities use `id-learningnemo-invoice-<role>-dev`, including the
   simulator. All six exact SQL EXECUTE grants and direct-table/internal-procedure
   denials passed live. Simulator and operator share a process, not a SQL identity.
- Exact AcrPull inventory is sixteen identities with explicit invoice-scope
   verification. The simulator has no registry pull grant. Planning/execution have
   access only to the APIM client-key secret, not the upstream provider key.
- An application-only `Invoice.Verify` role is assigned only to the coordinator.
   Human roles and API consent were preserved and read back. Actual coordinator
   token signature, audience, issuer, client/object ID, and private connectivity
   checks subsequently passed. Azure exec cooldowns must be respected.
- Agent/service images are built and digest-pinned; the corrected agent image and
   pinned GHCR base are staged. The dedicated rootless cache now uses native overlay
   at `/var/lib/learningnemo-invoice-cache/data/overlay-storage`, not VFS. Only the
   failed pull's unregistered attempt-owned cache was reclaimed. Rootful Podman
   remains disabled and temporary network exceptions were removed.
- Fresh MicroVM `48613521-4767-4ba8-b532-26d7efe7ca94` imported NeMo as UID 998,
   then was stopped and retained. This is not evidence of live model inference.
- Remote lifecycle, private encrypted manifest/stdin transfer, role policies,
   durable SQL jobs/activity, owner-filtered reconnect, and the opt-in UI are
   implemented. Uncertain admission attempts trigger owner-bound revocation without
   retrying admission or launching an agent. The host's real timer gates admission.
- Full Python regression: 706 passed, 27 warnings. Generated host/worker scripts
   compile; NeMo builder/dispatch tests use a mocked streaming model explicitly.
- Browser fixture: scenario setup did not start an agent; a healthy decision
   rendered; exactly two intended POSTs, zero legacy API reads, no overflow at
   desktop 1440/mobile 390. Fixture state was removed. Console image built, not yet
   published with invoice mode enabled.

Three real Planning attempts exposed runtime gaps. The first created a late-ready
sandbox after a 300-second client timeout; it was explicitly stopped and retained.
Provisioning now has a pre-creation stop watchdog, a 540-second conversion budget,
and a separate 600-second run timer. The second rejected OpenShell-added policy
defaults; those defaults are now explicit and exact comparison remains required.
The third started NeMo but OpenShell denied the connection because MicroVM DNS
failed. No model decision was produced. Capability revocation and sandbox stop
ran, and Stopped was independently observed. These are failed rehearsals, not
successful agent evidence.

The latest release applies the existing stopped-overlay Azure-DNS repair to each
fresh sandbox before capability issuance, and disables NeMo/OpenAI automatic
retries. The most recent prior release passed all live identity/SQL checks; this
transport release is undergoing renewed verification and rehearsal. Partial-run
reconciliation is implemented: exact owner-bound receipt inspection never replays
steps, and only a complete receipt set may trigger independent verification.
Its browser fixture issued one reconciliation request and zero agent starts.

## Current Remaining Gates

1. Finish live service verification after Azure's exec cooldown, including a real
    coordinator token and private verifier connectivity.
2. Run labelled automated Planning against an isolated fixture, with real model
    calls in a new sandbox. The rehearsal never submits or approves a plan.
3. Verify a separate real Execution agent and independent SQL verification. Human
    review and execution require the user's explicit interaction.
4. Exercise injection, runtime denial, tampering, replay, false-success, and
    partial-failure cases using evidence from the actual enforcing layer.
5. Publish and verify the invoice console; document queued-job restart behavior
    and partial-run reconciliation. No model narrative counts as independent proof.

Resources remain retained. Retention does not remove execution leases: renew
dependencies, NAT, and the actual host timer before a run. No VM, disk, sandbox,
snapshot, NAT, or user-record deletion occurred. MicroVM CPU/memory CLI flags are
not enforced by this installed driver and must not be presented as limits.

## Historical Checkpoint

The sections below describe the earlier local-only checkpoint and are retained
for historical context. The current state and remaining gates above supersede
their deployment, image-cache, test-count, and migration-status statements.

## Implemented Locally

- A versioned, immutable multi-step invoice plan with exact targets, revision
  preconditions, evidence hash, rationale, risks, and fixed operation identifiers.
- A real SQL lost-acknowledgement fixture: twelve orders become twenty-four
  invoices through a repeated import. A healthy fixture remains at twelve.
  Legitimate equal-value purchases have distinct order keys and must be preserved.
- Diagnostic-only SQL procedures, exact-set quarantine, total reconciliation,
  and activation of an idempotent importer. No arbitrary SQL or query cancellation.
- Independent review, short-lived run capabilities, a new execution-run claim,
  transactional per-step receipts, and a separate verifier including rollback-only
  import replay. The ordinary migration inventory does not include these files.
- NeMo Planning and Execution configurations with distinct tool catalogs,
  bounded loops, structured decisions, and no mutation retry after uncertainty.
- Role-specific tool gateway factories and a trusted APIM model adapter using
  the installed NeMo Guardrails check-only interface. Deployment factories and
  least-privilege identity wiring still need implementation.
- A host-side fresh-OpenShell-sandbox adapter, independent expiry timer creation,
  private stdin capability transfer, and separate creation-time policies.
- Human API factories and a sponsor-filtered activity metadata store. The activity
  viewer, durable remote run scheduling, reconnect semantics, and event transport
  from the retained host are not connected to the dashboard.

## Verified

The full Python suite passed: 676 tests, with 27 upstream deprecation warnings.
NeMo workflow construction and tool dispatch passed with an explicitly mocked
streaming model. This is not evidence of a live model-backed sandbox run.

Azure SQL job `caj-learningnemo-human-mig-dev-x07fyqr` passed a rollback-only
rehearsal at 2026-09-16 06:04 UTC. Both faulty and healthy cases passed. The faulty
case exercised the three-step repair, separate synthetic review identities,
independent integrity/replay checks, sponsor isolation, and capability revocation.
All invoice tables and fixture data were rolled back; no live human approval or
agent execution was simulated as success. Tested source hashes:

- 008_invoice_lab.sql: `5fe3590447fb4ed1cccaf9ae24f542de181f883a90c9cdfc904d164d76d567dc`
- 009_invoice_workflow.sql: `5aef0cd7bd4e5ba9553f2a1036633e8adee67a2043ad305549049161daffc46d`

The later addition of the owner-filtered evidence getter to migration 009 has
local parsing coverage but needs another real SQL validation before deployment.
The retained migration job was disabled and its SQL administrator identity
detached; Regional isolation was restored. Existing migration receipts and human
records remain unchanged.

## Private Image Staging

The private invoice-agent image was built and digest-pinned in the existing ACR.
OpenShell 0.0.116's MicroVM registry credential environment is driver-wide, rather
than scoped to one registry. Do not place an ACR token there while the same driver
also resolves the retained GHCR sandbox images.

A rootless Podman local-image cache was selected instead. The host had roughly
7.1 GiB available memory and 55 GiB free disk. Package installation required a
temporary, exact-address port-80 mirror exception because the mirror did not
respond over HTTPS. The wrapper required all sandboxes to be stopped, used signed
Ubuntu package metadata, and restored the original NSG rules. Maintenance attempt:
`178d53feaa5d4396a66956c851c992d9`.

The original rootless initialization failures under the host user's root-owned
configuration directories are resolved using the user-approved dedicated directory
approach. Existing directory ownership was not changed recursively. The cache uses:

- Configuration: `/var/lib/learningnemo-invoice-cache/config`
- Image storage: `/var/lib/learningnemo-invoice-cache/data/storage`
- Runtime storage: `/run/user/1000/learningnemo-invoice-cache/run`
- Cache/state: the dedicated `cache` and `state` subdirectories.

A user-service override sets the XDG paths and explicitly sets Podman's image and
runtime paths. Live API verification confirmed rootless execution, the expected
paths, and the `vfs` storage driver. The Docker-compatible version and image-list
endpoints responded through the user-owned Unix socket. Rootful Podman services
remain inactive and the original OpenShell gateway remains active. No network
exception was used for this directory repair, and no agent sandbox was started.

The image inventory was empty at verification. Private image pull/staging and an
actual OpenShell sandbox using that image remain unverified. Use the same remote
socket for future image operations; an ordinary unqualified local `podman pull`
would not necessarily select this dedicated store. Do not inject a driver-wide
ACR token as a shortcut.

## Remaining Acceptance Gates

1. Finish and verify private image staging without agent-visible registry keys,
   rootful sockets, or broader permanent network access.
2. Implement the actual remote host/controller transport and observed lifecycle
   checks. Test creation failure, cancellation, expiry, and retained-state cleanup.
3. Complete least-privilege gateway deployments, readiness checks, and separate
   identity/grant provisioning. Host code must not receive SQL credentials.
4. Add durable run admission/status so the browser obtains a run ID immediately
   and can reconnect without repeating a mutation. Validate real model requests
   against the mediated streaming API, including tool schemas and proxy/CA setup.
5. Integrate scenario setup, multi-step plan review, original-Operator execution,
   and provenance-labelled activity into the existing five-step UI.
6. Rehearse real Planning and Execution model calls in different OpenShell
   sandboxes, then real independent human review and explicit execution.
7. Add labelled prompt-injection, runtime-denial, plan-tampering, replay, and
   false-success tests with receipts from the enforcing layer. Never label a
   gateway denial as an OpenShell policy denial.
8. Verify failure reconciliation and capability issuance uncertainty. A model
   success message or a contract test is not sufficient for completion.

There were no VM, disk, sandbox, snapshot, NAT, or user-record deletions.
The previously deployed deterministic database workflow remains a separate
release and retains its own bounded service leases.