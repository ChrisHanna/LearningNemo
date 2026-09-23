from types import SimpleNamespace

from fastapi.testclient import TestClient

from task_agent.console.invoice_service import create_invoice_service
from test_invoice_service import Verifier


def test_inventory_and_deletion_require_verified_operator_and_exact_confirmation():
    calls=[]
    async def inventory(owner):
        calls.append(('inventory',owner))
        return {'source':'openshell-sandbox-inventory','remaining_count':14}
    async def delete(identifier,owner):
        calls.append(('delete',identifier,owner))
        return {'sandbox_id':identifier,'state':'deleted','evidence_archived':True}
    def client(identity):
        return TestClient(create_invoice_service(mode='operator',repository=None,identity_verifier=identity,expires_at=None,
            availability_mode='operator-managed',sandboxes=SimpleNamespace(inventory=inventory,delete=delete)))
    headers={'Authorization':'Bearer fixture'}
    path='/invoices/sandboxes/'+'b'*32+'/delete'
    body={'sandbox_id':'b'*32}
    assert client(Verifier()).get('/invoices/sandboxes').status_code==401
    assert client(Verifier()).get('/invoices/sandboxes',headers=headers).status_code==200
    assert client(Verifier()).post(path,json=body,headers=headers).status_code==403
    operator=client(Verifier(scopes=('agent.invoke','tasks.read','tasks.execute')))
    assert operator.post(path,json={**body,'command':'delete all'},headers=headers).status_code==422
    assert operator.post(path,json={'sandbox_id':'c'*32},headers=headers).status_code==422
    assert operator.post(path,json=body,headers=headers).status_code==200
    assert calls==[('inventory','a'*64),('delete','b'*32,'a'*64)]
    assert client(Verifier('approver',('agent.invoke','tasks.read','tasks.execute'))).post(path,json=body,headers=headers).status_code==403