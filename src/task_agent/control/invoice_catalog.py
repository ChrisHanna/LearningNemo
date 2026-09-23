"""Fixed invoice SQL calls and least-privilege service grant inventories."""

PARAMETERS = {
    'control.usp_admit_invoice_job': ('job_id', 'sponsor_hash', 'kind', 'target_id', 'plan_hash'),
    'control.usp_claim_invoice_job': ('job_id', 'sponsor_hash'),
    'control.usp_finish_invoice_job': ('job_id', 'sponsor_hash', 'state', 'result_json'),
    'control.usp_get_invoice_job': ('job_id', 'sponsor_hash'),
    'control.usp_append_invoice_activity': ('job_id', 'sponsor_hash', 'event_json'),
    'control.usp_read_invoice_activity': ('job_id', 'sponsor_hash', 'after'),
    'control.usp_read_invoice_retention': (),
    'control.usp_request_invoice_sandbox_test': ('job_id', 'sponsor_hash', 'sandbox_id'),
    'control.usp_claim_invoice_sandbox_test': ('job_id', 'sponsor_hash', 'sandbox_id'),
    'control.usp_record_invoice_sandbox_test': ('job_id', 'sponsor_hash', 'sandbox_id', 'receipt_json'),
    'control.usp_create_invoice_scenario': ('scenario_id', 'variant'),
    'control.usp_create_owned_invoice_scenario': ('scenario_id', 'variant', 'sponsor_hash'),
    'control.usp_read_invoice_scenario': ('scenario_id', 'sponsor_hash'),
    'ops.usp_diagnose_invoice_summary': ('scenario_id',),
    'ops.usp_diagnose_invoice_batches': ('scenario_id',),
    'ops.usp_verify_invoice_integrity': ('scenario_id',),
    'control.usp_register_invoice_planning': ('run_id', 'scenario_id', 'sponsor_hash', 'sandbox_id', 'policy_hash', 'capability_hash', 'expires_at', 'call_limit'),
    'control.usp_admit_invoice_tool': ('run_id', 'capability_hash', 'tool'),
    'control.usp_record_invoice_evidence': ('run_id', 'evidence_hash', 'evidence_json'),
    'control.usp_record_invoice_plan': ('plan_json', 'plan_hash', 'sponsor_hash'),
    'control.usp_submit_invoice_plan': ('plan_id', 'plan_hash', 'sponsor_hash'),
    'control.usp_decide_invoice_plan': ('plan_id', 'plan_hash', 'reviewer_hash', 'decision'),
    'control.usp_claim_invoice_execution': ('plan_id', 'plan_hash', 'sponsor_hash', 'run_id', 'sandbox_id', 'policy_hash', 'capability_hash'),
    'ops.usp_execute_invoice_step': ('run_id', 'capability_hash', 'step_json'),
    'control.usp_read_invoice_plans': ('sponsor_hash',),
    'control.usp_review_invoice_plans': (),
    'control.usp_revoke_invoice_run': ('run_id', 'sponsor_hash'),
    'control.usp_get_invoice_execution': ('run_id', 'sponsor_hash'),
    'ops.usp_verify_invoice_run': ('run_id',),
    'control.usp_complete_invoice_plan': ('plan_id', 'plan_hash', 'sponsor_hash'),
    'control.usp_get_invoice_evidence': ('run_id', 'sponsor_hash', 'evidence_hash'),
}

PROCEDURES = {name: ('EXEC ' + name + ' ' + ', '.join('@' + parameter + '=?' for parameter in parameters), parameters)
              for name, parameters in PARAMETERS.items()}

GRANTS = {
    'jobs': ('control.usp_admit_invoice_job', 'control.usp_claim_invoice_job', 'control.usp_finish_invoice_job', 'control.usp_get_invoice_job', 'control.usp_append_invoice_activity', 'control.usp_read_invoice_activity'),
    'simulator': ('control.usp_create_owned_invoice_scenario',),
    'controller': ('control.usp_register_invoice_planning', 'control.usp_claim_invoice_execution', 'control.usp_revoke_invoice_run', 'control.usp_read_invoice_retention', 'control.usp_request_invoice_sandbox_test', 'control.usp_claim_invoice_sandbox_test', 'control.usp_record_invoice_sandbox_test'),
    'diagnostic': ('control.usp_admit_invoice_tool', 'ops.usp_diagnose_invoice_summary', 'ops.usp_diagnose_invoice_batches', 'control.usp_record_invoice_evidence'),
    'inference': ('control.usp_admit_invoice_tool',),
    'incident': ('control.usp_record_invoice_plan', 'control.usp_submit_invoice_plan', 'control.usp_read_invoice_plans', 'control.usp_get_invoice_execution', 'control.usp_complete_invoice_plan', 'control.usp_get_invoice_evidence', 'control.usp_read_invoice_scenario'),
    'review': ('control.usp_review_invoice_plans', 'control.usp_decide_invoice_plan'),
    'broker': ('ops.usp_execute_invoice_step',),
    'verifier': ('ops.usp_verify_invoice_run',),
}