import base64
from pathlib import Path
import zlib


def test_real_planning_rehearsal_is_bounded_and_never_approves(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'infra/next-phase'))
    from rehearse_invoice_planning import planning_code
    code=planning_code('b6d251c543ee4fe986273eb85a79c610','7fe8bd096d5a83e7fa5bb271e4ff7257308d6c7a9f2c609460e2d29a046b7dd4')
    compile(code,'<planning-rehearsal>','exec')
    assert len(base64.b64encode(zlib.compress(code.encode()))) < 1700
    assert 'controller.analyze' in code
    assert 'usp_decide_invoice_plan' not in code
    assert 'usp_submit_invoice_plan' not in code
    assert 'controller.execute' not in code
    assert "'humanRehearsal':False" in code