from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / 'infra/next-phase/review-service/008_invoice_lab.sql'


def test_invoice_sql_parses_and_keeps_diagnostics_read_only():
    from sqlfluff.core import Linter
    source = SQL.read_text()
    parsed = Linter(dialect='tsql').parse_string(source)
    assert not parsed.violations, parsed.violations
    for name in ('ops.usp_diagnose_invoice_summary', 'ops.usp_diagnose_invoice_batches', 'ops.usp_verify_invoice_integrity'):
        procedure = source.split('CREATE OR ALTER PROCEDURE ' + name)[1].split('\nGO')[0]
        for forbidden in ('INSERT ', 'UPDATE ', 'DELETE ', 'EXEC ', 'KILL '):
            assert forbidden not in procedure
    assert source.count('EXEC lab.usp_import_invoice_batch') == 2
    assert 'DELETE ' not in source and 'KILL ' not in source
    assert 'WITH (UPDLOCK, HOLDLOCK)' in source
    assert 'invoice_duplicate_set_changed' in source


def test_invoice_workflow_sql_parses():
    from sqlfluff.core import Linter
    source = (SQL.parent / '009_invoice_workflow.sql').read_text()
    parsed = Linter(dialect='tsql').parse_string(source)
    assert not parsed.violations, parsed.violations
    assert 'WHERE ExecutionRunId IS NOT NULL' in source
    assert 'invoice_step_not_exactly_approved' in source
    assert 'SAVE TRANSACTION invoice_replay_check' in source
    assert 'ROLLBACK TRANSACTION invoice_replay_check' in source


def test_sql_call_catalog_and_grants_are_separated():
    from task_agent.control.invoice_catalog import GRANTS, PROCEDURES
    assert set(GRANTS['broker']) == {'ops.usp_execute_invoice_step'}
    assert set(GRANTS['verifier']) == {'ops.usp_verify_invoice_run'}
    assert not set(GRANTS['diagnostic']) & set(GRANTS['broker'])
    assert not set(GRANTS['incident']) & set(GRANTS['review'])
    assert 'lab.usp_apply_invoice_operation' not in PROCEDURES
    assert 'lab.usp_import_invoice_batch' not in PROCEDURES


def test_recovery_migration_parses_and_keeps_status_and_review_read_only():
    from sqlfluff.core import Linter
    from task_agent.control.invoice_catalog import GRANTS
    source = (SQL.parent / '011_invoice_recovery.sql').read_text()
    parsed = Linter(dialect='tsql').parse_string(source)
    assert not parsed.violations, parsed.violations
    for name in ('control.usp_read_invoice_scenario', 'control.usp_read_invoice_plans', 'control.usp_review_invoice_plans'):
        procedure = source.split('CREATE OR ALTER PROCEDURE ' + name)[1].split('\nGO')[0]
        assert not any(command in procedure for command in ('INSERT ', 'UPDATE ', 'DELETE ', 'EXEC ', 'KILL '))
    assert 'request.SponsorHash = @sponsor_hash' in source
    assert 'evidence.EvidenceHash = JSON_VALUE' in source
    assert GRANTS['simulator'] == ('control.usp_create_owned_invoice_scenario',)
    assert 'control.usp_read_invoice_scenario' in GRANTS['incident']
    assert 'control.usp_read_invoice_scenario' not in GRANTS['review']


def test_invoice_rehearsal_cannot_commit_or_impersonate_live_evidence():
    import inspect
    from task_agent.control.invoice_sql_probe import verify_invoice_workflow
    source = inspect.getsource(verify_invoice_workflow)
    assert 'COMMIT TRANSACTION' not in source
    assert 'finally:' in source and 'ROLLBACK TRANSACTION' in source
    assert "'humanRehearsal': False" in source and "'agentRun': False" in source


def test_retention_sql_is_read_only_old_successful_revoked_and_controller_only():
    from sqlfluff.core import Linter
    from task_agent.control.invoice_catalog import GRANTS
    source = (SQL.parent / '012_invoice_retention.sql').read_text()
    parsed = Linter(dialect='tsql').parse_string(source)
    assert not parsed.violations, parsed.violations
    assert not any(command in source for command in ('INSERT ', 'UPDATE ', 'DELETE ', 'KILL '))
    for boundary in ("job.State = 'finished'", 'run.RevokedAt IS NOT NULL', 'DATEADD(hour, -24', "'sandbox-stopped'", "'workspace-controller'", 'run.SponsorHash = job.SponsorHash'):
        assert boundary in source
    assert 'control.usp_read_invoice_retention' in GRANTS['controller']
    for kind in ('review', 'diagnostic', 'broker', 'simulator', 'verifier'):
        assert 'control.usp_read_invoice_retention' not in GRANTS[kind]


def test_review_window_preserves_old_deadlines_and_gates_on_one_persisted_value():
    from sqlfluff.core import Linter
    source = (SQL.parent / '013_invoice_review_window.sql').read_text()
    parsed = Linter(dialect='tsql').parse_string(source)
    assert not parsed.violations, parsed.violations
    assert 'SET ReviewExpiresAt = DATEADD(minute, 30, CreatedAt)\nWHERE ReviewExpiresAt IS NULL' in source
    assert 'DEFAULT DATEADD(minute, 90, SYSUTCDATETIME()) FOR ReviewExpiresAt' in source
    assert 'ApprovalExpiresAt = DATEADD(minute, 15, SYSUTCDATETIME())' in source
    for name in ('control.usp_submit_invoice_plan', 'control.usp_decide_invoice_plan', 'control.usp_review_invoice_plans'):
        procedure = source.split('CREATE OR ALTER PROCEDURE ' + name)[1].split('\nGO')[0]
        assert 'ReviewExpiresAt > SYSUTCDATETIME()' in procedure
        assert 'DATEADD(minute, -30' not in procedure
    assert "CONVERT(varchar(30), approved_plan.ReviewExpiresAt, 126) + 'Z' AS review_expires_at" in source
    assert 'PlanHash = @plan_hash' in source and 'SponsorHash <> @reviewer_hash' in source
    assert 'usp_claim_invoice_execution' not in source and 'lab.InvoiceScenarios SET' not in source


def test_migration_readback_mode_has_no_mutating_sql():
    import ast
    source = (ROOT / 'scripts/apply-invoice-migrations.py').read_text()
    tree = ast.parse(source)
    function = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == 'verify_only')
    statements = [item.args[0].value for item in ast.walk(function) if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute) and item.func.attr == 'execute']
    assert statements and all(statement.startswith('SELECT ') for statement in statements)
    assert 'range(2)' in ast.get_source_segment(source, function)


def test_doubled_windows_affect_only_new_proposals_and_new_independent_decisions():
    from sqlfluff.core import Linter
    source = (SQL.parent / '016_invoice_doubled_windows.sql').read_text()
    assert not Linter(dialect='tsql').parse_string(source).violations
    default, decision = source.split('CREATE OR ALTER PROCEDURE control.usp_decide_invoice_plan')
    assert 'DEFAULT DATEADD(minute, 180, SYSUTCDATETIME()) FOR ReviewExpiresAt' in default
    assert not any(command in default for command in ('UPDATE ', 'INSERT ', 'DELETE '))
    assert 'ApprovalExpiresAt = DATEADD(minute, 30, SYSUTCDATETIME())' in decision
    for gate in ("State = 'submitted'",'ReviewExpiresAt > SYSUTCDATETIME()','PlanHash = @plan_hash','SponsorHash <> @reviewer_hash'):
        assert gate in decision
    assert 'lab.InvoiceScenarios' not in source and 'usp_claim_invoice_execution' not in source