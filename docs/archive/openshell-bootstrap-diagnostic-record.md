# OpenShell Bootstrap Diagnostic Record

> **Archived dated record.** This describes the state on the date it was recorded, not current status. See [Status](../status.md) and the [archive index](README.md).

## Scope

This record covers the live Azure diagnosis of the OpenShell `0.0.116`
MicroVM bootstrap on 2026-09-14. The host VM, NIC, disk, no-RBAC identity, failed
bootstrap stack, and owner-only evidence were preserved while each defect was
isolated. The host VM never had a public IP.

Owner-only evidence is stored outside the repository at:

```text
~/.local/state/learningnemo/workspace-dev.diagnostics.json
```

## Diagnostic Method

1. Run local Bicep, shell, and Python gates before every source change.
2. Preview the bootstrap template and require exactly one active VM run-command
   change. VM, NIC, identity, NAT, public IP, and resource groups must be ignored.
3. On failure, capture guest, package, KVM, systemd, gateway journal, bootstrap,
   MicroVM console, state-file, and boot evidence before lifecycle changes.
4. Sanitize evidence on the VM, compress it, retrieve it in bounded chunks, and
   verify byte count, SHA-256, gzip integrity, markers, and redaction before an
   owner-only atomic write.
5. Repair the preserved VM in place. Do not rebuild the host to test a guest-only
   change.
6. After the live root cause is proven, reproduce the result through the clean
   deployment workflow before claiming completion.

## Findings

### Azure Run Command truncation

Azure Run Command returned only the final roughly 4 KiB of a large diagnostic
response. The opening marker was lost even though the closing marker survived.

Fix: `capture_workspace_diagnostics.py` now creates a sanitized mode-0600 guest
archive and retrieves it as verified 2 KiB chunks.

### Incompatible CLI arguments

OpenShell `0.0.116` rejects `sandbox create --output <format>` when a canonical
main command is also supplied after `--`.

Fix: remove `--output json`; creation output was discarded and was not needed.

### Non-idempotent gateway registration

A bootstrap retry attempted to add the already-registered local gateway and
never accepted its healthy existing registration.

The initial repair tested `openshell status` before adding the gateway. A later
clean deployment disproved that check: version `0.0.116` returns exit zero with
`{"status":"not_configured"}` for structured status output. Bootstrap skipped
registration, printed a false authentication success, and then failed sandbox
inventory with `No active gateway`.

Fix: read `gateway list --output json`, validate the exact local mTLS endpoint,
add only an absent registration, and explicitly select `openshell`. Readiness
requires JSON status `connected` and authentication status `authenticated`.
The independent runtime verifier now checks those values too.

### Root-owned XDG parent

The MicroVM launcher could not create the versioned runtime cache under the
admin user's XDG data directory because an intermediate directory was not owned
by that user.

Fix: create and secure `~/.local/share/openshell` for the admin user during
bootstrap.

### Temporary egress ordering

One interrupted preservation command detached and deleted the temporary NAT
before VM deallocation. The next registry validation failed while DNS still
worked but HTTPS did not.

Fix: the diagnostic workflow records each stage; bootstrap egress restoration
uses exact what-if validation, waits for the stack and subnet association, and
then probes GHCR from the VM. Ignored preserved-host resources no longer trigger
a false-positive egress what-if rejection; active compute changes remain
prohibited.

### VM process identity

The pinned community base image contains a non-root `sandbox` account with its
own numeric UID/GID. OpenShell `0.0.116` preserves that image account and prepares
`/sandbox` for it. Policies that forced a different numeric identity caused
child-process setup to exit after policy and network initialization.

The newer upstream architecture exposes VM `sandbox_uid` and `sandbox_gid`
configuration, but the pinned `0.0.116` gateway schema does not accept those TOML
keys and its managed gateway path does not expose that override.

Fix: use the image-native named `sandbox` account in all three policies. The
identity remains non-root and is bound to the immutable pinned image.

### MicroVM overlay root permissions

The final child-side failure was not a namespace, capability, Landlock, or
seccomp error. A syscall trace of a disposable clone of a fresh VM launch
showed all of those operations succeeding, followed by:

```text
setuid(998) = 0
setuid(0) = -1 EPERM
prctl(PR_SET_DUMPABLE, SUID_DUMP_DISABLE) = 0
prctl(PR_SET_NO_NEW_PRIVS, 1, ...) = 0
seccomp(SECCOMP_SET_MODE_FILTER, ...) = 0
execve("/bin/bash", ["/bin/bash", "-l"], ...) = -1 EACCES
```

The bootstrap starts with `umask 077`. The user systemd manager inherited that
mask, as did `openshell-gateway` and `openshell-driver-vm`. OpenShell `0.0.116`
creates the reusable overlay template from host staging directories without
overriding that inherited mask. The resulting overlay `/upper` inode was mode
`0700`, owned by UID 1000. OverlayFS used that inode as the merged `/newroot`,
while the canonical process correctly dropped to the image-native UID 998.
Every executable below the root was mode `0755`, but UID 998 could not traverse
the merged root, so the final `execve` returned `EACCES`.

A disposable guest A/B made only one change:

```text
/newroot mode=0700 uid=1000: /bin/true -> status 126, Permission denied
/newroot mode=0755 uid=1000: /bin/true -> status 0
```

Fix: set `UMask=0022` in the `openshell-gateway.service` drop-in. OpenShell
continues to apply explicit owner-only modes to the gateway database, state
directories, JWT keys, TLS key, sandbox token, and sandbox metadata. After
removing only the bad reusable overlay template, a fresh Probe MicroVM reached
`Ready` as UID 998 with `/` mode `0755`.

### Policy hostname rendering

The first live Planning route denial used the literal host
`__DIAGNOSTIC_HOST__`. Bicep replaced hostname placeholders in the bootstrap
shell source before inserting the base64 policy bodies. This both left the
placeholder inside the later-inserted policy and rewrote the runtime `sed`
search token, making that fallback unable to match.

Fix: render each hostname into its policy text in Bicep before base64 encoding
the policy. The bootstrap no longer mutates policy hostnames and fails with
`openshell_policy_render_failed` if any `__TOKEN__` remains after decoding.

### Gateway request-rate headroom

With three ready supervisors, 487 recent gateway records over roughly eight
minutes contained 315 `GetInferenceBundle` and 158 `GetSandboxConfig` calls.
That steady state is approximately 59 requests per minute. The configured
60-request, 60-second limit therefore left no reliable capacity for an exec
relay and produced gRPC `ResourceExhausted` responses.

Fix: retain a bounded gateway limit but raise it to 120 requests per 60 seconds,
matching the OpenShell configuration example and leaving measured headroom for
operator verification traffic.

### Pinned-release exec and restart limitations

OpenShell `0.0.116` `sandbox exec` can receive a complete command response and
still fail to terminate. This is tracked upstream as
[issue 1046](https://github.com/NVIDIA/OpenShell/issues/1046). Verification now
uses owner-only output from `openshell sandbox ssh-config` with system OpenSSH,
closed stdin, `setpriv`, base64-wrapped remote scripts, and hard time bounds.

A gateway restart performed more than 15 minutes after sandbox creation also
proved that persisted VM restore reuses the original static sandbox JWT. All
three restored guests failed policy fetch with `ExpiredSignature`, and token
refresh could not rebootstrap from an expired static source. The failure was
captured before the terminal sandboxes were replaced. This is an upstream
`0.0.116` recovery limitation; the reproducible deployment does not restart the
gateway between sandbox creation and readiness verification.

### Diagnostic report validation

The guest diagnostic archive was correctly sanitized, but final JSON validation
re-scanned serialized syntax. An empty field such as `TOKEN=` became
`TOKEN=\"` in JSON and triggered the credential regex even though no value was
present. Final validation now scans each report string value before
serialization. Empty values pass; actual unredacted values remain rejected.

### Interrupted stack cleanup

The first guarded cleanup client was canceled after Azure removed all four
deployment-stack records but before the VM, disk, NIC, identity, run command,
and temporary diagnostic extension were deleted. A retry would previously have
returned "no deletion is needed" based only on absent stack records.

Fix: `remove-workspace.sh` now checks the exact deterministic workspace
resource names as well as stack records. After stack deletion it idempotently
removes any remaining VM, NIC, OS disk, no-RBAC identity, temporary NAT/public
IP, and the four runtime-lock NSG rules, then refuses success if any deterministic
workspace resource remains. The foundation verifier still runs last, so the
NSG, route table, VNet, subnet, and baseline rules must remain intact.

### Preserved-VM retry and runtime-lock deployment

On 2026-09-14, the corrected bootstrap succeeded on the preserved clean VM.
Supporting-service leases were renewed through the guarded deployment scripts;
workspace parameters were regenerated and checked against dependency expiry.
Only ownership-checked resource expiry tags were renewed on the preserved host.
The original clean-host what-if gate rejected a VM update, so the VM and NIC
configuration were not reapplied. This tag-only recovery is not a clean-build
reproducibility result.

The bootstrap preview validator now supports an explicit
`--existing-resource-id` for exactly one run-command modification. Its default
remains create-only. The runtime-lock preview validator ignores unchanged
compute references but still rejects actual compute modifications.

Success diagnostics were captured before temporary NAT was detached and deleted.
Azure then rejected part of the runtime-lock deployment with
`SecurityRuleInvalidAccessType`: an `Allow` rule was rejected with `Deny` as the
allowed access value. The template includes an `Allow` rule for
`AzurePlatformDNS`; the supported resolver rule must be established and deployed
before claiming completion. Partial lock state must be inspected, not assumed
absent or complete. No successful final `verify_workspace.py` result was
established in this attempt.

Evidence for this attempt is owner-only, outside the repository:

```text
~/.local/state/learningnemo/workspace-dev.pre-retry-20260914T203807Z.diagnostics.json
~/.local/state/learningnemo/workspace-dev-retry-20260914T204049Z/success-diagnostics.json
```

These paths are historical evidence, not a guarantee that resources are still
running. The workspace lease for that attempt ended at `2026-09-15T00:40:49Z`.

### Runtime-lock repair and fresh Planning proof (2026-09-15)

The rejected `AzurePlatformDNS` allow rule was replaced with an explicit
`168.63.129.16/32`, port-53 allow rule. Microsoft's
[Azure resolver documentation](https://learn.microsoft.com/en-us/azure/virtual-network/what-is-ip-address-168-63-129-16)
describes DNS as exempt from NSGs by default unless targeted by the platform DNS
tag for denial. The repair does not open general Internet access. A bounded
preview and deployment of the four-rule stack succeeded without a host restart.

The independent verifier then exposed a response-shape defect: `az vm show`
flattens profile properties, while `get-instance-view` can wrap statuses in
`instanceView`. The verifier now handles CLI and ARM forms. Regression tests and
live verification passed, producing owner-only `workspace-dev.result.json`.

That verifier combines fresh host checks with the earlier bootstrap policy
evidence. It does not rerun the permitted API routes after lockdown. A new fixed
Planning probe revealed the gap: guest execution returned UID 998 and denied
`setuid(0)`, but both HTTP attempts timed out after NAT removal. The live receipt
at `2026-09-15T00:26:13Z` reports `routeStatus: unreachable`, not policy denial.
The Azure script also needed an explicit Bash shebang; without it, Run Command
used `/bin/sh` and rejected `pipefail` before reaching OpenShell.

The remaining dependency is approved outbound connectivity for the broker/API
route under the locked runtime network model. Neither VM running state nor the
old bootstrap receipt proves that path is usable now. The new console live proof
preserves this failure rather than substituting historical 401/403 results.

## Live Proof Sequence

The live repair used this bounded sequence while the original VM stayed
allocated and private:

1. Enable trace logging for one failed Probe attempt, restore `info`, and run
   `capture_workspace_diagnostics.py` before changing lifecycle state.
2. Clone the immutable root disk and sparse overlay while briefly pausing only
   the Probe launcher. Inject host `strace` plus its three missing libraries
   into the clone and replay the original launch metadata with a fresh token.
3. Confirm the final failing syscall is `execve("/bin/bash", ...) = EACCES`.
4. In another disposable clone, execute `/bin/true` as UID 998 before and after
   `chmod 0755 /newroot`; require status 126 before and status 0 after.
5. Add `UMask=0022`, stop only the user gateway, remove only
   `vm/images/overlay-templates`, restart the gateway, and create a fresh Probe.
6. Require the Probe to be `Ready`, UID 998, and root mode `0755` before
   replacing the three failed diagnostic sandboxes.
7. Render and apply the resolved Planning and Execution policies with
   `openshell policy set ... --wait`.
8. Raise the bounded gateway limit to 120 requests per 60 seconds based on the
   measured three-supervisor baseline.
9. Verify exact allowed and denied methods and paths through generated OpenSSH
   configs, then verify all Probe boundary denials.

The temporary cloned disks and trace launchers were removed after each probe.
The Azure VM, NIC, OS disk, no-RBAC identity, private subnet, and image cache
were not recreated during diagnosis.

## Upstream References

- [Build architecture](https://github.com/NVIDIA/OpenShell/blob/main/architecture/build.md)
- [Compute runtimes](https://github.com/NVIDIA/OpenShell/blob/main/architecture/compute-runtimes.md)
- [Bring your own container](https://github.com/NVIDIA/OpenShell/blob/main/examples/bring-your-own-container/README.md)
- [VM driver documentation](https://github.com/NVIDIA/OpenShell/blob/v0.0.116/crates/openshell-driver-vm/README.md)

The `main` branch documents behavior newer than the pinned release. Runtime
configuration decisions must be checked against the `v0.0.116` package and tag.

## Completion Criteria

The live investigation is complete only after:

1. Planning, Execution, and Probe MicroVMs all reach `Ready`.
2. Planning and Execution allow only their exact method and path.
3. External Internet, IMDS, Azure SQL, host files, policy writes, and privilege
   escalation are denied from the Probe sandbox.
4. Temporary NAT is detached and deleted.
5. The four-rule runtime NSG lock is applied.
6. The clean deployment workflow reproduces the result from a new host.
7. Owner-only readiness evidence and the full local test suite pass.
