from types import SimpleNamespace

import mssql_python

from task_agent.control.mssql_client import build_connection_string


class Provider:
    def get_token(self, _scope: str) -> SimpleNamespace:
        return SimpleNamespace(token="x")


value = build_connection_string(
    server="sql-example.database.windows.net",
    database="learningnemo",
    client_id="11111111-1111-4111-8111-111111111111",
    application_name="LearningNeMo",
)
try:
    mssql_python.connect(value, token_provider=Provider(), timeout=1)
except Exception as error:
    print(type(error).__name__)
    print(getattr(error, "driver_error", ""))
    print(getattr(error, "ddbc_error", ""))
