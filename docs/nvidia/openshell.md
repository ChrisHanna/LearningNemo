# NVIDIA OpenShell

[OpenShell](https://docs.nvidia.com/openshell/home) is the runtime that runs
each agent process in a sandbox governed by declarative policy: filesystem
(Landlock), process (non-root, seccomp), and network (a proxy that allows only
listed hosts, paths, and programs). NVIDIA names it the reference runtime
layer of the [Secure Agent Workspace](secure-agent-workspace.md).

| | |
| --- | --- |
| Version | `0.0.116`, installed from a hash-pinned `.deb` ([`dev.workspace.config.json`](../../infra/next-phase/environments/dev.workspace.config.json)) |
| Host | One private Ubuntu 24.04 Trusted Launch VM (`Standard_D2s_v5`, nested virtualization) |
| Compute driver | MicroVM (`vm`): each sandbox is its own KVM microVM, 1 vCPU / 1 GiB, set in the gateway config because the VM driver ignores `--cpu`/`--memory` |
| Images | Sandbox and supervisor images pinned by digest |
| Rationale | [ADR 0001](../decisions/0001-openshell-microvm-driver.md) |

## Gateway configuration

Written by [`scripts/bootstrap-saw-openshell.sh`](../../scripts/bootstrap-saw-openshell.sh):

| Setting | Value | Why |
| --- | --- | --- |
| `bind_address` | `127.0.0.1:17670` | Loopback only; sandboxes and the network cannot reach the gateway API |
| `mtls_auth` | enabled, unauthenticated users refused | OpenShell's recommendation for a single-user VM gateway |
| `gateway_jwt.ttl_secs` | 900 | Sandbox tokens expire; OpenShell's default (0) never expires |
| `ssh_session_ttl_secs` | 900 | Shorter than the 24-hour default |
| `policy_validation_failure_mode` | `fail_closed` | An invalid policy blocks the sandbox instead of being ignored |
| Container sockets | none exposed to sandboxes | No Docker or Podman socket inside a sandbox |

## Sandboxes and policies

Policies live in [`infra/next-phase/openshell/`](../../infra/next-phase/openshell).
Filesystem, Landlock, and process sections are fixed when a sandbox is created;
network rules can be changed on a running sandbox only by the operator through
the gateway.

**Invoice sandboxes** (the website agent): one fresh sandbox per run.

| Control | Planning | Execution |
| --- | --- | --- |
| Policy | [`invoice-planning-policy.yaml`](../../infra/next-phase/openshell/invoice-planning-policy.yaml) | [`invoice-execution-policy.yaml`](../../infra/next-phase/openshell/invoice-execution-policy.yaml) |
| Landlock | `hard_requirement`: refuses to start rather than run unrestricted | same |
| Writable | `/tmp` only | same |
| Network | Only `/usr/local/bin/python3.12` may reach the Planning gateway: `POST` summary, batches, inference | Only Python may reach the Execution gateway: `POST` execute_step, inference |
| L7 | `protocol: rest`, `enforcement: enforce`, exact method and path rules; HTTPS is inspected automatically | same |

The invoice agent image build fails if any path the policies list is missing
([`containers/invoice-agent.Dockerfile`](../../containers/invoice-agent.Dockerfile)).

**Fixed-proof sandboxes** (`planning-demo`, `execution-demo`, `probe-demo`):
created at bootstrap to prove boundaries, including a Probe sandbox with no
network access at all. Their policies are hash-pinned by the retained-workspace
scripts and still use Landlock `best_effort` and the deprecated no-op
`tls: terminate`; harden them only together with a full rebootstrap.

## Run credentials

Each invoice run is authorized by a short-lived, run-scoped capability that the
gateways check against SQL.

- **Default (`manifest`)**: the capability is delivered to the agent in the
  run manifest.
- **Opt-in (`provider`)**: the VM host mints the capability into a per-run
  [OpenShell provider](https://docs.nvidia.com/openshell/sandboxes/manage-providers).
  Its profile ([`invoice-planning-provider-profile.yaml`](../../infra/next-phase/openshell/invoice-planning-provider-profile.yaml),
  [`invoice-execution-provider-profile.yaml`](../../infra/next-phase/openshell/invoice-execution-provider-profile.yaml))
  binds it to that role's gateway routes. The agent receives only an
  `openshell:resolve:env:` placeholder, which the proxy resolves in the
  `Authorization` header; only the capability's hash leaves the VM. Stopping
  the run expires the credential. Enable with
  `LEARNINGNEMO_INVOICE_CREDENTIAL_MODE=provider` after one live verification;
  see the [infrastructure README](../../infra/next-phase/README.md#provider-mediated-run-capability-opt-in).

## Run lifecycle

Implemented in [`console/invoice_remote_runtime.py`](../../src/task_agent/console/invoice_remote_runtime.py)
and driven by [`control/invoice_controller.py`](../../src/task_agent/control/invoice_controller.py):

1. **Admit**: a root-owned gate file, one active sandbox, at most 24 retained,
   a 4 GiB disk floor.
2. **Prepare**: create the sandbox from the pinned image and policy, pin guest
   DNS to Azure DNS, arm a 600-second stop timer, and confirm OpenShell reports
   exactly the checked-in policy (otherwise the run is refused).
3. **Admit in SQL**: bind the run, sandbox ID, policy hash, and capability hash.
4. **Execute**: `sandbox exec` the NAT agent; events stream back and are
   checked for sequence and identity.
5. **Stop and retain**: the sandbox is stopped and kept for evidence.

## Evidence

OpenShell writes OCSF records of its network decisions, and denials include the
enforcing layer. Boundary challenges record a permitted control request, a
forbidden request (403 from the L7 policy), the matching OCSF denial, and a
confirmed sandbox stop. See the [invoice demo guide](../guides/invoice-demo.md)
and the [bootstrap diagnostic record](../archive/openshell-bootstrap-diagnostic-record.md).

## Gaps

- **Controller transport**: the workspace controller reaches the VM through
  Azure Run Command, which is root on the VM. An outbound, command-restricted
  channel is still to be built.
- **Egress**: the sandbox proxy allows only the gateway routes, but the VM's
  network rules allow HTTPS to any `AzureCloud` address; see
  [Secure Agent Workspace](secure-agent-workspace.md#network-perimeter).
- **Provider clean-up**: providers of deleted sandboxes are not
  garbage-collected; their credentials are already expired.
- **Signed policies**: policies are not signed or attested.
