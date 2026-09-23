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
  vm.runInNewContext(fs.readFileSync(require.resolve('../src/task_agent/console/static/invoice-evidence.js'),'utf8'),{window,document,createIcon:()=>document.createElement('svg')});
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