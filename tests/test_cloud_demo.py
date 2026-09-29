import json
from pathlib import Path
from unittest.mock import patch

import pytest

from task_agent.console.hosting import ConsoleHosting
from task_agent.console.remote_workspace import RemoteWorkspace
from task_agent.console.live_workspace import WorkspaceLiveError


def test_remote_controller_uses_fixed_routes_and_does_not_follow_redirects():
    transport = RemoteWorkspace('https://controller.internal.example.test')
    with patch('httpx.Client') as client:
        response = client.return_value.__enter__.return_value.post.return_value
        response.status_code = 200
        response.json.return_value = {'status': 'blocked'}
        assert transport.run('a' * 32, 'test-token') == {'status': 'blocked'}
        client.assert_called_once_with(timeout=230, follow_redirects=False)
        call = client.return_value.__enter__.return_value.post.call_args
        assert call.args[0].endswith('/workspace/run')
        assert call.kwargs['json'] == {'runId': 'a' * 32}
        assert call.kwargs['headers']['Authorization'] == 'Bearer test-token'
        response.status_code = 302
        with pytest.raises(WorkspaceLiveError):
            transport.check('test-token')


def test_cloud_images_are_non_root_and_do_not_include_operator_profiles():
    root = Path(__file__).parents[1]
    for kind in ('console', 'agent'):
        source = (root / f'containers/cloud-{kind}.Dockerfile').read_text()
        assert '@sha256:' in source
        assert 'USER 65532:65532' in source
        assert '--require-hashes' in source
        assert '.azure' not in source
        assert '.nemo-test-client.json' not in source
    script = (root / 'scripts/build-cloud-demo.sh').read_text()
    assert 'tar -C "$root"' in script
    assert 'cloud-demo-build' in script


def test_invoice_proof_overlay_preserves_pinned_serviceops_console():
    root = Path(__file__).parents[1]
    dockerfile = (root / 'containers/cloud-console-overlay.Dockerfile').read_text()
    expected = 'crlearningnemodevgruyrc4qwdvvm.azurecr.io/learningnemo/cloud-console@sha256:a87ee3b7ade4af17af1e2a49c3d4676a9d5dca3a42b0cb43261f5b5a3d4b8443'
    assert dockerfile.splitlines()[0] == 'FROM ' + expected
    assert dockerfile.count('COPY ') == 18
    for name in ('index.html', 'invoice-view.js', 'invoice-scene.js', 'invoice-evidence.js', 'invoice-experience.js', 'invoice-workflow.js', 'learningnemo.js', 'live-workspace.js', 'mission.js', 'mission-flow.js', 'pattern.js', 'invoice.css', 'openai-theme.css', 'session.js'):
        assert f'static/{name} /app/src/task_agent/console/static/{name}' in dockerfile
    assert dockerfile.rstrip().endswith('USER 65532:65532')
    script = (root / 'scripts/build-cloud-console-overlay.sh').read_text()
    assert 'cloud-console-overlay-build' in script
    assert 'containers/cloud-console-overlay.Dockerfile' in script
    assert 'cloud-console-overlay.image.txt' in script
    assert 'cp "$state/cloud-console-overlay.image.txt" "$state/cloud-console.image.txt"' in script
    assert 'az acr build' in script
    session = (root / 'src/task_agent/console/static/session.js').read_text()
    assert 'session?.authMode === "public-demo"' in session
    assert '"Analyze, propose, and submit for review"' in session
    cloud_build = (root / 'scripts/build-cloud-demo.sh').read_text()
    assert 'cloud-console-overlay.image.txt' in cloud_build
    assert 'ServiceOps source is absent from this workspace' in cloud_build
    assert 'scripts/build-cloud-console-overlay.sh' in cloud_build


def test_cloud_verifier_requires_exact_secret_scoped_serviceops_access():
    source=(Path(__file__).parents[1]/'infra/next-phase/verify-cloud-demo.py').read_text()
    assert 'serviceops-demo-users' in source and 'serviceops-demo-signing-key' in source
    assert '4633458b-17de-408a-b874-0445c86b69e6' in source
    assert "actual_grants" in source and "Dashboard permission inventory differs" in source
    assert 'LEARNINGNEMO_SERVICEOPS_OPERATOR_ORIGIN' in source
    assert 'LEARNINGNEMO_SERVICEOPS_REVIEW_ORIGIN' in source
    assert "values.get('controllerImage',values['consoleImage'])" in source
    assert 'for attempt in range(3):' in source and "'429','retry-after','too many requests'" in source
    assert "read-only cloud verification failed" in source