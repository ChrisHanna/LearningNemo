const test = require('node:test');
const assert = require('node:assert/strict');
const view = require('../src/task_agent/console/static/invoice-view.js');

const run = 'a'.repeat(32), sandbox = 'b'.repeat(32), policy = 'c'.repeat(64);
const job = {job_id:run,kind:'planning',state:'running',sandbox_test_closed:false,sandbox_test_requested:false};
const events = [{source:'workspace-controller',event_type:'sandbox-bound',kind:'planning',sandbox_id:sandbox,policy_hash:policy}];
const proof = {scope:'same-agent-sandbox',kind:'planning',run_id:run,sandbox_id:sandbox,policy_hash:policy,outcome:'denied',enforced_by:'OpenShell',actor:'controlled-probe',agent_requested:false,agent_authority_revoked:true,probe_capability_issued:false,sandbox_stopped:true,uid:998,requests:[{tool:'invoice_summary',status:401},{tool:'execute_step',status:403}],denial_evidence:['OCSF DENIED execute_step']};

test('only a bound active agent run has a same-sandbox test action', () => {
  assert.equal(view.sandboxTest(job,events).available,true);
  for(const changed of [{state:'finished'},{state:'uncertain'},{sandbox_test_requested:true},{sandbox_test_closed:true}]) assert.equal(view.sandboxTest({...job,...changed},events).available,false);
  assert.equal(view.sandboxTest(job,events,true).available,false);
  assert.equal(view.sandboxTest(job,[]).available,false);
  assert.equal(view.sandboxTest(job,[{...events[0],source:'agent-runtime'}]).available,false);
});

test('same-sandbox proof requires exact binding and actual enforcing-layer evidence', () => {
  assert.equal(view.sandboxTest({...job,state:'finished',sandbox_test:proof},events).confirmed,true);
  for(const changed of [{sandbox_id:'f'.repeat(32)},{run_id:'other'},{kind:'execution'},{policy_hash:'f'.repeat(64)},
    {agent_authority_revoked:false},{probe_capability_issued:true},{sandbox_stopped:false},{denial_evidence:[]},{uid:0},
    {requests:[{tool:'invoice_summary',status:401},{tool:'execute_step',status:0}]}]) {
    assert.equal(view.sandboxTest({...job,sandbox_test:{...proof,...changed}},events).confirmed,false);
  }
});

test('completed runs and uncertain requests are never offered a restart', () => {
  assert.match(view.sandboxTest({...job,state:'finished'},events).title,/will not be restarted/);
  assert.match(view.sandboxTest(job,events,true).title,/unconfirmed.*reconnect/);
  assert.match(view.sandboxTest({...job,sandbox_test_requested:true},events).title,/queued/);
  assert.equal(view.eventView({source:'agent-runtime',event_type:'sandbox-test-recorded'}).tone,'warning');
});