"""Review-safe presentation of dated engineering results, not live telemetry."""

SHOWCASE = {
    "schemaVersion": 1,
    "source": "recorded-summary",
    "recordedOn": "2026-09-14",
    "currentHealth": "unknown",
    "title": "Useful agents. Bounded authority.",
    "summary": "A private workspace, three distinct sandbox policies, and application authority that stays outside the model.",
    "qualification": "Historical snapshot from 14 September 2026, not current cloud status. The runtime-lock failure below is from that deployment attempt; this record does not report today's VM, network, or approval-service health.",
    "milestones": [
        {"label": "Private SAW", "value": "Bootstrap passed", "state": "passed", "icon": "server"},
        {"label": "OpenShell", "value": "3 MicroVMs", "state": "passed", "icon": "boxes"},
        {"label": "Temporary NAT", "value": "Removed", "state": "passed", "icon": "unplug"},
        {"label": "Runtime lock / 14 Sep attempt", "value": "Failed in this record", "state": "failed", "icon": "shield-alert"},
    ],
    "sandboxes": [
        {
            "id": "planning", "name": "Planning", "icon": "scan-search", "role": "Investigate",
            "process": "sandbox (non-root)", "destination": "Diagnostic API",
            "method": "GET", "path": "/v1/diagnostics/current",
            "purpose": "Observation without mutation",
            "reason": "Diagnosis needs evidence, not write access. The planning process gets one diagnostic route; application authentication is still required.",
            "tradeoff": "A new diagnostic endpoint requires a reviewed policy change. Less convenience buys a smaller network surface.",
            "checks": [
                {"label": "Read the approved route", "request": "GET /v1/diagnostics/current", "expected": "401", "observed": "401", "verdict": "Route reached", "kind": "allowed", "detail": "The request reached the protected API. Anonymous access was rejected. This is not an authenticated diagnosis."},
                {"label": "Attempt a write", "request": "POST /v1/diagnostics/current", "expected": "403", "observed": "403", "verdict": "Expected denial", "kind": "denied", "detail": "Changing only the HTTP method is denied by the sandbox policy. The allowed read does not confer write authority."},
                {"label": "Leave the approved path", "request": "GET /v1/diagnostics/not-approved", "expected": "403", "observed": "403", "verdict": "Expected denial", "kind": "denied", "detail": "A trusted hostname is not unrestricted access to every route on that host."},
            ],
            "receipt": "PASS openshell_planning_execution_separation",
        },
        {
            "id": "execution", "name": "Execution", "icon": "workflow", "role": "Act through a broker",
            "process": "sandbox (non-root)", "destination": "Remediation API",
            "method": "POST", "path": "/v1/remediations/execute",
            "purpose": "A narrow route, not a database credential",
            "reason": "The execution process can reach a specific broker operation. Authorization and approval belong to trusted services, not to a model-generated instruction.",
            "tradeoff": "Broker integration adds a service boundary. It also keeps SQL credentials and broad database access outside the sandbox.",
            "checks": [
                {"label": "Reach the execution broker", "request": "POST /v1/remediations/execute", "expected": "401", "observed": "401", "verdict": "Route reached", "kind": "allowed", "detail": "The broker route is reachable, but anonymous execution is denied. No approved remediation was performed by this probe."},
                {"label": "Use the wrong method", "request": "GET /v1/remediations/execute", "expected": "403", "observed": "403", "verdict": "Expected denial", "kind": "denied", "detail": "Execution permission is a method-and-path policy, not blanket access to the service."},
                {"label": "Use another broker route", "request": "POST /v1/remediations/not-approved", "expected": "403", "observed": "403", "verdict": "Expected denial", "kind": "denied", "detail": "An unapproved operation on the same destination remains outside this sandbox's authority."},
            ],
            "receipt": "PASS openshell_planning_execution_separation",
        },
        {
            "id": "probe", "name": "Probe", "icon": "shield-check", "role": "Test the boundary",
            "process": "sandbox (non-root)", "destination": "No network allow rules",
            "method": "NONE", "path": "Default-deny network policy",
            "purpose": "Test enforcement independently of the model",
            "reason": "A dedicated probe process tests what is reachable and writable. A refusal in assistant text is not evidence of operating-system or network isolation.",
            "tradeoff": "These bounded probes test specific destinations and operations. They are not a comprehensive isolation audit or proof against every escape technique.",
            "checks": [
                {"label": "External Internet", "request": "HTTPS request to example.com", "expected": "No access", "observed": "No access", "verdict": "Expected denial", "kind": "denied", "detail": "The bootstrap observed no successful external request. A negative network result alone does not identify the enforcing layer."},
                {"label": "Cloud metadata and SQL", "request": "IMDS request + direct SQL connection", "expected": "No access", "observed": "No access", "verdict": "Expected denial", "kind": "denied", "detail": "Both tested connections failed. The host identity is not a source of credentials available to the sandbox."},
                {"label": "Host and privilege boundary", "request": "Host paths / sockets / write to /etc / setuid(0)", "expected": "Denied", "observed": "Denied", "verdict": "Expected denial", "kind": "denied", "detail": "Host paths and sockets were absent, the process was non-root, and protected writes and privilege escalation failed."},
            ],
            "receipt": "PASS openshell_probe_denials",
        },
    ],
    "journey": [
        {"id": "workspace", "name": "Isolate", "owner": "Azure SAW", "status": "Recorded", "icon": "server", "heading": "One private engagement boundary", "body": "An Ubuntu Trusted Launch VM hosts the OpenShell gateway. No public VM IP was provisioned. The runtime identity has no Azure RBAC grants.", "proof": "Private host bootstrap and nested-virtualization checks passed.", "limit": "A running VM is not proof of sandbox health or complete host lockdown."},
        {"id": "plan", "name": "Constrain", "owner": "OpenShell", "status": "Recorded", "icon": "boxes", "heading": "Three policies, three different jobs", "body": "Planning reads a diagnostic route. Execution reaches a broker operation. Probe has no network allow rules. Each process uses the image-native non-root account.", "proof": "Permitted routes returned 401; disallowed methods and paths returned 403.", "limit": "These were unauthenticated routing probes, not the full incident workflow."},
        {"id": "authority", "name": "Authorize", "owner": "Trusted services", "status": "Separate evidence", "icon": "key-round", "heading": "The model cannot approve its own plan", "body": "The separately verified SQL control cycle binds a structured plan, represented approval actors, bounded remediation, and authoritative verification in a scripted control job.", "proof": "An eight-stage trusted-service incident cycle was independently verified.", "limit": "No interactive human approval UI or sandbox-originated incident cycle is demonstrated here."},
        {"id": "lock", "name": "Lock down", "owner": "Azure network", "status": "Failed / 14 Sep attempt", "icon": "shield-alert", "heading": "Historical runtime-lock failure", "body": "During the 14 September deployment, temporary bootstrap NAT was removed and Azure rejected part of the NSG deployment with SecurityRuleInvalidAccessType.", "proof": "The failed attempt is preserved here as historical evidence, not a current deployment status.", "limit": "This snapshot predates subsequent repair work. Current network rules and sandbox connectivity require separate checks."},
        {"id": "complete", "name": "Integrate", "owner": "Next milestone", "status": "Pending", "icon": "git-pull-request", "heading": "Close the loop with real execution", "body": "The next acceptance result is a complete sandbox-to-broker incident journey, followed by a reproducible deployment on a fresh host.", "proof": "Not yet established.", "limit": "The local identity walkthrough, sandbox probes, and trusted control cycle remain separate demonstrations."},
    ],
    "decisions": [
        {"id": "identity", "icon": "badge-check", "title": "Identity is not isolation", "choice": "Entra checks the caller; OpenShell constrains the process.", "reason": "A valid identity can still request an operation its process should never reach. Independent boundaries reduce the authority available at each layer.", "cost": "More policies to align and test.", "evidence": "Reader/Operator tool tests and separate sandbox route probes."},
        {"id": "nonroot", "icon": "user-round-check", "title": "Non-root by construction", "choice": "Use the pinned image's native sandbox account.", "reason": "The guest's own identity contract avoids arbitrary UID overrides and keeps process privilege separate from host administration.", "cost": "Image updates require compatibility tests.", "evidence": "Non-root execution, protected-write denial, and failed setuid(0)."},
        {"id": "supply", "icon": "fingerprint", "title": "Pin what actually runs", "choice": "Package hashes, immutable images, and source-bound release evidence.", "reason": "A version label is not enough to identify an artifact. Hash and signature checks let a reviewer trace the build inputs and accepted release.", "cost": "Updates require a new scan and release validation.", "evidence": "Separate SBOM, vulnerability, detached signature, and source-binding gates."},
        {"id": "diagnosis", "icon": "microscope", "title": "Keep the failed machine", "choice": "Capture evidence before restart or deletion.", "reason": "A syscall trace exposed an overlay traversal failure: execve returned EACCES after the process correctly dropped privilege. A controlled A/B established the cause.", "cost": "Diagnostic resources need a bounded lease and explicit cleanup.", "evidence": "Overlay root 0700 failed; 0755 succeeded. Gateway service UMask=0022 fixes creation."},
        {"id": "expiry", "icon": "timer", "title": "Temporary by design", "choice": "Bound workspace lifetime to its dependencies.", "reason": "Demo infrastructure should not become unowned standing infrastructure. Expiration is a runtime control; scoped teardown is a separate obligation.", "cost": "Rehearsals need valid leases and a cleanup owner.", "evidence": "Guest expiry timer plus guarded Azure cleanup. Poweroff alone does not guarantee deallocation or stop all charges."},
    ],
    "build": [
        {"label": "Host", "value": "Ubuntu 24.04 / Trusted Launch", "kind": "Configured"},
        {"label": "OpenShell", "value": "0.0.116 / hash-verified package", "kind": "Recorded"},
        {"label": "Compute", "value": "KVM + libkrun MicroVMs", "kind": "Recorded"},
        {"label": "Gateway", "value": "mTLS / 900-second sandbox JWT", "kind": "Configured"},
        {"label": "Images", "value": "Immutable OCI digest references", "kind": "Configured"},
        {"label": "Fresh-host proof", "value": "Pending", "kind": "Pending"},
    ],
    "documents": [
        {"id": "build", "title": "Build & reproduce", "description": "Dependencies, release gates, deployment, and cleanup", "icon": "blocks"},
        {"id": "demo", "title": "Presenter runbook", "description": "Eight-minute script, expected outcomes, and claim boundaries", "icon": "presentation"},
        {"id": "diagnostics", "title": "Engineering record", "description": "Failures, controlled experiments, and verified repairs", "icon": "file-search"},
    ],
}

SHOWCASE_DOCUMENTS = {
    "build": "build-and-reproduce.md",
    "demo": "capability-demo.md",
    "diagnostics": "openshell-bootstrap-diagnostic-record.md",
}