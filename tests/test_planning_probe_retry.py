import base64
import shlex
import subprocess

import pytest

from task_agent.console.live_workspace import parse_probe, probe_script


@pytest.mark.parametrize('failed_reads,expected_reads,passed', [(0, 1, True), (1, 2, True), (2, 2, False)])
def test_planning_read_retry_is_bounded_and_never_retries_post(tmp_path, failed_reads, expected_reads, passed):
    script = probe_script('demo.region.azurecontainerapps.io', 'abc')
    command = shlex.split(script.splitlines()[-1])[-1]
    guest = base64.b64decode(shlex.split(command)[2]).decode()
    calls = tmp_path / 'calls'
    fake = tmp_path / 'curl.py'
    fake.write_text('''import pathlib,sys
calls=pathlib.Path(sys.argv[1])
failed_reads=int(sys.argv[2])
history=calls.read_text() if calls.exists() else ''
post='POST' in sys.argv[3:]
calls.write_text(history+('POST\\n' if post else 'GET\\n'))
if not post and history.count('GET\\n') < failed_reads:
    raise SystemExit(28)
print('403' if post else '401',end='')
''')
    guest = guest.replace('/usr/bin/curl', f'python3 {shlex.quote(str(fake))} {shlex.quote(str(calls))} {failed_reads}')
    guest = guest.replace('/usr/bin/python3 -c', '/bin/false')
    guest = guest.replace('uid=$(id -u)', 'uid=998')
    result = subprocess.run(['sh'], input=guest, text=True, capture_output=True, check=True)
    assert calls.read_text().splitlines() == ['GET'] * expected_reads + ['POST']
    assert parse_probe({'value': [{'message': result.stdout}]}, 'abc')['passed'] is passed