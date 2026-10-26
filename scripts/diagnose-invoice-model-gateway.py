"""Compare direct HTTP, async model client and guardrails inside the live gateway."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'infra/next-phase'))
from verify_invoice_services import remote


CODE = '''
import asyncio,json,os,time,socket,httpx
from urllib.parse import urlsplit
from azure.identity import ManagedIdentityCredential
from langchain_openai import ChatOpenAI
from task_agent.control.invoice_model import build_guardrails
origin=os.environ['OPENAI_GUARDRAIL_BASE_URL']
credential=ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
token=credential.get_token('https://vault.azure.net/.default')
response=httpx.get('https://kvnemo8370187d.vault.azure.net/secrets/llm-gateway-client-key?api-version=7.4',headers={'Authorization':'Bearer '+token.token},timeout=20)
response.raise_for_status()
key=response.json()['value']
report={'dns':sorted({item[4][0] for item in socket.getaddrinfo(urlsplit(origin).hostname,443)}),'proxy_configured':any(os.environ.get(name) for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY'))}
started=time.monotonic()
try:
    response=httpx.post(origin+'/chat/completions',headers={'Authorization':'Bearer '+key},json={'model':'gpt-4o-mini','messages':[{'role':'user','content':'Reply No.'}],'max_tokens':3},timeout=20)
    report['direct_http']={'status':response.status_code,'seconds':round(time.monotonic()-started,2)}
except Exception as error:report['direct_http']={'error_type':type(error).__name__}
pre_loop_rails=build_guardrails(base_url=origin,api_key=key)
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    report['uvloop']=True
except ImportError:report['uvloop']=False
async def probe():
    for mode in ('async_llm','guardrails_pre_loop','guardrails'):
        started=time.monotonic()
        try:
            if mode=='async_llm':
                model=ChatOpenAI(model='gpt-4o-mini',base_url=origin,api_key=key,max_retries=0,timeout=20,max_tokens=3)
                await model.ainvoke('Reply No.')
                value='responded'
            else:
                from nemoguardrails.rails.llm.options import RailType
                rails=pre_loop_rails if mode=='guardrails_pre_loop' else build_guardrails(base_url=origin,api_key=key)
                result=await rails.check_async(messages=[{'role':'user','content':'Investigate invoice totals without changing data.'}],rail_types=[RailType.INPUT])
                value=result.status
            report[mode]={'result':value,'seconds':round(time.monotonic()-started,2)}
        except Exception as error:report[mode]={'error_type':type(error).__name__,'seconds':round(time.monotonic()-started,2)}
asyncio.run(probe())
print('INVOICE_MODEL_DIAGNOSTIC '+json.dumps(report),flush=True)
'''


INPUT_SERIES_CODE = '''
import asyncio,json,os,time,httpx
from azure.identity import ManagedIdentityCredential
from task_agent.control.invoice_model import InvoiceModelGateway,build_guardrails,CLASSIFIER_REQUEST_TIMEOUT_SECONDS,CLASSIFIER_RAIL_TIMEOUT_SECONDS
credential=ManagedIdentityCredential(client_id=os.environ['AZURE_CLIENT_ID'])
token=credential.get_token('https://vault.azure.net/.default')
response=httpx.get('https://kvnemo8370187d.vault.azure.net/secrets/llm-gateway-client-key?api-version=7.4',headers={'Authorization':'Bearer '+token.token},timeout=20)
response.raise_for_status()
rails=build_guardrails(base_url=os.environ['OPENAI_GUARDRAIL_BASE_URL'],api_key=response.json()['value'])
gateway=InvoiceModelGateway(origin=os.environ['OPENAI_BASE_URL'],client=None,api_key='unused',rails=rails)
async def probe():
    results=[]
    for index in range(5):
        started=time.monotonic()
        try:
            passed=await gateway.check([{'role':'user','content':'Invoice totals do not reconcile with orders. Investigate without changing the database, then publish your supported decision.'}])
            result={'passed':passed}
        except Exception as error:result={'error_type':type(error).__name__}
        result['seconds']=round(time.monotonic()-started,2)
        results.append(result)
    print('INVOICE_MODEL_DIAGNOSTIC '+json.dumps({'mode':'read-only-input-series','attempt_timeout_seconds':CLASSIFIER_REQUEST_TIMEOUT_SECONDS,'rail_timeout_seconds':CLASSIFIER_RAIL_TIMEOUT_SECONDS,'samples':results}),flush=True)
asyncio.run(probe())
'''


RAIL_CODE = '''
import asyncio,json,os
from task_agent.control.invoice_model import InvoiceModelGateway,build_guardrails
kind=os.environ['INVOICE_SERVICE_KIND']
gateway=InvoiceModelGateway(origin=os.environ['OPENAI_BASE_URL'],client=None,api_key='unused-local-rail-check',rails=build_guardrails(base_url=os.environ['OPENAI_GUARDRAIL_BASE_URL'],api_key='unused-local-rail-check'))
async def probe():
    names=('invoice_summary','invoice_batches','publish_decision') if kind=='planning' else ('execute_step',)*3
    history=[{'role':'user','content':'Invoice result history'}]
    for index,name in enumerate(names):
        identifier='diagnostic-'+str(index)
        history.extend([{'role':'assistant','content':None,'tool_calls':[{'id':identifier,'type':'function','function':{'name':name,'arguments':'{}'}}]}, {'role':'tool','tool_call_id':identifier,'content':'{"result":"bounded"}'}])
        assert await gateway.check_tool_results(history,kind=kind)
    history[4]['content']='{"note":"Password=fixture"}'
    assert not await gateway.check_tool_results(history,kind=kind)
    terminal=await gateway.check_response('',[{'id':'local-finish','name':'finish_execution','arguments':'{}'}],kind=kind,scenario_id='b'*32)
    assert terminal==(kind=='execution')
    import httpx
    assert httpx.post('http://127.0.0.1:8080/v2/invoice/tools/finish_execution',json={},timeout=10).status_code==404
    print('INVOICE_MODEL_DIAGNOSTIC '+json.dumps({'kind':kind,'three_turn_history':'passed','poisoned_middle_result':'blocked','terminal_tool_role_scoped':True,'terminal_tool_has_no_http_route':True,'provider_calls':0}),flush=True)
asyncio.run(probe())
'''


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rails-only',action='store_true')
    parser.add_argument('--input-series',action='store_true')
    parser.add_argument('--kind',choices=('planning','execution'),default='planning')
    args=parser.parse_args()
    if args.rails_only and args.input_series:
        parser.error('choose either local rails or read-only provider input series')
    remote(args.kind,RAIL_CODE if args.rails_only else INPUT_SERIES_CODE if args.input_series else CODE,'INVOICE_MODEL_DIAGNOSTIC',read_only=True)