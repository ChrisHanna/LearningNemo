const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const view = require('../src/task_agent/console/static/invoice-view.js');

function renderEvidence(rows, before, changes = {}) {
  const document = { createElement(tag) {
    return { tag, children: [], setAttribute() {}, append(...children) { this.children.push(...children); } };
  } };
  const window = { invoiceView: view };
  vm.runInNewContext(fs.readFileSync(require.resolve('../src/task_agent/console/static/invoice-evidence.js'), 'utf8'), { window, document });
  return window.renderInvoiceEvidence({ state: 'submitted', plan_json: { plan_id: 'plan', scenario_id: 'scenario', evidence_hash: 'evidence' }, plan_hash: 'hash' }, {
    source: 'diagnostic-sql-observation', plan_id: 'plan', plan_hash: 'hash', summary: { scenario_id: 'scenario', revision: 1, active_invoices: 24, duplicate_invoices: 12, reported_cents: 295600 }, observed_at: '2026-09-18T23:54:20Z', rows, ...changes,
  }, before);
}

function renderRows(rows) {
  const section = renderEvidence(rows);
  const descendants = element => [element, ...element.children.flatMap(descendants)];
  return descendants(section).find(element => element.tag === 'tbody').children;
}

test('quarantined history does not mark the retained active invoice as a duplicate', () => {
  const rows = renderRows([{ order_id: 'order', quarantined: false }, { order_id: 'order', quarantined: true }]);
  assert.equal(rows[0].className, '');
  assert.equal(rows[0].children.at(-1).textContent, 'Active');
  assert.equal(rows[1].className, 'quarantined');
  assert.equal(rows[1].children.at(-1).textContent, 'Quarantined');
});

test('two active invoices still display as duplicates', () => {
  const rows = renderRows([{ order_id: 'order', quarantined: false }, { order_id: 'order', quarantined: false }]);
  assert.ok(rows.every(row => row.className === 'duplicate'));
});

test('pre-repair ledger labels one recorded reading with an explicit UTC time', () => {
  const before = { source: 'invoice-diagnostic-api', scenario_id: 'scenario', evidence_hash: 'evidence', revision: 1, active_invoices: 24, duplicate_invoices: 12, reported_cents: 295600 };
  const section = renderEvidence([], before);
  const text = element => [element.textContent || '', ...element.children.map(text)].join(' ');
  const rendered = text(section);
  assert.match(rendered, /Invoice records from this reading/);
  assert.match(rendered, /UTC/);
  assert.match(rendered, /database revision 1/);
  assert.doesNotMatch(rendered, /snapshot|Observed after|Planning baseline/);
  assert.equal((rendered.match(/2,956.00/g) || []).length, 1);
});

test('ledger refuses untrusted or differently bound observations', () => {
  for (const changes of [{ source: 'agent-runtime' }, { plan_hash: 'different' }, { observed_at: 'invalid' }]) {
    const section = renderEvidence([], null, changes);
    assert.equal(section.children.at(-1).textContent, 'No matching database observation.');
  }
});

test('same-sandbox renderer offers a direct action and removes commands from Audience and closed runs', () => {
  const document = { createElement(tag) {
    return {tag,children:[],attributes:{},listeners:{},setAttribute(name,value){this.attributes[name]=value;},append(...children){this.children.push(...children);},prepend(...children){this.children.unshift(...children);},addEventListener(name,action){this.listeners[name]=action;}};
  }};
  const window = {invoiceView:view};
  vm.runInNewContext(fs.readFileSync(require.resolve('../src/task_agent/console/static/invoice-evidence.js'),'utf8'),{window,document,invoiceView:view,createIcon:()=>document.createElement('svg')});
  const job={job_id:'a'.repeat(32),kind:'planning',state:'running'};
  const events=[{source:'workspace-controller',event_type:'sandbox-bound',kind:'planning',sandbox_id:'b'.repeat(32),policy_hash:'c'.repeat(64)}];
  const descendants=element=>[element,...element.children.flatMap(descendants)];
  const calls=[];
  const elements=descendants(window.renderInvoiceSandboxTest({job,events,permitted:true,request:(...values)=>calls.push(values)}));
  const action=elements.find(element=>element.tag==='button');
  assert.equal(elements.some(element=>element.tag==='input'),false);
  assert.equal(action.disabled,false);action.listeners.click();
  assert.deepEqual(calls,[[job.job_id,events[0].sandbox_id]]);
  assert.ok(elements.some(element=>element.textContent==='Forbidden request: Request an invoice repair'));
  for(const options of [{readOnly:true},{job:{...job,state:'finished'}},{pending:true}]) {
    const hidden=descendants(window.renderInvoiceSandboxTest({job,events,permitted:true,...options}));
    assert.equal(hidden.filter(element=>['input','button'].includes(element.tag)).length,0);
  }
});

test('investigation launcher distinguishes real sandboxes from local previews', () => {
  const document = { createElement(tag) {
    return {tag,textContent:'',className:'',children:[],attributes:{},listeners:{},disabled:false,setAttribute(name,value){this.attributes[name]=value;},append(...children){this.children.push(...children);},prepend(...children){this.children.unshift(...children);},addEventListener(name,action){this.listeners[name]=action;}};
  }};
  const window = {invoiceView:view};
  vm.runInNewContext(fs.readFileSync(require.resolve('../src/task_agent/console/static/invoice-evidence.js'),'utf8'),{window,document,invoiceView:view,createIcon:()=>document.createElement('svg')});
  const descendants=element=>[element,...element.children.flatMap(descendants)];
  const calls=[];
  const live=descendants(window.renderInvoiceChallenge({kind:'sql-denied',executionMode:'openshell',permitted:true,blocked:false,busy:false,onKind:value=>calls.push(['select',value]),start:value=>calls.push(['start',value])}));
  assert.ok(live.some(element=>element.textContent==='Test OpenShell security boundaries'));
  assert.ok(live.some(element=>element.textContent==='1. Choose a test scenario'));
  assert.ok(live.some(element=>element.textContent==='OpenShell MicroVM'));
  assert.ok(live.some(element=>element.textContent==='Run OpenShell test'));
  assert.ok(live.some(element=>element.tag==='code'&&element.textContent==='infra/next-phase/openshell/invoice-planning-policy.yaml'));
  assert.ok(live.some(element=>element.tag==='code'&&element.textContent.includes('include_workdir: false')&&element.textContent.includes('port: 443')));
  assert.ok(live.some(element=>element.textContent==='OpenShell enforces this checked-in policy for the launched MicroVM.'));
  assert.equal(live.some(element=>element.tag==='select'),false);
  const choices=live.filter(element=>element.tag==='button'&&Object.hasOwn(element.attributes,'aria-pressed'));
  assert.equal(choices.length,8);
  assert.equal(choices.find(element=>element.attributes['aria-pressed']==='true').children[1].children[0].textContent,'Try a direct SQL connection');
  assert.ok(choices.some(element=>element.children[1].children[0].textContent==='Try to modify application code'));
  assert.ok(choices.some(element=>element.children[1].children[0].textContent==='Call the approved Planning API'));
  assert.ok(choices.some(element=>element.children[1].children[0].textContent==='Call an unapproved external API'));
  assert.ok(choices.some(element=>element.children[1].children[0].textContent==='Try a symlink escape'));
  live.find(element=>element.textContent==='Run OpenShell test').listeners.click();
  assert.deepEqual(calls,[['start','sql-denied']]);
  const preview=descendants(window.renderInvoiceChallenge({kind:'query-draft',executionMode:'fixture',permitted:true,blocked:false,busy:false,onKind(){},start(){}}));
  assert.ok(preview.some(element=>element.textContent==='Fixture preview'));
  assert.ok(preview.some(element=>element.textContent==='Preview test'));
  assert.ok(preview.some(element=>element.textContent==='This is the policy the deployed OpenShell flow enforces; the local walkthrough only previews it.'));
  assert.equal(preview.some(element=>element.textContent==='Run OpenShell test'),false);
});

test('live investigation renders verified OpenShell execution and lifecycle', () => {
  const document = { createElement(tag) {
    return {tag,textContent:'',className:'',children:[],attributes:{},listeners:{},disabled:false,setAttribute(name,value){this.attributes[name]=value;},append(...children){this.children.push(...children);},prepend(...children){this.children.unshift(...children);},addEventListener(name,action){this.listeners[name]=action;}};
  }};
  const window = {invoiceView:view};
  vm.runInNewContext(fs.readFileSync(require.resolve('../src/task_agent/console/static/invoice-evidence.js'),'utf8'),{window,document,invoiceView:view,createIcon:()=>document.createElement('svg')});
  const sandbox='b'.repeat(32),policy='c'.repeat(64),run='d'.repeat(32);
  const result={kind:'query-draft',challenge_id:run,state:'finished',events:[
    {event_type:'preparing-sandbox',reason:'Preparing a fresh OpenShell sandbox'},
    {event_type:'sandbox-bound',reason:'OpenShell sandbox identity and policy confirmed'},
    {event_type:'investigation-started',reason:'Executing the bounded investigation inside the OpenShell sandbox'},
    {event_type:'investigation-recorded',reason:'Bound sandbox execution receipt captured'},
    {event_type:'sandbox-stopped',reason:'OpenShell sandbox stop confirmed; retained for evidence'},
  ],result:{run_id:run,sandbox_id:sandbox,scenario:'query-draft',uid:998,evidence_mode:'live',policy_hash:policy,actor:'sandbox-investigation',agent_requested:false,model_involved:false,executed_in_sandbox:true,sandbox_runtime:'OpenShell MicroVM',sandbox_executor:'/opt/venv/bin/python',sandbox_stopped:true,sandbox_retained:true,outcome:'allowed',operation:'generate-read-query',path:'/tmp/investigation.sql',query_class:'SELECT',statement_hash:'e'.repeat(64),query_executed:false}};
  const descendants=element=>[element,...element.children.flatMap(descendants)];
  const elements=descendants(window.renderInvoiceChallenge({result,kind:'query-draft',executionMode:'openshell',permitted:true,blocked:false,busy:false,onKind(){},start(){},reconnect(){}}));
  for(const expected of ['Verified sandbox execution','Confirmed inside sandbox','OpenShell MicroVM','/opt/venv/bin/python','UID 998','Stopped and retained','Executing investigation in sandbox','Sandbox stopped and retained']) {
    assert.ok(elements.some(element=>element.textContent===expected),expected);
  }
  assert.ok(elements.some(element=>element.textContent===sandbox));
  assert.ok(elements.some(element=>element.textContent===policy));
});

test('same-sandbox evidence retains Agents API user and environment binding', () => {
  const document = { createElement(tag) {
    return {tag,children:[],textContent:'',attributes:{},listeners:{},setAttribute(name,value){this.attributes[name]=value;},append(...children){this.children.push(...children);},prepend(...children){this.children.unshift(...children);},addEventListener(name,action){this.listeners[name]=action;}};
  }};
  const window = {invoiceView:view};
  vm.runInNewContext(fs.readFileSync(require.resolve('../src/task_agent/console/static/invoice-evidence.js'),'utf8'),{window,document,invoiceView:view,createIcon:()=>document.createElement('svg')});
  const job={job_id:'a'.repeat(32),kind:'planning',state:'running',agent_runtime:{runtime:'agents_api',session_id:'session-1',environment_id:'environment-1',state:'running',user_context:{sponsor_hash:'sponsor',persona:'operator',permissions:['tasks.read'],expires_at:'2026-09-24T01:00:00Z'}}};
  const events=[{source:'workspace-controller',event_type:'sandbox-bound',kind:'planning',sandbox_id:'b'.repeat(32),policy_hash:'c'.repeat(64)}];
  const descendants=element=>[element,...element.children.flatMap(descendants)];
  const elements=descendants(window.renderInvoiceSandboxTest({job,events,readOnly:true}));
  for(const expected of ['OpenAI Agents API','session-1','environment-1','sponsor','operator','tasks.read']) assert.ok(elements.some(element=>element.textContent===expected),expected);
  assert.ok(elements.some(element=>element.textContent.includes('API completion alone does not establish a database repair')));
});