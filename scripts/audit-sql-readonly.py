"""Collect SQL metadata and bounded workflow diagnostics without mutations."""

import base64
from datetime import datetime
import hashlib
import json
import os
import re
import zlib

from task_agent.control.mssql_client import build_connection_string, managed_identity_connect


def normalized(source):
    source = re.sub(r'(?is)^\s*CREATE\s+(?:OR\s+ALTER\s+)?(?:PROC(?:EDURE)?|VIEW)\s+', 'MODULE ', source)
    return re.sub(r'\s+', ' ', source).strip().rstrip(';')


def collect():
    expected = json.loads(zlib.decompress(base64.b64decode(os.environ['SQL_AUDIT_EXPECTED'])))
    server = os.environ['LEARNINGNEMO_SQL_SERVER']
    identity = os.environ['AZURE_CLIENT_ID']
    connection_string = build_connection_string(server=server, database='learningnemo', client_id=identity,
                                                application_name='LearningNeMoReadOnlySchemaAudit')
    report = {'read_only': True, 'errors': {}}
    with managed_identity_connect(connection_string, identity, timeout_seconds=90, server=server) as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute('SET LOCK_TIMEOUT 5000; SET NOCOUNT ON;')

        def query(name, sql):
            try:
                with connection.cursor() as cursor:
                    cursor.execute(sql)
                    if cursor.description is None:
                        report[name] = []
                        return []
                    columns = [column[0] for column in cursor.description]
                    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                    report[name] = rows
                    return rows
            except Exception as error:
                report['errors'][name] = str(error)[:1000]
                return []

        query('database', 'SELECT DB_NAME() AS database_name, CURRENT_USER AS principal, SYSUTCDATETIME() AS observed_at, DATABASEPROPERTYEX(DB_NAME(),\'Status\') AS status')
        receipts = query('migration_receipts', 'SELECT MigrationId,ContentHash,AppliedAt FROM control.SchemaMigrations ORDER BY MigrationId')
        actual_receipts = {row['MigrationId']: row['ContentHash'] for row in receipts}
        report['migration_comparison'] = {
            'missing': sorted(set(expected['receipts']) - set(actual_receipts)),
            'hash_mismatch': [name for name, digest in expected['receipts'].items() if name in actual_receipts and actual_receipts[name] != digest],
            'not_hash_compared': sorted(set(actual_receipts) - set(expected['receipts'])),
        }
        objects = query('objects', "SELECT s.name+'.'+o.name AS name,o.type_desc,o.object_id FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id WHERE o.is_ms_shipped=0 AND o.type IN ('U','P','V','FN','IF','TF') ORDER BY name")
        report['missing_objects'] = sorted(set(expected['objects']) - {row['name'] for row in objects})
        query('columns', "SELECT s.name+'.'+t.name AS table_name,c.name AS column_name,ty.name AS type_name,c.max_length,c.precision,c.scale,c.is_nullable,c.is_identity,c.is_computed,dc.definition AS default_definition FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id JOIN sys.columns c ON c.object_id=t.object_id JOIN sys.types ty ON ty.user_type_id=c.user_type_id LEFT JOIN sys.default_constraints dc ON dc.object_id=c.default_object_id WHERE t.is_ms_shipped=0 ORDER BY table_name,c.column_id")
        query('parameters', "SELECT s.name+'.'+o.name AS procedure_name,p.name AS parameter_name,ty.name AS type_name,p.max_length,p.precision,p.scale,p.is_output FROM sys.parameters p JOIN sys.objects o ON o.object_id=p.object_id JOIN sys.schemas s ON s.schema_id=o.schema_id JOIN sys.types ty ON ty.user_type_id=p.user_type_id WHERE o.is_ms_shipped=0 ORDER BY procedure_name,p.parameter_id")
        query('constraints', "SELECT s.name+'.'+t.name AS table_name,c.name,c.type_desc,cc.definition,cc.is_disabled,cc.is_not_trusted FROM sys.objects c JOIN sys.tables t ON t.object_id=c.parent_object_id JOIN sys.schemas s ON s.schema_id=t.schema_id LEFT JOIN sys.check_constraints cc ON cc.object_id=c.object_id WHERE c.type IN ('PK','UQ','C','F') ORDER BY table_name,c.name")
        query('foreign_keys', "SELECT OBJECT_SCHEMA_NAME(parent_object_id)+'.'+OBJECT_NAME(parent_object_id) AS parent_table,name,OBJECT_SCHEMA_NAME(referenced_object_id)+'.'+OBJECT_NAME(referenced_object_id) AS referenced_table,is_disabled,is_not_trusted FROM sys.foreign_keys")
        query('indexes', "SELECT OBJECT_SCHEMA_NAME(i.object_id)+'.'+OBJECT_NAME(i.object_id) AS table_name,i.name,i.type_desc,i.is_unique,i.is_disabled,i.has_filter,i.filter_definition FROM sys.indexes i JOIN sys.tables t ON t.object_id=i.object_id WHERE t.is_ms_shipped=0 AND i.index_id>0")
        with connection.cursor() as cursor:
            cursor.execute("SELECT s.name+'.'+o.name,m.definition,m.execute_as_principal_id FROM sys.sql_modules m JOIN sys.objects o ON o.object_id=m.object_id JOIN sys.schemas s ON s.schema_id=o.schema_id WHERE o.is_ms_shipped=0")
            modules = list(cursor.fetchall())
        report['modules'] = []
        report['module_binding_errors'] = []
        for name, definition, execute_as in modules:
            source = expected['modules'].get(name)
            item = {'name': name, 'visible': definition is not None, 'execute_as': execute_as,
                    'compared': source is not None}
            if definition is not None and source is not None:
                item['matches'] = normalized(source) == normalized(definition)
                item['live_sha256'] = hashlib.sha256(normalized(definition).encode()).hexdigest()
                item['expected_sha256'] = hashlib.sha256(normalized(source).encode()).hexdigest()
                if not item['matches']:
                    item['live_definition'] = definition
            report['modules'].append(item)
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT referenced_entity_name FROM sys.dm_sql_referenced_entities(?,\'OBJECT\')', name)
                    cursor.fetchall()
            except Exception as error:
                report['module_binding_errors'].append({'name': name, 'error': str(error)[:1000]})
        query('principals', "SELECT name,type_desc,CONVERT(varchar(170),sid,1) AS sid FROM sys.database_principals WHERE name LIKE 'id-learningnemo-%'")
        permissions = query('permissions', "SELECT u.name,p.class,p.state_desc,p.permission_name,CASE WHEN p.class=1 THEN OBJECT_SCHEMA_NAME(p.major_id)+'.'+OBJECT_NAME(p.major_id) ELSE CONVERT(varchar(20),p.major_id) END AS target FROM sys.database_permissions p JOIN sys.database_principals u ON u.principal_id=p.grantee_principal_id WHERE u.name LIKE 'id-learningnemo-%' ORDER BY u.name,target")
        query('role_memberships', "SELECT u.name,r.name AS role_name FROM sys.database_role_members m JOIN sys.database_principals u ON u.principal_id=m.member_principal_id JOIN sys.database_principals r ON r.principal_id=m.role_principal_id WHERE u.name LIKE 'id-learningnemo-%'")
        report['invoice_grant_drift'] = {}
        for principal, grants in expected['grants'].items():
            rows = [row for row in permissions if row['name'] == principal and row['permission_name'] != 'CONNECT']
            actual = {row['target'] for row in rows if row['class'] == 1 and row['state_desc'] == 'GRANT' and row['permission_name'] == 'EXECUTE'}
            extra = [row for row in rows if row['target'] not in grants or row['class'] != 1 or row['state_desc'] != 'GRANT' or row['permission_name'] != 'EXECUTE']
            if actual != set(grants) or extra:
                report['invoice_grant_drift'][principal] = {'missing': sorted(set(grants)-actual), 'unexpected': extra}
        report['table_counts'] = []
        for item in objects:
            if item['type_desc'] == 'USER_TABLE':
                schema, table = item['name'].split('.')
                with connection.cursor() as cursor:
                    cursor.execute('SELECT COUNT_BIG(*) FROM ['+schema.replace(']',']]')+'].['+table.replace(']',']]')+']')
                    report['table_counts'].append({'table': item['name'], 'rows': cursor.fetchone()[0]})
        query('jobs', "SELECT TOP(30) JobId,Kind,TargetId,State,CreatedAt,ExpiresAt,CASE WHEN ExpiresAt<=SYSUTCDATETIME() THEN 1 ELSE 0 END AS expired,JSON_VALUE(ResultJson,'$.outcome') AS outcome,JSON_VALUE(ResultJson,'$.error') AS error,JSON_VALUE(ResultJson,'$.reason') AS reason,(SELECT COUNT(*) FROM control.InvoiceActivity a WHERE a.JobId=j.JobId) AS activity_count FROM control.InvoiceJobs j ORDER BY CreatedAt DESC")
        query('recent_activity', "SELECT JobId,Sequence,ObservedAt,JSON_VALUE(EventJson,'$.event_type') AS event_type,JSON_VALUE(EventJson,'$.source') AS source,JSON_VALUE(EventJson,'$.stage') AS stage,JSON_VALUE(EventJson,'$.state') AS state,JSON_VALUE(EventJson,'$.detail') AS detail,JSON_VALUE(EventJson,'$.reason') AS reason,JSON_VALUE(EventJson,'$.sandbox_id') AS sandbox_id,JSON_VALUE(EventJson,'$.error') AS error FROM (SELECT TOP(80) * FROM control.InvoiceActivity ORDER BY Sequence DESC) a ORDER BY Sequence")
        query('plans', 'SELECT TOP(30) PlanId,State,CreatedAt,ReviewExpiresAt,ApprovedAt,ApprovalExpiresAt FROM control.InvoicePlans ORDER BY CreatedAt DESC')
        query('runs', 'SELECT TOP(40) RunId,ScenarioId,SandboxId,Kind,ExpiresAt,RevokedAt,Calls,CallLimit FROM control.InvoiceRuns ORDER BY ExpiresAt DESC')
        query('workflow_integrity', "SELECT 'orphan_activity' AS issue,COUNT_BIG(*) AS violations FROM control.InvoiceActivity a WHERE NOT EXISTS(SELECT 1 FROM control.InvoiceJobs j WHERE j.JobId=a.JobId) UNION ALL SELECT 'finished_job_without_result',COUNT_BIG(*) FROM control.InvoiceJobs WHERE State='finished' AND ResultJson IS NULL UNION ALL SELECT 'plan_missing_planning_run',COUNT_BIG(*) FROM control.InvoicePlans p WHERE NOT EXISTS(SELECT 1 FROM control.InvoiceRuns r WHERE r.RunId=p.PlanningRunId AND r.Kind='planning') UNION ALL SELECT 'plan_missing_execution_run',COUNT_BIG(*) FROM control.InvoicePlans p WHERE p.ExecutionRunId IS NOT NULL AND NOT EXISTS(SELECT 1 FROM control.InvoiceRuns r WHERE r.RunId=p.ExecutionRunId AND r.Kind='execution')")
        query('quota_today', 'SELECT COUNT(*) AS launches_today FROM control.InvoiceGuestLaunches WHERE CreatedAt>=CONVERT(date,SYSUTCDATETIME())')
        query('request_blocking', 'SELECT session_id,status,command,blocking_session_id,wait_type,wait_time,total_elapsed_time FROM sys.dm_exec_requests WHERE database_id=DB_ID() AND session_id<>@@SPID')
        query('resources', 'SELECT TOP(5) end_time,avg_cpu_percent,avg_data_io_percent,avg_log_write_percent,max_worker_percent,max_session_percent FROM sys.dm_db_resource_stats ORDER BY end_time DESC')
        query('constraint_violations', 'DBCC CHECKCONSTRAINTS WITH ALL_CONSTRAINTS')
    return report


if __name__ == '__main__':
    result = collect()
    content = json.dumps(result, default=str, separators=(',', ':')).encode()
    packed = base64.b64encode(zlib.compress(content)).decode()
    chunks = [packed[index:index+1800] for index in range(0, len(packed), 1800)]
    for index, chunk in enumerate(chunks):
        print('SQL_AUDIT '+str(index+1)+'/'+str(len(chunks))+' '+chunk, flush=True)
    # Keep the artifact available for authorized collection until the job's
    # five-minute deadline. Bind loopback only; there is no ingress endpoint.
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class ResultHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/audit':
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    HTTPServer(('127.0.0.1', 8765), ResultHandler).serve_forever()