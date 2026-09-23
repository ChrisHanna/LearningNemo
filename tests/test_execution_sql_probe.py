import inspect

from task_agent.control.execution_sql_probe import verify_rolled_back_workflow


def test_sql_rehearsal_is_explicitly_transactional_and_never_deletes_records():
    source = inspect.getsource(verify_rolled_back_workflow)
    assert 'finally:' in source and 'ROLLBACK TRANSACTION' in source
    assert 'COMMIT TRANSACTION' not in source and 'DELETE ' not in source
    assert "'humanRehearsal': False" in source
    assert 'ExecutionStatus.model_validate_json' in source
    assert 'usp_propose_readonly_analysis' in source
    assert 'query_cancel' not in source and 'usp_begin_human_investigation' not in source