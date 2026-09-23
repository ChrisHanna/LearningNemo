"""Canonical tool access policies used for diagnostics and configuration tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolAccessPolicy:
    scopes: frozenset[str]
    roles: frozenset[str]


TOOL_ACCESS_POLICIES = {
    "current_datetime": ToolAccessPolicy(
        scopes=frozenset({"agent.invoke"}),
        roles=frozenset({"Task.Reader"}),
    ),
    "list_tasks": ToolAccessPolicy(
        scopes=frozenset({"tasks.read"}),
        roles=frozenset({"Task.Reader"}),
    ),
    "execute_task": ToolAccessPolicy(
        scopes=frozenset({"tasks.execute"}),
        roles=frozenset({"Task.Operator"}),
    ),
    "reset_tasks": ToolAccessPolicy(
        scopes=frozenset({"tasks.execute"}),
        roles=frozenset({"Task.Operator"}),
    ),
}


def evaluate_tool_access(
    tool_name: str,
    granted_scopes: frozenset[str],
    granted_roles: frozenset[str],
) -> dict[str, object]:
    """Explain the deterministic scope-and-role decision for one tool."""
    policy = TOOL_ACCESS_POLICIES[tool_name]
    missing_scopes = sorted(policy.scopes - granted_scopes)
    missing_roles = sorted(policy.roles - granted_roles)
    allowed = not missing_scopes and not missing_roles
    if allowed:
        reason = "All required delegated scopes and app roles are present."
    else:
        reasons = []
        if missing_scopes:
            reasons.append(f"missing scope: {', '.join(missing_scopes)}")
        if missing_roles:
            reasons.append(f"missing app role: {', '.join(missing_roles)}")
        reason = "; ".join(reasons)
    return {
        "tool": tool_name,
        "allowed": allowed,
        "required": {
            "scopes": sorted(policy.scopes),
            "roles": sorted(policy.roles),
        },
        "granted": {
            "scopes": sorted(granted_scopes),
            "roles": sorted(granted_roles),
        },
        "missing": {
            "scopes": missing_scopes,
            "roles": missing_roles,
        },
        "reason": reason,
        "enforcedBy": "RequireAccessMiddleware before tool execution",
    }