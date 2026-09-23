(() => {
  const find = id => document.getElementById(id);
  const stages = [
    ['Analyze', 'scan-search', 'Analyze the database', 'Current query version and observed query states.', 'Read-only diagnostics. No query starts, process cancellation, or database configuration changes.'],
    ['Propose', 'file-pen-line', 'Propose an exact change', 'A draft bound to the evidence, target, and query version.', 'A proposal does not execute a change. Active queries are never cancelled by analysis.'],
    ['Approve', 'file-check-2', 'Independent approval', 'A different person reviews the exact plan and its expiry.', 'The sponsor cannot approve their own proposal. Approval is not execution.'],
    ['Execute', 'workflow', 'Execute the approved operation', 'One approved plan, one registered change through the trusted broker.', 'No arbitrary SQL or process-killing authority. Lost responses require reconciliation, not a repeated write.'],
    ['Verify', 'clipboard-check', 'Verify the database outcome', 'Independent checks and an explicit completion acknowledgement.', 'Completion requires recorded verification, not a successful-looking response.'],
  ];
  let step = 0, analysis = null, incident = null, review = null, execution = null;
  let generation = 0, identity = null, analysisBusy = false, diagnosticBusy = false;
  const operations = new Set();
  const context = () => ({ step, session: state.session, analysis, incident, review, execution, busy: analysisBusy || operations.size > 0 });
  const identityParent = find('identityWorkspace').parentElement;
  const node = (tag, text, className = '') => { const element = document.createElement(tag); element.textContent = text; element.className = className; return element; };
  const analysisPanel = node('section', '', 'analysis-panel'); analysisPanel.id = 'analysisPanel'; find('missionTools').append(analysisPanel);
  document.querySelector('.mission-boundary').hidden = true;
  const fact = (label, value) => { const row = node('div', ''); row.append(node('dt', label), node('dd', value)); find('missionFacts').append(row); };
  function renderAnalysis() {
    analysisPanel.replaceChildren(); const receipt = analysis?.receipt; if (!receipt) return;
    const refresh = node('button', '', 'icon-button'); refresh.type = 'button'; refresh.title = 'Refresh read-only analysis'; refresh.setAttribute('aria-label', refresh.title);
    refresh.append(createIcon('refresh-cw')); refresh.disabled = context().busy; refresh.addEventListener('click', () => analyze(true));
    const header = node('header', ''); header.append(node('h3', 'Diagnostic snapshot'), refresh); analysisPanel.append(header);
    analysisPanel.append(node('p', `Active query version: ${receipt.snapshot.active_query_version || 'Not set'}`));
    const entries = Object.entries(receipt.snapshot.query_run_states);
    if (entries.length) {
      const table = node('table', '', 'analysis-table'), head = node('tr', ''); head.append(node('th', 'Observed query'), node('th', 'State')); table.append(head);
      for (const [run, status] of entries.slice(0, 100)) { const row = node('tr', ''); row.append(node('td', run), node('td', status)); table.append(row); }
      analysisPanel.append(table);
    }
    analysisPanel.append(node('p', entries.length ? `${entries.length} query records observed${entries.length > 100 ? ' / first 100 shown' : ''}.` : 'No query records returned.'));
  }
  function render() {
    if (state.invoiceEnabled) return;
    const stage = stages[step], action = window.incidentFlow.nextAction(context()), allowed = window.incidentFlow.availableSteps(context());
    find('missionRail').replaceChildren(...stages.map((item, index) => {
      const button = node('button', ''); button.type = 'button'; button.id = `mission-step-${index}`;
      button.setAttribute('role', 'tab'); button.setAttribute('aria-selected', String(index === step)); button.setAttribute('aria-controls', 'missionStepPanel');
      button.tabIndex = index === step ? 0 : -1; button.disabled = !allowed.includes(index) || context().busy;
      button.append(createIcon(item[1]), node('strong', `${index + 1}. ${item[0]}`), node('small', index === step ? 'Current step' : button.disabled ? 'Waiting' : 'Available'));
      button.addEventListener('click', () => select(index)); return button;
    }));
    find('missionStepPanel').setAttribute('aria-labelledby', `mission-step-${step}`); find('missionStepNumber').textContent = `${String(step + 1).padStart(2, '0')} / 05`;
    find('missionStepTitle').textContent = stage[2]; find('missionStepDescription').textContent = stage[3]; find('missionBoundaryText').textContent = stage[4];
    const button = find('missionAction'); button.querySelector('span').textContent = action.label; button.disabled = action.disabled; button.hidden = action.id === 'plan'; button.firstElementChild.replaceWith(createIcon(stage[1]));
    find('missionStepState').textContent = context().busy ? 'Request in progress' : action.disabled ? 'Awaiting prerequisite' : 'Ready';
    find('missionActionStatus').textContent = action.message; find('sessionNextAction').textContent = action.message;
    find('missionRunState').textContent = incident?.planId ? `${incident.taskId} / ${incident.planState}` : analysis?.receipt ? 'Analysis recorded' : 'Awaiting analysis';
    const receipt = step >= 3 ? execution?.record : step <= 1 ? analysis?.receipt : null;
    find('missionEvidenceKind').textContent = receipt ? 'Recorded evidence' : 'No action completed'; find('missionFacts').replaceChildren();
    fact('Current account', state.session?.status === 'authenticated' ? `${state.session.persona} / ${state.session.accountFingerprint}` : 'Not signed in');
    if (step <= 1 && analysis?.receipt) { fact('Source', 'Azure SQL / read-only diagnostic service'); fact('Observed at', analysis.receipt.observed_at); fact('Workload changes', 'None'); fact('Queries cancelled', 'None'); fact('Evidence hash', analysis.receipt.evidence_hash); }
    if (incident?.planId) { fact('Plan', incident.planId); fact('Plan hash', incident.planHash); }
    if (step === 2) fact('Review service', review?.connected ? 'Connected' : review?.error ? 'Unavailable' : 'Not checked');
    if (step >= 3 && execution?.record) { fact('Execution', execution.record.state); for (const [name, value] of Object.entries(execution.record.verification?.checks || {})) fact(name.replaceAll('_', ' '), value ? 'Passed' : 'Failed'); }
    find('missionVerdict').textContent = step <= 1 ? analysis?.receipt ? 'Diagnostics retrieved; no workload changed' : analysis?.error ? 'Analysis unavailable' : 'Analysis not run' : step === 2 ? 'Independent decision required' : execution?.record?.state === 'completed' ? 'Incident completed' : execution?.record?.state === 'verification' ? 'Recovery independently verified' : 'No verified execution result';
    const verified = step <= 1 ? Boolean(analysis?.receipt) : step >= 3 && ['verification', 'completed'].includes(execution?.record?.state);
    find('missionVerdict').className = 'mission-verdict' + (verified ? ' is-passed' : ''); find('missionClaim').textContent = stage[4];
    find('missionReceipt').parentElement.hidden = !receipt; find('missionReceipt').textContent = receipt ? JSON.stringify(receipt, null, 2) : '';
    renderAnalysis(); refreshIcons();
  }
  function select(index) {
    if (!window.incidentFlow.availableSteps(context()).includes(index)) return;
    step = index; find('identityWorkspace').hidden = true; identityParent.append(find('identityWorkspace')); analysisPanel.hidden = step !== 0;
    if ([1, 2].includes(step)) { find('missionTools').append(find('identityWorkspace')); find('identityWorkspace').hidden = false; }
    render();
  }
  async function analyze(refresh) {
    if (state.session?.persona !== 'operator' || analysisBusy) return;
    const current = generation; analysisBusy = true; render();
    try { const result = await api('/api/analysis', refresh ? { method: 'POST' } : {}); if (current !== generation) return;
      if (!result.available || (refresh && !result.analysis)) throw new Error('Diagnostic receipt unavailable'); analysis = { receipt: result.analysis, error: '' };
    } catch (error) { if (current === generation) analysis = { receipt: null, error: error.message }; }
    finally { if (current === generation) { analysisBusy = false; render(); } }
  }
  async function propose() {
    if (!analysis?.receipt || analysisBusy) return;
    const current = generation; analysisBusy = true; render();
    try { const receipt = analysis.receipt; const result = await api('/api/analysis/propose', { method: 'POST', body: JSON.stringify({ analysis_id: receipt.analysis_id, evidence_hash: receipt.evidence_hash }) }); if (current === generation) window.dispatchEvent(new CustomEvent('console-incident-proposed', { detail: { planId: result.planId } })); }
    catch (error) { if (current === generation) analysis.error = error.message; }
    finally { if (current === generation) { analysisBusy = false; render(); } }
  }
  const panels = ['liveWorkspace', 'patternWorkspace', 'showcaseWorkspace', 'buildWorkspace', 'missionAgentCheck'];
  function evidence(panel = 'liveWorkspace') { for (const id of panels) { find(id).hidden = id !== panel; find('missionDrawerBody').append(find(id)); } find('missionEvidenceSource').value = panel; if (!find('missionEvidenceDrawer').open) find('missionEvidenceDrawer').showModal(); }
  window.missionNavigate = tab => { const technical = { liveViewTab: 'liveWorkspace', patternViewTab: 'patternWorkspace', workspaceViewTab: 'showcaseWorkspace', buildViewTab: 'buildWorkspace' }; if (technical[tab]) return evidence(technical[tab]); select(state.session?.persona === 'approver' ? 2 : 0); };
  find('missionAction').addEventListener('click', () => {
    const action = window.incidentFlow.nextAction(context()); if (action.disabled) return;
    const navigation = { 'analyze-step': 0, 'propose-step': 1, 'approve-step': 2, 'execute-step': 3, 'verify-step': 4 };
    const events = { 'execution-status': 'console-execution-requested', execute: 'console-execute-approved', reconcile: 'console-reconcile-execution', complete: 'console-complete-execution' };
    if (action.id in navigation) select(navigation[action.id]); else if (action.id in events) window.dispatchEvent(new Event(events[action.id]));
    else if (action.id === 'signin') find('sessionSignIn').click(); else if (action.id === 'switch') find('sessionSwitch').click();
    else if (action.id === 'analyze') analyze(true); else if (action.id === 'propose') propose();
    else if (action.id === 'reviews') find('approverRefresh').click(); else if (action.id === 'incidents') find('incidentRefresh').click();
    else if (action.id === 'authorize') authenticate(true).catch(error => toast(error.message, 'error'));
  });
  find('missionEvidenceOpen').addEventListener('click', () => evidence()); find('missionEvidenceClose').addEventListener('click', () => find('missionEvidenceDrawer').close());
  find('missionEvidenceSource').addEventListener('change', event => evidence(event.target.value));
  find('missionAgentRun').addEventListener('click', async () => {
    if (diagnosticBusy || !['reader', 'operator'].includes(state.session?.persona)) return;
    const current = generation; diagnosticBusy = true; find('missionAgentRun').disabled = true;
    try { const result = await api('/api/agent-check', { method: 'POST' }); if (current === generation) find('missionAgentResult').textContent = result.content; }
    catch (error) { if (current === generation) find('missionAgentResult').textContent = error.message; }
    finally { diagnosticBusy = false; find('missionAgentRun').disabled = false; }
  });
  find('missionRail').addEventListener('keydown', event => { const allowed = window.incidentFlow.availableSteps(context()), position = allowed.indexOf(step); const target = { ArrowRight: allowed[Math.min(position + 1, allowed.length - 1)], ArrowLeft: allowed[Math.max(position - 1, 0)], Home: allowed[0], End: allowed.at(-1) }; if (event.key in target && !context().busy) { event.preventDefault(); select(target[event.key]); find(`mission-step-${step}`).focus(); } });
  for (const [name, assign] of [['incident', value => { incident = value; }], ['review', value => { review = value; }], ['execution', value => { execution = value; }]]) window.addEventListener(`console-${name}-changed`, event => { assign(event.detail); render(); });
  window.addEventListener('console-operation-changed', event => { if (event.detail.busy) operations.add(event.detail.id); else operations.delete(event.detail.id); render(); });
  function sessionChanged() { if (state.invoiceEnabled) { identity = null; generation += 1; return; } const next = state.session?.status === 'authenticated' ? `${state.session.persona}:${state.session.accountFingerprint}` : null; if (next !== identity) { identity = next; generation += 1; analysis = incident = review = execution = null; analysisBusy = false; find('missionAgentResult').textContent = 'No agent diagnostic requested.'; select(state.session?.persona === 'approver' ? 2 : 0); } render(); }
  window.addEventListener('console-session-changed', sessionChanged); sessionChanged();
})();