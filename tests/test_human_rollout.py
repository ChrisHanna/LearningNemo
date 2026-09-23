import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


@pytest.fixture
def rollout(monkeypatch):
    import sys
    monkeypatch.syspath_prepend(str(ROOT / "infra/next-phase"))
    spec = importlib.util.spec_from_file_location("human_rollout", ROOT / "infra/next-phase/deploy_human_services.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("change", ["Delete", "Unsupported", "outside", "unexpected"])
def test_rollout_refuses_unsafe_preview_before_apply(rollout, monkeypatch, change):
    calls = []
    def az(*args, **kwargs):
        calls.append(args)
        return {"status": "Succeeded", "changes": [{
            "changeType": change if change in ("Delete", "Unsupported") else "Create",
            "resourceId": "/subscriptions/test/resourceGroups/" + ("other" if change == "outside" else rollout.GROUP) + "/providers/Microsoft.App/jobs/job",
            "after": {"type": "Microsoft.Authorization/roleAssignments" if change == "unexpected" else "Microsoft.App/jobs"},
        }]}
    monkeypatch.setattr(rollout, "az", az)
    monkeypatch.setattr(rollout, "save", lambda *args: None)
    with pytest.raises(ValueError):
        rollout.preview_apply(Path("template"), Path("parameters"), "test", {"Microsoft.App/jobs"})
    assert len(calls) == 1


def test_migration_receipt_accepts_embedded_azure_log(rollout, tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    monkeypatch.setattr(rollout, "STATE", tmp_path)
    (tmp_path / "human-migration.execution.json").write_text(json.dumps({"name": "execution-test"}))
    monkeypatch.setattr(rollout, "az", lambda *args: [{"name": "execution-test", "properties": {"status": "Succeeded"}}])
    receipt = {"status": "verified", "verifiedAcrossConnections": True, "migrations": {
        f"human-{name}": hashlib.sha256((ROOT / "infra/next-phase/review-service" / name).read_text().encode()).hexdigest()
        for name in ("001_review_boundary.sql", "002_incident_handoff.sql", "003_execution_coordination.sql", "004_incident_initiation.sql", "005_execution_status.sql", "006_execution_reconciliation.sql", "007_readonly_analysis.sql")}, "originalReceiptsPreserved": 9, "principals": ["execution", "incident", "review"]}
    log = '{"Log":"F PASS human_migration ' + json.dumps(receipt) + '"}'
    monkeypatch.setattr(rollout.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=log))
    rollout.verify_migration({"values": {"image": "pinned"}, "principals": {"incident": "identity", "review": "other"}})
    assert json.loads((tmp_path / "human-migration.verified.json").read_text())["receipt"] == receipt


@pytest.mark.parametrize('kind', ['incident', 'review', 'execution'])
def test_runtime_verification_script_compiles_without_placeholder_collision(kind):
    spec = importlib.util.spec_from_file_location('human_verify', ROOT / 'infra/next-phase/verify_human_services.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    code = module.runtime_code(kind)
    compile(code, '<fixture>', 'exec')
    assert 'DATABASE_PRINCIPAL_ID()' in code
    assert '__PRINCIPAL__' not in code
    import base64
    import zlib
    for section in ('sql', 'workers'):
        code = module.runtime_code(kind, section)
        compile(code, '<bounded-remote-command>', 'exec')
        assert len(base64.b64encode(zlib.compress(code.encode()))) < 1800