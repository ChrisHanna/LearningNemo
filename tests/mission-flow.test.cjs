const { test } = require('node:test');
const assert = require('node:assert/strict');
const flow = require('../src/task_agent/console/static/mission-flow.js');
const now = Date.parse('2026-09-15T12:00:00Z');
const session = { status: 'authenticated', persona: 'operator' };
const ready = { enabled: true, cloud: { vm: 'running', lease: 'valid', runtimeLock: 'deployed', nat: 'runtime-verified', readyForProbe: true, checkedAt: new Date(now).toISOString(), expiresAt: new Date(now + 3600000).toISOString(), freshForSeconds: 60 } };

test('step two header distinguishes expired lease from a running or completed proof', () => {
  const workspace = { cloud: { vm: 'running', lease: 'expired-or-too-short', readyForProbe: false } };
  assert.deepEqual(flow.progressLabels({ step: 1, workspace }), { title: 'Workspace lease expired', evidence: 'Proof not run' });
  assert.equal(flow.progressLabels({ step: 1, workspace, busy: true }).title, 'Checking workspace');
  workspace.result = { receipt: { passed: true } };
  assert.equal(flow.progressLabels({ step: 1, workspace }).title, 'Containment passed');
});

test('Operator must check readiness before running', () => {
  assert.equal(flow.nextAction({ step: 1, session, workspace: { enabled: true }, now }).id, 'check');
  assert.equal(flow.nextAction({ step: 1, session, workspace: ready, now }).id, 'run');
  assert.equal(flow.nextAction({ step: 1, session, workspace: ready, now: now + 61000 }).id, 'check');
});
test('stopped VM explains environment blocker, never offers run', () => {
  const workspace = { ...ready, cloud: { ...ready.cloud, vm: 'not-running', readyForProbe: false } };
  const action = flow.nextAction({ step: 1, session, workspace, now });
  assert.equal(action.id, 'check');
  assert.match(action.message, /VM is stopped/);
  assert.deepEqual(flow.unlockedSteps({ session, workspace }), [0, 1]);
});
test('failed or absent proof cannot advance; successful proof unlocks only investigation', () => {
  for (const passed of [false, true]) {
    const workspace = { ...ready, result: { receipt: { passed } } };
    assert.equal(flow.nextAction({ step: 1, session, workspace, now }).id, passed ? 'continue' : 'run');
    assert.deepEqual(flow.unlockedSteps({ session, workspace }), passed ? [0, 1, 2] : [0, 1]);
  }
});
test('missing incident cannot become approval or execution', () => {
  const action = flow.nextAction({ step: 2, session, incident: { connected: true, count: 0 } });
  assert.equal(action.disabled, true);
  assert.match(action.message, /initiation is not enabled/);
});
test('connected initiation offers one explicit command without unlocking review', () => {
  const incident = { connected: true, count: 0, initiationAvailable: true };
  assert.equal(flow.nextAction({ step: 2, session, incident }).id, 'initiate');
  assert.equal(flow.nextAction({ step: 2, session, incident }).disabled, false);
  assert.deepEqual(flow.unlockedSteps({ session, incident, workspace: { result: { receipt: { passed: true } } } }), [0, 1, 2]);
  assert.equal(flow.nextAction({ step: 2, session, incident: { ...incident, pendingRequest: 'pending' } }).label, 'Check investigation request');
  for (const persona of ['reader', 'approver']) assert.equal(flow.nextAction({ step: 2, session: { ...session, persona }, incident }).id, 'switch');
});
test('signed-out, Reader and Approver never get a run command', () => {
  assert.deepEqual(flow.unlockedSteps({}), [0]);
  assert.equal(flow.nextAction({ step: 1, workspace: ready, now }).id, 'signin');
  for (const persona of ['reader', 'approver']) assert.equal(flow.nextAction({ step: 1, session: { ...session, persona }, workspace: ready, now }).id, 'switch');
  assert.deepEqual(flow.unlockedSteps({ session: { ...session, persona: 'approver' } }), [0, 3]);
});
test('pending operations expose one disabled action', () => {
  const action = flow.nextAction({ step: 1, session, workspace: ready, busy: true, now });
  assert.equal(action.disabled, true);
  assert.equal(action.id, 'wait');
});
test('pending sign-in can reopen the dialog without being disabled', () => {
  const action = flow.nextAction({ step: 0, session: { status: 'pending' } });
  assert.equal(action.id, 'signin');
  assert.equal(action.label, 'Continue sign-in');
  assert.equal(action.disabled, false);
});
test('fresh observation cannot override expired lease', () => {
  const workspace = { ...ready, cloud: { ...ready.cloud, expiresAt: new Date(now - 1000).toISOString() } };
  assert.equal(flow.nextAction({ step: 1, session, workspace, now }).id, 'check');
});
test('execution requires approval and completion requires persisted verification', () => {
  const incident = { planId: 'plan-test', planHash: 'a'.repeat(64), planState: 'approved', planVersion: 3 };
  const execution = { connected: true, planId: incident.planId, record: null };
  assert.equal(flow.nextAction({ step: 4, session, incident, execution }).id, 'execute');
  assert.equal(flow.nextAction({ step: 4, session, incident, execution: { ...execution, record: { state: 'claimed' } } }).id, 'reconcile');
  assert.equal(flow.nextAction({ step: 2, session, incident: { ...incident, connected: true, count: 1 } }).id, 'execution-step');
  assert.equal(flow.nextAction({ step: 5, session, incident, execution }).disabled, true);
  execution.record = { state: 'verification' };
  assert.equal(flow.nextAction({ step: 5, session, incident, execution }).id, 'complete');
  assert.ok(flow.unlockedSteps({ session, incident, execution }).includes(5));
  execution.record.state = 'completed';
  assert.equal(flow.nextAction({ step: 5, session, incident, execution }).id, 'done');
});