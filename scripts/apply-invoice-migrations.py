"""Install additive invoice workflow tables and exact dedicated identity grants."""

from datetime import UTC,datetime
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import UUID
import sys

from task_agent.control.invoice_catalog import GRANTS
from task_agent.control.mssql_client import build_connection_string,managed_identity_connect


def invoice_grants():
    return {'operator':set(GRANTS['controller']+GRANTS['incident']+GRANTS['jobs']), 'review':set(GRANTS['review']),
            'planning':set(GRANTS['diagnostic']+GRANTS['inference']), 'execution':set(GRANTS['broker']+GRANTS['inference']), 'verifier':set(GRANTS['verifier']), 'simulator':set(GRANTS['simulator'])}


def verify_only():
    expiry = datetime.fromisoformat(os.environ['LEARNINGNEMO_MIGRATION_EXPIRES_AT'].replace('Z', '+00:00'))
    if not 0 < (expiry - datetime.now(UTC)).total_seconds() <= 3600: raise ValueError('bounded verification lease required')
    principals = json.loads(os.environ['LEARNINGNEMO_INVOICE_PRINCIPALS'])
    grants = invoice_grants()
    if set(principals) != set(grants) or len({value['clientId'] for value in principals.values()}) != 6: raise ValueError('six distinct invoice identities required')
    names = ('008_invoice_lab.sql','009_invoice_workflow.sql','010_invoice_jobs.sql','011_invoice_recovery.sql','012_invoice_retention.sql','013_invoice_review_window.sql','014_invoice_bound_sandbox_test.sql','015_invoice_retention_pressure.sql','016_invoice_doubled_windows.sql')
    expected = {'human-' + name: hashlib.sha256((Path('/app/migrations') / name).read_text().encode()).hexdigest() for name in names}
    server, client_id = os.environ['LEARNINGNEMO_SQL_SERVER'], os.environ['AZURE_CLIENT_ID']
    connection_string = build_connection_string(server=server, database=os.environ['LEARNINGNEMO_SQL_DATABASE'], client_id=client_id, application_name='InvoiceMigrationReadback')
    for _ in range(2):
        with managed_identity_connect(connection_string, client_id, timeout_seconds=120, server=server) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT MigrationId,ContentHash FROM control.SchemaMigrations')
                persisted = dict(cursor.fetchall())
                if any(persisted.get(key) != value for key, value in expected.items()): raise ValueError('migration hashes differ')
                cursor.execute("SELECT defaults.definition,columns.is_nullable FROM sys.default_constraints AS defaults JOIN sys.columns AS columns ON columns.object_id=defaults.parent_object_id AND columns.column_id=defaults.parent_column_id WHERE defaults.parent_object_id=OBJECT_ID(N'control.InvoicePlans') AND columns.name=N'ReviewExpiresAt'")
                default = cursor.fetchone()
                if default is None or default[1] or re.sub(r'[\s()]','',default[0]).lower() != 'dateaddminute,180,sysutcdatetime': raise ValueError('new review deadline default differs')
                cursor.execute("SELECT OBJECT_DEFINITION(OBJECT_ID(N'control.usp_decide_invoice_plan'))")
                decision = re.sub(r'[\s()]','',cursor.fetchone()[0]).lower()
                if 'approvalexpiresat=dateaddminute,30,sysutcdatetime' not in decision: raise ValueError('new approval lifetime differs')
                cursor.execute("SELECT ReviewExpiresAt,DATEADD(minute,30,CreatedAt) FROM control.InvoicePlans WHERE CreatedAt < (SELECT AppliedAt FROM control.SchemaMigrations WHERE MigrationId=N'human-013_invoice_review_window.sql')")
                legacy = cursor.fetchall()
                if any(actual != original for actual, original in legacy): raise ValueError('legacy deadline changed')
                cursor.execute("SELECT ApprovalExpiresAt,DATEADD(minute,15,ApprovedAt) FROM control.InvoicePlans WHERE ApprovalExpiresAt IS NOT NULL AND ApprovedAt < (SELECT AppliedAt FROM control.SchemaMigrations WHERE MigrationId=N'human-016_invoice_doubled_windows.sql')")
                legacy_approvals = cursor.fetchall()
                if any(actual != original for actual, original in legacy_approvals): raise ValueError('legacy approval deadline changed')
                for kind, identity in principals.items():
                    name = 'id-learningnemo-invoice-' + kind + '-dev'
                    cursor.execute('SELECT sid FROM sys.database_principals WHERE name=?', name)
                    if bytes(cursor.fetchone()[0]) != UUID(identity['clientId']).bytes_le: raise ValueError('SQL identity differs')
                    cursor.execute('SELECT COUNT(*) FROM sys.database_role_members WHERE member_principal_id=DATABASE_PRINCIPAL_ID(?)', name)
                    if cursor.fetchone()[0] != 0: raise ValueError('unexpected SQL role')
                    cursor.execute("SELECT OBJECT_SCHEMA_NAME(major_id)+'.'+OBJECT_NAME(major_id),permission_name,state_desc,class FROM sys.database_permissions WHERE grantee_principal_id=DATABASE_PRINCIPAL_ID(?)", name)
                    permissions = {(procedure, permission, state, level) for procedure, permission, state, level in cursor.fetchall() if (permission, state, level) != ('CONNECT', 'GRANT', 0)}
                    if permissions != {(procedure, 'EXECUTE', 'GRANT', 1) for procedure in grants[kind]}: raise ValueError('exact SQL grant inventory differs')
    print('PASS invoice_migration ' + json.dumps({'principals': principals, 'migrations': expected, 'verifiedAcrossConnections': True,
        'reviewWindowMinutes': 180, 'approvalWindowMinutes': 30, 'existingReviewDeadlinesPreserved': len(legacy),
        'existingApprovalDeadlinesPreserved': len(legacy_approvals), 'verificationOnly': True}), flush=True)


def main():
    expiry=datetime.fromisoformat(os.environ['LEARNINGNEMO_MIGRATION_EXPIRES_AT'].replace('Z','+00:00'))
    if not 0<(expiry-datetime.now(UTC)).total_seconds()<=3600:raise ValueError('bounded migration lease required')
    principals=json.loads(os.environ['LEARNINGNEMO_INVOICE_PRINCIPALS']);grants=invoice_grants()
    if set(principals)!=set(grants) or len({value['clientId'] for value in principals.values()})!=6:raise ValueError('six distinct invoice identities required')
    server=os.environ['LEARNINGNEMO_SQL_SERVER'];client_id=os.environ['AZURE_CLIENT_ID']
    connection_string=build_connection_string(server=server,database=os.environ['LEARNINGNEMO_SQL_DATABASE'],client_id=client_id,application_name='InvoiceMigration')
    migration_hashes={}
    with managed_identity_connect(connection_string,client_id,timeout_seconds=120,server=server) as connection:
        connection.autocommit=True
        with connection.cursor() as cursor:
            try:
                cursor.execute('SET XACT_ABORT ON; BEGIN TRANSACTION;')
                cursor.execute("DECLARE @lock int; EXEC @lock=sys.sp_getapplock @Resource=N'learningnemo-human-migrations',@LockMode=N'Exclusive',@LockOwner=N'Transaction',@LockTimeout=15000; IF @lock<0 THROW 52090,'migration_busy',1;")
                cursor.execute('SELECT MigrationId,ContentHash FROM control.SchemaMigrations');before=dict(cursor.fetchall())
                cursor.execute("SELECT OBJECT_ID(N'control.InvoicePlans',N'U'),COL_LENGTH(N'control.InvoicePlans',N'ReviewExpiresAt')")
                table_id, deadline_column = cursor.fetchone()
                review_before = {}
                approval_before = {}
                if table_id is not None:
                    deadline = 'ReviewExpiresAt' if deadline_column is not None else 'DATEADD(minute,30,CreatedAt)'
                    cursor.execute('SELECT PlanId,' + deadline + ' FROM control.InvoicePlans')
                    review_before = dict(cursor.fetchall())
                    cursor.execute('SELECT PlanId,ApprovalExpiresAt FROM control.InvoicePlans WHERE ApprovalExpiresAt IS NOT NULL')
                    approval_before = dict(cursor.fetchall())
                for name in ('008_invoice_lab.sql','009_invoice_workflow.sql','010_invoice_jobs.sql','011_invoice_recovery.sql','012_invoice_retention.sql','013_invoice_review_window.sql','014_invoice_bound_sandbox_test.sql','015_invoice_retention_pressure.sql','016_invoice_doubled_windows.sql'):
                    source=(Path('/app/migrations')/name).read_text();digest=hashlib.sha256(source.encode()).hexdigest();key='human-'+name
                    migration_hashes[key]=digest
                    if key in before:
                        if before[key]!=digest:raise ValueError('applied invoice migration differs')
                        continue
                    for batch in re.split(r'(?im)^\s*GO\s*$',source):
                        if batch.strip():cursor.execute(batch)
                    cursor.execute('INSERT control.SchemaMigrations(MigrationId,ContentHash) VALUES(?,?)',key,digest)
                for kind,identity in principals.items():
                    name='id-learningnemo-invoice-'+kind+'-dev';sid=UUID(identity['clientId']).bytes_le
                    cursor.execute('SELECT sid FROM sys.database_principals WHERE name=?',name);row=cursor.fetchone()
                    if row is None:cursor.execute(f'CREATE USER [{name}] WITH SID=0x{sid.hex()},TYPE=E')
                    elif bytes(row[0])!=sid:raise ValueError('invoice SQL identity collision')
                    cursor.execute('SELECT COUNT(*) FROM sys.database_role_members WHERE member_principal_id=DATABASE_PRINCIPAL_ID(?)',name)
                    if cursor.fetchone()[0]!=0:raise ValueError('invoice SQL identity has role membership')
                    cursor.execute("SELECT OBJECT_SCHEMA_NAME(major_id)+'.'+OBJECT_NAME(major_id),permission_name,state_desc,class FROM sys.database_permissions WHERE grantee_principal_id=DATABASE_PRINCIPAL_ID(?)",name)
                    for procedure,permission,state,level in cursor.fetchall():
                        if (permission,state,level)==('CONNECT','GRANT',0):continue
                        if procedure not in grants[kind] or (permission,state,level)!=('EXECUTE','GRANT',1):raise ValueError('unexpected invoice privilege')
                    for procedure in sorted(grants[kind]):cursor.execute(f'GRANT EXECUTE ON OBJECT::{procedure} TO [{name}]')
                cursor.execute('SELECT MigrationId,ContentHash FROM control.SchemaMigrations');after=dict(cursor.fetchall())
                if any(after.get(key)!=value for key,value in before.items()):raise ValueError('existing receipts changed')
                cursor.execute('COMMIT TRANSACTION;')
            except Exception:
                cursor.execute('IF @@TRANCOUNT>0 ROLLBACK TRANSACTION;');raise
    with managed_identity_connect(connection_string,client_id,timeout_seconds=120,server=server) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT MigrationId,ContentHash FROM control.SchemaMigrations');persisted=dict(cursor.fetchall())
            if any(persisted.get(key)!=value for key,value in migration_hashes.items()):raise ValueError('independent receipt check failed')
            cursor.execute('SELECT PlanId,ReviewExpiresAt FROM control.InvoicePlans')
            deadlines = dict(cursor.fetchall())
            if any(deadlines.get(key) != value for key, value in review_before.items()): raise ValueError('existing review deadline changed')
            cursor.execute('SELECT PlanId,ApprovalExpiresAt FROM control.InvoicePlans WHERE ApprovalExpiresAt IS NOT NULL')
            approval_deadlines = dict(cursor.fetchall())
            if any(approval_deadlines.get(key) != value for key, value in approval_before.items()): raise ValueError('existing approval deadline changed')
            cursor.execute("SELECT defaults.definition,columns.is_nullable FROM sys.default_constraints AS defaults JOIN sys.columns AS columns ON columns.object_id=defaults.parent_object_id AND columns.column_id=defaults.parent_column_id WHERE defaults.parent_object_id=OBJECT_ID(N'control.InvoicePlans') AND columns.name=N'ReviewExpiresAt'")
            default = cursor.fetchone()
            if default is None or default[1] or re.sub(r'[\s()]','',default[0]).lower() != 'dateaddminute,180,sysutcdatetime': raise ValueError('new review deadline default differs')
            cursor.execute("SELECT OBJECT_DEFINITION(OBJECT_ID(N'control.usp_decide_invoice_plan'))")
            decision = re.sub(r'[\s()]','',cursor.fetchone()[0]).lower()
            if 'approvalexpiresat=dateaddminute,30,sysutcdatetime' not in decision: raise ValueError('new approval lifetime differs')
    print('PASS invoice_migration '+json.dumps({'principals':principals,'migrations':migration_hashes,'verifiedAcrossConnections':True,
        'reviewWindowMinutes':180,'approvalWindowMinutes':30,'existingReviewDeadlinesPreserved':len(review_before),
        'existingApprovalDeadlinesPreserved':len(approval_before)}))


if __name__=='__main__':
    if sys.argv[1:] == ['--verify-only']: verify_only()
    elif sys.argv[1:]: raise ValueError('unsupported migration command')
    else: main()