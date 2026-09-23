"""Read-only private API connection diagnostic from the cloud dashboard."""

import base64
import os
import pty
import zlib

from verify_human_services import az


CODE = '''
import os,socket,httpx,json
print('PROXY_VARIABLE_NAMES',json.dumps([name for name in os.environ if name.lower() in ('http_proxy','https_proxy','all_proxy','no_proxy')]),flush=True)
for kind in ('INCIDENT','REVIEW','EXECUTION'):
    origin=os.environ.get('LEARNINGNEMO_'+kind+'_ORIGIN')
    if not origin:
        print(kind,'not-configured',flush=True)
        continue
    from urllib.parse import urlsplit
    host=urlsplit(origin).hostname
    try:
        addresses=sorted({item[4][0] for item in socket.getaddrinfo(host,443)})
        print(kind,'DNS',json.dumps(addresses),flush=True)
        for trust_environment in (True,False):
            with httpx.Client(timeout=15,follow_redirects=False,trust_env=trust_environment) as client:
                try:
                    response=client.get(origin+'/readyz')
                    print(kind,'trust_env',trust_environment,'READY',response.status_code,flush=True)
                    path={'INCIDENT':'/incidents','REVIEW':'/reviews','EXECUTION':'/executions/plan-readiness'}[kind]
                    response=client.get(origin+path)
                    print(kind,'trust_env',trust_environment,'ANONYMOUS',response.status_code,flush=True)
                except Exception as error:
                    print(kind,'trust_env',trust_environment,type(error).__name__,flush=True)
    except Exception as error:
        print(kind,'FAILED',type(error).__name__,flush=True)
print('HANDOFF_DIAGNOSTIC_FINISHED',flush=True)
'''


def main():
    if az('account', 'show')['id'] != os.environ['AZURE_SUBSCRIPTION_ID']:
        raise ValueError('subscription mismatch')
    compile(CODE, '<handoff-diagnostic>', 'exec')
    encoded = base64.b64encode(zlib.compress(CODE.encode())).decode()
    pty.spawn(['az', 'containerapp', 'exec', '-g', 'rg-learningnemo-demo-dev', '-n', 'ca-learningnemo-dashboard-dev', '--command',
        f"python -c exec(__import__('zlib').decompress(__import__('base64').b64decode('{encoded}')))"])


if __name__ == '__main__':
    main()