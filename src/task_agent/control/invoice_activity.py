"""Bounded, sponsor-filtered activity metadata; never a terminal input channel."""

from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sqlite3


class InvoiceActivityStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS activity (sequence INTEGER PRIMARY KEY, sponsor TEXT NOT NULL, run_id TEXT NOT NULL, observed_at TEXT NOT NULL, event_json TEXT NOT NULL)')
            connection.execute('CREATE INDEX IF NOT EXISTS activity_owner_run ON activity(sponsor, run_id, sequence)')
        self.path.chmod(0o600)

    async def append(self, *, sponsor_hash, run_id, event):
        if not re.fullmatch('[a-f0-9]{64}', sponsor_hash) or not re.fullmatch('[a-f0-9]{32}', run_id):
            raise ValueError('invalid activity owner or run')
        fields = {'source', 'event_type', 'sandbox_id', 'policy_hash', 'kind', 'actor', 'tool', 'outcome', 'reason', 'http_status', 'uid', 'executed_steps', 'independently_verified'}
        safe = {key: value for key, value in event.items() if key in fields and type(value) in (str, int, bool)}
        for key, value in safe.items():
            if isinstance(value, str):
                safe[key] = ''.join(character for character in value[:160] if 32 <= ord(character) <= 126)
        encoded = json.dumps(safe, separators=(',', ':'))
        with sqlite3.connect(self.path) as connection:
            count = connection.execute('SELECT COUNT(*) FROM activity WHERE sponsor=? AND run_id=?', (sponsor_hash, run_id)).fetchone()[0]
            if count >= 200:
                raise ValueError('activity budget exhausted')
            connection.execute('INSERT INTO activity(sponsor,run_id,observed_at,event_json) VALUES(?,?,?,?)',
                               (sponsor_hash, run_id, datetime.now(UTC).isoformat(), encoded))

    def read(self, *, sponsor_hash, run_id, after=0):
        if type(after) is not int or after < 0:
            raise ValueError('invalid activity cursor')
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute('SELECT sequence,observed_at,event_json FROM activity WHERE sponsor=? AND run_id=? AND sequence>? ORDER BY sequence LIMIT 100',
                                      (sponsor_hash, run_id, after)).fetchall()
        return [{'sequence': sequence, 'observed_at': timestamp, **json.loads(event)} for sequence, timestamp, event in rows]