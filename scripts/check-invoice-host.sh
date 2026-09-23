#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import json
import pathlib
import shutil
import subprocess

memory = dict(line.split(':', 1) for line in pathlib.Path('/proc/meminfo').read_text().splitlines())
available = int(memory['MemAvailable'].strip().split()[0]) * 1024
disk = shutil.disk_usage('/var/lib')
version = subprocess.run(['openshell', '--version'], capture_output=True, text=True, check=True).stdout.strip()
print(json.dumps({'source': 'retained-host-preflight', 'memoryAvailableBytes': available,
                  'diskFreeBytes': disk.free, 'diskTotalBytes': disk.total, 'openshell': version,
                  'dockerAvailable': shutil.which('docker') is not None}))
PY