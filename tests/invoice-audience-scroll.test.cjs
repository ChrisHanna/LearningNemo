const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const view = require('../src/task_agent/console/static/invoice-view.js');

function fixture() {
  const window = { invoiceView: view, invoiceScroll: require('../src/task_agent/console/static/invoice-scroll.js'), scrollX: 0, scrollY: 0, addEventListener() {},
    scrollTo({ left, top }) { this.scrollX = left; this.scrollY = top; } };
  const document = { activeElement: null };
  const createElement = tag => ({
    tagName: tag.toUpperCase(), textContent: '', className: '', children: [], attributes: {}, scrollLeft: 0, scrollTop: 0,
    classList: { toggle() {} }, append(...children) { this.children.push(...children); }, prepend(...children) { this.children.unshift(...children); },
    replaceChildren() { this.children = []; window.scrollX = 0; window.scrollY = 0; }, after() {}, addEventListener() {},
    getAttribute(name) { return this.attributes[name] || null; }, setAttribute(name, value) { this.attributes[name] = value; },
    scrollTo({ left, top }) { this.scrollLeft = left; this.scrollTop = top; },
    descendants() { return this.children.flatMap(child => [child, ...child.descendants()]); },
    contains(element) { return this.descendants().includes(element); },
    matches(selector) { return selector.split(',').some(value => {
      value = value.trim();
      if (value === '.trace-events') return this.className === 'trace-events';
      if (value === '.audience-stage pre') return this.tagName === 'PRE';
      if (value === '[data-scroll-key]') return Boolean(this.getAttribute('data-scroll-key'));
      if (value === 'details[open]') return this.tagName === 'DETAILS' && this.open;
      return this.tagName === value.toUpperCase();
    }); },
    querySelectorAll(selector) { return this.descendants().filter(child => child.matches(selector)); },
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
    focus(options) { document.activeElement = this; this.focusOptions = options; },
  });
  document.createElement = createElement;
  document.getElementById = () => createElement('div');
  document.body = createElement('body');
  const invoiceExperience = { audience() {
    const section = createElement('section'), list = createElement('ol'), detail = createElement('details');
    section.className = 'audience-stage'; list.className = 'trace-events';
    list.setAttribute('data-scroll-key', 'events'); detail.setAttribute('data-scroll-key', 'binding');
    const summary = createElement('summary'); summary.textContent = 'Selected event and binding';
    const raw = createElement('pre'); raw.setAttribute('data-scroll-key', 'raw');
    detail.append(summary, raw); section.append(list, detail); return section;
  } };
  const state = { invoiceEnabled: true, session: { status: 'authenticated', persona: 'operator' } };
  const context = { window, document, state, invoiceExperience, createIcon: () => createElement('svg'), refreshIcons() {}, setInterval() {} };
  const source = fs.readFileSync(require.resolve('../src/task_agent/console/static/invoice-workflow.js'), 'utf8');
  vm.runInNewContext(source.replace(/\}\)\(\);\s*$/, `
    identity = 'operator:first'; presenting = true; newInvestigation = true; newJobId = 'a'.repeat(32);
    demoSession = {state:'active',expires_at:new Date(Date.now()+14400000).toISOString()};
    job = { job_id: newJobId, kind: 'planning', state: 'running' };
    window.fixture = { root, render, update(values) {
      if (values.identity) identity = values.identity;
      if (values.runId) { newJobId = values.runId; job = { ...job, job_id: newJobId }; }
      if (values.state) job = { ...job, state: values.state };
      if (values.event) events.push(values.event);
      if (values.presenting !== undefined) presenting = values.presenting;
    } };
  })();`), context);
  const result = { ...window.fixture, window, document };
  result.render();
  return result;
}

test('same-run refresh preserves horizontal history, page scroll and evidence disclosure', () => {
  const result = fixture();
  const history = result.root.querySelector('.trace-events');
  history.scrollLeft = 740; history.scrollTop = 12;
  const detail = result.root.querySelector('details'); detail.open = true;
  const evidence = result.root.querySelector('.audience-stage pre'); evidence.scrollTop = 170; evidence.scrollLeft = 28;
  detail.querySelector('summary').focus();
  result.window.scrollX = 5; result.window.scrollY = 610;
  result.update({ event: { sequence: 1, source: 'workspace-controller', event_type: 'sandbox-bound' } });
  result.render();
  assert.notEqual(result.root.querySelector('.trace-events'), history);
  assert.equal(result.root.querySelector('.trace-events').scrollLeft, 740);
  assert.equal(result.root.querySelector('.trace-events').scrollTop, 12);
  assert.equal(result.root.querySelector('.audience-stage pre').scrollTop, 170);
  assert.equal(result.root.querySelector('.audience-stage pre').scrollLeft, 28);
  assert.equal(result.root.querySelector('details').open, true);
  assert.equal(result.document.activeElement, result.root.querySelector('summary'));
  assert.equal(result.document.activeElement.focusOptions.preventScroll, true);
  assert.equal(result.window.scrollY, 610);
  assert.equal(result.window.scrollX, 5);
});

test('later updates honor the newest user scroll and never force the newest event into view', () => {
  const result = fixture();
  for (const offset of [0, 1000, 350, 0]) {
    result.root.querySelector('.trace-events').scrollLeft = offset;
    result.render();
    assert.equal(result.root.querySelector('.trace-events').scrollLeft, offset);
  }
  result.root.querySelector('.trace-events').scrollLeft = 530;
  result.update({ state: 'finished' }); result.render();
  assert.equal(result.root.querySelector('.trace-events').scrollLeft, 530);
});

test('a different run or account does not inherit scroll or opened evidence', () => {
  for (const update of [{ runId: 'b'.repeat(32) }, { identity: 'operator:second' }]) {
    const result = fixture();
    result.root.querySelector('.trace-events').scrollLeft = 740;
    result.root.querySelector('details').open = true;
    result.update(update); result.render();
    assert.equal(result.root.querySelector('.trace-events').scrollLeft, 0);
    assert.notEqual(result.root.querySelector('details').open, true);
  }
});