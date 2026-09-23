const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

test('inspector supports named tabs, roving focus, arrows, Home and End', () => {
  const nodes = [];
  const document = { createElement(tag) {
    const element = { tag, children: [], attributes: {}, handlers: {}, dataset: {}, classList: { add() {}, toggle() {} },
      append(...children) { this.children.push(...children); }, setAttribute(name, value) { this.attributes[name] = value; },
      addEventListener(name, handler) { this.handlers[name] = handler; }, focus() { this.focused = true; } };
    nodes.push(element); return element;
  }, getElementById(id) { return nodes.find(node => node.id === id); } };
  const selected = [];
  const window = { invoiceView: require('../src/task_agent/console/static/invoice-view.js') };
  vm.runInNewContext(fs.readFileSync(require.resolve('../src/task_agent/console/static/invoice-scene.js'), 'utf8'), { window, document, createIcon: () => document.createElement('svg') });
  window.renderInvoiceScene({ stage: 0, session: null, row: null, job: null, events: [], observation: {}, selected: 'planning', tab: 'Authority', challenge: 0, onTab: value => selected.push(value) });
  const tabs = nodes.filter(node => node.attributes.role === 'tab');
  assert.deepEqual(tabs.map(tab => tab.tabIndex), [0, -1, -1]);
  assert.ok(tabs.every(tab => tab.attributes['aria-controls'] === 'invoice-inspector-panel'));
  assert.equal(document.getElementById('invoice-inspector-panel').attributes['aria-labelledby'], tabs[0].id);
  const list = nodes.find(node => node.attributes.role === 'tablist');
  for (const key of ['ArrowRight', 'ArrowLeft', 'End', 'Home']) list.handlers.keydown({ key, preventDefault() {} });
  assert.deepEqual(selected, ['Boundaries', 'Evidence', 'Evidence', 'Authority']);
  assert.ok(tabs.every(tab => tab.focused));
});