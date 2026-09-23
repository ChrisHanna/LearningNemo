(() => {
  const view = window.invoiceView;
  const node = (tag, text = '', className = '', scrollKey = '') => { const element = document.createElement(tag); element.textContent = text; element.className = className; if (scrollKey) element.setAttribute('data-scroll-key', scrollKey); return element; };
  const value = (amount, money) => amount === null ? 'Not observed' : money ? (amount / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : amount.toLocaleString();
  const recordedTime = timestamp => Number.isFinite(Date.parse(timestamp)) ? new Date(timestamp).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' }) : null;
  function outcomes(row, before, observation) {
    const model = view.businessOutcome(row, before, observation), section = node('section', '', 'outcome-strip');
    section.setAttribute('aria-label', 'Invoice outcomes');
    const heading = node('div', '', 'outcome-heading');
    heading.append(node('span', 'RECORDED INVOICE FINDINGS', 'eyebrow'), node('strong', model.finding), node('span', model.repairStatus, model.verified ? 'verified-label' : ''));
    const timestamp = recordedTime(model.observedAt);
    if (timestamp) heading.append(node('time', model.source + ' / ' + timestamp));
    section.append(heading);
    const metrics = node('dl', '', 'outcome-metrics');
    for (const metric of model.metrics) {
      const entry = node('div'); entry.append(node('dt', metric.label + (metric.money ? ' / currency units' : '')));
      const values = node('dd', '', model.compare ? 'metric-comparison' : '');
      if (model.compare) {
        const baseline = node('span', '', 'metric-baseline'); baseline.append(node('small', 'Planning baseline'), node('strong', value(metric.before, metric.money))); values.append(baseline);
        const current = node('span', '', 'metric-current'); current.append(node('small', 'Latest database read'), node('strong', value(metric.after, metric.money))); values.append(createIcon('arrow-right'), current);
      } else {
        const reading = node('span', '', 'metric-current'); reading.append(node('strong', value(metric.after ?? metric.before, metric.money))); values.append(reading);
      }
      entry.append(values);
      if (metric.label === 'Active invoices' && model.orders !== null) entry.append(node('small', model.orders + ' source orders'));
      if (metric.money && model.expected !== null) {
        entry.append(node('small', 'Order total: ' + value(model.expected, true)));
        if (model.difference !== null) entry.append(node('small', model.difference === 0 ? 'Matches the order total' : (model.difference > 0 ? 'Overstated by ' : 'Understated by ') + value(Math.abs(model.difference), true)));
      }
      metrics.append(entry);
    }
    section.append(metrics);
    if (row?.scenario_expires_at && Date.parse(row.scenario_expires_at) <= Date.now()) section.append(node('p', 'Scenario expired. These are retained findings, not a live database reading.', 'authority-note'));
    return section;
  }
  function changes(row, before, receipts = []) {
    const section = node('section', '', 'change-review'); section.setAttribute('aria-label', 'Exact change review');
    section.append(node('h3', row.plan_json.diagnosis), node('p', row.plan_json.rationale, 'change-rationale'));
    const heading = node('div', '', 'change-review-heading'); heading.append(node('strong', 'Change scope'), node('span', row.state === 'draft' ? 'Proposed / not applied' : row.state === 'submitted' ? 'Awaiting independent review' : ['approved','executing'].includes(row.state) ? 'Approved artifact' : 'Recorded artifact')); section.append(heading);
    const list = node('ol', '', 'change-list');
    for (const item of view.changeReview(row, before)) {
      const entry = node('li'); entry.append(createIcon(view.operation(item.operation).icon));
      const content = node('div'); content.append(node('strong', item.title), node('p', item.change), node('small', item.preserved));
      content.append(node('span', view.verifiedChecks(row) ? 'Independently verified' : receipts.includes(item.step_id) ? 'SQL receipt recorded' : ['executing','verified','completed'].includes(row.state) ? 'No SQL receipt observed' : 'Not executed', 'change-proof'));
      const details = node('details', '', '', 'change-scope:'+row.plan_json.plan_id+':'+item.step_id); details.append(node('summary', item.scope + ' / revision ' + item.expected_revision + ' to ' + (item.expected_revision + 1)), node('code', item.target));
      if (item.duplicate_set_hash) details.append(node('small', 'Exact duplicate-set hash'), node('code', item.duplicate_set_hash));
      content.append(details); entry.append(content); list.append(entry);
    }
    section.append(list);
    const risk = node('div', '', 'review-risk'); risk.append(node('strong', 'Risks')); const risks = node('ul');
    for (const text of row.plan_json.risks) risks.append(node('li', text));
    if (!row.plan_json.risks.length) risks.append(node('li', 'No risks recorded by the Planning agent'));
    risk.append(risks); section.append(risk);
    const artifact = node('details', '', '', 'plan-artifact:'+row.plan_json.plan_id); artifact.append(node('summary', 'Exact plan and integrity hashes'), node('pre', JSON.stringify({ plan: row.plan_json, plan_hash: row.plan_hash }, null, 2), '', 'plan-json:'+row.plan_json.plan_id)); section.append(artifact);
    return section;
  }
  function trace({ job, events, row, stage, sequence, onSelect }) {
    const section = node('section', '', 'request-trace'); section.setAttribute('aria-label', 'Request and enforcement evidence');
    section.append(node('h2', 'Request & proof'));
    const bounded = view.boundEvents(job, events, row, stage);
    if (!bounded.length) { section.append(node('p', 'No events recorded for this run.', 'evidence-empty')); return section; }
    const selected = bounded.find(event => event.sequence === sequence) || bounded.at(-1);
    const detail = view.traceDetail(job, events, row, stage, selected.sequence);
    const chain = node('ol', '', 'proof-chain');
    for (const [label, text, icon] of [['Recorded action', detail.title, 'activity'], ['Run authority', detail.authority, 'key-round'], ['Boundary', detail.boundary, 'shield-check'], ['Independent proof', detail.proof, 'file-check-2']]) {
      const item = node('li'); item.append(createIcon(icon), node('small', label), node('strong', text)); chain.append(item);
    }
    section.append(chain, node('p', detail.source + ' / ' + detail.correlation, 'evidence-provenance'));
    const list = node('ol', '', 'trace-events', 'events:'+job.job_id); list.setAttribute('aria-label', 'Recorded run events');
    for (const event of bounded) {
      const item = node('li'), button = node('button', '', 'trace-event'); button.type = 'button'; button.dataset.sequence = event.sequence;
      button.setAttribute('aria-pressed', String(selected.sequence === event.sequence));
      const info = view.eventView(event); button.append(node('time', new Date(event.observed_at).toLocaleTimeString()), node('strong', info.title), node('small', info.source));
      button.addEventListener('click', () => onSelect(event.sequence)); item.append(button); list.append(item);
    }
    section.append(list);
    const raw = node('details', '', '', 'event-binding:'+job.job_id); raw.append(node('summary', 'Selected event and binding'), node('pre', JSON.stringify({ run_id: detail.runId, plan_hash: detail.planHash, event: detail.event }, null, 2), '', 'event-json:'+job.job_id)); section.append(raw);
    return section;
  }
  function audience({ row, before, observation, job, events, stage, sequence, onSelect, probe, probeKind, mode, runObservation }) {
    const section = node('section', '', 'audience-stage'); section.setAttribute('aria-label', 'Read-only audience view');
    if (mode === 'challenge' && job) {
      section.append(node('p','SELECTED AGENT SANDBOX','eyebrow'),window.renderInvoiceSandboxTest({job,events,readOnly:true}));
      return section;
    }
    if (mode === 'challenge') {
      const proof = view.challengeProof(probe || {kind:probeKind}); section.append(node('p', 'CONTROLLED SANDBOX PROBE', 'eyebrow'), node('h2', probe ? proof.title : 'No challenge run'));
      const chain = node('ol', '', 'audience-challenge-chain');
      for (const [label, text] of [['Attempt', view.tools[proof.forbidden]], ['Boundary', 'OpenShell route policy'], ['Verdict', proof.confirmed ? 'Denied with evidence' : 'Not confirmed'], ['Proof', proof.source]]) { const item=node('li'); item.append(node('small',label),node('strong',text));chain.append(item); }
      section.append(chain, node('p', 'No model or database capability issued', 'audience-source'));
      if (probe?.result?.denial_evidence?.length) { const receipt=node('details','','','audience-probe:'+probe.challenge_id);receipt.append(node('summary','Recorded OpenShell receipt'),node('pre',probe.result.denial_evidence.join('\n'),'','audience-probe-log:'+probe.challenge_id));section.append(receipt); }
      return section;
    }
    section.append(node('p', ['ANALYZE','PROPOSE','APPROVE','EXECUTE','VERIFY'][stage], 'eyebrow'), node('h2', stage === 4 && view.verifiedChecks(row) ? 'Invoice integrity independently verified' : row?.plan_json.diagnosis || 'Awaiting an investigation'));
    section.append(outcomes(row, before, observation));
    const state = node('div', '', 'audience-run-state'); state.dataset.runId = job?.job_id || ''; state.setAttribute('role','status'); state.append(node('strong', runObservation.title), node('span', runObservation.label)); section.append(state);
    section.append(trace({ job, events, row, stage, sequence, onSelect }));
    if (job?.sandbox_test || job?.result?.sandbox_test || job?.sandbox_test_requested) section.append(window.renderInvoiceSandboxTest({job,events,readOnly:true}));
    if (view.verifiedChecks(row)) { const checks=node('ul','','audience-checks');for(const title of Object.values(view.checks)){const entry=node('li');entry.append(createIcon('check'),node('span',title));checks.append(entry);}section.append(checks); }
    return section;
  }
  window.invoiceExperience = { outcomes, changes, trace, audience };
})();