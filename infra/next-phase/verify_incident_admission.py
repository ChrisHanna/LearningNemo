"""Check real SQL admission in a rolled-back transaction, without human impersonation."""

import base64
import os
import pty
import zlib

from verify_human_services import az, GROUP


CODE = '''
import os
from datetime import datetime,timedelta
from uuid import uuid4
from task_agent.control.mssql_client import MssqlProcedureClient,PROCEDURES
client=MssqlProcedureClient(server=os.environ['LEARNINGNEMO_SQL_SERVER'],database=os.environ['LEARNINGNEMO_SQL_DATABASE'],client_id=os.environ['AZURE_CLIENT_ID'],application_name='AdmissionRollbackCheck')
request_id=str(uuid4())
suffix=uuid4().hex
now=datetime.utcnow()
parameters={'task_id':'task-'+suffix,'engagement_id':'engagement-'+suffix,'workspace_id':'workspace-'+suffix,'logical_agent_id':'investigator-'+suffix,'request_id':request_id,'sponsor_hash':'0'*64,'run_id':'run-'+suffix,'plan_id':'plan-'+suffix,'expires_at':now+timedelta(minutes=10),'now_utc':now}
statement,order=PROCEDURES['control.usp_begin_human_investigation']
with client._driver_connect(client._connection_string) as connection:
    connection.autocommit=True
    with connection.cursor() as cursor:
        cursor.execute('BEGIN TRANSACTION')
        try:
            for expected in ('admitted','running'):
                cursor.execute(statement,*[parameters[name] for name in order])
                rows=cursor.fetchall()
                assert len(rows)==1 and tuple(rows[0])==(expected,parameters['plan_id'])
                cursor.execute('SELECT @@TRANCOUNT')
                assert cursor.fetchone()[0]==1
        finally:
            cursor.execute('IF @@TRANCOUNT>0 ROLLBACK TRANSACTION')
        cursor.execute('SELECT @@TRANCOUNT')
        assert cursor.fetchone()[0]==0
        cursor.execute('BEGIN TRANSACTION')
        try:
            cursor.execute(statement,*[parameters[name] for name in order])
            assert tuple(cursor.fetchone())==('admitted',parameters['plan_id'])
        finally:
            cursor.execute('IF @@TRANCOUNT>0 ROLLBACK TRANSACTION')
print('PASS real_sql_admission_duplicate_blocked_and_rollback_verified; no incident committed')
'''


def main():
    if az('account', 'show')['id'] != os.environ['AZURE_SUBSCRIPTION_ID']:
        raise ValueError('subscription mismatch')
    compile(CODE, '<admission-verification>', 'exec')
    encoded = base64.b64encode(zlib.compress(CODE.encode())).decode()
    output = bytearray()
    def capture(descriptor):
        chunk = os.read(descriptor, 4096)
        output.extend(chunk)
        return chunk
    pty.spawn(['az', 'containerapp', 'exec', '-g', GROUP, '-n', 'ca-learningnemo-incident-dev', '--command',
        f"python -c exec(__import__('zlib').decompress(__import__('base64').b64decode('{encoded}')))"], master_read=capture)
    if b'PASS real_sql_admission_duplicate_blocked_and_rollback_verified' not in output or b'Traceback' in output:
        raise ValueError('SQL admission verification incomplete')


if __name__ == '__main__':
    main()