from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[1]


def test_recovery_renews_only_existing_dependencies_with_explicit_grant_scope():
    script = ROOT / 'scripts/renew-demo-window.sh'
    subprocess.run(['bash', '-n', str(script)], check=True)
    source = script.read_text()
    assert '--ttl-hours 2' in source
    assert '--allow-human-services' in source
    assert 'deploy-workspace.sh' not in source
    assert 'build-cloud-demo.sh agent' not in source
    assert 'renew-trusted-workers' in source
    artifacts = (ROOT/'infra/next-phase/deploy-artifacts.sh').read_text()
    assert 'verification_scope=()' in artifacts
    assert 'verification_scope=(--allow-cloud-demo --allow-human-services)' in artifacts