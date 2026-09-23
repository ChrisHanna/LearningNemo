const { test } = require('node:test');
const assert = require('node:assert/strict');
const { observationStatus } = require('../src/task_agent/console/static/workspace-observation.js');
const { nextAction } = require('../src/task_agent/console/static/mission-flow.js');
const now = Date.parse('2026-09-15T18:00:00Z');
const snapshot = { checkedAt: new Date(now).toISOString(), freshForSeconds: 60, expiresAt: new Date(now - 3600000).toISOString(), vm: 'not-running', lease: 'expired-or-too-short', runtimeLock: 'deployed', nat: 'absent', readyForProbe: false };

test('fresh observation of an expired stopped workspace is not stale', () => {
  const observed = observationStatus(snapshot, false, now);
  assert.equal(observed.stale, false);
  assert.equal(observed.leaseExpired, true);
  assert.equal(observed.label, 'Fresh Azure observation');
  const action = nextAction({ step: 1, session: { status: 'authenticated', persona: 'operator' }, workspace: { enabled: true, cloud: snapshot, stale: observed.stale }, now });
  assert.equal(action.id, 'check');
  assert.match(action.message, /VM is stopped/);
});
test('age, not lease status, determines staleness at the exact boundary', () => {
  assert.equal(observationStatus(snapshot, false, now + 59999).stale, false);
  assert.equal(observationStatus(snapshot, false, now + 60000).stale, true);
  assert.match(observationStatus(snapshot, false, now + 60000).source, /Last Azure check/);
});

test('aging a blocked observation retains its last-known cause without allowing execution', () => {
  const observed = observationStatus(snapshot, false, now + 60000);
  const action = nextAction({ step: 1, session: { status: 'authenticated', persona: 'operator' }, workspace: { enabled: true, cloud: snapshot, stale: observed.stale }, now: now + 60000 });
  assert.equal(action.id, 'check');
  assert.match(action.message, /^Last observation:.*VM is stopped/);
  assert.match(action.message, /Recheck to confirm/);
});
test('failed refresh and invalid clocks cannot produce fresh evidence', () => {
  assert.equal(observationStatus(snapshot, true, now).stale, true);
  assert.equal(observationStatus(snapshot, false, now - 1000).stale, true);
  for (const checkedAt of [null, 'invalid']) assert.equal(observationStatus({ ...snapshot, checkedAt }, false, now).stale, true);
  for (const freshForSeconds of [0, undefined, '60']) assert.equal(observationStatus({ ...snapshot, freshForSeconds }, false, now).stale, true);
});
test('absence of a query is labelled not checked', () => {
  assert.equal(observationStatus(null, false, now).label, 'Not checked');
});