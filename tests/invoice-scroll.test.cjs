const test = require('node:test');
const assert = require('node:assert/strict');
const scroll = require('../src/task_agent/console/static/invoice-scroll.js');

function panel(key, left, top, open) {
  return { tagName: open === undefined ? 'PRE' : 'DETAILS', scrollLeft: left, scrollTop: top, open,
    getAttribute() { return key; },
    scrollTo({ left, top, behavior }) { assert.equal(behavior, 'instant'); this.scrollLeft = left; this.scrollTop = top; } };
}
const root = elements => ({ querySelectorAll: () => elements });
const viewport = () => ({ scrollX: 15, scrollY: 800, scrollTo({ left, top, behavior }) {
  assert.equal(behavior, 'instant'); this.scrollX = left; this.scrollY = top;
} });

test('every panel restores its own two-axis offset even after sections reorder or insert', () => {
  const window = viewport();
  const panels = ['history', 'ledger', 'plan', 'diagnostics', 'receipt', 'architecture'].map((key, index) => panel(key, index * 70, index * 130));
  const saved = scroll.capture(root(panels), window);
  const restored = [...panels].reverse().map(element => panel(element.getAttribute(), 0, 0));
  const inserted = panel('new-panel', 0, 0); restored.splice(2, 0, inserted);
  scroll.restore(root(restored), saved, window);
  for (const element of restored.filter(element => element !== inserted)) {
    const original = panels.find(item => item.getAttribute() === element.getAttribute());
    assert.equal(element.scrollLeft, original.scrollLeft);
    assert.equal(element.scrollTop, original.scrollTop);
  }
  assert.equal(inserted.scrollTop, 0);
  assert.equal(window.scrollY, 800);
});

test('open and closed disclosures are restored before their nested scroll offsets', () => {
  const window = viewport();
  const saved = scroll.capture(root([panel('open', 0, 0, true), panel('closed', 0, 0, false), panel('raw', 31, 172)]), window);
  const opened = panel('open', 0, 0, false), closed = panel('closed', 0, 0, true), raw = panel('raw', 0, 0);
  const apply = raw.scrollTo;
  raw.scrollTo = function (values) { assert.equal(opened.open, true); assert.equal(closed.open, false); apply.call(this, values); };
  scroll.restore(root([raw, closed, opened]), saved, window);
  assert.equal(raw.scrollTop, 172);
});

test('capture respects new manual positions including returning to zero', () => {
  const window = viewport(), element = panel('history', 1000, 600);
  for (const [left, top] of [[700, 260], [0, 0], [800, 10]]) {
    element.scrollLeft = left; element.scrollTop = top;
    const snapshot = scroll.capture(root([element]), window);
    const replacement = panel('history', 0, 0);
    scroll.restore(root([replacement]), snapshot, window);
    assert.equal(replacement.scrollLeft, left); assert.equal(replacement.scrollTop, top);
  }
});

test('absent snapshot or different content keys never inherit another panel position', () => {
  const window = viewport(), element = panel('plan-B', 0, 0);
  scroll.restore(root([element]), null, window);
  const snapshot = scroll.capture(root([panel('plan-A', 10, 500)]), window);
  scroll.restore(root([element]), snapshot, window);
  assert.equal(element.scrollTop, 0);
});

test('same-session outer refresh preserves page position before account-bar reflow', () => {
  const fs = require('node:fs'), vm = require('node:vm');
  const source = fs.readFileSync(require.resolve('../src/task_agent/console/static/learningnemo.js'), 'utf8');
  const fragment = source.slice(source.indexOf('let sessionViewportKey = null;'), source.indexOf('async function bootstrap()'));
  const node = () => ({ classList: { toggle() {} }, lastChild: {}, replaceChildren() {}, querySelector: () => node() });
  const elements = new Proxy({}, { get: () => node() });
  const window = viewport();
  window.dispatchEvent = () => { window.scrollY += 19; };
  const state = { invoiceEnabled: true, session: { status: 'authenticated', persona: 'operator', accountFingerprint: 'first', grantedRoles: [], grantedScopes: [] } };
  vm.runInNewContext(fragment + '\nwindow.renderSession = renderSession;', { window, state, elements,
    personaLabel: value => value, statusLabel: value => value, statusClass: value => value, createIcon() {}, scopeElement() {},
    renderIdentityStatus() { window.scrollY += 12; }, refreshIcons() {}, CustomEvent: class {}, document: { createElement: node } });
  window.renderSession();
  window.scrollX = 5; window.scrollY = 1100;
  window.renderSession();
  assert.equal(window.scrollY, 1100);
  assert.equal(window.scrollX, 5);
  state.session.accountFingerprint = 'second';
  window.renderSession();
  assert.equal(window.scrollY, 1131);
});