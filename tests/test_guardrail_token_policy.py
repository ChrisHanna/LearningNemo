from pathlib import Path
import xml.etree.ElementTree as ET


def test_guardrail_policy_replaces_conflicting_token_limit():
    path = Path(__file__).parents[1]/'infra/policies/semantic-guardrail.xml'
    document = ET.parse(path)
    body = document.find('./inbound/set-body').text
    assert body.index('body.Remove("max_completion_tokens")') < body.index('body["max_tokens"] = 3')
    assert len(document.findall('.//base')) == 4
    assert document.find('./inbound/rewrite-uri').attrib['template'] == '/chat/completions'


def test_azure_json_bom_is_accepted(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]/'infra/next-phase'))
    import deploy_human_services
    monkeypatch.setattr(deploy_human_services.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='\ufeff{"properties":{}}'))
    assert deploy_human_services.az('rest','--method','get') == {'properties':{}}