import base64
from pathlib import Path
import zlib


def test_execution_rehearsal_is_bounded_and_uses_independent_verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'infra/next-phase'))
    from rehearse_invoice_execution import execution_code
    code=execution_code('ac4836e188fd45e3905bfe7b0dbd8f3a','b8edc873d7984f2b7ef1df4c397c52fecd18a76b398aa5bd99b283f149e168ee','7fe8bd096d5a83e7fa5bb271e4ff7257308d6c7a9f2c609460e2d29a046b7dd4','62f2b9f8ee984f76a12dea9ec63c24db')
    compile(code,'<execution-rehearsal>','exec')
    assert len(base64.b64encode(zlib.compress(code.encode())))<1700
    assert 'RemoteInvoiceVerifier' in code and "'humanRehearsal':False" in code
    assert 'usp_complete_invoice_plan' not in code