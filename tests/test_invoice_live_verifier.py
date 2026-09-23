from pathlib import Path
import base64
import zlib


def test_invoice_live_verifier_commands_compile_and_are_bounded(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'infra/next-phase'))
    from verify_invoice_services import sql_code, workload_code, operator_code, connectivity_code
    for kind in ('operator','review','planning','execution','verifier','simulator'):
        code=sql_code(kind)
        compile(code,'<invoice-verifier>','exec')
        assert len(base64.b64encode(zlib.compress(code.encode())))<1700
        assert 'lab.usp_apply_invoice_operation' in code
        assert 'timeout_seconds=120' in code
    code = workload_code()
    compile(code,'<invoice-workload-verifier>','exec')
    assert len(base64.b64encode(zlib.compress(code.encode()))) < 1700
    assert 'print(token' not in code
    code = operator_code()
    compile(code,'<invoice-operator-verifier>','exec')
    assert len(base64.b64encode(zlib.compress(code.encode()))) < 1700
    code = connectivity_code()
    compile(code,'<invoice-connectivity-verifier>','exec')
    assert len(base64.b64encode(zlib.compress(code.encode()))) < 1700


def test_invoice_app_names_fit_azure_and_match_policy_hosts():
    import yaml
    root = Path(__file__).parents[1]
    for kind in ('operator', 'review', 'planning', 'execution', 'verifier'):
        name = f'ca-nemo-invoice-{kind}-dev'
        assert len(name) <= 32
        if kind in ('planning', 'execution'):
            text = (root/'infra/next-phase/openshell'/f'invoice-{kind}-policy.yaml').read_text()
            yaml.safe_load(text)
            assert name + '.jollybeach-503c7ed1.eastus.azurecontainerapps.io' in text
    assert "name: 'ca-nemo-invoice-${kind}-dev'" in (root/'infra/next-phase/invoice-services.bicep').read_text()