(function (root, factory) {
  const view = factory();
  if (typeof module === 'object' && module.exports) module.exports = view;
  else root.invoiceView = view;
})(typeof window === 'object' ? window : globalThis, function () {
  const operations = {
    'invoice.quarantine-duplicates.v1': { title: 'Quarantine duplicate invoices', effect: 'Separate the evidenced duplicate rows; preserve legitimate invoices.', icon: 'layers' },
    'invoice.rebuild-total.v1': { title: 'Reconcile the reported total', effect: 'Recalculate the total from the remaining active invoices.', icon: 'calculator' },
    'invoice.activate-idempotent-import.v1': { title: 'Prevent duplicate imports', effect: 'Activate the idempotent importer for subsequent retries.', icon: 'shield-check' },
  };
  const tools = { invoice_summary: 'Read invoice summary', invoice_batches: 'Compare import attempts', execute_step: 'Request approved operation', inference: 'Request model response' };
  const chapters = [
    { title: 'Investigate without changing the database', question: 'What can a read-only agent discover?', focus: 'planning', lesson: 'Planning receives diagnostic tools, not repair authority.' },
    { title: 'Turn evidence into a bounded proposal', question: 'A recommendation is not permission.', focus: 'planning', lesson: 'The agent proposes operations. The database has not been repaired by a proposal.' },
    { title: 'A different person grants exact authority', question: 'Who is allowed to approve this change?', focus: 'human', lesson: 'Independent review binds one artifact. Approval does not execute it.' },
    { title: 'Different agent. Different sandbox. Exact scope.', question: 'What changes after approval?', focus: 'execution', lesson: 'Execution gets a new run capability, limited to the approved operations.' },
    { title: 'Verify the result independently', question: 'Who establishes that the repair worked?', focus: 'verifier', lesson: 'SQL verification, not the agent\'s final message, establishes the outcome.' },
  ];
  const checks = { no_duplicates: 'No duplicate invoices', legitimate_invoices_preserved: 'Legitimate invoices preserved', total_reconciles: 'Reported total reconciles', idempotent_version_active: 'Idempotent importer active', replay_created_no_invoices: 'Import replay adds no invoices' };
  function operation(name) { return operations[name] || { title: 'Unrecognized operation', effect: 'Inspect the exact artifact before proceeding.', icon: 'file-check-2' }; }
  function eventView(event) {
    const tool = tools[event.tool] || 'Tool request';
    const names = {
      'preparing-sandbox': ['Preparing a fresh sandbox', 'harness'],
      'sandbox-bound': ['Sandbox identity and policy bound', 'sandbox'],
      'authority-issued': ['Short-lived run authority issued', 'harness'],
      'agent-started': ['Agent process started', 'agent'],
      'tool-requested': [tool, event.tool === 'execute_step' ? 'broker' : 'diagnostics'],
      'tool-returned': [tool + ': response received', event.tool === 'execute_step' ? 'broker' : 'diagnostics'],
      'tool-denied': [tool + ': denied by agent adapter', 'agent'],
      'tool-unconfirmed': [tool + ': outcome unconfirmed', 'agent'],
      'decision-produced': ['Agent published a decision', 'agent'],
      'agent-finished': ['Agent finished; not independent verification', 'agent'],
      'verification-started': ['Independent database checks requested', 'verifier'],
      'verification-passed': ['Independent database checks passed', 'verifier'],
      'authority-revoked': ['Run authority revoked', 'harness'],
      'sandbox-stopped': ['Sandbox stop confirmed', 'sandbox'],
      'sandbox-test-started': ['Testing this agent sandbox after revocation', 'sandbox'],
      'sandbox-test-recorded': ['Same-sandbox test receipt recorded', 'sandbox'],
      'diagnostic-observed': ['SQL observed ' + event.duplicate_invoices + ' duplicate invoices', 'diagnostics'],
      'step-receipt-recorded': ['Step ' + event.step_id + ': ' + operation(event.operation).title + ' / SQL receipt recorded', 'broker'],
    };
    const [title, component] = names[event.event_type] || ['Recorded event', 'harness'];
    const reported = event.source === 'agent-runtime';
    const authoritative = ['preparing-sandbox','sandbox-bound','authority-issued','verification-started','verification-passed','authority-revoked','sandbox-stopped','sandbox-test-started','sandbox-test-recorded','diagnostic-observed','step-receipt-recorded'].includes(event.event_type);
    if (authoritative && event.source !== 'workspace-controller') return { title: 'Unverified claim: ' + title, component: 'agent', source: 'Not a harness observation', tone: 'warning', raw: event };
    return { title, component, source: reported ? 'Agent-reported' : event.source === 'workspace-controller' ? 'Harness observation' : 'Source: ' + (event.source || 'unknown'),
      tone: /denied|unconfirmed/.test(event.event_type || '') ? 'warning' : 'neutral', raw: event };
  }
  function capacityMessage(job) {
    const result = job?.result;
    const reserved = result?.reserved_slots ?? 0;
    return result?.reason === 'sandbox-capacity' && result.sandbox_created === false && result.agent_started === false &&
      [0,1].includes(reserved) && Number.isSafeInteger(result.retained_sandboxes) && Number.isSafeInteger(result.retained_limit) && result.retained_limit > reserved && result.retained_sandboxes >= result.retained_limit-reserved ?
      (reserved ? 'Sandbox admission paused: ' : 'Sandbox capacity reached: ')+result.retained_sandboxes+' retained / '+result.retained_limit+' limit'+(reserved ? '; 1 slot reserved' : '')+'. No agent started.' : null;
  }
  function observation(job, events, connected, observedAt, now = Date.now()) {
    if (!job) return { title: 'No agent run requested', state: 'idle', label: 'Not running', live: false };
    if (!connected) return { title: 'Observation disconnected', state: 'disconnected', label: 'Last known state', live: false };
    if (job.state === 'uncertain' || job.state === 'admission-unconfirmed') return { title: capacityMessage(job) || 'Run outcome unconfirmed', state: 'uncertain', label: 'Do not replay', live: false };
    if (job.state === 'finished') return { title: 'Run finished', state: 'finished', label: 'Recorded', live: false };
    if (!Number.isFinite(observedAt) || now - observedAt > 15000) return { title: 'Awaiting a fresh observation', state: 'stale', label: 'Last known state', live: false };
    const last = events.at(-1);
    return { title: last ? eventView(last).title : job.state === 'queued' ? 'Request queued' : 'Run admitted; waiting for sandbox evidence', state: job.state, label: 'Live observation', live: true };
  }
  function permissions(session) {
    const scopes = new Set(session?.grantedScopes || []), roles = new Set(session?.grantedRoles || []);
    const authenticated = session?.status === 'authenticated';
    const operator = authenticated && session.persona === 'operator' && roles.has('Task.Operator') && roles.has('Task.Reader') && !roles.has('Task.Approver');
    const approver = authenticated && session.persona === 'approver' && roles.has('Task.Approver') && !roles.has('Task.Operator');
    const read = operator && scopes.has('agent.invoke') && scopes.has('tasks.read');
    return { authenticated, investigate: read, submit: read, execute: read && scopes.has('tasks.execute'), approve: approver && scopes.has('agent.invoke') && scopes.has('plans.review') };
  }
  function boundEvents(job, events, row, stage) {
    if (!job) return [];
    if (!row) return stage === 0 && job.kind === 'planning' ? events : [];
    if (stage >= 3) return executionMatches(job, row) ? events : [];
    const expected = stage >= 3 ? row.execution_run_id : row.plan_json.planning_run_id;
    return expected === job.job_id ? events : [];
  }
  function executionMatches(job, row) {
    if (!job?.job_id || job.kind !== 'execution' || !row) return false;
    if (row.execution_run_id) return row.execution_run_id === job.job_id;
    return row.state === 'approved' && Boolean(row.plan_json.plan_id) && job.target_id === row.plan_json.plan_id && (!job.plan_hash || job.plan_hash === row.plan_hash);
  }
  function verifiedChecks(row) {
    if (!row || !['verified', 'completed'].includes(row.state)) return null;
    try {
      const result = typeof row.verification_json === 'string' ? JSON.parse(row.verification_json) : row.verification_json;
      return result && Object.keys(checks).every(key => result[key] === true) ? result : null;
    } catch { return null; }
  }
  function diagnostics(job, row) {
    const result = job?.result?.diagnostics;
    if (!result || result.source !== 'invoice-diagnostic-api' || job.kind !== 'planning' || job.state !== 'finished') return null;
    if (row && (row.plan_json.planning_run_id !== job.job_id || row.plan_json.evidence_hash !== result.evidence_hash || row.plan_json.scenario_id !== result.scenario_id)) return null;
    if (['orders','active_invoices','duplicate_invoices'].some(key => !Number.isSafeInteger(result[key]) || result[key] < 0)) return null;
    return result;
  }
  function latestEvidence(row, ...observations) {
    const matching = observations.filter(value => value?.source === 'diagnostic-sql-observation' && value.plan_id === row.plan_json.plan_id && value.plan_hash === row.plan_hash && value.summary?.scenario_id === row.plan_json.scenario_id && Number.isSafeInteger(value.summary.revision) && value.summary.revision >= 0 && Number.isFinite(Date.parse(value.observed_at)));
    return matching.reduce((latest, value) => !latest || value.summary.revision > latest.summary.revision || value.summary.revision === latest.summary.revision && Date.parse(value.observed_at) > Date.parse(latest.observed_at) ? value : latest, null);
  }
  function planStatus(row, executionJob) {
    if (row.state !== 'executing') return row.state;
    if (executionJob?.job_id !== row.execution_run_id) return 'Execution recorded / status unconfirmed';
    if (executionJob.state === 'uncertain') return 'Needs receipt inspection';
    if (['queued', 'running'].includes(executionJob.state)) return 'Execution in progress (last observed)';
    return 'Execution recorded / verification pending';
  }
  function submissionStatus(row, now = Date.now()) {
    const expires = Date.parse(row?.review_expires_at);
    if (row?.state !== 'draft') return { allowed: false, expires: null, label: 'Only a draft plan can be submitted.' };
    if (!Number.isFinite(expires)) return { allowed: false, expires: null, label: 'Submission deadline is unconfirmed. Refresh plans.' };
    return { allowed: expires > now, expires, label: expires > now ? 'Submit this proposal for independent review.' : 'Submission window expired. Start a new investigation for a fresh proposal.' };
  }
  function reviewStatus(row, now = Date.now()) {
    const expires = Date.parse(row?.review_expires_at);
    return { allowed: row?.state === 'submitted' && expires > now, expires,
      label: !Number.isFinite(expires) ? 'Review deadline is unconfirmed. Refresh plans.' : expires > now ? 'Waiting for independent approval. Switch to the Approver account.' : 'Review window expired. Start a new investigation for a fresh proposal.' };
  }
  function executionStatus(row, now = Date.now()) {
    const expires = Date.parse(row?.execution_before);
    return { allowed: row?.state === 'approved' && expires > now, expires,
      label: !Number.isFinite(expires) ? 'Execution deadline is unconfirmed. Refresh plans.' : expires > now ? 'Open Execute and confirm the exact approved plan.' : 'Execution preparation window expired. Start a new investigation and obtain a new review.' };
  }
  function scenarioStatus(record, now = Date.now()) {
    if (record?.source !== 'owned-scenario-status' || record.state !== 'created') return { allowed: false, label: record?.state === 'pending' ? 'Creation in progress. Do not submit another request.' : 'Scenario status unconfirmed. Check creation status; do not repeat creation.' };
    if (!Number.isFinite(Date.parse(record.expires_at)) || !Number.isFinite(Date.parse(record.planning_before))) return { allowed: false, label: 'Scenario deadline unconfirmed. Check creation status.' };
    if (Date.parse(record.expires_at) <= now) return { allowed: false, label: 'Scenario expired. Create new test data before analyzing.' };
    return { allowed: Date.parse(record.planning_before) > now, label: Date.parse(record.planning_before) > now ? 'Scenario confirmed. Ready to analyze.' : 'Insufficient scenario lifetime for Planning. Create new test data.' };
  }
  function stageState(row, index) {
    if (row?.state === 'completed') return 'Completed';
    const current = row ? row.state === 'rejected' ? 2 : planStage(row) : 0;
    return index < current ? 'Completed' : index > current ? 'Waiting' : row?.state === 'submitted' ? 'Waiting for Approver' : row?.state === 'rejected' ? 'Rejected' : 'Current';
  }
  function planStage(row) {
    if (['approved','executing'].includes(row?.state)) return 3;
    if (['verified','completed'].includes(row?.state)) return 4;
    return row?.state === 'submitted' ? 2 : 1;
  }
  function challengeNextAction(job, probe) {
    if (blocksNewWork(job)) return 'Invoice work is active or unconfirmed. Reconnect in Invoice workflow.';
    if (!probe) return 'Select Planning or Execution, then confirm the isolated challenge.';
    if (['queued','running'].includes(probe.state)) return 'Challenge '+probe.state+'. Observe this request; do not submit another.';
    if (challengeProof(probe).confirmed) return 'Challenge finished. Inspect the recorded OpenShell denial.';
    return 'Challenge outcome unconfirmed. Reconnect its recorded status; do not replay.';
  }
  function selectPlan(plans, selected, newInvestigation) {
    if (newInvestigation) return null;
    return plans.some(row => row.plan_json.plan_id === selected) ? selected : plans[0]?.plan_json.plan_id || null;
  }
  function blocksNewWork(job) { return Boolean(job && !['finished', 'uncertain'].includes(job.state)); }
  function runtimeStatus(snapshot, now = Date.now()) {
    if (snapshot?.source !== 'live-azure-query' || !Number.isFinite(Date.parse(snapshot.checkedAt)) || Date.parse(snapshot.checkedAt) > now + 5000) return { label: 'Runtime not checked', blocked: false };
    const expiry = Date.parse(snapshot.expiresAt);
    const managed = snapshot.availabilityMode === 'operator-managed' && snapshot.expiresAt === null;
    if (!managed && (!Number.isFinite(expiry) || expiry <= now + 1260000)) return { label: 'Workspace lease expired or too short. Renewal required.', blocked: true };
    if (now - Date.parse(snapshot.checkedAt) > 60000) return { label: 'Runtime observation is stale. Check again.', blocked: false };
    if (snapshot.readyForProbe !== true || snapshot.nat !== 'runtime-verified') return { label: 'Workspace or network is unavailable. Renewal or inspection required.', blocked: true };
    return { label: managed ? 'Operator-managed workspace; per-run admission still required.' : 'Host and network checks passed; sandbox admission still required.', blocked: false };
  }
  function nextAction(session, row, job, probe, draftMode, scenarioId, now = Date.now()) {
    if (session?.status !== 'authenticated') return 'Sign in with your assigned account.';
    if (session.persona === 'approver') return row?.state === 'submitted' ? reviewStatus(row, now).allowed ? 'Review this submitted plan and diagnostic snapshot, then approve or reject.' : reviewStatus(row, now).label : 'Select a submitted plan for independent review.';
    if (probe && !['finished','uncertain'].includes(probe.state)) return ['queued','running'].includes(probe.state) ? 'Sandbox challenge '+probe.state+'. Observe its result below.' : 'Challenge admission is unconfirmed. Reconnect the existing challenge.';
    if (blocksNewWork(job)) return ['queued','running'].includes(job.state) ? (job.kind === 'execution' ? 'Execution' : 'Planning')+' '+job.state+'. Observe this run; do not submit it again.' : 'Run admission is unconfirmed. Reconnect observations; do not replay.';
    if (job?.state === 'uncertain' && (draftMode && job.kind === 'planning' && job.target_id === scenarioId || row && completionBelongsToSelection(job,row,false,null))) return capacityMessage(job) || 'Run outcome unconfirmed. Reconnect observations; do not replay.';
    if (row?.state === 'executing') return 'Inspect the recorded execution receipts. Do not replay the run.';
    if (row?.state === 'approved') return executionStatus(row, now).label;
    if (row?.state === 'verified') return 'Open Verify, inspect the checks, then acknowledge completion.';
    if (row?.state === 'completed') return 'Investigation completed. Select another or start a new investigation.';
    if (row?.state === 'submitted') return reviewStatus(row, now).label;
    if (row?.state === 'draft') return submissionStatus(row, now).label;
    if (row?.state === 'rejected') return 'Plan rejected. Start a new investigation for a revised proposal.';
    return draftMode && scenarioId ? 'Scenario selected. Confirm its status before starting Planning.' : 'Create or select a scenario for the investigation.';
  }
  function restoreSelection(value) {
    const validId = value => typeof value === 'string' && /^[a-f0-9]{32}$/.test(value);
    const scenario = value?.draft;
    return {
      mode: value?.mode === 'saved' && validId(value?.planId) ? 'saved' : 'draft',
      planId: validId(value?.planId) ? value.planId : null,
      draft: { scenarioId: validId(scenario?.scenarioId) ? scenario.scenarioId : '',
        name: typeof scenario?.name === 'string' ? scenario.name.slice(0, 120) : '',
        variant: scenario?.variant === 'healthy' ? 'healthy' : 'lost-acknowledgement',
        jobId: validId(scenario?.jobId) ? scenario.jobId : null,
        creationRequested: scenario?.creationRequested === true,
        scenarioStatus: scenario?.scenarioStatus?.scenario_id === scenario?.scenarioId && scenario?.scenarioStatus?.source === 'owned-scenario-status' ? { ...scenario.scenarioStatus, state: scenario.scenarioStatus.state === 'created' ? 'created' : 'unconfirmed' } : null },
    };
  }
  function completionBelongsToSelection(job, row, draftMode, draftJobId) {
    if (draftMode) return Boolean(draftJobId && job.job_id === draftJobId);
    return Boolean(row && (job.kind === 'execution' ? executionMatches(job, row) : row.plan_json.planning_run_id === job.job_id));
  }
  function componentEvents(component, job, events) {
    if (!job) return [];
    if (['planning', 'execution'].includes(component)) return job.kind === component ? events.filter(event => event.source === 'agent-runtime') : [];
    return events.filter(event => eventView(event).component === component && event.source === 'workspace-controller');
  }
  function businessOutcome(row, before, observation) {
    const current = row && latestEvidence(row, observation);
    const summary = current?.summary;
    const baseline = before?.source === 'invoice-diagnostic-api' && (!row || before.scenario_id === row.plan_json.scenario_id && before.evidence_hash === row.plan_json.evidence_hash) ? before : null;
    const count = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
    const recorded = summary || baseline;
    const verified = Boolean(verifiedChecks(row));
    const executionRecorded = Boolean(row?.execution_run_id) || ['executing','verified','completed'].includes(row?.state);
    const expected = count(recorded?.expected_cents), reported = count(recorded?.reported_cents);
    const duplicates = count(recorded?.duplicate_invoices);
    return { verified, observedAt: current?.observed_at || baseline?.observed_at || null,
      source: current ? 'Database read' : baseline ? 'Planning diagnostic read' : null,
      finding: duplicates === null ? 'Invoice counts not observed' : duplicates === 0 ? 'No duplicate invoices in this reading' : duplicates + ' duplicate invoices found',
      repairStatus: verified ? 'Repair independently verified' : executionRecorded ? 'Execution recorded; verification not confirmed' : row ? 'Repair not started' : 'No repair recorded',
      orders: count(recorded?.orders), expected, difference: expected !== null && reported !== null ? reported - expected : null,
      compare: Boolean(executionRecorded && baseline && summary && Number.isSafeInteger(baseline.revision) && summary.revision > baseline.revision),
      metrics: [
        { label: 'Active invoices', before: count(baseline?.active_invoices), after: count(summary?.active_invoices) },
        { label: 'Duplicate invoices', before: count(baseline?.duplicate_invoices), after: count(summary?.duplicate_invoices) },
        { label: 'Recorded invoice total', before: count(baseline?.reported_cents), after: count(summary?.reported_cents), money: true },
      ] };
  }
  function changeReview(row, before) {
    if (!row) return [];
    const evidence = businessOutcome(row, before, null);
    return row.plan_json.steps.map(item => ({ ...item, title: operation(item.operation).title,
      scope: item.target === row.plan_json.scenario_id ? 'Selected scenario' : 'Target differs from scenario',
      change: item.operation === 'invoice.quarantine-duplicates.v1' ? (evidence.metrics[1].before ?? 'Unobserved count of')+' duplicate invoices to quarantine' : item.operation === 'invoice.rebuild-total.v1' ? 'Reported total recalculated from active invoices' : item.operation === 'invoice.activate-idempotent-import.v1' ? 'Importer changed to idempotent-v2' : 'Unrecognized operation',
      preserved: item.operation === 'invoice.quarantine-duplicates.v1' ? 'Original invoices retained; duplicate rows preserved in quarantine' : 'Original order keys and invoice records retained' }));
  }
  function traceDetail(job, events, row, stage, sequence) {
    const bounded = boundEvents(job, events, row, stage);
    const event = bounded.find(item => item.sequence === sequence);
    if (!event) return null;
    const metadata = eventView(event);
    const lifecycle = bounded.filter(item => item.source === 'workspace-controller' && item.sequence <= event.sequence);
    const issued = lifecycle.some(item => item.event_type === 'authority-issued');
    const revoked = lifecycle.some(item => item.event_type === 'authority-revoked');
    const step = row?.plan_json.steps.find(item => item.step_id === event.step_id);
    const receipt = event.source === 'workspace-controller' && event.event_type === 'step-receipt-recorded' && step &&
      event.target === row.plan_json.scenario_id && step.target === event.target && step.operation === event.operation &&
      event.revision === step.expected_revision + 1 && /^[a-f0-9]{64}$/.test(event.receipt_hash || '');
    const verification = event.source === 'workspace-controller' && event.event_type === 'verification-passed' && Boolean(verifiedChecks(row));
    return { event, title: metadata.title, source: metadata.source, component: metadata.component,
      authority: revoked ? 'Revocation recorded' : issued ? 'Run capability issuance recorded' : 'No issuance observation',
      boundary: ({verifier:'Independent SQL verifier',sandbox:'OpenShell sandbox',harness:'Workspace admission harness',broker:'Exact-plan SQL broker',diagnostics:'Read-only diagnostic gateway'})[metadata.component] || (job.kind === 'execution' ? 'Exact-plan SQL broker' : 'Read-only diagnostic gateway'),
      proof: receipt ? 'SQL step receipt recorded' : verification ? 'Five independent checks passed' : 'No independent receipt linked to this event',
      receipt: Boolean(receipt), verified: verification,
      correlation: receipt ? 'Plan step, target, operation and revision match' : 'Run context only; no request-to-receipt correlation ID',
      planHash: row?.plan_hash || null, runId: job.job_id };
  }
  function challengeProof(result) {
    const record = result?.result;
    const kind = result?.kind;
    const control = kind === 'planning' ? 'invoice_summary' : 'execute_step';
    const forbidden = kind === 'planning' ? 'execute_step' : 'invoice_summary';
    const requests = record?.requests || [];
    const confirmed = ['planning','execution'].includes(kind) && result.state === 'finished' && record?.outcome === 'denied' &&
      record.run_id === result.challenge_id && record.actor === 'controlled-probe' && record.agent_requested === false &&
      record.database_capability_issued === false && record.sandbox_stopped === true && Number.isInteger(record.uid) && record.uid > 0 &&
      requests.length === 2 && requests[0].tool === control && requests[0].status === 401 && requests[1].tool === forbidden && requests[1].status === 403 &&
      record.denial_evidence?.some(line => line.includes('OCSF') && line.includes('DENIED') && line.includes(forbidden));
    return { confirmed: Boolean(confirmed), control, forbidden, requests,
      title: confirmed ? 'Forbidden route denied by OpenShell' : result && ['queued','running'].includes(result.state) ? 'Challenge '+result.state : result ? 'Challenge outcome unconfirmed' : 'No challenge run',
      source: confirmed ? 'OpenShell enforcement receipt' : 'No confirmed enforcement receipt' };
  }
  function sandboxTest(job, events = [], pending = false) {
    const binding = events.find(event => event.source === 'workspace-controller' && event.event_type === 'sandbox-bound' && event.kind === job?.kind);
    const proof = job?.sandbox_test || job?.result?.sandbox_test;
    const control = job?.kind === 'execution' ? 'execute_step' : 'invoice_summary';
    const forbidden = job?.kind === 'execution' ? 'invoice_summary' : 'execute_step';
    const requests = proof?.requests || [];
    const bound = Boolean(job?.job_id && ['planning','execution'].includes(job.kind) && /^[a-f0-9]{32}$/.test(binding?.sandbox_id || '') && /^[a-f0-9]{64}$/.test(binding?.policy_hash || ''));
    const matching = Boolean(bound && proof?.scope === 'same-agent-sandbox' && proof.run_id === job.job_id && proof.sandbox_id === binding.sandbox_id && proof.kind === job.kind && proof.policy_hash === binding.policy_hash);
    const confirmed = matching && proof.outcome === 'denied' && proof.enforced_by === 'OpenShell' && proof.actor === 'controlled-probe' && proof.agent_requested === false &&
      proof.probe_capability_issued === false && proof.agent_authority_revoked === true && proof.sandbox_stopped === true && Number.isInteger(proof.uid) && proof.uid > 0 &&
      requests.length === 2 && requests[0].tool === control && requests[0].status === 401 && requests[1].tool === forbidden && requests[1].status === 403 &&
      proof.denial_evidence?.some(line => line.includes('OCSF') && line.includes('DENIED') && line.includes(forbidden));
    const closed = job?.sandbox_test_closed === true || events.some(event => event.source === 'workspace-controller' && ['authority-revoked','sandbox-stopped'].includes(event.event_type));
    const requested = job?.sandbox_test_requested === true;
    const available = bound && job.state === 'running' && !closed && !requested && !pending;
    const title = proof ? confirmed ? 'Forbidden request denied in this agent sandbox' : 'Same-sandbox test outcome unconfirmed' : requested ?
      ['finished','uncertain'].includes(job.state) ? 'Test requested; no confirmed receipt' : closed ? 'Same-sandbox test and cleanup in progress' : 'Test queued for this sandbox' : pending ? 'Test request unconfirmed; reconnect this run' :
      !job ? 'No agent run selected' : ['finished','uncertain'].includes(job.state) || closed ? 'Test window closed; sandbox will not be restarted' : bound ? 'This agent sandbox is available to test' : 'Waiting for this run\'s sandbox binding';
    return { available, confirmed: Boolean(confirmed), matching, requested, title, control, forbidden, sandboxId: binding?.sandbox_id || null,
      runId: job?.job_id || null, proof: matching ? proof : null };
  }
  return { chapters, tools, checks, operation, eventView, observation, permissions, boundEvents, executionMatches, verifiedChecks, diagnostics, latestEvidence, planStatus, submissionStatus, reviewStatus, executionStatus, scenarioStatus, stageState, planStage, challengeNextAction, selectPlan, blocksNewWork, runtimeStatus, nextAction, restoreSelection, completionBelongsToSelection, componentEvents, businessOutcome, changeReview, traceDetail, challengeProof, sandboxTest };
});