# Human Handoff Deployment Contract

The implemented path is trusted investigation -> Operator submission ->
independent Approver decision -> refreshed Operator plan state. Execution and
independent recovery verification are not performed by either human-facing API.
The investigator must still be connected to actual sandbox and worker results.

## Three Authorities

| Principal | Fixed SQL procedure permissions | Must not have |
| --- | --- | --- |
| Trusted investigation runtime | `control.usp_record_human_investigation` | Human review decisions or direct remediation |
| Incident API managed identity | `control.usp_list_human_incidents`, `control.usp_submit_human_plan` | Investigation recording, approval issuing, execution, direct table DML |
| Review API managed identity | `control.usp_list_review_plans`, `control.usp_decide_review_plan` | Investigation recording, submission, direct `usp_issue_approval`, execution, direct table DML |

The public dashboard receives none of these grants. It forwards the user's
token to fixed private routes; both APIs independently verify the token.
`Task.Operator` plus the existing task scopes admit incident submission;
`Task.Approver` plus `agent.invoke` and `plans.review` admit independent review.
The token's signed tenant/object IDs determine the human identity hash.

Do not expose the recording procedure through the browser. A hash is a content
binding, not proof of authenticity: only the trusted investigation runtime may
record worker results, after verifying their origin and context. Legacy scripted
actor hashes and fabricated evidence rows must not be migrated into this path.

## Build

From WSL at the repository root:

```bash
export AZURE_SUBSCRIPTION_ID="<expected-subscription-id>"
LEARNINGNEMO_AZURE_APPLY=cloud-demo-build bash scripts/build-cloud-demo.sh human
```

The new image uses [cloud-human.Dockerfile](../../containers/cloud-human.Dockerfile).
The build uses the existing allowlisted context, runs both API import checks
and SQL driver import as UID 65532, and records the pinned reference in
`~/.local/state/learningnemo/cloud-human.image.txt`. Building does not deploy a
service, grant SQL access, or start an incident. The existing `all` build still
means console and agent; the human image is opt-in.

## Stage and Validate

[human-services.bicep](../../infra/next-phase/human-services.bicep) is resource-group
scoped. Use a separately owned disposable group, not the existing demo group.
Its default `deployApps=false` creates only the two managed identities. It
contains no implicit ACR or SQL role grants. Apply only after an owned-resource
what-if and the existing subscription budget/lease checks.

Required parameters: `location`, `environmentId`, `registryServer`, `image`,
`expiresAt`, `tenantId`, `apiClientId`, `publicClientId`, `sqlServer`,
`sqlDatabase`, and `deployApps`. Use the existing East US demo environment,
the built digest, and a UTC lease between five minutes and two hours. The lease
must also fit within the environment, registry, database and workspace leases.

Compile and validate before a deployment preview:

```bash
az bicep build --file infra/next-phase/human-services.bicep \
  --outfile "$HOME/.local/state/learningnemo/human-services.template.json"
python3 infra/next-phase/validate_human_services.py \
  --template "$HOME/.local/state/learningnemo/human-services.template.json" \
  --parameters "$HOME/.local/state/learningnemo/human-services.parameters.json" \
  --subscription "$AZURE_SUBSCRIPTION_ID"
```

The operator parameter file is intentionally not generated from guessed live
resources. Use verified values. Review the resource-group what-if before apply;
reject unexpected resources, deletions, role grants, or existing ownership
collisions. A successful local validator is not approval to skip live preflight.

## SQL and Activation

Version and apply the staged SQL through the reviewed migration mechanism:

1. [001_review_boundary.sql](../../infra/next-phase/review-service/001_review_boundary.sql)
2. [002_incident_handoff.sql](../../infra/next-phase/review-service/002_incident_handoff.sql)

These are not automatically appended to the six existing immutable migration
receipts. Do not edit or replay an old applied migration to include them.
Provision each contained SQL principal with only the procedure grants above;
grant each API identity AcrPull at the single registry. Review those additions
against existing exact-grant verifiers before changing their expected sets.

Register and consent `plans.review` for the intended Entra API/client, without
giving Approver an Operator role. After grants, enable `deployApps=true` with
enough remaining lease for startup and rehearsal. Readiness must pass using
the real SQL driver and the deployed procedures. Test commit/rollback,
concurrent submissions/decisions, expired contexts, wrong-owner reads, and
replay against actual SQL before accepting the deployment.

Only after those checks, set the exact internal service origins and deploy the
dashboard through its existing guarded script:

```bash
export LEARNINGNEMO_INCIDENT_ORIGIN="https://ca-learningnemo-incident-dev.internal.<existing-environment-domain>"
export LEARNINGNEMO_REVIEW_ORIGIN="https://ca-learningnemo-review-dev.internal.<existing-environment-domain>"
export LEARNINGNEMO_HUMAN_SERVICES_VERIFIED=yes
LEARNINGNEMO_AZURE_APPLY=cloud-demo-build bash scripts/build-cloud-demo.sh console
LEARNINGNEMO_AZURE_APPLY=cloud-demo bash infra/next-phase/deploy-cloud-demo.sh --apply
```

The acknowledgement is a deployment gate, not automated proof of the preceding
SQL/consent checks. Preserve these explicit origins for subsequent deployments;
omitting them leaves the human integrations disconnected. They are not added
to existing cloud deployment parameters by merely building the new image.

## Rehearsal and Cleanup

The incident producer must authenticate the sponsoring Operator, bind a real
engagement and AgentRunner, and obtain matching diagnosis/containment results.
`TrustedInvestigationRecorder.record` validates and stores those documents;
it does not itself run a model, create a workspace, or contact the workers.

In separate browser profiles, submit the draft as its Operator and review the
same hash/version as Approver. Return to Operator to observe the changed plan
state. Neither browser may directly write evidence, choose an actor hash,
approve as Operator, submit as Approver, or execute through these APIs.

At expiry, the APIs reject new business requests. That does not delete compute.
Remove only the dedicated human-service resources and their exact ACR/SQL
grants after preserving sanitized receipts; the existing cloud-demo removal
script does not own this separate resource group. No automatic cleanup is
claimed by this template.