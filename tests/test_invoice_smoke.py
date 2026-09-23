from pathlib import Path
import subprocess

import pytest


def test_smoke_has_no_database_or_model_authority(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'infra/next-phase'))
    from smoke_invoice_sandbox import guest_script
    script = guest_script('a'*32, 'crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/invoice-agent@sha256:'+'b'*64)
    subprocess.run(['bash','-n'],input=script,text=True,check=True)
    assert 'sandbox delete' not in script
    assert 'INVOICE_RUNTIME_READY' in script and 'os.getuid()!=0' in script
    assert 'OPENAI_API_KEY' not in script
    with pytest.raises(ValueError):
        guest_script('a'*32, 'example.com/unpinned:latest')


def test_bootstrap_cache_script_has_no_registry_credentials(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'infra/next-phase'))
    from stage_invoice_bootstrap import SCRIPT
    subprocess.run(['bash','-n'],input=SCRIPT,text=True,check=True)
    compile(SCRIPT.split("python3 - <<'PY'\n",1)[1].rsplit('\nPY',1)[0],'<bootstrap-cache>','exec')
    assert 'OPENSHELL_REGISTRY_TOKEN' not in SCRIPT and 'RepoDigests' in SCRIPT