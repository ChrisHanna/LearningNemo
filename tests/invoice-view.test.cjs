const test = require('node:test');
const assert = require('node:assert/strict');
const view = require('../src/task_agent/console/static/invoice-view.js');

test('disconnected and uncertain jobs never animate as live', () => {
  assert.equal(view.observation({ state: 'running' }, [], false, Date.now()).live, false);
  assert.equal(view.observation({ state: 'uncertain' }, [], true, Date.now()).live, false);
  assert.equal(view.observation({ state: 'running' }, [], true, 100, 20000).state, 'stale');
  assert.equal(view.observation({ state: 'running' }, [], true, 100, 101).live, true);
});
test('agent completion is not database verification or sandbox cleanup', () => {
  assert.match(view.eventView({ event_type: 'agent-finished', source: 'agent-runtime' }).title, /not independent verification/);
  assert.equal(view.verifiedChecks({ state: 'executing', verification_json: {} }), null);
  assert.equal(view.verifiedChecks({ state: 'verified', verification_json: { no_duplicates: true } }), null);
  assert.match(view.eventView({ event_type: 'verification-passed', source: 'agent-runtime' }).title, /Unverified claim/);
  assert.equal(view.eventView({ event_type: 'sandbox-stopped', source: 'agent-runtime' }).component, 'agent');
});
test('roles, scopes, and sign-in are separate gates', () => {
  const session = { status: 'authenticated', persona: 'operator', grantedRoles: ['Task.Reader', 'Task.Operator'], grantedScopes: ['agent.invoke', 'tasks.read'] };
  assert.equal(view.permissions(session).investigate, true);
  assert.equal(view.permissions(session).execute, false);
  assert.equal(view.permissions(session).approve, false);
  assert.equal(view.permissions({ ...session, grantedRoles: [...session.grantedRoles, 'Task.Approver'] }).investigate, false);
});
test('a selected plan never borrows another run activity', () => {
  const row = { plan_json: { planning_run_id: 'planning' }, execution_run_id: 'execution' };
  const events = [{ event_type: 'agent-started' }];
  assert.deepEqual(view.boundEvents({ job_id: 'other' }, events, row, 0), []);
  assert.deepEqual(view.boundEvents({ job_id: 'planning' }, events, row, 3), []);
  assert.deepEqual(view.boundEvents({ job_id: 'execution', kind: 'execution' }, events, row, 3), events);
});

test('business metrics require diagnostic provenance and selected-plan binding', () => {
  const row = { plan_json: { planning_run_id: 'run', scenario_id: 'scenario', evidence_hash: 'hash' } };
  const job = { job_id: 'run', kind: 'planning', state: 'finished', result: { diagnostics: { source: 'invoice-diagnostic-api', scenario_id: 'scenario', evidence_hash: 'hash', orders: 12, active_invoices: 24, duplicate_invoices: 12 } } };
  assert.equal(view.diagnostics(job, row).orders, 12);
  assert.equal(view.diagnostics({ ...job, job_id: 'another' }, row), null);
  assert.equal(view.diagnostics({ ...job, kind: 'execution' }, row), null);
});

test('new investigation remains unselected when old plans refresh', () => {
  const plans = [{ plan_json: { plan_id: 'old' }, state: 'executing' }];
  assert.equal(view.selectPlan(plans, null, true), null);
  assert.equal(view.selectPlan(plans, 'old', false), 'old');
});

test('executing plan is not proof of a running agent', () => {
  const row = { state: 'executing', execution_run_id: 'run' };
  assert.equal(view.planStatus(row, { job_id: 'run', state: 'uncertain' }), 'Needs receipt inspection');
  assert.equal(view.planStatus(row, { job_id: 'other', state: 'running' }), 'Execution recorded / status unconfirmed');
  assert.equal(view.planStatus(row, { job_id: 'run', state: 'running' }), 'Execution in progress (last observed)');
});

test('browsing new scenarios does not bypass active or unconfirmed admission', () => {
  for (const state of [undefined, 'queued', 'running', 'admission-unconfirmed']) assert.equal(view.blocksNewWork({ state }), true);
  for (const state of ['finished', 'uncertain']) assert.equal(view.blocksNewWork({ state }), false);
  assert.equal(view.blocksNewWork(null), false);
});

test('draft scenario survives saved-investigation selection and page restoration', () => {
  const draft = { scenarioId: 'a'.repeat(32), name: 'Healthy import', variant: 'healthy', jobId: 'b'.repeat(32), scenarioStatus: null, creationRequested: false };
  const saved = view.restoreSelection({ mode: 'saved', planId: 'c'.repeat(32), draft });
  assert.equal(saved.mode, 'saved');
  assert.deepEqual(saved.draft, draft);
  assert.deepEqual(view.restoreSelection({ ...saved, mode: 'draft' }).draft, draft);
  assert.equal(view.restoreSelection(null).mode, 'draft');
  assert.equal(view.restoreSelection({ draft: { scenarioId: 'invalid' } }).draft.scenarioId, '');
});

test('completion of a different job cannot choose a saved investigation or a new draft', () => {
  const row = { plan_json: { planning_run_id: 'planning' }, execution_run_id: 'execution' };
  assert.equal(view.completionBelongsToSelection({ job_id: 'old', kind: 'planning' }, row, false, null), false);
  assert.equal(view.completionBelongsToSelection({ job_id: 'old', kind: 'execution' }, null, true, null), false);
  assert.equal(view.completionBelongsToSelection({ job_id: 'execution', kind: 'execution' }, row, false, null), true);
  assert.equal(view.completionBelongsToSelection({ job_id: 'new', kind: 'planning' }, null, true, 'new'), true);
});

test('component evidence never borrows the other agent or agent-reported harness claims', () => {
  const events = [{ source: 'agent-runtime', event_type: 'tool-returned' }, { source: 'workspace-controller', event_type: 'sandbox-bound' }];
  assert.deepEqual(view.componentEvents('execution', { kind: 'planning' }, events), []);
  assert.deepEqual(view.componentEvents('planning', { kind: 'planning' }, events), [events[0]]);
  assert.deepEqual(view.componentEvents('sandbox', { kind: 'planning' }, events), [events[1]]);
});

test('pending execution is visible and completes before the approved plan row refreshes', () => {
  const row = { state: 'approved', plan_json: { plan_id: 'plan' }, plan_hash: 'hash', execution_run_id: null };
  const job = { job_id: 'run', kind: 'execution', target_id: 'plan', plan_hash: 'hash' };
  const events = [{ event_type: 'agent-started' }];
  assert.equal(view.executionMatches(job, row), true);
  assert.deepEqual(view.boundEvents(job, events, row, 3), events);
  assert.equal(view.completionBelongsToSelection(job, row, false, null), true);
  assert.equal(view.executionMatches({ ...job, plan_hash: undefined }, row), true);
  assert.equal(view.executionMatches({ ...job, target_id: 'other' }, row), false);
  assert.equal(view.executionMatches({ ...job, plan_hash: 'other' }, row), false);
  assert.equal(view.executionMatches({ ...job, kind: 'planning' }, row), false);
  assert.equal(view.executionMatches(job, { ...row, execution_run_id: 'other' }), false);
  assert.equal(view.executionMatches(job, { ...row, state: 'draft' }), false);
  assert.equal(view.completionBelongsToSelection(job, row, true, null), false);
});

test('newer SQL evidence supersedes cached pre-repair data without losing binding', () => {
  const row = { plan_json: { plan_id: 'plan', scenario_id: 'scenario' }, plan_hash: 'hash' };
  const before = { source: 'diagnostic-sql-observation', plan_id: 'plan', plan_hash: 'hash', summary: { scenario_id: 'scenario', revision: 1, duplicate_invoices: 12 }, observed_at: '2026-09-16T23:00:00Z' };
  const after = { ...before, summary: { ...before.summary, revision: 4, duplicate_invoices: 0 }, observed_at: '2026-09-16T23:01:00Z' };
  const inspected = { ...after, observed_at: '2026-09-16T23:02:00Z', receipts: [{ step_id: 1 }] };
  assert.equal(view.latestEvidence(row, before, after), after);
  assert.equal(view.latestEvidence(row, inspected, after), inspected);
  assert.equal(view.latestEvidence(row, { ...before, observed_at: inspected.observed_at }, after), after);
  for (const invalid of [{ ...after, plan_id: 'other' }, { ...after, plan_hash: 'other' }, { ...after, source: 'agent-runtime' }, { ...after, summary: { ...after.summary, scenario_id: 'other' } }, { ...after, observed_at: 'invalid' }, { ...after, summary: { ...after.summary, revision: undefined } }]) {
    assert.equal(view.latestEvidence(row, before, invalid), before);
  }
  assert.equal(view.latestEvidence(row, null, undefined), null);
});

test('runtime observations distinguish unchecked, expired, stale and network-blocked', () => {
  const now = Date.parse('2026-09-17T16:00:00Z');
  const snapshot = { source: 'live-azure-query', checkedAt: new Date(now).toISOString(), expiresAt: new Date(now+3600000).toISOString(), readyForProbe: true, nat: 'runtime-verified' };
  assert.equal(view.runtimeStatus(null, now).label, 'Runtime not checked');
  assert.equal(view.runtimeStatus(snapshot, now).blocked, false);
  assert.equal(view.runtimeStatus({ ...snapshot, expiresAt: new Date(now+1260000).toISOString() }, now).blocked, true);
  assert.equal(view.runtimeStatus({ ...snapshot, nat: 'absent' }, now).blocked, true);
  assert.match(view.runtimeStatus(snapshot, now+61000).label, /stale/);
  assert.match(view.runtimeStatus(snapshot, now+3600000).label, /expired/);
  assert.equal(view.runtimeStatus({ ...snapshot, source: 'agent-runtime' }, now).label, 'Runtime not checked');
});

test('next action distinguishes draft, approval expiry and unconfirmed challenges', () => {
  const session = { status: 'authenticated', persona: 'operator' };
  assert.match(view.nextAction(session, null, null, { state: 'admission-unconfirmed' }, true, ''), /Challenge admission is unconfirmed/);
  assert.match(view.nextAction(session, null, { state: 'running', kind: 'planning' }, null, true, 'scenario'), /Planning running/);
  assert.match(view.nextAction(session, { state: 'submitted', review_expires_at: new Date(Date.now()+60000).toISOString() }, null, null, false, ''), /Waiting for independent approval/);
  assert.match(view.nextAction(session, { state: 'approved', execution_before: '2020-01-01T00:00:00Z' }, null, null, false, ''), /window expired/);
  assert.match(view.nextAction(session, null, null, null, true, 'scenario'), /Scenario selected/);
});

test('submission availability uses the persisted SQL deadline', () => {
  const now = Date.parse('2026-09-17T17:00:00Z');
  const row = { state: 'draft', review_expires_at: new Date(now+29*60000).toISOString(), plan_json: { created_at: new Date(now-60000).toISOString() } };
  assert.equal(view.submissionStatus(row, now).allowed, true);
  assert.equal(view.submissionStatus(row, now+29*60000).allowed, false);
  assert.match(view.submissionStatus(row, now+30*60000).label, /expired/);
  assert.equal(view.submissionStatus({ ...row, state: 'submitted' }, now).allowed, false);
  assert.equal(view.submissionStatus({ ...row, review_expires_at: 'invalid' }, now).allowed, false);
});

test('server deadlines gate scenario, review and execution at the exact boundary', () => {
  const now = Date.now(), deadline = new Date(now).toISOString();
  assert.equal(view.reviewStatus({ state: 'submitted', review_expires_at: deadline }, now).allowed, false);
  assert.equal(view.executionStatus({ state: 'approved', execution_before: deadline, approval_expires_at: new Date(now+120000).toISOString() }, now).allowed, false);
  assert.equal(view.executionStatus({ state: 'approved' }, now).allowed, false);
  const record = { source: 'owned-scenario-status', state: 'created', expires_at: new Date(now+600000).toISOString(), planning_before: deadline };
  assert.equal(view.scenarioStatus(record, now-1).allowed, true);
  assert.equal(view.scenarioStatus(record, now).allowed, false);
  assert.match(view.scenarioStatus({ ...record, expires_at: deadline }, now).label, /expired/);
});

test('reload preserves uncertain creation ID without advertising readiness', () => {
  const scenarioId = 'a'.repeat(32);
  const draft = view.restoreSelection({ draft: { scenarioId, scenarioStatus: { scenario_id: scenarioId, source: 'owned-scenario-status', state: 'pending' } } }).draft;
  assert.equal(draft.scenarioId, scenarioId);
  assert.equal(draft.scenarioStatus.state, 'unconfirmed');
  assert.equal(view.scenarioStatus(draft.scenarioStatus).allowed, false);
});

test('stage state follows workflow progress, not the viewed tab', () => {
  assert.equal(view.stageState({ state: 'submitted' }, 0), 'Completed');
  assert.equal(view.stageState({ state: 'submitted' }, 2), 'Waiting for Approver');
  assert.equal(view.stageState({ state: 'submitted' }, 3), 'Waiting');
  assert.equal(view.stageState({ state: 'completed' }, 4), 'Completed');
});

test('saved plans open at the actionable stage and challenge guidance stays separate', () => {
  for (const [state, stage] of [['draft',1],['submitted',2],['approved',3],['executing',3],['verified',4],['completed',4]]) assert.equal(view.planStage({ state }), stage);
  assert.match(view.challengeNextAction(null,null), /isolated challenge/);
  assert.match(view.challengeNextAction(null,{state:'finished',result:{outcome:'denied'}}), /unconfirmed/);
  assert.match(view.challengeNextAction({state:'running'},{state:'finished'}), /Invoice work is active/);
  assert.match(view.challengeNextAction(null,{state:'uncertain'}), /do not replay/);
});

test('managed runtime has no deadline but still needs fresh network and admission evidence', () => {
  const now=Date.now(), snapshot={source:'live-azure-query',checkedAt:new Date(now).toISOString(),expiresAt:null,availabilityMode:'operator-managed',nat:'runtime-verified',readyForProbe:true};
  assert.match(view.runtimeStatus(snapshot,now).label,/Operator-managed/);
  assert.equal(view.runtimeStatus({...snapshot,readyForProbe:false},now).blocked,true);
  assert.equal(view.runtimeStatus({...snapshot,nat:'attached'},now).blocked,true);
  assert.equal(view.runtimeStatus({...snapshot,availabilityMode:'leased'},now).blocked,true);
  assert.match(view.runtimeStatus(snapshot,now+61000).label,/stale/);
});

test('business outcomes never substitute desired state for missing observations', () => {
  const row={state:'draft',plan_hash:'hash',plan_json:{plan_id:'plan',scenario_id:'scenario',evidence_hash:'evidence',steps:[]}};
  const before={source:'invoice-diagnostic-api',scenario_id:'scenario',evidence_hash:'evidence',active_invoices:24,duplicate_invoices:12,reported_cents:295600};
  const result=view.businessOutcome(row,before,null);
  assert.equal(result.metrics[1].before,12);
  assert.equal(result.metrics[1].after,null);
  assert.equal(result.verified,false);
  assert.equal(view.businessOutcome(row,{...before,scenario_id:'other'},null).metrics[1].before,null);
});

test('trace requires bound run and exact independent receipt, never temporal inference', () => {
  const row={state:'executing',plan_hash:'hash',execution_run_id:'run',plan_json:{scenario_id:'scenario',steps:[{step_id:1,target:'scenario',operation:'invoice.rebuild-total.v1',expected_revision:2}]}};
  const job={job_id:'run',kind:'execution'};
  const events=[{sequence:1,source:'workspace-controller',event_type:'authority-issued'},
    {sequence:2,source:'agent-runtime',event_type:'tool-returned',tool:'execute_step'},
    {sequence:3,source:'workspace-controller',event_type:'step-receipt-recorded',step_id:1,target:'scenario',operation:'invoice.rebuild-total.v1',revision:3,receipt_hash:'a'.repeat(64)}];
  assert.equal(view.traceDetail(job,events,row,3,2).receipt,false);
  assert.match(view.traceDetail(job,events,row,3,2).correlation,/no request-to-receipt/);
  assert.equal(view.traceDetail(job,events,row,3,3).receipt,true);
  assert.equal(view.traceDetail({...job,job_id:'other'},events,row,3,3),null);
  assert.equal(view.traceDetail(job,[{...events[2],source:'agent-runtime'}],row,3,3).receipt,false);
  assert.equal(view.traceDetail(job,[{...events[2],target:'other'}],row,3,3).receipt,false);
  const verified={...row,state:'verified',verification_json:Object.fromEntries(Object.keys(view.checks).map(key=>[key,true]))};
  const verification=view.traceDetail(job,[{sequence:4,source:'workspace-controller',event_type:'verification-passed'}],verified,4,4);
  assert.equal(verification.boundary,'Independent SQL verifier');
  assert.equal(verification.verified,true);
});

test('challenge verdict requires control, denial, identity and cleanup evidence', () => {
  const result={kind:'planning',challenge_id:'run',state:'finished',result:{run_id:'run',outcome:'denied',actor:'controlled-probe',agent_requested:false,database_capability_issued:false,sandbox_stopped:true,uid:998,requests:[{tool:'invoice_summary',status:401},{tool:'execute_step',status:403}],denial_evidence:['OCSF HTTP:POST DENIED execute_step']}};
  assert.equal(view.challengeProof(result).confirmed,true);
  for(const changed of [{sandbox_stopped:false},{run_id:'other'},{denial_evidence:[]},{uid:0},{requests:[{tool:'invoice_summary',status:0},{tool:'execute_step',status:403}]}]) assert.equal(view.challengeProof({...result,result:{...result.result,...changed}}).confirmed,false);
});

test('new ninety-minute review deadlines do not revive existing thirty-minute plans', () => {
  const created = Date.parse('2026-09-19T00:00:00Z');
  const oldPlan = { state: 'submitted', review_expires_at: new Date(created+30*60000).toISOString(), plan_json: { created_at: new Date(created).toISOString() } };
  const newPlan = { ...oldPlan, review_expires_at: new Date(created+90*60000).toISOString() };
  assert.equal(view.reviewStatus(oldPlan, created+45*60000).allowed, false);
  assert.equal(view.reviewStatus(newPlan, created+89*60000).allowed, true);
  assert.equal(view.reviewStatus(newPlan, created+90*60000).allowed, false);
  assert.equal(view.submissionStatus({ ...newPlan, state: 'draft' }, created+89*60000).allowed, true);
  assert.equal(view.submissionStatus({ ...oldPlan, state: 'draft' }, created+45*60000).allowed, false);
});