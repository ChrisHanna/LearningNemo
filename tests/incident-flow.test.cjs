const { test } = require('node:test');
const assert = require('node:assert/strict');
const flow = require('../src/task_agent/console/static/incident-flow.js');
const session = { status: 'authenticated', persona: 'operator' };
const receipt = { observed_at: new Date().toISOString(), snapshot: { active_query_version: 'cycle-unsafe-v1', query_run_states: {} } };
test('analysis is available without a Planning VM or proof', () => {
  assert.deepEqual(flow.availableSteps({ session }), [0]);
  assert.equal(flow.nextAction({ step: 0, session, workspace: { cloud: { readyForProbe: false } } }).id, 'analyze');
});
test('fresh analysis unlocks proposal but not approval or execution', () => {
  const analysis = { receipt };
  assert.deepEqual(flow.availableSteps({ session, analysis }), [0, 1]);
  assert.equal(flow.nextAction({ step: 1, session, analysis }).id, 'propose');
});
test('running query is reported, never cancelled', () => {
  const analysis = { receipt: { ...receipt, snapshot: { ...receipt.snapshot, query_run_states: { owned: 'running' } } } };
  assert.equal(flow.nextAction({ step: 1, session, analysis }).id, 'analyze-step');
});
test('approver cannot analyze and operator cannot approve', () => {
  assert.equal(flow.nextAction({ step: 0, session: { ...session, persona: 'approver' } }).id, 'switch');
  assert.equal(flow.nextAction({ step: 2, session }).id, 'switch');
});
test('missing or stale evidence cannot create a plan', () => {
  assert.equal(flow.nextAction({ step: 1, session }).id, 'analyze-step');
  assert.equal(flow.nextAction({ step: 1, session, analysis: { receipt: { ...receipt, observed_at: '2020-01-01T00:00:00Z' } } }).id, 'analyze-step');
  for (const observed_at of ['invalid', new Date(Date.now() + 60000).toISOString()]) {
    assert.equal(flow.nextAction({ step: 1, session, analysis: { receipt: { ...receipt, observed_at } } }).id, 'analyze-step');
  }
});
test('existing approval resumes without a new analysis and execution remains explicit', () => {
  const incident = { planId: 'plan-existing', planState: 'approved', expired: false };
  assert.equal(flow.nextAction({ step: 0, session, incident }).id, 'execute-step');
  assert.equal(flow.nextAction({ step: 3, session, incident }).id, 'execution-status');
  const execution = { connected: true, planId: incident.planId, record: null };
  assert.equal(flow.nextAction({ step: 3, session, incident, execution }).id, 'execute');
  assert.equal(flow.nextAction({ step: 3, session, incident: { ...incident, expired: true }, execution }).id, 'incidents');
  assert.equal(flow.nextAction({ step: 0, session, incident: { ...incident, expired: true } }).id, 'analyze');
});