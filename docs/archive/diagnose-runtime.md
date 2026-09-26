# Runtime Diagnosis and Logs

> **Archived dated record.** This describes the state on the date it was recorded, not current status. See [Status](../status.md) and the [archive index](README.md).

For the latest single-screen dashboard, the reproduced agent 422 and worker
credential fix, and the owner's no-deletion hold, see [Guided Demo](guided-demo.md).
Do not follow earlier cleanup recommendations until the owner authorizes them.

## Recovery Update: 15 September

The cloud console diagnostic image was published and verified on 2026-09-15.
Both private human services were renewed and passed live SQL readiness,
identity/grant, and privilege-denial checks. The cloud agent image was preserved.
The browser loads with Review enabled but remains signed out; the earlier
human-authorized agent `422` has not been reproduced.

Boot diagnostics exposed an earlier startup blocker: cloud-init retried IMDS
ten times because the runtime `AzurePlatformIMDS` deny applies to the host too.
An IP-address allow did not override this platform-specific control. The guarded
maintenance path now records the existing rules, temporarily removes only
`deny-sandbox-host-imds` on a deallocated owned VM, starts it, rearms and verifies
the real shutdown timer, then restores the existing runtime-lock template and
compares all rules before publishing the renewed lease. Failures deallocate the
VM. This succeeded in maintenance attempt `69f928d5514246e99a81209b3ca3aae2`:
startup took 73 seconds, timer verification returned, and rule restoration passed.
Runtime admission remains blocked while the IMDS deny is absent.

GHCR pinning is now live-verified. Attempt `c4a2849661ee4ceea6a8218fba313dc8`
passed root and OpenShell-user registry TLS/401 controls and provisioned all
three pinned sandboxes to Ready. Registry rule and host-pin cleanup passed.
The initial Planning proof was UID 998 with privilege escalation denied,
but HTTP remained unreachable. New curl diagnostics and MicroVM console logs
identified guest DNS fallback to `8.8.8.8`/`8.8.4.4`, which the runtime Internet
deny blocks. The stopped writable overlay was repaired to use Azure DNS
`168.63.129.16`, without changing the base image or allowing public DNS. An empty
resolver is also a valid starting state. Stop/start must remain within the
pinned-registry window: this OpenShell version revalidates GHCR at start.

After DNS repair, POST returned 403 but GET timed out. Both REST endpoint policies
were missing `tls: terminate`, required for HTTPS method/path inspection. The
Planning correction was applied with `policy set --wait`; the Execution YAML
correction is local and has not been applied to its failed live instance.

> **Correction (2026-09-26).** OpenShell v0.0.116 documents `tls: terminate` as
> deprecated with no effect: the proxy auto-detects and terminates TLS for
> inspected endpoints. The GET recovery recorded here therefore came from
> something else in the same sequence, most likely the DNS repair or
> re-applying the policy with `policy set --wait`, not from the `tls` field.
> The invoice policies no longer set it.

**Live proof passed at 2026-09-15 14:52:54 UTC**:

- Planning UID 998; privilege escalation denied.
- GET `/v1/diagnostics/current`: 401 over the protected route.
- POST to the same route: 403 from the OpenShell L7 policy.
- Runtime lock deployed, original eight NSG rules restored, owned runtime NAT
  verified, and actual VM timer/lease valid during the proof.

Proof directory:
`~/.local/state/learningnemo/proof-attempts/1b9ef05e3f7d4a309a1d9e11d73cbd6c/`.
This proves containment and route enforcement, not a model call, SQL change,
or completed human incident.

The bounded recovery session was then closed successfully: same VM/disk preserved,
VM deallocated, runtime NAT/public IP removed, network rules unchanged. The live
proof is historical evidence; the stopped VM is not currently available.

### Remaining Gates

Execution failed after stop/start with `RefreshSandboxToken` Unauthenticated
and policy-fetch retries. Its old supervisor token could not rebootstrap.
Planning's earlier failed-start overlay was backed up with a verified matching
SHA-256 before replacing only that Error instance. OpenShell 0.0.116 rejects both
stop and start from Error; do not force its database state or retry that transition.
Execution's failed instance and logs remain on the preserved disk. Probe has not
received the resolver repair. A complete three-sandbox lifecycle remains unverified.

The earlier cloud 422 still needs a real signed-in Operator request and matching
request ID. The full AgentRunner/incident/execution integration remains incomplete.

### Repeating Recovery

1. Renew dependency leases with **LearningNeMo: renew demo recovery window**.
  It preserves signed worker images and verifies the explicit ten-reader ACR
  allowlist. It does not renew the public console by itself.
2. Deploy runtime egress before **LearningNeMo: recover preserved workspace**.
  The latter temporarily suspends only the IMDS platform block for startup,
  restores it, and verifies the real timer before publishing the VM lease.
3. Inspect the existing inventory and saved policy hashes. Do not run the clean
  workspace deployment against this preserved VM. The local Execution TLS policy
  is newer than its deployed host file; reconcile that exact reviewed change
  before attempting a hash-gated sandbox recovery.
4. Fresh sandbox provisioning now runs the resolver repair before removing
  registry access. `--repair-resolvers` accepts only the fixed Ready/Stopped
  inventory, plus a missing Planning instance; it refuses Error instances.
  Backup/inspect failed instances before any authorized replacement.
5. Run **LearningNeMo: logged workspace proof** only after registry cleanup and
  original runtime-rule verification. Both HTTP results must pass.
6. Close with **LearningNeMo: close runtime recovery** when unattended.

Recovery source verification: **550 tests passed**, two upstream deprecation
warnings. Live Planning proof and cleanup passed separately from those tests.

Use the dedicated tasks **LearningNeMo: summarize boot diagnostics**,
**LearningNeMo: recovery progress**, and **LearningNeMo: logged workspace proof**.
They retain private timestamped evidence; a prior successful record is not a
claim that a lease remains valid now. The findings below describe the earlier
read-only collection, not the subsequently renewed deployment.

## Findings

Read-only collection at **2026-09-15 07:21 UTC** recorded:

- SAW: `PowerState/deallocated`, lease expired at 07:04:09 UTC, no NAT attached,
  no temporary registry rule present. No sandbox can execute in this state.
- Dashboard, controller, agent, Incident and Review containers still report
  Running, but their scheduled lease tags have expired. Incident/Review enforce
  admission expiry in application code. Container running state is not proof
  that business requests are available, and tags do not stop compute charges.
- The last preserved provisioning evidence allowed GHCR address
  `140.82.112.34/32`, then observed a connection resolving to `140.82.114.33` and
  `REGISTRY_CONTROL 000`. This is a recorded DNS/allowlist mismatch consistent
  with the timeout. It is not an authentication verdict from GHCR: no HTTP
  response arrived. Earlier OpenShell wording about registry authentication
  wrapped the network failure.
- Prior evidence established host-to-Azure diagnostic API TLS/401 reachability
  with runtime NAT. That did not prove sandbox provisioning or an agent run.
- No matching request failure appears in the current bounded dashboard/agent
  log tails. The earlier cloud `422` therefore remains unclassified. A new
  authorized request on a known revision, with its matching request ID, is
  required. A missing event in a short retained tail does not prove success.
- The complete incident producer and sandbox credential-mediation integration
  is still incomplete. Logging cannot turn missing integration into a working
  end-to-end demonstration.

Evidence for the latest collection is private in WSL:

```text
/home/aygul/.local/state/learningnemo/diagnostics/d4c49d3854e44cfa8f11543b7daf7249/
```

Prior fixed-name files can have been overwritten by earlier attempts. They are
preserved as historical inputs with file timestamps, not represented as one
newly correlated runtime trace. The staged GHCR pinning fix still needs live
validation in a renewed bounded window. No resource was restarted, renewed,
deployed, or deleted during this diagnostic/logging task.

## Collect Now

Run the VS Code task **LearningNeMo: collect runtime diagnostics**. It creates a
new immutable run directory, reads current resource state and saved evidence,
and collects safe error metadata from the available dashboard/agent log tails.
It never starts the VM, executes an incident, changes a rule, or renews a lease.

Equivalent WSL command from the repository root:

```bash
export AZURE_SUBSCRIPTION_ID="<expected-subscription-id>"
python3 infra/next-phase/collect_runtime_diagnostics.py --cloud-agent-logs
```

For saved evidence only, with no Azure calls:

```bash
python3 infra/next-phase/collect_runtime_diagnostics.py --offline
```

The tool prints `DIAGNOSTICS <run-id> / <directory>`. Open its `*-diagnosis.json`
and `events.jsonl` first. Collection issues are explicit; an unavailable log is
not presented as an empty successful result. All timestamps are UTC.

## Next Provisioning Attempt

The existing guarded sandbox-resume command now generates a unique run ID and
writes to `~/.local/state/learningnemo/sandbox-attempts/<run-id>/`. It retains:

1. Workspace identity, lease, NAT, and NSG observations.
2. Expected image/policy hashes, VM DNS results, and selected registry addresses.
3. The exact network preview, apply response, observed rule, and effective NSG.
4. Guest output as soon as Run Command returns, before rule cleanup.
5. Primary provisioning failure and network-cleanup failure as separate events.
6. The retrieved, digest-verified guest log archive and final nonce-bound proof.

An Azure CLI exit code of zero means only that transport returned. It is logged
as `returned`, not guest success. Guest markers, log integrity, policy cleanup,
and actual HTTP probe outcomes are checked separately. The proof is not run if
provisioning, cleanup, or diagnostic integrity is unconfirmed.

Guest logs are stored under
`/var/lib/learningnemo-saw/attempts/<run-id>/logs.json.gz`. The script records:

- Stage start/failure/exit code and UTC time with the run ID.
- Exact policy hashes and the pinned versus resolved GHCR address.
- Registry controls as root and as the OpenShell user: HTTP status, remote IP,
  TLS verification result, DNS/connect/TLS/total durations, and curl exit code.
- Output of each named sandbox creation command.
- Bounded gateway journal entries since the attempt began, final inventory, and
  recent guest-console tails. Truncated tails are labelled with their byte count.
- Host-pin cleanup success independently of the primary operation result.

The archive is sanitized before transfer. A small `SANDBOX_LOG_MANIFEST` gives
its run ID, byte count and SHA-256. Retrieval uses 2-KiB chunks to avoid Azure's
Run Command output limit, verifies the complete digest/run binding, and applies
a decompression limit. It does not unpack arbitrary filesystem paths.

The resume command attempts retrieval automatically after network cleanup.
When the VM is still running, a missed archive can also be collected explicitly:

```bash
python3 infra/next-phase/collect_runtime_diagnostics.py \
  --guest-attempt "<32-character-attempt-id>"
```

If the VM is stopped, the collector reports that it cannot retrieve guest logs;
it will not start it. Preserve the disk until approved recovery can retrieve
them. If Run Command itself times out, guest completion and pin cleanup are
unknown until inspected. Do not delete a sandbox or restart the gateway to
clear an error before collecting these records.

## Cloud Request Correlation

The local console changes attach `X-Request-ID` to upstream agent errors and
show the ID in the browser error message. The same generated ID is sent to NeMo
and logged with HTTP status and one of:

| Category | Meaning |
| --- | --- |
| `request_validation` | HTTP 422 with a validation-detail list; safe field locations/types retained |
| `workflow_rejected` | HTTP 422 without that validation shape; requires matching worker logs |
| `transport_timeout` | No completed response; operation outcome is unknown |
| `transport_error` | Connection/transport failure; no successful outcome assumed |
| `invalid_response` | Response did not match the expected completion structure |
| `upstream_error` | Other upstream HTTP failure |

The walkthrough previously converted a 422 into an expected Reader denial
based only on local role prediction. That masked genuine validation/workflow
failures. It now leaves 422 as an error and logs every upstream failure before
response mapping. Do not interpret historic mapped denials as proof of tool
authorization enforcement without matching audit events.

The cloud log collector retains request IDs, status/category, allowlisted schema
locations/types, authorization requirements/missing roles, exception types and
stack file/function/line references. It deliberately omits prompts, response
bodies, arbitrary validation messages, and rejected input values. It queries a
bounded tail; capture promptly on the same revision before any rollout.

Console error-handling changes were subsequently published and verified during
the recovery described above. Guest instrumentation has also been exercised
live. Neither establishes a completed human-authorized agent request or incident.

## Handling and Verification

Directories use mode 0700; evidence files use mode 0600 and unique names. Tokens,
authorization/cookie fields, key material, sensitive assignments, URL credentials,
and query strings are redacted. IPs, fixed hostnames, revisions, policy hashes,
and timing remain because they are needed for diagnosis. These are private
engineering logs, not automatically safe public interview artifacts: review them
before sharing. No raw command arguments or script bodies are recorded, only
script hashes. Earlier logs cannot be repaired retroactively by redaction tests.

Verification: **543 repository tests passed**, including real-shell failure
archive generation, redaction, corruption/run-binding rejection, preservation of
primary failure when cleanup also fails, and correlated 422/timeout handling.
The live read-only collection completed without collection issues. A new live
sandbox attempt was intentionally not performed against expired resources.