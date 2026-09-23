"""Opt-in, operator-transport workspace proof with no caller-supplied commands."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import re
import shlex
import subprocess
import threading
from typing import Any

from task_agent.console.identity import EntraTestSettings
from task_agent.security.jwt_claims import decode_jwt_payload, string_claim_values


class WorkspaceLiveError(RuntimeError):
    pass


class WorkspaceIdentityVerifier:
    def __init__(self, settings: EntraTestSettings) -> None:
        from nat.authentication.jwt.jwt_auth_provider import JwtAuthProvider
        from nat.authentication.jwt.jwt_auth_provider_config import JwtAuthProviderConfig

        self.settings = settings
        self.provider = JwtAuthProvider(JwtAuthProviderConfig(
            issuer_url=f"https://login.microsoftonline.com/{settings.tenant_id}/v2.0",
            jwks_uri=f"https://login.microsoftonline.com/{settings.tenant_id}/discovery/v2.0/keys",
            audience=settings.api_client_id,
            scopes=[],
        ))

    async def verify(self, token: str) -> dict[str, Any]:
        result = await self.provider.verify(token)
        now = dt.datetime.now(dt.UTC).timestamp()
        if (
            not result.active or not result.subject
            or result.client_id != self.settings.public_client_id
            or result.iat is None or not 0 <= now - result.iat <= 900
        ):
            raise WorkspaceLiveError("Identity verification failed; sign in again.")
        claims = decode_jwt_payload(token)
        if claims.get("sub") != result.subject:
            raise WorkspaceLiveError("Identity verification failed; sign in again.")
        scopes = string_claim_values(claims, "scp")
        roles = string_claim_values(claims, "roles")
        required_scopes = {"agent.invoke", "tasks.execute"}
        required_roles = {"Task.Reader", "Task.Operator"}
        is_approver = "Task.Approver" in roles
        if is_approver and "Task.Operator" in roles:
            raise WorkspaceLiveError("Approver and Operator assignments must be separate.")
        return {
            "allowed": not is_approver and required_scopes <= scopes and required_roles <= roles,
            "canRead": not is_approver and {"agent.invoke", "tasks.read"} <= scopes and "Task.Reader" in roles,
            "canReview": is_approver and "agent.invoke" in scopes,
            "requiredScopes": sorted(required_scopes), "requiredRoles": sorted(required_roles),
            "grantedScopes": sorted(scopes), "grantedRoles": sorted(roles),
            "enforcedBy": "Console workspace-proof broker after JWT signature verification",
        }


RULES = {
    "deny-sandbox-host-imds": (121, "Deny", "AzurePlatformIMDS", "*", "*"),
    "allow-azure-platform-dns": (122, "Allow", "168.63.129.16/32", "53", "*"),
    "allow-approved-azure-https": (125, "Allow", "AzureCloud", "443", "Tcp"),
    "deny-general-internet-runtime": (130, "Deny", "Internet", "*", "*"),
}

BASE_RULES = {
    "deny-all-inbound": (100, "Inbound", "*"),
    "deny-trusted-platform-outbound": (100, "Outbound", "10.40.0.0/16"),
    "deny-saw-east-west": (110, "Outbound", "10.50.0.0/16"),
    "deny-direct-azure-sql": (120, "Outbound", "Sql"),
}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def cloud_snapshot(vm: dict, stack: dict, rules: list, subnet: dict, runtime_nat: dict | None = None) -> dict:
    now = dt.datetime.now(dt.UTC)
    expiry = (vm.get("tags") or {}).get("expiresAt")
    operator_managed = (vm.get('tags') or {}).get('availabilityMode') == 'operator-managed' and not expiry
    try:
        expires = dt.datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
        valid_lease = expires > now + dt.timedelta(minutes=5)
        expiry = expires.astimezone(dt.UTC).isoformat(timespec="seconds")
    except (ValueError, TypeError):
        valid_lease = False
        expiry = None
    if operator_managed:
        valid_lease = (vm.get('tags') or {}).get('admission') == 'enabled'
    observed = {rule.get("name"): rule for rule in rules}
    expected_names = set(RULES) | set(BASE_RULES)
    rules_match = set(observed) == expected_names and len(observed) == len(rules) and all(
        name in observed and (
            observed[name].get("priority"), observed[name].get("access"),
            observed[name].get("destinationAddressPrefix"), observed[name].get("destinationPortRange"),
            observed[name].get("protocol"),
        ) == expected
        and observed[name].get("direction") == "Outbound"
        and observed[name].get("sourceAddressPrefix") == "*"
        and observed[name].get("sourcePortRange") == "*"
        for name, expected in RULES.items()
    )
    rules_match = rules_match and all(
        (observed[name].get("priority"), observed[name].get("direction"), observed[name].get("destinationAddressPrefix")) == expected
        and observed[name].get("access") == "Deny"
        and all(observed[name].get(field) == "*" for field in ("protocol", "sourceAddressPrefix", "sourcePortRange", "destinationPortRange"))
        for name, expected in BASE_RULES.items()
    )
    lock = str(stack.get("provisioningState", "")).lower() == "succeeded" and rules_match
    running = vm.get("powerState") == "VM running"
    private = not vm.get("publicIps")
    owned = (vm.get("tags") or {}).get("project") == "learningnemo" and (vm.get("tags") or {}).get("owner") == "learningnemo-portfolio"
    nat_absent = not subnet.get("natGateway")
    runtime_nat_ready = False
    if runtime_nat:
        nat_tags = runtime_nat.get("tags") or {}
        try:
            managed_nat = operator_managed and nat_tags.get('availabilityMode') == 'operator-managed' and not nat_tags.get('expiresAt')
            nat_expiry = None if managed_nat else dt.datetime.fromisoformat(nat_tags.get("expiresAt", "").replace("Z", "+00:00"))
            runtime_nat_ready = (
                runtime_nat.get("name") == "nat-learningnemo-saw-runtime-dev"
                and runtime_nat.get("id", "").casefold() == str((subnet.get("natGateway") or {}).get("id", "")).casefold()
                and runtime_nat.get("properties", runtime_nat).get("provisioningState") == "Succeeded"
                and nat_tags.get("purpose") == "runtime-approved-egress"
                and nat_tags.get("project") == "learningnemo" and nat_tags.get("owner") == "learningnemo-portfolio"
                and (managed_nat or nat_expiry > now + dt.timedelta(minutes=5))
            )
        except (TypeError, ValueError):
            pass
    return {
        "source": "live-azure-query", "checkedAt": now.isoformat(timespec="seconds"),
        "availabilityMode": "operator-managed" if operator_managed else "leased",
        "expiresAt": expiry, "freshForSeconds": 60,
        "vm": "running" if running else "not-running",
        "runtimeLock": "deployed" if lock else "blocked",
        "nat": "runtime-verified" if runtime_nat_ready else "absent" if nat_absent else "attached",
        "lease": "operator-managed" if operator_managed and valid_lease else "valid" if valid_lease else "expired-or-too-short",
        "gateway": "not-checked", "sandbox": "not-checked",
        "readyForProbe": all((running, private, owned, lock, nat_absent or runtime_nat_ready, valid_lease)),
    }


def probe_script(host: str, nonce: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9]{1,64}", nonce):
        raise WorkspaceLiveError("Invalid proof identifier.")
    if not re.fullmatch(r"[a-z0-9.-]+\.azurecontainerapps\.io", host):
        raise WorkspaceLiveError("Diagnostic endpoint is outside the fixed demo boundary.")
    guest = f"""set -eu
uid=$(id -u)
[ "$uid" -gt 0 ]
printf 'SANDBOX_RESOLVER\\n' >&2
/bin/cat /etc/resolv.conf >&2
for read_attempt in 1 2; do
    printf 'CURL_READ_ATTEMPT %s\\n' "$read_attempt" >&2
    read_code=$(/usr/bin/curl -sS --connect-timeout 5 --max-time 20 -o /dev/null -w '%{{http_code}}%{{stderr}}CURL_READ remote=%{{remote_ip}} tls=%{{ssl_verify_result}} connect=%{{time_connect}} total=%{{time_total}}\\n' 'https://{host}/v1/diagnostics/current') || read_code=000
    [ "$read_code" = 000 ] || break
done
write_code=$(/usr/bin/curl -sS --connect-timeout 5 --max-time 20 -o /dev/null -w '%{{http_code}}%{{stderr}}CURL_WRITE remote=%{{remote_ip}} tls=%{{ssl_verify_result}} connect=%{{time_connect}} total=%{{time_total}}\\n' -X POST 'https://{host}/v1/diagnostics/current') || write_code=000
if /usr/bin/python3 -c 'import os; os.setuid(0)' >/dev/null 2>&1; then exit 9; fi
printf 'PROOF_%s %s %s %s denied\\n' '{nonce}' "$uid" "$read_code" "$write_code"
"""
    payload = base64.b64encode(guest.encode()).decode()
    command = shlex.quote(f"printf '%s' '{payload}' | base64 -d | /bin/sh")
    return f"""#!/usr/bin/env bash
set -euo pipefail
admin_uid=$(id -u sawadmin)
admin_gid=$(id -g sawadmin)
admin_home=$(getent passwd sawadmin | cut -d: -f6)
run_user() {{ runuser -u sawadmin -- env HOME="$admin_home" XDG_RUNTIME_DIR="/run/user/$admin_uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$admin_uid/bus" "$@"; }}
run_user openshell --gateway openshell status --output json | python3 -c '
import json,sys
value=json.load(sys.stdin)
assert value.get("gateway")=="openshell" and value.get("server")=="https://127.0.0.1:17670" and value.get("status")=="connected" and value.get("authentication",{{}}).get("status")=="authenticated"
'
config=$(mktemp)
trap 'rm -f "$config"' EXIT
run_user openshell --gateway openshell sandbox ssh-config planning-demo > "$config"
chown sawadmin:sawadmin "$config"
chmod 600 "$config"
alias_name=$(awk '$1 == "Host" {{print $2; exit}}' "$config")
[[ "$alias_name" =~ ^[A-Za-z0-9._-]+$ ]]
timeout --signal=TERM --kill-after=2s 70s setpriv --reuid="$admin_uid" --regid="$admin_gid" --init-groups env HOME="$admin_home" XDG_RUNTIME_DIR="/run/user/$admin_uid" ssh -n -F "$config" -T -o BatchMode=yes -o ConnectTimeout=10 "$alias_name" {command}
"""


def parse_probe(response: dict, nonce: str) -> dict:
    messages = [item.get("message", "") for item in response.get("value", []) if isinstance(item, dict)]
    matches = re.findall(rf"^PROOF_{re.escape(nonce)} (\d+) (\d{{3}}) (\d{{3}}) denied\s*$", "\n".join(messages), re.M)
    if len(matches) != 1:
        raise WorkspaceLiveError("The sandbox did not return a complete proof receipt. No success is assumed.")
    uid, read_code, write_code = matches[0]
    if not 0 < int(uid) <= 65535:
        raise WorkspaceLiveError("The sandbox non-root check failed.")
    return {
        "sandbox": "planning-demo", "uid": int(uid),
        "readHttpStatus": int(read_code), "writeHttpStatus": int(write_code),
        "privilegeEscalation": "denied", "passed": read_code == "401" and write_code == "403",
        "routeStatus": "observed" if read_code != "000" and write_code != "000" else "unreachable",
        "meaning": "401 proves protected-route reachability; 403 is the denied method. No SQL remediation or model call was performed.",
    }


class LiveWorkspace:
    def __init__(self, subscription: str | None) -> None:
        if subscription is not None and not re.fullmatch(r"[0-9a-fA-F-]{36}", subscription):
            raise ValueError("Expected a subscription UUID")
        self.subscription = subscription
        self.lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.subscription)

    def _az(self, arguments: list[str], timeout: int = 30) -> Any:
        if not self.enabled:
            raise WorkspaceLiveError("Live workspace transport is disabled.")
        try:
            result = subprocess.run(
                ["az", *arguments, "--subscription", self.subscription, "--output", "json", "--only-show-errors"],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
            if result.returncode:
                raise WorkspaceLiveError("Azure operation failed. Check operator access and resource state.")
            return json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            raise WorkspaceLiveError("Azure operation unavailable or timed out; completion is not assumed.") from error

    def _check(self) -> dict:
        target = ["--resource-group", "rg-learningnemo-saw-dev"]
        vm = self._az(["vm", "show", *target, "--name", "vm-learningnemo-saw-dev", "--show-details"])
        stack = self._az(["stack", "group", "show", *target, "--name", "learningnemo-saw-runtime-lock-dev"])
        rules = self._az(["network", "nsg", "rule", "list", *target, "--nsg-name", "nsg-vnet-learningnemo-saw-dev-workspace"])
        subnet = self._az(["network", "vnet", "subnet", "show", *target, "--vnet-name", "vnet-learningnemo-saw-dev", "--name", "snet-workspace"])
        runtime_nat = None
        expected_nat = f"/subscriptions/{self.subscription}/resourceGroups/rg-learningnemo-saw-dev/providers/Microsoft.Network/natGateways/nat-learningnemo-saw-runtime-dev"
        if str((subnet.get("natGateway") or {}).get("id", "")).casefold() == expected_nat.casefold():
            runtime_nat = self._az(["network", "nat", "gateway", "show", *target, "--name", "nat-learningnemo-saw-runtime-dev"])
        return cloud_snapshot(vm, stack, rules, subnet, runtime_nat)

    def check(self) -> dict:
        if not self.lock.acquire(blocking=False):
            raise WorkspaceLiveError("Another workspace operation is in progress.")
        try:
            return self._check()
        finally:
            self.lock.release()

    def run(self, run_id: str) -> dict:
        if not self.lock.acquire(blocking=False):
            raise WorkspaceLiveError("Another workspace operation is in progress.")
        try:
            snapshot = self._check()
            if not snapshot["readyForProbe"]:
                return {"status": "blocked", "cloud": snapshot, "detail": "Workspace state, lock, or lease does not permit a proof run."}
            app = self._az(["containerapp", "show", "--resource-group", "rg-learningnemo-platform-dev", "--name", "ca-learningnemo-diagnostic-dev"])
            host = app["properties"]["configuration"]["ingress"]["fqdn"]
            script = probe_script(host, run_id)
            result = self._az([
                "vm", "run-command", "invoke", "--resource-group", "rg-learningnemo-saw-dev",
                "--name", "vm-learningnemo-saw-dev", "--command-id", "RunShellScript", "--scripts", script,
            ], timeout=180)
            receipt = parse_probe(result, run_id)
            return {
                "status": "passed" if receipt["passed"] else "failed", "cloud": snapshot,
                "receipt": receipt, "completedAt": utc_now(),
                "scriptSha256": hashlib.sha256(script.encode()).hexdigest(),
                "transport": "Local Azure CLI operator -> Azure Run Command -> OpenShell SSH -> Planning MicroVM",
            }
        finally:
            self.lock.release()