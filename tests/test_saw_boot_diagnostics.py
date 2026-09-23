import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'infra/next-phase'))
SPEC = importlib.util.spec_from_file_location('saw_boot', ROOT / 'infra/next-phase/inspect_saw_boot.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_boot_summary_keeps_failure_and_redacts_credentials():
    summary = MODULE.summarize('\x1b[0;31mERROR metadata token=private\x1b[0m\r\n' + 'normal\n' * 60 + 'Powering off\n')
    assert 'ERROR metadata' in summary and 'Powering off' in summary
    assert 'private' not in summary and '\x1b' not in summary
    assert len(summary.splitlines()) <= 76