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