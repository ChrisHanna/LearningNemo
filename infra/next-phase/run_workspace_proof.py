"""Run the fixed read-only Planning proof with immutable transport evidence."""

import json
import os
from pathlib import Path
import sys

from diagnostic_attempt import DiagnosticAttempt


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
    from task_agent.console.live_workspace import LiveWorkspace
    attempt = DiagnosticAttempt(Path.home() / '.local/state/learningnemo/proof-attempts', 'workspace-proof')
    subscription = os.environ['AZURE_SUBSCRIPTION_ID']
    if attempt.command('account', ['account', 'show'])['id'] != subscription:
        raise ValueError('subscription mismatch')
    workspace = LiveWorkspace(subscription)
    workspace._az = lambda arguments, timeout=30: attempt.command('workspace-query', arguments, timeout=timeout)
    print('ATTEMPT', attempt.directory, flush=True)
    proof = workspace.run(attempt.run_id)
    attempt.record('proof', proof)
    print(json.dumps(proof, indent=2), flush=True)
    if proof['status'] != 'passed':
        for path in attempt.directory.glob('*workspace-query.json'):
            document = json.loads(path.read_text())['data']['response']
            if isinstance(document, dict) and 'value' in document:
                print(json.dumps(document)[-6000:], flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    main()