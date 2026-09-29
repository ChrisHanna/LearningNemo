const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync(require.resolve('../src/task_agent/console/static/index.html'), 'utf8');
const script = fs.readFileSync(require.resolve('../src/task_agent/console/static/learningnemo.js'), 'utf8');
const css = fs.readFileSync(require.resolve('../src/task_agent/console/static/openai-theme.css'), 'utf8');

test('signed-out users receive a dedicated role-aware login experience', () => {
  assert.equal((html.match(/<head>/g) || []).length, 1);
  assert.equal((html.match(/<body>/g) || []).length, 1);
  assert.equal((html.match(/class="app-shell" hidden/g) || []).length, 1);
  assert.match(html, /id="loginExperience"[^>]*aria-labelledby="loginTitle"/);
  assert.ok(html.indexOf('id="loginExperience"') < html.indexOf('class="app-shell" hidden'));
  assert.match(html, /See what an AI agent can do, and where its authority stops\./);
  assert.match(html, /id="loginOperatorButton"/);
  assert.match(html, /id="loginApproverButton"/);
  assert.match(html, /id="loginContinueButton"[^>]*hidden/);
  assert.match(html, /network\s+allowlisted routes only/);
  assert.match(html, /authority\s+proposal is not approval/);
  assert.match(script, /elements\.loginExperience\.hidden = authenticated/);
  assert.match(script, /elements\.appShell\.hidden = !authenticated/);
  assert.match(script, /loginOperatorButton\.addEventListener\("click", \(\) => signInLocal\("operator"\)/);
  assert.match(script, /loginApproverButton\.addEventListener\("click", \(\) => signInLocal\("approver"\)/);
  assert.match(css, /\.login-experience\s*\{/);
  assert.match(css, /@media \(max-width: 600px\)/);
});