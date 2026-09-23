from __future__ import annotations

import copy
import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest


sys.dont_write_bytecode = True
ROOT = Path(__file__).parents[1]
PHASE_DIR = ROOT / "infra" / "next-phase"
CONFIG = PHASE_DIR / "environments" / "dev.budget.config.json"
sys.path.insert(0, str(PHASE_DIR))

import budget_parameters
import record_budget
import summarize_what_if
import verify_budget


def load_hyphenated_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_budget = load_hyphenated_module(
    "validate_budget",
    PHASE_DIR / "validate-budget.py",
)


def safe_budget_template() -> dict[str, object]:
    notifications = {}
    for name, (threshold, threshold_type) in validate_budget.EXPECTED_NOTIFICATIONS.items():
        notifications[name] = {
            "contactEmails": ["[parameters('notificationEmail')]"],
            "contactGroups": [],
            "contactRoles": ["Owner"],
            "enabled": True,
            "locale": "en-us",
            "operator": "GreaterThanOrEqualTo",
            "threshold": threshold,
            "thresholdType": threshold_type,
        }
    return {
        "$schema": "https://schema.management.azure.com/schemas/2018-05-01/subscriptionDeploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "resources": [
            {
                "type": "Microsoft.Consumption/budgets",
                "properties": {
                    "amount": "[parameters('monthlyAmount')]",
                    "category": "Cost",
                    "notifications": notifications,
                    "timeGrain": "Monthly",
                    "timePeriod": {"startDate": "[parameters('startDate')]"},
                },
            }
        ],
    }


def test_budget_config_is_bounded_and_contains_no_recipient() -> None:
    config = budget_parameters.load_config(CONFIG)
    assert config["monthlyAmount"] == 50
    assert "@" not in CONFIG.read_text(encoding="utf-8")


def test_budget_parameters_materialize_privately(tmp_path: Path) -> None:
    output = tmp_path / "budget.parameters.json"
    budget_parameters.materialize(
        CONFIG,
        output,
        "2026-09-01T00:00:00Z",
        "reviewer@example.com",
    )

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["parameters"]["monthlyAmount"]["value"] == 50
    assert document["parameters"]["notificationEmail"]["value"] == "reviewer@example.com"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("start_date", "email"),
    [
        ("2026-09-02T00:00:00Z", "reviewer@example.com"),
        ("not-a-date", "reviewer@example.com"),
        ("2026-09-01T00:00:00Z", "not-an-email"),
    ],
)
def test_budget_parameters_reject_invalid_runtime_values(
    tmp_path: Path,
    start_date: str,
    email: str,
) -> None:
    with pytest.raises(budget_parameters.BudgetParameterError):
        budget_parameters.materialize(CONFIG, tmp_path / "invalid.json", start_date, email)


def test_budget_template_policy_accepts_exact_notifications() -> None:
    assert validate_budget.validate_template(safe_budget_template()) == []


def test_budget_template_policy_rejects_weakened_notification() -> None:
    template = safe_budget_template()
    template["resources"][0]["properties"]["notifications"]["actual50"]["enabled"] = False

    failures = validate_budget.validate_template(template)

    assert any("actual50" in failure for failure in failures)


def test_budget_verifier_rejects_missing_runtime_recipient() -> None:
    notifications = copy.deepcopy(
        safe_budget_template()["resources"][0]["properties"]["notifications"]
    )
    for notification in notifications.values():
        notification["contactEmails"] = ["reviewer@example.com"]
    verify_budget.verify_notifications(notifications)
    notifications["actual80"]["contactEmails"] = []
    with pytest.raises(verify_budget.VerificationError, match="recipient"):
        verify_budget.verify_notifications(notifications)


def test_what_if_summary_removes_account_scope() -> None:
    resource_id = (
        "/subscriptions/00000000-0000-0000-0000-000000000000/"
        "providers/Microsoft.Consumption/budgets/budget-learningnemo-dev"
    )
    summary = summarize_what_if.safe_resource_name(resource_id)

    assert summary == "Microsoft.Consumption/budgets/budget-learningnemo-dev"
    assert "subscriptions" not in summary.casefold()


def test_budget_record_excludes_recipient_and_account_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template = tmp_path / "budget.json"
    template.write_text(json.dumps(safe_budget_template()), encoding="utf-8")
    resolved = tmp_path / "budget.parameters.json"
    budget_parameters.materialize(
        CONFIG,
        resolved,
        "2026-09-01T00:00:00Z",
        "reviewer@example.com",
    )
    deployment = tmp_path / "deployment.json"
    deployment.write_text(
        json.dumps(
            {
                "name": "learningnemo-cost-budget-dev",
                "properties": {"provisioningState": "Succeeded"},
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "manifest.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "record_budget.py",
            "--deployment-result",
            str(deployment),
            "--template",
            str(template),
            "--config",
            str(CONFIG),
            "--resolved-parameters",
            str(resolved),
            "--output",
            str(output),
        ],
    )

    assert record_budget.main() == 0
    serialized = output.read_text(encoding="utf-8").casefold()
    assert "@" not in serialized
    assert "/subscriptions/" not in serialized
    assert json.loads(serialized)["recipientstored"] is False


def test_budget_mutation_requires_subscription_and_acknowledgement() -> None:
    deploy = (PHASE_DIR / "deploy-budget.sh").read_text(encoding="utf-8")
    remove = (PHASE_DIR / "remove-budget.sh").read_text(encoding="utf-8")

    assert 'mode="what-if"' in deploy
    assert "AZURE_SUBSCRIPTION_ID" in deploy
    assert "LEARNINGNEMO_AZURE_APPLY" in deploy
    assert "cost-budget" in deploy
    assert "--notification-email-file" in deploy
    assert "record_budget.py" in deploy
    assert "verify_budget.py" in deploy
    assert "AZURE_SUBSCRIPTION_ID" in remove
    assert "LEARNINGNEMO_AZURE_DELETE" in remove
    assert "cost-budget" in remove
    assert "verify_budget.py" in remove