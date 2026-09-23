import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "infra/next-phase"))
SPEC = importlib.util.spec_from_file_location("worker_renewal", ROOT / "infra/next-phase/renew_trusted_workers.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("path", ["properties.tags.expiresAt", "properties.template.containers", "identity", "properties.configuration"])
def test_worker_renewal_cannot_publish_configuration_changes(path):
    preview = {"changes": [{"changeType": "Modify", "after": {"type": "Microsoft.Resources/tags"}, "delta": [{"path": path}]}]}
    if path == "properties.tags.expiresAt":
        MODULE.verify_expiry_only(preview)
    else:
        with pytest.raises(ValueError): MODULE.verify_expiry_only(preview)