# Dedicated Approver Account

The demo now has a dedicated Entra account named `LearningNeMo Approver`, with
UPN `learningnemo-approver@<initial-tenant-domain>` and the API app role
`Task.Approver`. It is distinct from the Reader and Operator users. It has no
Reader or Operator app-role assignment; provisioning grants no directory role,
Azure RBAC permission, license, or mailbox.

## Provisioning and Verification

Use the repository's existing non-secret Entra settings and intentionally select
the expected subscription. From the repository root in WSL:

```bash
export AZURE_SUBSCRIPTION_ID="<expected-subscription-id>"
python3 infra/create-entra-approver.py
LEARNINGNEMO_AZURE_APPLY=approver-identity \
  python3 infra/create-entra-approver.py --apply
```

The first command previews the role/user/assignment changes. The second applies
them. The initial managed tenant domain is resolved from Microsoft Graph. The
role ID is deterministic. Existing app roles, client scopes, users, and their
assignments are preserved. Reruns reuse only an account with matching private
ownership evidence and never reset its password. An unowned username collision,
disabled account, differing role contract, or unexpected LearningNeMo role
assignment blocks the workflow for review.

## First Sign-In

The generated temporary credential is stored only in this owner-only WSL file,
outside the repository:

```text
/home/aygul/.local/state/learningnemo/approver-dev.temporary-credential.json
```

Open it locally, outside any recording or shared screen. Do not paste the
password into chat or a source file. Use a separate browser profile to sign into
[Microsoft My Account](https://myaccount.microsoft.com/) and complete the required
password change. Apply the tenant's MFA/Conditional Access requirements normally;
the provisioning script does not disable them. Delete the local temporary
credential after securely establishing the new password. Keep the non-secret
owner-only identity record for idempotent provisioning:

```text
~/.local/state/learningnemo/approver-dev.identity.json
```

## Capability Boundary

The connected queue and exact-plan decision APIs are now deployed with stable
Entra tenant/object identity binding. After ordinary Approver sign-in, choose
**Authorize review access** for the separately consented `plans.review` scope.
The [live checkpoint](human-handoff-live-checkpoint.md) records deployment proof,
remaining real-user rehearsal gates, and the scheduled service expiry.

The console accepts the dedicated `Task.Approver` account in the same
**Demo session** view as Reader and Operator. Its content is limited to reviewer
availability and authorized plan review; it does not need `Task.Reader` or `Task.Operator`.
The API re-verifies cloud JWTs and requires `plans.review` for the connected queue
and exact-plan decisions. Chat, task walkthroughs, workspace status, and
workspace execution are blocked server-side. Combined Approver/Operator role
assignments are rejected instead of silently selecting an elevated persona.

Refresh the cloud page and sign in again after deployment. The old rejection
was an application limitation, not a reason to add execution roles or reset
the account password. First-sign-in password change and tenant security policies
still apply normally.

The deployed review service reads actual persisted plans. Approval controls
appear only for eligible plans and require explicit exact-plan confirmation.
An empty result is not an incident run, and no plan is fabricated to populate
the queue. Approval never executes remediation. Without configured service
origins, the console retains an explicit unavailable state.

Reader and Operator task tests do not create a plan or hand off to this account.
Use the common Switch account control to clear the current demo session and
start Microsoft sign-in for a different assigned account. This changes the
actual authenticated identity, not a browser-selected permission profile.

The deployed review boundary enforces `Task.Approver` plus `plans.review` on the
server. The remaining full incident integration must supply genuine recorded
investigations and connect the separate execution/verification handoff.
The critical-plan author/executor must not approve their own plan.
Approval must bind the exact plan hash and cannot grant direct execution rights
or expose an arbitrary SQL/command interface. The current scripted cycle's
Azure-CLI-derived approval subject is not replaced by creating this account.