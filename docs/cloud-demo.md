# Cloud-Hosted Demo

This project's core purpose is demonstrating OpenShell sandboxes and
per-sandbox MicroVM isolation inside the Secure Agent Workspace (SAW); see the
[root README](../README.md#why-openshell-and-microvms-matter) and
[ADR 0001](decisions/0001-openshell-microvm-driver.md) for that architecture.
This guide covers the supporting cloud deployment: it moves the dashboard, its
fixed workspace controller, and the existing NeMo task API into Azure so the
sandbox demonstration and its supporting services are reachable without a
running local console. It does not move an operator's Azure CLI profile, SSH
key, or provider API key into the dashboard image.

The cloud workspace controller described below is a trusted Azure service that
calls into the SAW/OpenShell sandbox lifecycle; the existing NeMo task API is a
separate cloud service and is not itself agent code running inside a sandbox,
and not all cloud workflows covered here execute inside a MicroVM. Where a
separate invoice-agent integration runs Planning/Execution agents inside their
own OpenShell MicroVMs, see the [governed invoice incident demo](invoice-demo.md)
for that distinct, dated workflow — do not treat it as the same thing as the
fixed workspace-controller proof described here.

## Runtime Boundaries

| Component | Hosting | Authority |
| --- | --- | --- |
| Dashboard | HTTPS Azure Container App, public sign-in shell | Entra-verified session; only AcrPull on its identity |
| Workspace controller | Internal-ingress Container App | Separate managed identity; selected reads and Run Command on the single SAW VM |
| NeMo task API | Internal-ingress Container App | Existing JWT/tool authorization; managed-identity read of the internal APIM client secret |
| SAW/OpenShell | Private Azure VM with expiring sandbox session | Image-native non-root processes, exact policy routes, no host runtime-identity RBAC |
| Trusted SQL services | Existing separate Container Apps and private SQL | Dedicated identities and approval-bound operations |

Only the user's browser runs on the workstation. Once deployed, cloud requests
must not depend on a running WSL console or local agent API. The NeMo task API
is a cloud service, not an agent running inside OpenShell. The current workspace
proof is a separate fixed operation, not the full model/approval/SQL workflow.

The public dashboard shell exposes sign-in, but demo records and operations
require assigned Entra identities. Reader/Operator retain their task permissions;
the dedicated Approver can access its status view and, after explicit review
authorization, the connected plan queue and exact-plan decisions. It cannot use
chat or workspace operations. Combined Approver/Operator assignments are rejected.
All roles share one Demo session destination and the same account controls.
Role tests are previewed without task execution. Reader tests denial; Operator
tests reset/write/read-back. Neither test creates a pending Approver plan.
The current device-code flow is
retained for the scheduled prototype; never share login codes or credentials.
Cloud cookies are Secure, HttpOnly, and SameSite; mutation requests require the
configured HTTPS Origin and session CSRF token. Proxy headers are not trusted to
choose the security origin. Controller authorization independently verifies the
same user token, instead of trusting browser-supplied role fields.

The controller's Azure Run Command permission is privileged on the host. Azure
RBAC limits it to the SAW VM but does not restrict script contents. The private
controller code permits only the fixed proof, with lease and network checks.
Do not describe that transport grant as an unprivileged sandbox identity.

## Build and Deploy

The separate human-handoff services are now deployed alongside this three-service
dashboard deployment. See the [live checkpoint](human-handoff-live-checkpoint.md)
for verified SQL, identity, and ingress results and outstanding human rehearsal.
Preserve the explicit incident/review origin settings when redeploying the
dashboard; the default deployment does not enable them automatically.

Prerequisites: follow [Build and Reproduce](build-and-reproduce.md) to establish
the existing Container Apps environment, ACR, Entra client/audiences, APIM,
Key Vault secret, and preserved SAW resource. Azure CLI deployment credentials
are needed on the operator workstation only for deployment, not runtime.

Run from the repository root in WSL. Select the intended subscription explicitly:

```bash
export AZURE_SUBSCRIPTION_ID="<expected-subscription-id>"
test "$(az account show --query id -o tsv)" = "$AZURE_SUBSCRIPTION_ID"
LEARNINGNEMO_AZURE_APPLY=cloud-demo-build bash scripts/build-cloud-demo.sh
bash infra/next-phase/deploy-cloud-demo.sh --what-if
LEARNINGNEMO_AZURE_APPLY=cloud-demo bash infra/next-phase/deploy-cloud-demo.sh --apply
python3 infra/next-phase/verify-cloud-demo.py \
  --parameters "$HOME/.local/state/learningnemo/cloud-demo.parameters.json" \
  --output "$HOME/.local/state/learningnemo/cloud-demo.result.json"
```

The build uses an allowlisted temporary context, excludes bytecode and local
settings, installs hash-locked Python dependencies, and records immutable OCI
image references outside the repository.

For a dashboard-only change, pass `console` to `scripts/build-cloud-demo.sh`.
It builds the shared dashboard/controller image and records its own source
hash without rebuilding or replacing the cloud agent image. The default builds
both images. Deploy through the same preview and verification steps afterward.

The Python base is digest-pinned;
Debian OS package installation in the agent build is not bit-for-bit pinned.
These cloud image build records are not the separately signed WP3 trusted-worker
release attestations. A built image must not be described as scanned or signed
without its own corresponding evidence.

Deployment is limited to `rg-learningnemo-demo-dev`, three identities/apps, and
seven declared assignments on the existing registry, gateway secret, SAW, and
diagnostic app. The dashboard has no VM execution grant. The controller cannot
change the VM, its network policy, or role assignments. The cloud agent cannot
read the provider's OpenAI secret; it retrieves only `llm-gateway-client-key`.

The three custom role definitions and assignments have deterministic names so
the what-if preview can resolve them before identity creation. Unknown preview
changes must be investigated, not accepted as success.

## Availability and Limits

This is a scheduled, single-replica demo deployment. Each service currently has
one minimum replica during the scheduled window; session state is in memory and
is lost on restart. This avoids adding a token database, but it is not a
multi-replica production session design. Revisions/restarts require sign-in again.

The deployment lease is bounded by the registry and Container Apps environment.
The SAW has a separate lease and can already be stopped or expired while the
dashboard is available. The dashboard checks the workspace on explicit request;
it neither extends the lease nor starts the VM automatically.

Expiry tags and subscription budget alerts do not automatically remove cloud
compute. Until a trusted scheduler invokes cleanup, the operator must do so at
the end of the scheduled window. A stopped guest also does not guarantee Azure
deallocation or zero charges.

The first user rehearsal still requires actual role-assigned accounts. No role
assignment is created for an arbitrary signed-in user by the app. A browser
`401` before sign-in is expected, not proof that the live scenario failed.
Conversely, a running container is not proof that the agent answered a prompt or
that the workspace reached its permitted broker route.

## Verification Levels

1. Local tests: HTTPS host/origin/cookie behavior, JWT verification, Reader denial,
   fixed controller routes, request binding, and no arbitrary command payloads.
2. Cloud verifier: deployed images, running service state, ingress split, identity
   assignments, security headers, and anonymous data denial.
3. Human rehearsal: Reader read/denial, Operator task write/read-back, and a
   user-bound workspace proof with its actual network result.
4. Full integration: AgentRunner delegation, sandbox reasoning, separate human
   approval, broker remediation, authoritative verification, and correlated proof.

Levels 3 and 4 are not implied by passing level 2. The existing SAW proof observed
non-root execution but timed out reaching the protected API after NAT removal.
That runtime-connectivity problem is not solved by cloud-hosting the dashboard.

## Cleanup

Preview first, then explicitly remove the cloud demo apps, identities, grants,
and unused custom role definitions:

```bash
python3 infra/next-phase/remove-cloud-demo.py
LEARNINGNEMO_AZURE_DELETE=cloud-demo \
  python3 infra/next-phase/remove-cloud-demo.py --apply
```

This does not delete the SAW, trusted workers, registry, Key Vault, or shared
Container Apps environment. Remove the cloud demo before tearing down those
dependencies. Its grants intentionally expand the registry's pull allowlist:

```bash
python3 infra/next-phase/verify_artifacts.py \
  --parameters "$HOME/.local/state/learningnemo/artifacts-dev.parameters.json" \
  --allow-cloud-demo
```

The original artifact verifier remains strict by default. After cloud demo
cleanup, run it without `--allow-cloud-demo` to require the original five pull
identities again. Preserve evidence and build records outside the repository.