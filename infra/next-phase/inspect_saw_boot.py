"""Preserve and summarize managed boot diagnostics without starting the VM."""

import os
from pathlib import Path
import re

from diagnostic_attempt import DiagnosticAttempt, sanitize


def summarize(text):
    text = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', sanitize(text))
    lines = text.replace('\r', '').splitlines()
    findings = [line for line in lines if re.search(r'error|fail|timed out|metadata|waagent|expire|power.?off|panic|emergency|IMDS', line, re.I)]
    return '\n'.join(findings[-40:] + ['BOOT TAIL'] + lines[-35:])


def main():
    attempt = DiagnosticAttempt(Path.home() / '.local/state/learningnemo/diagnostics', 'vm-boot')
    if attempt.command('account', ['account', 'show'])['id'] != os.environ['AZURE_SUBSCRIPTION_ID']:
        raise ValueError('subscription mismatch')
    arguments = ['-g', 'rg-learningnemo-saw-dev', '-n', 'vm-learningnemo-saw-dev']
    view = attempt.command('instance-view', ['vm', 'get-instance-view', *arguments])
    print('STATUS', view.get('instanceView', {}).get('statuses'), flush=True)
    boot = attempt.command('boot-console', ['vm', 'boot-diagnostics', 'get-boot-log', *arguments])
    if not isinstance(boot, str):
        raise ValueError('unexpected boot console format')
    print(summarize(boot), flush=True)
    print('EVIDENCE', attempt.directory, flush=True)


if __name__ == '__main__':
    main()