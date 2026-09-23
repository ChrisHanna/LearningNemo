const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const view = require('../src/task_agent/console/static/invoice-view.js');

function workflow(api, initial = {}) {
  const element = () => ({ after() {}, addEventListener() {} });
  const window = { invoiceView: view, addEventListener() {}, confirm() { return true; } };
  const storage = new Map(initial.storage || []);
  const context = { window, document: { createElement: element, getElementById: element }, api,
    state: { session: { status: 'authenticated', persona: 'operator', authMode: initial.authMode } },
    sessionStorage: { setItem: (key, value) => storage.set(key, value), getItem: key => storage.get(key), removeItem:key=>storage.delete(key) },
    crypto: require('node:crypto').webcrypto, setInterval() {}, setTimeout() {}, clearTimeout() {}, console, initial, storage };
  const source = fs.readFileSync(require.resolve('../src/task_agent/console/static/invoice-workflow.js'), 'utf8');
  vm.runInNewContext(source.replace(/\}\)\(\);\s*$/, `
    render = () => {};
    identity = 'operator:test';
    plans = initial.plans || []; selected = initial.selected || null; job = initial.job || null; step = initial.step || 0;
    newInvestigation = initial.newInvestigation || false; newJobId = initial.newJobId || null;
    state.session.grantedRoles = ['Task.Reader','Task.Operator']; state.session.grantedScopes = ['agent.invoke','tasks.read','tasks.execute'];
    if (initial.draft) draft = view.restoreSelection({draft: initial.draft}).draft;
    events = initial.events || [];
    demoSession = initial.demoSession || {source:'invoice-demo-session',session_id:'f'.repeat(32),state:'active',expires_at:new Date(Date.now()+14400000).toISOString(),can_end:true};
    demoPending = JSON.parse(storage.get('invoice-demo-pending:operator:test') || 'null');
    sandboxInventory = initial.sandboxInventory || null;
    window.fixture = { refreshPlans, planAction, reconnect, poll, start, loadHistory, history, createScenario, checkScenario, beginInvestigation, requestSandboxTest, refreshSandboxes, deleteSandbox, refreshDemo, demoAction, storage,
      snapshot: () => ({ busy, plansLoading, plansError, message, plans, selected, historyError, draft, scenarioError, step, newInvestigation, job, demoPending, demoError }) };
  })();`), context);
  return window.fixture;
}

const planId = 'a'.repeat(32), runId = 'b'.repeat(32), otherRun = 'c'.repeat(32);
const row = () => ({ state: 'draft', plan_hash: 'hash', review_expires_at: new Date(Date.now()+1800000).toISOString(), plan_json: { plan_id: planId, planning_run_id: runId, created_at: new Date().toISOString() } });

test('history loading and failure do not take the command lock or overwrite action status', async () => {
  let reject;
  const fixture = workflow(() => new Promise((_resolve, failure) => { reject = failure; }));
  const pending = fixture.refreshPlans();
  assert.equal(fixture.snapshot().plansLoading, true);
  assert.equal(fixture.snapshot().busy, false);
  reject(new Error('cold SQL'));
  await pending;
  assert.equal(fixture.snapshot().plansLoading, false);
  assert.match(fixture.snapshot().plansError, /cold SQL/);
  assert.equal(fixture.snapshot().message, '');
});

test('confirmed submission survives a failed list refresh without replay', async () => {
  const calls = [];
  const fixture = workflow(async (path, options) => {
    calls.push([path, options?.method]);
    if (options?.method === 'POST') return { plan_id: planId, state: 'submitted' };
    throw new Error('503');
  }, { plans: [row()], selected: planId });
  await fixture.planAction('submit');
  assert.equal(fixture.snapshot().plans[0].state, 'submitted');
  assert.match(fixture.snapshot().message, /Plan submitted/);
  assert.match(fixture.snapshot().plansError, /503/);
  await fixture.planAction('submit');
  assert.equal(calls.filter(call => call[1] === 'POST').length, 1);
});

test('confirmed approval survives a failed list refresh', async () => {
  const fixture = workflow(async (_path, options) => {
    if (options?.method === 'POST') return { plan_id: planId, decision: 'approve' };
    throw new Error('503');
  }, { plans: [{ ...row(), state: 'submitted', diagnostics: {} }], selected: planId });
  await fixture.planAction('decision', 'approve');
  assert.equal(fixture.snapshot().plans[0].state, 'approved');
  assert.match(fixture.snapshot().message, /Plan approved/);
});

test('an overlapping history read cannot detach the acknowledged action from the displayed row', async () => {
  let acknowledge, reads = 0;
  const fixture = workflow(async (path, options) => {
    if (options?.method === 'POST') return new Promise(resolve => { acknowledge = resolve; });
    if (path === '/api/invoices/plans' && ++reads === 1) return { source: 'azure-sql', plans: [row()] };
    throw new Error('refresh unavailable');
  }, { plans: [row()], selected: planId });
  const action = fixture.planAction('submit');
  await fixture.refreshPlans();
  acknowledge({ plan_id: planId, state: 'submitted' });
  await action;
  assert.equal(fixture.snapshot().plans[0].state, 'submitted');
  assert.match(fixture.snapshot().message, /Plan submitted/);
});

test('reconnect refreshes selected run A and revalidates its cache, not global run B', async () => {
  const calls = [];
  const fixture = workflow(async path => {
    calls.push(path);
    return path.includes('/events') ? { job_id: runId, events: [{ sequence: 2 }] } : { job_id: runId, state: 'finished' };
  }, { plans: [row()], selected: planId, job: { job_id: otherRun } });
  fixture.history.set(runId, { job: { job_id: runId, state: 'running' }, events: [] });
  await fixture.reconnect();
  assert.equal(calls.length, 2);
  assert.ok(calls.every(path => path.includes(runId) && !path.includes(otherRun)));
  assert.equal(fixture.history.get(runId).job.state, 'finished');
  assert.equal(fixture.history.get(runId).events[0].sequence, 2);
});

test('creation ID is saved before POST and reload reconciles the same ID with GET only', async () => {
  let savedId;
  const fixture = workflow(async (_path, options) => {
    const saved = JSON.parse(fixture.storage.get('invoice-selection:operator:test'));
    savedId = saved.draft.scenarioId;
    assert.equal(savedId, JSON.parse(options.body).scenario_id);
    assert.equal(saved.draft.scenarioStatus.state, 'pending');
    throw new Error('response lost');
  });
  await fixture.createScenario('healthy');
  assert.equal(fixture.snapshot().draft.scenarioStatus.state, 'unconfirmed');
  fixture.beginInvestigation();
  assert.equal(fixture.snapshot().draft.scenarioId, savedId);
  const calls = [];
  const restored = workflow(async (path, options) => {
    calls.push([path, options]);
    return { source: 'owned-scenario-status', scenario_id: savedId, state: 'created', variant: 'healthy', expires_at: new Date(Date.now()+7200000).toISOString(), planning_before: new Date(Date.now()+6600000).toISOString() };
  }, { draft: fixture.snapshot().draft });
  await restored.checkScenario();
  await restored.createScenario('healthy');
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/api/invoices/scenarios/' + savedId);
  assert.equal(calls[0][1], undefined);
  assert.equal(restored.snapshot().draft.scenarioStatus.state, 'created');
});

test('same-sandbox action persists its run before one POST and never starts another job', async () => {
  const sandboxId = 'd'.repeat(32), calls = [];
  const fixture = workflow(async (path, options) => {
    calls.push([path,options]);
    assert.equal(JSON.parse(fixture.storage.get('invoice-sandbox-tests:operator:test'))[0],runId);
    throw new Error('response lost');
  }, { plans:[row()],selected:planId,job:{job_id:runId,kind:'planning',state:'running'},
    events:[{source:'workspace-controller',event_type:'sandbox-bound',kind:'planning',sandbox_id:sandboxId,policy_hash:'e'.repeat(64)}] });
  await fixture.requestSandboxTest(runId,sandboxId);
  await fixture.requestSandboxTest(runId,sandboxId);
  assert.equal(calls.length,1);
  assert.equal(calls[0][0],'/api/invoices/jobs/'+runId+'/sandbox-test');
  assert.deepEqual(JSON.parse(calls[0][1].body),{sandbox_id:sandboxId});
});

test('Planning stays on Analyze while running then opens its recorded proposal without another POST', async () => {
  const calls=[];
  let finished=false;
  const fixture=workflow(async (path,options)=>{
    calls.push([path,options?.method]);
    if(path==='/api/invoices/plans')return{source:'azure-sql',plans:[row()]};
    if(path.includes('/events?'))return{job_id:runId,events:[]};
    return{job_id:runId,kind:'planning',state:finished?'finished':'running',result:finished?{plan:row().plan_json}:null};
  },{job:{job_id:runId,kind:'planning',state:'queued'},newInvestigation:true,newJobId:runId,draft:{scenarioId:'d'.repeat(32),jobId:runId}});
  await fixture.poll();
  assert.equal(fixture.snapshot().step,0);
  assert.equal(fixture.snapshot().selected,null);
  finished=true;
  await fixture.poll();
  assert.equal(fixture.snapshot().step,1);
  assert.equal(fixture.snapshot().selected,planId);
  assert.equal(fixture.snapshot().newInvestigation,false);
  assert.equal(fixture.snapshot().draft.jobId,null);
  assert.ok(calls.every(([,method])=>!method||method==='GET'));
});

test('an uncertain Planning attempt cannot be submitted again for the same scenario', async () => {
  const scenarioId='d'.repeat(32);
  const fixture=workflow(async()=>assert.fail('must not submit another run'),{
    job:{job_id:runId,kind:'planning',target_id:scenarioId,state:'uncertain'},newInvestigation:true,newJobId:runId,
    draft:{scenarioId,jobId:runId,scenarioStatus:{source:'owned-scenario-status',scenario_id:scenarioId,state:'created',expires_at:new Date(Date.now()+7200000).toISOString(),planning_before:new Date(Date.now()+6600000).toISOString()}}});
  await fixture.start('planning',scenarioId);
  assert.match(fixture.snapshot().message,/unconfirmed.*will not be replayed/);
  assert.equal(fixture.snapshot().job.job_id,runId);
});

test('local demo clears a remembered run only when the backend confirms it is absent', async () => {
  const missing = Object.assign(new Error('Local job not found'), { status: 404 });
  const local = workflow(async () => { throw missing; }, {
    authMode: 'local-demo', job: { job_id: runId, kind: 'execution', state: 'admission-unconfirmed' },
    storage: [['invoice-job:operator:test', JSON.stringify({ job_id: runId })]],
  });
  await local.poll();
  assert.equal(local.snapshot().job, null);
  assert.equal(local.storage.has('invoice-job:operator:test'), false);
  assert.match(local.snapshot().message, /start the approved execution/);

  const deployed = workflow(async () => { throw missing; }, {
    job: { job_id: runId, kind: 'execution', state: 'admission-unconfirmed' },
  });
  await deployed.poll();
  assert.equal(deployed.snapshot().job.job_id, runId);
  assert.match(deployed.snapshot().message, /run was not restarted/);
});

test('capacity failure is explicit and never opens Propose or borrows a saved plan', async () => {
  const scenarioId='d'.repeat(32),result={reason:'sandbox-capacity',retained_sandboxes:24,retained_limit:24,sandbox_created:false,agent_started:false};
  const failed={job_id:runId,kind:'planning',target_id:scenarioId,state:'uncertain',result};
  const fixture=workflow(async path=>path==='/api/invoices/plans'?{source:'azure-sql',plans:[row()]}:path.includes('/events?')?{job_id:runId,events:[]}:failed,
    {job:{...failed,state:'running',result:null},newInvestigation:true,newJobId:runId,draft:{scenarioId,jobId:runId}});
  await fixture.poll();
  assert.match(fixture.snapshot().message,/capacity reached: 24 retained \/ 24 limit/);
  assert.equal(fixture.snapshot().selected,null);
  assert.equal(fixture.snapshot().step,0);
  assert.match(view.nextAction({status:'authenticated',persona:'operator'},null,failed,null,true,scenarioId),/capacity reached/);
  assert.doesNotMatch(view.nextAction({status:'authenticated',persona:'operator'},null,failed,null,true,'e'.repeat(32)),/capacity reached/);
});

test('sandbox deletion stores exact pending ID before POST and never repeats after response loss', async()=>{
  const identifier='d'.repeat(32),calls=[];
  const fixture=workflow(async(path,options)=>{
    calls.push([path,options]);
    assert.deepEqual(JSON.parse(fixture.storage.get('invoice-sandbox-deletes:operator:test')),[identifier]);
    throw Error('response lost');
  },{sandboxInventory:{sandboxes:[{sandbox_id:identifier,name:'ip-example',deletable:true}]}});
  await fixture.deleteSandbox(identifier);await fixture.deleteSandbox(identifier);await fixture.deleteSandbox('e'.repeat(32));
  assert.equal(calls.length,1);assert.equal(calls[0][0],'/api/invoices/sandboxes/'+identifier+'/delete');
  assert.deepEqual(JSON.parse(calls[0][1].body),{sandbox_id:identifier});
});

test('inventory refresh is GET only and does not hold the command lock', async()=>{
  let respond;
  const fixture=workflow((path,options)=>{assert.equal(path,'/api/invoices/sandboxes');assert.equal(options,undefined);return new Promise(resolve=>respond=resolve);});
  const pending=fixture.refreshSandboxes();assert.equal(fixture.snapshot().busy,false);
  respond({source:'openshell-sandbox-inventory',remaining_count:14,sandboxes:[]});await pending;
});

test('protected capacity pauses admission before consuming the final slot',()=>{
  const job={job_id:runId,kind:'planning',state:'uncertain',target_id:'d'.repeat(32),result:{reason:'sandbox-capacity',retained_sandboxes:23,retained_limit:24,reserved_slots:1,sandbox_created:false,agent_started:false}};
  assert.match(view.observation(job,[],true,Date.now()).title,/admission paused: 23 retained \/ 24 limit; 1 slot reserved/);
  assert.match(view.nextAction({status:'authenticated',persona:'operator'},null,job,null,true,job.target_id),/1 slot reserved/);
  assert.doesNotMatch(view.observation({...job,result:{...job.result,retained_sandboxes:18}},[],true,Date.now()).title,/reserved/);
});

test('idle demo refresh only reads session status, never invoice SQL endpoints',async()=>{
  const calls=[];
  const fixture=workflow(async path=>{calls.push(path);return{source:'invoice-demo-session',state:'idle',session_id:null};},
    {demoSession:{state:'idle'},job:{job_id:runId,kind:'planning',state:'running'}});
  await fixture.refreshDemo();await fixture.refreshPlans();await fixture.poll();await fixture.refreshSandboxes();await fixture.createScenario('healthy');
  assert.deepEqual(calls,['/api/invoices/demo-session']);
});

test('starting a demo records request before POST and never starts an agent',async()=>{
  const calls=[];let id;
  const fixture=workflow(async(path,options)=>{
    calls.push([path,options?.method]);
    if(options?.method==='POST') {
      id=JSON.parse(options.body).session_id;
      assert.equal(JSON.parse(fixture.storage.get('invoice-demo-pending:operator:test')).session_id,id);
      return{source:'invoice-demo-session',session_id:id,state:'active'};
    }
    if(path==='/api/invoices/demo-session')return{source:'invoice-demo-session',session_id:id,state:'active',expires_at:new Date(Date.now()+14400000).toISOString()};
    if(path==='/api/invoices/plans')return{source:'azure-sql',plans:[]};
    return{source:'openshell-sandbox-inventory',remaining_count:18,sandboxes:[]};
  },{demoSession:{state:'idle'}});
  await fixture.demoAction('start');
  assert.deepEqual(calls.filter(([,method])=>method==='POST').map(([path])=>path),['/api/invoices/demo-session/start']);
});

test('lost demo start remains pending across idle status and reload without another POST',async()=>{
  const calls=[];
  const api=async(path,options)=>{
    calls.push([path,options?.method]);
    if(options?.method==='POST')throw new Error('response lost');
    return{source:'invoice-demo-session',state:'idle',session_id:null};
  };
  const fixture=workflow(api,{demoSession:{state:'idle'}});
  await fixture.demoAction('start');await fixture.refreshDemo();await fixture.demoAction('start');
  const restored=workflow(api,{demoSession:{state:'idle'},storage:[...fixture.storage]});
  await restored.refreshDemo();await restored.demoAction('start');
  assert.equal(calls.filter(([,method])=>method==='POST').length,1);
  assert.ok(restored.storage.get('invoice-demo-pending:operator:test'));
});

test('lost demo end blocks data reads until exact ending status confirms it',async()=>{
  const calls=[];let ending=false;
  const fixture=workflow(async(path,options)=>{
    calls.push([path,options?.method]);
    if(options?.method==='POST')throw new Error('response lost');
    return{source:'invoice-demo-session',state:ending?'ending':'active',session_id:'f'.repeat(32),expires_at:new Date(Date.now()+14400000).toISOString(),can_end:true};
  });
  await fixture.demoAction('end');await fixture.refreshDemo();await fixture.poll();await fixture.refreshPlans();await fixture.demoAction('end');
  assert.ok(fixture.storage.get('invoice-demo-pending:operator:test'));
  ending=true;await fixture.refreshDemo();
  assert.equal(fixture.storage.has('invoice-demo-pending:operator:test'),false);
  assert.equal(calls.filter(([,method])=>method==='POST').length,1);
  assert.ok(calls.every(([path])=>path.startsWith('/api/invoices/demo-session')));
});

test('definitive demo end rejection clears pending state without hiding the error',async()=>{
  const rejected=Object.assign(new Error('Request validation failed'),{status:422});
  const fixture=workflow(async(_path,options)=>{if(options?.method==='POST')throw rejected;return{source:'invoice-demo-session',state:'active',session_id:'f'.repeat(32),expires_at:new Date(Date.now()+14400000).toISOString(),can_end:true};});
  await fixture.demoAction('end');
  assert.equal(fixture.snapshot().demoPending,null);
  assert.equal(fixture.storage.has('invoice-demo-pending:operator:test'),false);
  assert.match(fixture.snapshot().demoError,/Request validation failed.*rejected before the demo state changed/);
});