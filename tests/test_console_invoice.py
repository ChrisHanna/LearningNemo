from fastapi.testclient import TestClient

from task_agent.console.app import create_app
from test_console import FakeAuthManager, FakeAgentClient, _settings


class Remote:
    def __init__(self): self.calls = []
    async def request(self, method, path, token, body, **kwargs):
        self.calls.append((method,path,token,body,kwargs))
        return {'job_id': body['job_id']} if body else {'plans': []}


def test_invoice_job_requires_session_csrf_and_fixed_payload():
    remote = Remote()
    client = TestClient(create_app(_settings(), 'https://example.test', auth_manager=FakeAuthManager('fixture','operator'),
                                 agent_client=FakeAgentClient(), invoice_service=remote))
    boot = client.get('/api/bootstrap').json()
    body = {'job_id':'a'*32, 'kind':'planning', 'target_id':'b'*32}
    assert client.post('/api/invoices/jobs',json=body).status_code == 403
    from task_agent.console.browser_sessions import CSRF_HEADER
    headers={'Origin':'http://testserver',CSRF_HEADER:boot['csrfToken']}
    assert client.post('/api/invoices/jobs',json={**body,'sql':'arbitrary'},headers=headers).status_code == 422
    assert client.post('/api/invoices/jobs',json=body,headers=headers).status_code == 202
    assert len(remote.calls)==1 and remote.calls[0][2]=='fixture'