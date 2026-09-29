import base64
import importlib.util
from pathlib import Path
import sys
import zlib

import pytest


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'infra/next-phase'))
SPEC = importlib.util.spec_from_file_location('invoice_investigation_rehearsal', ROOT / 'infra/next-phase/rehearse_invoice_investigation.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_investigation_rehearsal_is_bounded_and_uses_real_openshell_path():
    code = MODULE.investigation_code('a' * 32, 'b' * 64)
    compile(code, '<investigation-rehearsal>', 'exec')
    assert len(base64.b64encode(zlib.compress(code.encode()))) < 1700
    assert "InvoiceChallenges(controller" in code
    assert "AzureInvoiceRuntime" in code and "run_probe" in code
    assert "executed_in_sandbox'] is True" in code
    assert "sandbox_executor']=='/opt/venv/bin/python'" in code
    assert "query_executed'] is False" in code
    assert "['preparing-sandbox','sandbox-bound','investigation-started','investigation-recorded','sandbox-stopped']" in code
    for forbidden in ('publish_decision', 'execute_step', 'usp_decide_invoice_plan', 'usp_claim_invoice_execution'):
        assert forbidden not in code


def test_investigation_rehearsal_requires_explicit_acknowledgement(monkeypatch):
    monkeypatch.delenv('LEARNINGNEMO_AZURE_APPLY', raising=False)
    with pytest.raises(ValueError, match='acknowledgement'):
        MODULE.main()
