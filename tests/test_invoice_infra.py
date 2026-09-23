import json
from pathlib import Path
import subprocess


def test_invoice_template_has_fixed_roles_and_no_secret_values():
    root=Path(__file__).parents[1]
    result=subprocess.run(['az','bicep','build','--file',str(root/'infra/next-phase/invoice-services.bicep'),'--stdout'],capture_output=True,text=True,check=True)
    compiled=json.loads(result.stdout)
    assert compiled['variables']['kinds']==['operator','review','planning','execution','verifier']
    assert compiled['parameters']['deployApps']['defaultValue'] is False
    assert len(compiled['resources'])==3
    app=next(resource for resource in compiled['resources'] if resource['type']=='Microsoft.App/containerApps')
    assert app['properties']['configuration']['secrets']==[]
    assert app['properties']['template']['scale']=={'minReplicas':1,'maxReplicas':1}