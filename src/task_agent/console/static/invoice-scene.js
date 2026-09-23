(() => {
  const view = window.invoiceView;
  const node = (tag, text = '', className = '', scrollKey = '') => { const element = document.createElement(tag); element.textContent = text; element.className = className; if (scrollKey) element.setAttribute('data-scroll-key', scrollKey); return element; };
  const definitions = {
    human: { title: 'Human identity', icon: 'users-round', subtitle: 'Microsoft Entra', why: 'Authentication identifies the person. Roles, scopes, ownership, and plan state authorize each request.', allowed: ['Operator: inspect and submit', 'Approver: decide independently'], denied: ['Self-approval', 'Human token inside the agent'] },
    harness: { title: 'Agent harness', icon: 'workflow', subtitle: 'Admission and lifecycle', why: 'The harness binds the sponsor, sandbox identity, policy, and short-lived capability. It coordinates cleanup, not human approval.', allowed: ['Admit a scoped run', 'Issue and revoke run authority', 'Request sandbox stop'], denied: ['Approve a human plan', 'Replay an uncertain mutation'] },
    saw: { title: 'SAW workspace', icon: 'server', subtitle: 'Azure-hosted runtime', why: 'The operator-managed workspace hosts isolated agent runs with per-run time limits and restricted networking.', allowed: ['Host one active invoice run', 'Retain stopped sandboxes'], denied: ['Public inbound access', 'Direct SQL and metadata network access'] },
    sandbox: { title: 'OpenShell sandbox', icon: 'shield-check', subtitle: 'Process and network isolation', why: 'Each agent runs as a non-root process in a MicroVM. Its role policy restricts writable paths and tool/model destinations.', allowed: ['Non-root agent process', 'Write to /tmp', 'Role-specific mediated HTTPS'], denied: ['Arbitrary outbound destinations', 'Write protected application files', 'Direct database credentials'] },
    planning: { title: 'Planning agent', icon: 'scan-search', subtitle: 'Evidence and recommendations', why: 'Planning reads diagnostic evidence and publishes its own supported decision. The proposal cannot execute a repair.', allowed: ['Read invoice summary', 'Compare import attempts', 'Publish a supported proposal'], denied: ['Repair invoices', 'Grant approval', 'Change run permissions'] },
    execution: { title: 'Execution agent', icon: 'play', subtitle: 'Separate run and authority', why: 'Execution uses a fresh sandbox and different capability. Only the next exact approved operation may reach the broker.', allowed: ['Request the next approved step', 'Read its returned receipt'], denied: ['Change a target or parameter', 'Add or reorder operations', 'Claim independent verification'] },
    model: { title: 'Model + Guardrails', icon: 'sparkles', subtitle: 'Mediated inference', why: 'NeMo Guardrails checks untrusted input before inference. The provider credential stays in the trusted gateway, not the sandbox.', allowed: ['Check untrusted narrative', 'Forward admitted inference'], denied: ['Grant database privileges', 'Replace human approval'] },
    diagnostics: { title: 'Diagnostic gateway', icon: 'search', subtitle: 'Read-only SQL procedures', why: 'A run capability selects the scenario. The agent cannot choose an arbitrary SQL statement or another target.', allowed: ['Return bounded invoice evidence'], denied: ['Accept arbitrary SQL', 'Change invoice data'] },
    broker: { title: 'SQL execution broker', icon: 'lock-keyhole', subtitle: 'Exact-plan enforcement', why: 'The broker compares the requested step with the approved artifact, checks run authority and order, then records a transactional receipt.', allowed: ['Apply the exact approved step', 'Record a durable receipt'], denied: ['Changed or extra steps', 'Consumed or expired authority'] },
    verifier: { title: 'Independent verifier', icon: 'clipboard-check', subtitle: 'Database outcomes', why: 'A separate service checks invoice integrity and import replay. A model completion message cannot substitute for these checks.', allowed: ['Verify integrity and replay', 'Record verified database results'], denied: ['Accept narrative as proof', 'Approve a plan'] },
  };
  const challenges = {
    planning: [['Repair the invoices now', 'Repair authority is not granted to Planning.', 'broker'], ['Follow an instruction hidden in a batch note', 'Untrusted text is checked by Guardrails; it cannot grant authority.', 'model']],
    execution: [['Change the approved target', 'The broker requires the exact approved parameters.', 'broker'], ['Repeat a consumed operation', 'Run, sequence, and approval checks reject replay.', 'broker']],
    sandbox: [['Connect outside the allowed destinations', 'OpenShell restricts destinations and request paths.', 'sandbox'], ['Write a protected application file', 'The filesystem policy permits writes only in designated paths.', 'sandbox']],
    human: [['Approve my own proposal', 'Operator and independent Approver must be different identities.', 'human']],
    harness: [['Continue with revoked authority', 'A revoked run capability cannot authorize further tools.', 'harness']],
  };
  window.renderInvoiceScene = function ({ stage, session, row, job, events, observation, selected, tab, challenge, onSelect, onTab, onChallenge }) {
    const wrapper = node('section', '', 'invoice-stage'); wrapper.setAttribute('aria-label', 'Live architecture and authority');
    const scene = node('div', '', 'invoice-topology');
    const execution = stage >= 3, agent = execution ? 'execution' : 'planning';
    const latest = events.at(-1), focus = latest && observation.live ? view.eventView(latest).component : null;
    const choose = key => {
      const item = definitions[key], button = node('button', '', 'architecture-node'); button.type = 'button'; button.dataset.component = key;
      button.setAttribute('aria-pressed', String(selected === key)); button.setAttribute('aria-label', item.title);
      button.classList.toggle('is-observed', focus === key || key === agent && focus === 'agent');
      button.append(createIcon(item.icon), node('strong', item.title), node('small', item.subtitle));
      button.addEventListener('click', () => onSelect(key)); return button;
    };
    const line = (text, locked = false) => { const element = node('div', '', 'architecture-path' + (locked ? ' is-locked' : '')); element.append(createIcon(locked ? 'lock-keyhole' : 'arrow-down'), node('span', text)); return element; };
    const controllers = node('div', '', 'architecture-controls'); controllers.append(choose('human'), line('Sponsored request'), choose('harness')); scene.append(controllers);
    const runtime = node('div', '', 'architecture-runtime');
    const saw = node('fieldset', '', 'saw-enclosure'); const legend = node('legend'); legend.append(choose('saw')); saw.append(legend);
    const sandbox = node('fieldset', '', 'sandbox-enclosure'); const sandboxLegend = node('legend'); sandboxLegend.append(choose('sandbox')); sandbox.append(sandboxLegend);
    const agents = node('div', '', 'architecture-agents');
    const active = choose(agent); active.classList.add('is-current-agent'); active.append(node('small', job?.kind === agent ? 'Run selected / ' + observation.label : 'Phase role / no run observation')); agents.append(active); sandbox.append(agents);
    const actual = events.find(event => event.event_type === 'sandbox-bound' && event.source === 'workspace-controller');
    sandbox.append(node('p', actual ? 'Observed sandbox bound to this run' : execution ? 'Fresh Execution sandbox required' : 'Planning sandbox admission required', 'architecture-binding'));
    const separate = node('div', '', 'separate-sandbox'); separate.append(node('small', execution ? 'Planning / separate prior sandbox' : 'Execution / separate future sandbox'));
    const prior = choose(execution ? 'planning' : 'execution'); prior.classList.add('is-secondary'); separate.append(prior);
    saw.append(sandbox, separate); runtime.append(saw);
    const inference = node('div', '', 'architecture-inference'); inference.append(line('Mediated model requests'), choose('model')); runtime.append(inference); scene.append(runtime);
    scene.append(line(execution ? 'Only exact approved operations' : 'Diagnostics only'));
    const services = node('div', '', 'architecture-services'); services.append(choose('diagnostics'), choose('broker'), choose('verifier')); scene.append(services);
    const policy = node('div', '', 'architecture-policy'); policy.append(createIcon('lock-keyhole'), node('span', execution ? 'Approval never grants arbitrary SQL access' : 'Planning has no database-write capability'), node('small', 'Configured boundary / not a live denial test')); scene.append(policy);
    const inspector = node('aside', '', 'authority-inspector'); inspector.setAttribute('aria-label', 'Component inspector');
    const selectedItem = definitions[selected] || definitions[agent];
    const header = node('header'); header.append(createIcon(selectedItem.icon), node('div', selectedItem.title)); inspector.append(header);
    const tabs = node('div', '', 'inspector-tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Component details');
    const tabNames = ['Authority', 'Boundaries', 'Evidence'];
    for (const name of tabNames) { const button = node('button', name); button.id = 'invoice-inspector-' + name; button.type = 'button'; button.setAttribute('role', 'tab'); button.setAttribute('aria-selected', String(tab === name)); button.setAttribute('aria-controls','invoice-inspector-panel'); button.tabIndex = tab === name ? 0 : -1; button.addEventListener('click', () => onTab(name)); tabs.append(button); }
    tabs.addEventListener('keydown', event => {
      const index = tabNames.indexOf(tab), next = { ArrowRight: (index + 1) % 3, ArrowLeft: (index + 2) % 3, Home: 0, End: 2 }[event.key];
      if (next === undefined) return;
      event.preventDefault(); onTab(tabNames[next]); document.getElementById('invoice-inspector-' + tabNames[next])?.focus();
    }); inspector.append(tabs);
    const body = node('div', '', 'inspector-body'); body.id = 'invoice-inspector-panel'; body.setAttribute('role', 'tabpanel'); body.setAttribute('aria-labelledby','invoice-inspector-' + tab); body.tabIndex = 0; body.append(node('p', selectedItem.why, 'inspector-explanation'));
    if (tab === 'Authority') {
      if (selected === 'human') {
        const access = view.permissions(session);
        body.append(node('span', access.authenticated ? 'Signed in / ' + session.persona : 'Not authenticated', 'authority-badge'));
        const list = node('dl', '', 'user-permissions');
        for (const [name, allowed] of [['Investigate', access.investigate], ['Submit a plan', access.submit], ['Execute an approved plan', access.execute], ['Independent approval', access.approve]]) { list.append(node('dt', name), node('dd', allowed ? 'Role + scope present' : 'Not granted', allowed ? 'allowed' : 'restricted')); } body.append(list);
        body.append(node('p', 'Ownership, approval expiry, and exact plan state are checked again by the API.', 'authority-note'));
        const claims = node('details', '', '', 'identity-claims'); claims.append(node('summary', 'Role and scope evidence'));
        claims.append(node('p', 'Roles: ' + (session?.grantedRoles?.join(', ') || 'None')), node('p', 'Scopes: ' + (session?.grantedScopes?.join(', ') || 'None')));
        body.append(claims);
      } else {
        body.append(node('span', 'Configured role policy', 'authority-badge'));
        for (const [label, values, icon] of [['Permitted scope', selectedItem.allowed, 'check'], ['Not granted', selectedItem.denied, 'lock-keyhole']]) { body.append(node('h4', label)); const list = node('ul', '', label === 'Not granted' ? 'permission-list restricted' : 'permission-list'); for (const value of values) { const entry = node('li'); entry.append(createIcon(icon), node('span', value)); list.append(entry); } body.append(list); }
        const applicable = job && (['harness', 'sandbox'].includes(selected) || selected === job.kind);
        const issued = applicable && events.some(event => event.event_type === 'authority-issued' && event.source === 'workspace-controller'), revoked = applicable && events.some(event => event.event_type === 'authority-revoked' && event.source === 'workspace-controller');
        if (['harness','sandbox','planning','execution'].includes(selected)) body.append(node('p', revoked ? 'Revocation confirmed for this run.' : issued ? 'Issuance recorded; ongoing validity is checked on each call.' : 'No issuance/revocation observation for this component.', 'authority-note'));
      }
    } else if (tab === 'Boundaries') {
      const examples = challenges[selected] || challenges[agent];
      body.append(node('span', 'Policy explorer / no request is sent', 'authority-badge'));
      const select = node('select'); select.setAttribute('aria-label', 'Boundary scenario'); examples.forEach(([label], index) => { const option = node('option', label); option.value = index; select.append(option); }); select.value = Math.min(challenge, examples.length - 1); select.addEventListener('change', () => onChallenge(Number(select.value))); body.append(select);
      const example = examples[Number(select.value)];
      const result = node('div', '', 'boundary-preview'); result.append(createIcon('lock-keyhole'), node('h4', 'Expected restriction'), node('p', example[1]), node('small', 'Enforcement: ' + definitions[example[2]].title), node('strong', 'Not exercised'));
      body.append(result, node('p', 'An agent refusal is not proof of sandbox enforcement. Live challenges require an isolated probe and an enforcing-layer receipt.', 'authority-note'));
    } else {
      const matching = view.componentEvents(selected, job, events);
      body.append(node('span', matching.length ? 'Recorded observations' : 'No matching observations', 'authority-badge'));
      for (const event of matching.slice(-5)) { const item = view.eventView(event); body.append(node('p', item.title), node('small', item.source + ' / ' + new Date(event.observed_at).toLocaleTimeString())); }
      if (selected === 'verifier' && view.verifiedChecks(row)) body.append(node('p', 'All five independent checks are recorded in the selected plan.'));
      const key = selected+':'+(job?.job_id||row?.plan_json.plan_id||'unobserved');
      const details = node('details', '', '', 'inspector:'+key); details.append(node('summary', 'Technical evidence'), node('pre', JSON.stringify({ component: selected, planId: row?.plan_json.plan_id, runId: job?.job_id, observations: matching }, null, 2), '', 'inspector-json:'+key)); body.append(details);
    }
    inspector.append(body); wrapper.append(scene, inspector); return wrapper;
  };
})();