#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import json,pathlib,re
paths=sorted(pathlib.Path('/var/lib/learningnemo-saw').glob('invoice-smoke-*'),key=lambda path:path.stat().st_mtime)
assert paths
path=paths[-1]
assert not path.is_symlink()
for name in ('create.log','import.log','stop.log'):
    file=path/name
    if file.exists() and not file.is_symlink():
        text=file.read_text(errors='replace')[-1800:]
        text=re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+','<jwt-redacted>',text)
        text=re.sub(r'(?i)Bearer\s+\S+','Bearer <redacted>',text)
        print(json.dumps({'attempt':path.name,'file':name,'tail':text}))
PY