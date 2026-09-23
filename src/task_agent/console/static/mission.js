(() => {
  const find = id => document.getElementById(id);
  const stages = [
    { name: 'Sponsor', icon: 'user-round-check', title: 'A human sponsors the work', description: 'The verified caller establishes the task authority. The agent cannot choose its own role.', boundary: 'Entra scopes and app roles are checked server-side. An Approver cannot act as the Operator.', nodes: ['human'], action: 'Sign in as sponsor' },
    { name: 'Contain', icon: 'shield-check', title: 'Useful access. A hard boundary.', description: 'Run the same endpoint through two methods. OpenShell permits the diagnostic read and rejects the write.', boundary: 'SAW contains the host. OpenShell constrains the process, filesystem, and outbound method and path. Credentials stay outside the sandbox.', nodes: ['saw', 'gateway', 'planning', 'probe'], action: 'Run containment proof' },
    { name: 'Investigate', icon: 'scan-search', title: 'A diagnosis becomes an exact plan', description: 'The trusted investigation binds the sponsor, owned query, diagnostic evidence, and proposed change into one persisted record.', boundary: 'This fixed investigation is controller-run, not a sandbox model response. Only an independent reviewer can authorize the proposed remediation.', nodes: ['trusted'], action: 'Start investigation' },
    { name: 'Review', icon: 'file-check-2', title: 'A different person authorizes the change', description: 'Inspect the exact plan hash, target, parameters, and deadline. Approval binds this plan, not future model output.', boundary: 'The sponsor cannot approve their own plan. Approval does not execute the change.', nodes: ['human', 'trusted'], action: 'Open review queue' },
    { name: 'Execute', icon: 'workflow', title: 'One approval. One bounded operation.', description: 'The execution broker must consume a valid approval before a dedicated workload identity can apply the fixed operation.', boundary: 'The agent never receives SQL credentials. A replay, changed plan, or expired approval must fail closed.', nodes: ['execution', 'trusted'], action: 'Execution not connected' },
    { name: 'Verify', icon: 'clipboard-check', title: 'Prove the outcome independently', description: 'An independent verifier checks the actual result. Completion requires persisted evidence and an explicit human acknowledgement.', boundary: 'A successful tool response or an approved plan is not proof of recovery.', nodes: ['trusted'], action: 'Verification not connected' },
  ];
  let selected = 0;
  let workspace = null;
  let incident = null;
  let review = null;
  let execution = null;
  let identityKey = null;
  let agentBusy = false;
  let agentGeneration = 0;
  let renderKey = '';
  let command = null;
  const operations = new Set();
  const flowContext = () => ({ step: selected, session: state.session, workspace, incident, review, execution, busy: agentBusy || operations.size > 0 });
  const originalParents = new Map();
  for (const id of ['liveWorkspace', 'identityWorkspace', 'patternWorkspace', 'showcaseWorkspace', 'buildWorkspace']) originalParents.set(id, find(id).parentElement);
  const signedIn = () => state.session?.status === 'authenticated';
  const appendFact = (label, value) => {
    const row = document.createElement('div');
    const term = document.createElement('dt');
    const detail = document.createElement('dd');
    term.textContent = label;
    detail.textContent = value;
    row.append(term, detail);
    find('missionFacts').append(row);
  };
  function render() {
    const focusedStep = find('missionRail').contains(document.activeElement) ? document.activeElement.id : null;
    const stage = stages[selected];
    const role = signedIn() ? state.session.persona : null;
    const proof = workspace?.result;
    const receipt = proof?.receipt;
    const progress = window.missionFlow.progressLabels(flowContext());
    find('missionRunState').textContent = progress.title;
    find('missionAgentCheck').hidden = !find('missionEvidenceDrawer').open || find('missionEvidenceSource').value !== 'missionAgentCheck';
    find('missionAgentRun').disabled = agentBusy || !['operator', 'reader'].includes(role);
    find('missionRail').replaceChildren(...stages.map((item, index) => {
      const button = document.createElement('button');
      button.type = 'button'; button.id = `mission-step-${index}`;
      button.setAttribute('role', 'tab'); button.setAttribute('aria-selected', String(index === selected));
      button.setAttribute('aria-controls', 'missionStepPanel'); button.tabIndex = index === selected ? 0 : -1;
      button.disabled = !window.missionFlow.unlockedSteps(flowContext()).includes(index) || operations.size > 0;
      const icon = document.createElement('i'); icon.dataset.lucide = item.icon;
      const title = document.createElement('strong'); title.textContent = `${index + 1}. ${item.name}`;
      const status = document.createElement('small');
      status.textContent = index === 0 && role ? 'Identity verified' : index === 1 && receipt?.passed ? 'Proof recorded' : index === 2 && incident?.planId ? 'Plan recorded' : index === 3 && incident?.planState === 'awaiting_approval' ? 'Awaiting reviewer' : index === 5 && execution?.record?.state === 'completed' ? 'Completed' : index === 4 && execution?.record ? 'Execution recorded' : button.disabled ? 'Waiting' : index === selected ? 'Current step' : 'Available';
      button.append(icon, title, status);
      button.addEventListener('click', () => select(index));
      return button;
    }));
    find('missionStepPanel').setAttribute('aria-labelledby', `mission-step-${selected}`);
    find('missionStepNumber').textContent = `${String(selected + 1).padStart(2, '0')} / 06`;
    find('missionStepTitle').textContent = stage.title;
    find('missionStepDescription').textContent = stage.description;
    find('missionBoundaryText').textContent = stage.boundary;
    find('missionStepState').textContent = workspace?.busy ? 'Request in progress' : selected >= 4 ? 'Integration pending' : role ? 'Live controls' : 'Sign-in required';
    document.querySelectorAll('[data-mission-node]').forEach(node => node.classList.toggle('is-focused', stage.nodes.includes(node.dataset.missionNode)));
    const action = find('missionAction');
    command = window.missionFlow.nextAction(flowContext());
    action.querySelector('span').textContent = command.label;
    const icon = document.createElement('i'); icon.dataset.lucide = selected === 3 && role !== 'approver' ? 'users-round' : stage.icon;
    action.firstElementChild.replaceWith(icon);
    action.disabled = command.disabled;
    action.hidden = command.id === 'plan';
    find('missionActionStatus').textContent = command.message;
    find('sessionNextAction').textContent = command.message;
    find('missionStepState').textContent = command.id === 'wait' ? 'Running' : command.id === 'unavailable' || (selected === 1 && workspace?.cloud && !workspace.cloud.readyForProbe) ? 'Blocked' : role ? 'Ready for next action' : 'Sign-in required';
    find('missionFacts').replaceChildren();
    appendFact('Human identity', role ? `${role} / ${state.session.accountFingerprint}` : 'Not signed in');
    appendFact('Source', selected === 1 ? proof?.completedAt ? `Live proof / ${proof.completedAt}` : 'No proof in this account session' : 'Current account session');
    if (selected === 1) {
      appendFact('SAW / network', workspace?.cloud ? `${workspace.cloud.vm} / ${workspace.cloud.runtimeLock}${workspace.stale ? ' / stale' : ''}` : 'Not checked');
      appendFact('Non-root process', receipt ? `UID ${receipt.uid}` : 'Not observed');
      appendFact('GET / diagnostic read', receipt ? String(receipt.readHttpStatus || 'Unreachable') : 'Expected 401; not run');
      appendFact('POST / forbidden write', receipt ? String(receipt.writeHttpStatus || 'Unreachable') : 'Expected 403; not run');
      appendFact('Privilege escalation', receipt?.privilegeEscalation || 'Not tested');
    } else if (selected === 2) {
      appendFact('Persisted investigations', incident?.connected ? `${incident.count} returned for this account` : 'Not checked');
      appendFact('Investigation producer', incident?.initiationAvailable ? 'Trusted fixed investigation / connected' : 'Not connected');
    } else if (selected === 3) {
      appendFact('Review service', review?.connected ? 'Connected' : review?.authorizationRequired ? 'Review authorization required' : 'Not checked');
      appendFact('Reviewable plans', review?.connected ? String(review.count) : 'Not observed');
    } else if (selected >= 4) {
      appendFact('Execution service', execution?.connected ? 'Connected / separate identity' : 'Not checked');
      appendFact('Execution state', execution?.record?.state || 'Not executed');
      if (execution?.record) appendFact('Execution ID', execution.record.execution_id);
      const checks = execution?.record?.verification?.checks;
      if (checks) for (const [name, passed] of Object.entries(checks)) appendFact(name.replaceAll('_', ' '), passed ? 'Passed' : 'Failed');
    }
    else appendFact('Credential boundary', 'Tokens remain server-side');
    if (incident?.planId) { appendFact('Selected plan', incident.planId); appendFact('Exact plan hash', incident.planHash); }
    find('missionVerdict').textContent = selected === 1 && receipt ? receipt.passed ? 'Containment proof passed' : 'Containment proof failed' : selected >= 4 ? 'Awaiting integrated execution' : 'Awaiting correlated incident evidence';
    find('missionVerdict').className = `mission-verdict${selected === 1 && receipt ? receipt.passed ? ' is-passed' : ' is-failed' : ''}`;
    if (selected === 1 && workspace?.cloud && !workspace.stale && !workspace.cloud.readyForProbe) {
      find('missionVerdict').textContent = workspace.cloud.vm !== 'running' ? 'Workspace stopped, not a role denial' : 'Workspace prerequisites blocked';
      find('missionVerdict').className = 'mission-verdict is-failed';
    }
    find('missionClaim').textContent = selected === 1 ? 'This fixed probe proves process isolation and route enforcement. It is not a model investigation or SQL remediation.' : 'A step is not complete until its own correlated receipt is recorded. No successful incident is inferred from component health.';
    find('missionReceipt').textContent = selected >= 4 && execution?.record ? JSON.stringify(execution.record, null, 2) : selected === 1 && proof ? JSON.stringify(proof, null, 2) : 'No correlated receipt for this step.';
    if (selected >= 4 && execution?.record) {
      find('missionVerdict').textContent = execution.record.state === 'completed' ? 'Incident completed' : execution.record.state === 'verification' ? 'Recovery independently verified' : 'Execution requires reconciliation';
      find('missionVerdict').className = 'mission-verdict' + (['verification', 'completed'].includes(execution.record.state) ? ' is-passed' : '');
    }
    find('missionEvidenceKind').textContent = progress.evidence;
    window.lucide?.createIcons({ root: find('missionWorkspace') });
    if (focusedStep) find(focusedStep)?.focus({ preventScroll: true });
  }
  function select(index) {
    if (!window.missionFlow.unlockedSteps(flowContext()).includes(index)) return;
    selected = index;
    for (const id of ['liveWorkspace', 'identityWorkspace']) { find(id).hidden = true; originalParents.get(id).append(find(id)); }
    const panel = selected === 1 ? 'liveWorkspace' : selected === 2 || selected === 3 ? 'identityWorkspace' : null;
    if (panel) { find('missionTools').append(find(panel)); find(panel).hidden = false; }
    if (selected === 2) window.dispatchEvent(new Event('console-incident-requested'));
    find('missionActionStatus').textContent = 'No new request has been sent.';
    render();
  }
  function openEvidence(panel = 'patternWorkspace') {
    for (const id of ['patternWorkspace', 'showcaseWorkspace', 'buildWorkspace', 'missionAgentCheck']) { find(id).hidden = id !== panel; find('missionDrawerBody').append(find(id)); }
    find('missionEvidenceSource').value = panel;
    if (!find('missionEvidenceDrawer').open) find('missionEvidenceDrawer').showModal();
  }
  window.missionNavigate = tab => {
    const technical = { patternViewTab: 'patternWorkspace', workspaceViewTab: 'showcaseWorkspace', buildViewTab: 'buildWorkspace' };
    if (technical[tab]) { openEvidence(technical[tab]); return; }
    if (find('missionEvidenceDrawer').open) find('missionEvidenceDrawer').close();
    select(state.session?.persona === 'approver' ? 3 : signedIn() ? 1 : 0);
  };
  find('missionAction').addEventListener('click', () => {
    const next = window.missionFlow.nextAction(flowContext());
    if (next.disabled) return;
    if (next.id === 'signin') find('sessionSignIn').click();
    else if (next.id === 'continue') select(selected === 0 ? state.session?.persona === 'approver' ? 3 : 1 : selected + 1);
    else if (next.id === 'check') find('liveCheck').click();
    else if (next.id === 'run') find('liveRun').click();
    else if (next.id === 'incidents') find('incidentRefresh').click();
    else if (next.id === 'initiate') window.dispatchEvent(new Event('console-incident-start'));
    else if (next.id === 'execution-status') window.dispatchEvent(new Event('console-execution-requested'));
    else if (next.id === 'execute') window.dispatchEvent(new Event('console-execute-approved'));
    else if (next.id === 'complete') window.dispatchEvent(new Event('console-complete-execution'));
    else if (next.id === 'reconcile') window.dispatchEvent(new Event('console-reconcile-execution'));
    else if (next.id === 'execution-step') select(4);
    else if (next.id === 'reviews') find('approverRefresh').click();
    else if (next.id === 'switch') find('sessionSwitch').click();
    else if (next.id === 'authorize') authenticate(true).catch(error => toast(error.message, 'error'));
  });
  find('missionAgentRun').addEventListener('click', async () => {
    if (agentBusy || !signedIn() || state.session.persona === 'approver') return;
    const generation = agentGeneration;
    notifyOperation('agent-diagnostic', true, 'Checking the read-only task agent. This does not create an incident.');
    agentBusy = true; render();
    find('missionAgentResult').textContent = 'Request in progress. No result yet.';
    try {
      const result = await api('/api/agent-check', { method: 'POST' });
      if (generation !== agentGeneration) return;
      find('missionAgentResult').textContent = `HTTP ${result.httpStatus} / request ${result.requestId}\n${result.content}`;
    } catch (error) {
      if (generation === agentGeneration) find('missionAgentResult').textContent = `Agent request failed: ${error.message}`;
    } finally { agentBusy = false; notifyOperation('agent-diagnostic', false, 'Task-agent diagnostic finished.'); render(); }
  });
  find('missionRail').addEventListener('keydown', event => {
    const available = window.missionFlow.unlockedSteps(flowContext());
    const position = available.indexOf(selected);
    const directions = { ArrowRight: available[Math.min(available.length - 1, position + 1)], ArrowLeft: available[Math.max(0, position - 1)], Home: available[0], End: available.at(-1) };
    if (!(event.key in directions)) return;
    event.preventDefault(); select(directions[event.key]); find(`mission-step-${selected}`).focus();
  });
  find('missionEvidenceOpen').addEventListener('click', () => openEvidence());
  find('missionEvidenceClose').addEventListener('click', () => find('missionEvidenceDrawer').close());
  find('missionEvidenceDrawer').addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); find('missionEvidenceDrawer').close(); }
  });
  find('missionEvidenceSource').addEventListener('change', event => openEvidence(event.target.value));
  const diagnosticOption = document.createElement('option');
  diagnosticOption.value = 'missionAgentCheck';
  diagnosticOption.textContent = 'Task-agent diagnostic';
  find('missionEvidenceSource').append(diagnosticOption);
  window.addEventListener('console-workspace-changed', event => {
    const next = JSON.stringify(event.detail);
    workspace = event.detail;
    if (next !== renderKey) { renderKey = next; render(); }
  });
  window.addEventListener('console-incident-changed', event => { incident = event.detail; render(); });
  window.addEventListener('console-review-changed', event => { review = event.detail; render(); });
  window.addEventListener('console-execution-changed', event => { execution = event.detail; render(); });
  window.addEventListener('console-operation-changed', event => { if (event.detail.busy) operations.add(event.detail.id); else operations.delete(event.detail.id); render(); });
  window.addEventListener('console-session-changed', () => {
    const next = signedIn() ? `${state.session.accountFingerprint}:${state.session.persona}` : null;
    if (identityKey !== next) {
      identityKey = next;
      agentGeneration += 1;
      find('missionAgentResult').textContent = 'No agent request sent for this account.';
      workspace = null;
      incident = null;
      review = null;
      execution = null;
      renderKey = '';
      select(state.session?.persona === 'approver' ? 3 : signedIn() ? 1 : 0);
      window.dispatchEvent(new Event('console-workspace-requested'));
    }
    render();
  });
  identityKey = signedIn() ? `${state.session.accountFingerprint}:${state.session.persona}` : null;
  select(state.session?.persona === 'approver' ? 3 : signedIn() ? 1 : 0);
  window.dispatchEvent(new Event('console-workspace-requested'));
})();