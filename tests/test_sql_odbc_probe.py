from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "probe_sql_odbc",
    ROOT / "scripts" / "probe-sql-odbc.py",
)
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class DriverError(RuntimeError):
    def __init__(self, driver_error: str, ddbc_error: str):
        super().__init__("sanitized")
        self.driver_error = driver_error
        self.ddbc_error = ddbc_error


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (DriverError("Connection operation failed", "SQLSTATE:28000:Login failed for user"), "sql_authentication_rejected_as_expected"),
        (DriverError("Connection operation failed", "SSL Provider certificate verify failed"), "sql_odbc_tls_failed"),
        (DriverError("Connection operation failed", "SQLSTATE:08001:Client unable to establish connection"), "sql_odbc_prelogin_failed"),
        (DriverError("Connection operation failed", "SQLSTATE:08S01:Communication link failure"), "sql_odbc_link_failed"),
        (DriverError("Connection operation failed", "SQLSTATE:HYT00:Login timeout"), "sql_odbc_login_timeout"),
        (DriverError("Connection operation failed", "unclassified"), "sql_odbc_unknown_failed"),
    ],
)
def test_odbc_probe_classifies_only_safe_categories(error, category) -> None:
    assert probe.classify_odbc_failure(error) == category


def test_odbc_probe_reads_structured_driver_error_fields() -> None:
    error = DriverError("Connection operation failed", "SQLSTATE:28000:Login failed")
    assert "28000" in probe.failure_text(error)