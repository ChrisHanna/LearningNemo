import json
from pathlib import Path
import subprocess


def test_human_migration_job_has_no_retry_or_server_surface():
    root = Path(__file__).parents[1]
    result = subprocess.run(['az', 'bicep', 'build', '--file', str(root / 'infra/next-phase/human-migration-job.bicep'), '--stdout'], capture_output=True, text=True, check=True)
    document = json.loads(result.stdout)
    assert len(document['resources']) == 1
    job = document['resources'][0]
    assert job['type'] == 'Microsoft.App/jobs'
    assert 2 <= len(job['name']) <= 32
    config = job['properties']['configuration']
    assert config['triggerType'] == 'Manual'
    assert config['replicaRetryLimit'] == 0
    assert config['replicaTimeout'] == 600
    assert config['secrets'] == []
    assert 'ingress' not in config
    container = job['properties']['template']['containers'][0]
    assert document['parameters']['invoiceProbeOnly']['defaultValue'] is False
    assert document['parameters']['invoiceApply']['defaultValue'] is False
    assert document['parameters']['invoiceVerifyOnly']['defaultValue'] is False
    assert container['command'] == "[if(parameters('invoiceVerifyOnly'), createArray('python', '/app/scripts/apply-invoice-migrations.py', '--verify-only'), createArray('python', if(parameters('invoiceApply'), '/app/scripts/apply-invoice-migrations.py', if(parameters('invoiceProbeOnly'), '/app/scripts/verify-invoice-sql.py', '/app/scripts/apply-human-migrations.py'))))]"