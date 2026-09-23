const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const view = require('../src/task_agent/console/static/invoice-view.js');

function outcomes(row, before, observation) {
  const document = { createElement(tag) { return { tag, children: [], setAttribute() {}, append(...children) { this.children.push(...children); } }; } };
  const window = { invoiceView: view };
  vm.runInNewContext(fs.readFileSync(require.resolve('../src/task_agent/console/static/invoice-experience.js'), 'utf8'), { window, document, createIcon: () => document.createElement('svg') });
  const element = window.invoiceExperience.outcomes(row, before, observation);
  const text = element => [element.textContent || '', ...element.children.map(text)].join(' ');
  return { text: text(element), element };
}

const row = { state: 'submitted', plan_hash: 'hash', plan_json: { plan_id: 'plan', scenario_id: 'scenario', evidence_hash: 'evidence' } };
const before = { source: 'invoice-diagnostic-api', scenario_id: 'scenario', evidence_hash: 'evidence', revision: 1, orders: 12, active_invoices: 24, duplicate_invoices: 12, expected_cents: 147800, reported_cents: 295600, observed_at: '2026-09-18T23:53:35Z' };
const observation = { source: 'diagnostic-sql-observation', plan_id: 'plan', plan_hash: 'hash', summary: { ...before }, observed_at: '2026-09-18T23:54:20Z' };

test('unrepaired identical readings show one set of facts and the actual discrepancy', () => {
  const result = outcomes(row, before, observation);
  assert.match(result.text, /12 duplicate invoices found/);
  assert.match(result.text, /Repair not started/);
  assert.match(result.text, /12 source orders/);
  assert.match(result.text, /Order total: 1,478.00/);
  assert.match(result.text, /Overstated by 1,478.00/);
  assert.match(result.text, /UTC/);
  assert.equal((result.text.match(/2,956.00/g) || []).length, 1);
  assert.doesNotMatch(result.text, /SQL snapshot|Observed after|Before|Planning baseline/);
});

test('expired scenarios explicitly identify cached findings', () => {
  const result = outcomes({ ...row, scenario_expires_at: '2020-01-01T00:00:00Z' }, before, observation);
  assert.match(result.text, /Scenario expired.*retained findings, not a live database reading/);
});

test('higher revision after execution compares actual readings without inferring verification', () => {
  const updated = { ...observation, summary: { ...before, revision: 4, active_invoices: 12, duplicate_invoices: 0, reported_cents: 147800 } };
  const result = outcomes({ ...row, state: 'executing', execution_run_id: 'run' }, before, updated);
  assert.match(result.text, /Planning baseline/);
  assert.match(result.text, /Latest database read/);
  assert.match(result.text, /Matches the order total/);
  assert.match(result.text, /verification not confirmed/);
  assert.doesNotMatch(result.text, /Repair independently verified/);
});

test('unbound readings cannot claim findings or trusted data sources', () => {
  const result = outcomes(row, { ...before, scenario_id: 'other' }, { ...observation, plan_hash: 'wrong' });
  assert.match(result.text, /Invoice counts not observed/);
  assert.doesNotMatch(result.text, /Database read|Planning diagnostic read|duplicate invoices found|Order total|Overstated/);
});

test('verified state does not manufacture a changed database reading', () => {
  const verified = { ...row, state: 'verified', verification_json: Object.fromEntries(Object.keys(view.checks).map(key => [key, true])) };
  const result = outcomes(verified, before, null);
  assert.match(result.text, /Repair independently verified/);
  assert.match(result.text, /Planning diagnostic read/);
  assert.match(result.text, /12 duplicate invoices found/);
  assert.doesNotMatch(result.text, /Latest database read|Matches the order total/);
});