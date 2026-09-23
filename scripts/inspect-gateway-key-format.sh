#!/bin/bash
set -euo pipefail
python3 - <<'PY'
import base64
from pathlib import Path
import sqlite3

root = Path('/home/sawadmin/.local/state/openshell/tls/jwt')
for name in ('signing.pem', 'public.pem'):
    lines = (root / name).read_text().splitlines()
    label = lines[0]
    if not label.startswith('-----BEGIN ') or not label.endswith('-----'):
        raise ValueError('unexpected PEM label')
    print(name, label, 'decodedBytes', len(base64.b64decode(''.join(lines[1:-1]), validate=True)))
for base in (Path('/home/sawadmin/.local/state/openshell'), Path('/home/sawadmin/.local/share/openshell')):
    for path in base.rglob('*.db'):
        if path.is_symlink():
            continue
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as connection:
            print('DATABASE_SCHEMA', path)
            for row in connection.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"):
                print(row)
PY