from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[1]


def test_resolver_repair_requires_stopped_overlay_and_preserves_image_policy():
    path = ROOT / 'scripts/repair-sandbox-resolver.sh'
    subprocess.run(['bash', '-n', str(path)], check=True)
    source = path.read_text()
    assert "selected[0]['phase']=='Stopped'" in source
    assert 'overlay still open' in source
    assert 'cp -a "$resolver" "$backup/resolv.conf.before"' in source
    assert 'nameserver 168.63.129.16' in source
    assert 'sandbox delete' not in source
    assert '--policy' not in source and '--from' not in source
    assert source.index('umount "$mountpoint"\nmounted=false') < source.index('sandbox start')


def test_https_method_policies_require_tls_termination():
    import yaml
    for mode in ('planning', 'execution'):
        policy = yaml.safe_load((ROOT / f'infra/next-phase/openshell/{mode}-policy.yaml').read_text())
        for network in policy['network_policies'].values():
            for endpoint in network['endpoints']:
                if endpoint.get('port') == 443 and endpoint.get('protocol') == 'rest':
                    assert endpoint['tls'] == 'terminate'