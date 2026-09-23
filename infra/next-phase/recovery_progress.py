"""Print bounded, sanitized evidence for the latest recovery attempts."""

import json
from pathlib import Path

from diagnostic_attempt import sanitize


def main():
    state = Path.home() / '.local/state/learningnemo'
    for name in ('maintenance-attempts', 'sandbox-attempts'):
        candidates = list((state / name).glob('*/events.jsonl'))
        if not candidates:
            continue
        journal = max(candidates, key=lambda path: path.stat().st_mtime)
        print('ATTEMPT', name, journal.parent.name, flush=True)
        events = [json.loads(line) for line in journal.read_text().splitlines()]
        for event in events[-12:]:
            print(json.dumps(sanitize(event)), flush=True)
        for event in events:
            evidence = event.get('evidence', '')
            if event['outcome'] == 'failed' and evidence and Path(evidence).name == evidence:
                print(sanitize((journal.parent / evidence).read_text())[-6000:], flush=True)
        for path in journal.parent.glob('*sandbox-provision.json'):
            print(sanitize(path.read_text())[-6000:], flush=True)
        for path in journal.parent.glob('*guest-logs*.json'):
            print(sanitize(path.read_text())[-12000:], flush=True)


if __name__ == '__main__':
    main()