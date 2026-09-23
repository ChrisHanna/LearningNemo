"""Compile and rehearse proposed invoice SQL without persisting schema or data."""

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re

from task_agent.control.invoice_sql_probe import verify_invoice_workflow
from task_agent.control.mssql_client import build_connection_string, managed_identity_connect


def main():
    expiry = datetime.fromisoformat(os.environ['LEARNINGNEMO_MIGRATION_EXPIRES_AT'].replace('Z', '+00:00'))
    if expiry.tzinfo is None or not 0 < (expiry - datetime.now(UTC)).total_seconds() <= 3600:
        raise ValueError('bounded SQL validation lease required')
    server, client_id = os.environ['LEARNINGNEMO_SQL_SERVER'], os.environ['AZURE_CLIENT_ID']
    connection_string = build_connection_string(server=server, database=os.environ['LEARNINGNEMO_SQL_DATABASE'],
        client_id=client_id, application_name='LearningNeMoInvoiceValidation')
    paths = [Path('/app/migrations') / name for name in ('008_invoice_lab.sql', '009_invoice_workflow.sql')]
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    results = []
    for healthy in (False, True):
        with managed_identity_connect(connection_string, client_id, timeout_seconds=120, server=server) as connection:
            connection.autocommit = True
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT OBJECT_ID(N'lab.InvoiceScenarios', N'U')")
                    if cursor.fetchone()[0] is not None:
                        raise ValueError('temporary-schema validator refuses an installed invoice lab')
                    cursor.execute('SET XACT_ABORT ON; BEGIN TRANSACTION;')
                    cursor.execute("DECLARE @result int; EXEC @result=sys.sp_getapplock @Resource=N'learningnemo-invoice-validation', @LockMode=N'Exclusive', @LockOwner=N'Transaction', @LockTimeout=1000; IF @result < 0 THROW 51999, 'invoice_validation_busy', 1;")
                    for path in paths:
                        for batch in re.split(r'(?im)^\s*GO\s*$', path.read_text()):
                            if batch.strip():
                                cursor.execute(batch)
                results.append(verify_invoice_workflow(connection, healthy=healthy, temporary_schema=True))
            finally:
                with connection.cursor() as cursor:
                    cursor.execute('IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;')
    print('PASS invoice_sql_probe ' + json.dumps({'migrations': hashes, 'results': results, 'schemaPersisted': False}))


if __name__ == '__main__':
    main()