# Status

This page is the single summary of what is implemented and what has been
verified. Dated records in [`archive/`](archive/README.md) hold the evidence;
a recorded result is not current live health.

Last updated 2026-09-26.

## Verified live

| Capability | Result | Evidence |
| --- | --- | --- |
| Local task agent authorization | Reader denied mutations, Operator executed, both through Entra roles and scopes | [Local walkthrough](guides/local-authorization.md) |
| Private workspace VM and OpenShell bootstrap | Three MicroVM sandboxes created; policy and boundary probes passed (2026-09-14) | [Bootstrap record](archive/openshell-bootstrap-diagnostic-record.md) |
| Runtime network lock | Runtime NSG rules repaired and verified; fixed Planning route proof passed under lockdown (2026-09-15) | [Runtime diagnosis](archive/diagnose-runtime.md) |
| Trusted SQL control cycle | Eight-stage incident cycle independently verified | [Build guide](guides/build-and-reproduce.md) |
| Invoice workflow | Real Planning and Execution agents in separate MicroVMs, broker receipts, five independent SQL checks (2026-09-16, synthetic reviewer) | [Invoice demo](guides/invoice-demo.md#observed-acceptance) |
| Approver UI | Independent human approval path validated (2026-09-19) | [Invoice demo](guides/invoice-demo.md#approver-ui-validation-september-19) |
| CI | Tests, infrastructure gates, and browser tests run on every pull request | [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) |

## Merged, not yet deployed

These are in `main` and covered by tests, but the live environment runs images
built before them. Each image builds on the previous one, so rebuild in this
order: `scripts/build-cloud-demo.sh agent`, `scripts/build-invoice-agent.sh`,
`scripts/build-invoice-services.sh`; then stage the invoice agent image on the
VM (`infra/next-phase/stage_invoice_image.py`) and redeploy
(`infra/next-phase/deploy_invoice_services.py`, `infra/next-phase/deploy-cloud-demo.sh`).

| Change | Where described |
| --- | --- |
| NeMo Guardrails output and execution rails on both agents | [NeMo Guardrails](nvidia/nemo-guardrails.md) |
| Invoice sandbox policies: Landlock `hard_requirement`, Python-only egress | [OpenShell](nvidia/openshell.md#sandboxes-and-policies) |
| Opt-in OpenShell provider mediation of run capabilities | [OpenShell](nvidia/openshell.md#run-credentials) |

After redeploying, run one Planning and Execution cycle and the boundary
challenges before relying on them.

## Open gaps

| Gap | Component |
| --- | --- |
| Controller reaches the VM through Azure Run Command (root on the VM) | [OpenShell](nvidia/openshell.md#gaps) |
| VM egress allows HTTPS to any `AzureCloud` address; no firewall with hostname rules | [SAW](nvidia/secure-agent-workspace.md#network-perimeter) |
| No signed policies or signed delegation record | [SAW](nvidia/secure-agent-workspace.md) |
| No brokered interactive SSO; device-code sign-in | [SAW](nvidia/secure-agent-workspace.md) |
| Fixed-proof sandbox policies still use Landlock `best_effort` | [OpenShell](nvidia/openshell.md#sandboxes-and-policies) |
| No guardrail evaluation dataset | [NeMo Guardrails](nvidia/nemo-guardrails.md#gaps) |
| Only the trusted-worker image is signed and scanned | [`containers/README.md`](../containers/README.md) |
| Clean-host reproduction of the workspace not yet repeated | [Build guide](guides/build-and-reproduce.md) |
