(() => {
  const node = (tag, text = '', className = '', scrollKey = '') => { const element = document.createElement(tag); element.textContent = text; element.className = className; if (scrollKey) element.setAttribute('data-scroll-key', scrollKey); return element; };
  const find = id => document.getElementById(id);
  const root = node('main', '', 'mission invoice-mission'); root.id = 'invoiceWorkspace'; root.hidden = true;
  find('missionWorkspace').after(root);
  let identity = null, generation = 0, step = 0, plans = [], selected = null, job = null, events = [], cursor = 0, busy = false, timer = null, message = '';
  const view = window.invoiceView;
  let component = 'planning', inspectorTab = 'Authority', challenge = 0, presenting = false, connected = false, observedAt = null, polling = false;
  let newInvestigation = false, newJobId = null, navigation = 0, scenarioVariant = 'lost-acknowledgement';
  let draft = view.restoreSelection(null).draft, renderPending = false;
  const observations = new Map();
  const traceSelections = new Map();
  let probe = null, probeTimer = null, probePolling = false, probeKind = 'planning';
  let revealArchitecture = false;
  let runtimeSnapshot = null, runtimeError = '';
  let mode = 'workflow', probeMessage = '', selectionLoaded = false;
  let plansLoading = false, plansError = '', plansRead = 0, historyError = '';
  let scenarioLoading = false, scenarioError = '', deadlineSignature = '';
  let renderContext = null;
  const pendingSandboxTests = new Set();
  let sandboxInventory = null, sandboxLoading = false, sandboxError = '';
  const pendingSandboxDeletes = new Set();
  let demoSession = null, demoLoading = false, demoError = '', demoTimer = null, demoPending = null, demoWriting = false, demoVersion = 0;
  const demoActive = () => demoSession?.state === 'active' && Date.parse(demoSession.expires_at) > Date.now() && demoPending?.action !== 'end';
  async function refreshDemo(current = generation) {
    if(demoLoading || demoWriting || !signedIn())return;
    clearTimeout(demoTimer);demoLoading=true;
    const previous=demoActive(),previousId=demoSession?.session_id,version=demoVersion;
    try {
      const result=await api('/api/invoices/demo-session');
      if(current!==generation || version!==demoVersion)return;
      if(result.source!=='invoice-demo-session' || !['idle','active','ending'].includes(result.state))throw new Error('Demo status unconfirmed');
      demoSession=result;demoError='';
      if(demoPending && result.session_id===demoPending.session_id && (demoPending.action==='start' || ['ending','idle'].includes(result.state))) {
        demoPending=null;sessionStorage.removeItem('invoice-demo-pending:'+identity);
      }
      if(demoPending)demoError='Demo request remains unconfirmed. No repeat request will be sent.';
      if(!demoActive()){clearTimeout(timer);clearTimeout(probeTimer);connected=false;presenting=false;}
      render();
      if(demoActive() && (!previous || previousId!==result.session_id)) {
        refreshPlans(current);
        if(!approver()) {
          if(draft.scenarioId)checkScenario(current);
          refreshSandboxes(current);
          if(job)poll(current);
          if(probe)pollProbe(current);
        }
      }
    } catch(error){if(current===generation && version===demoVersion){demoSession=null;demoError=error.message;clearTimeout(timer);clearTimeout(probeTimer);connected=false;render();}}
    finally{if(current===generation){demoLoading=false;demoTimer=setTimeout(()=>refreshDemo(current),10000);render();}}
  }
  async function demoAction(action) {
    if(busy || presenting || demoPending || demoLoading || !view.permissions(state.session).execute)return;
    if(action==='start' && demoSession?.state!=='idle' || action==='end' && (!demoActive() || !demoSession.can_end))return;
    if(!window.confirm(action==='start'?'Start a demo session for up to four hours? SQL checks and usage will resume.':'End this demo? New work will be blocked; active work and final cleanup will finish.'))return;
    const current=generation,identifier=action==='start'?crypto.randomUUID().replaceAll('-',''):demoSession.session_id;
    demoPending={action,session_id:identifier};sessionStorage.setItem('invoice-demo-pending:'+identity,JSON.stringify(demoPending));
    demoVersion+=1;demoWriting=true;
    await request(async()=>{
      try {
        const result=await api('/api/invoices/demo-session/'+action,{method:'POST',body:JSON.stringify({session_id:identifier})});
        if(current!==generation)return;
        if(result.source!=='invoice-demo-session' || result.session_id!==identifier)throw new Error('Demo action receipt differs');
        demoPending=null;sessionStorage.removeItem('invoice-demo-pending:'+identity);
        if(action==='end'){demoSession=result;clearTimeout(timer);clearTimeout(probeTimer);}
        demoWriting=false;
        await refreshDemo(current);
      } catch(error){
        if(current===generation){
          if(Number.isInteger(error.status) && error.status>=400 && error.status<500){
            demoPending=null;sessionStorage.removeItem('invoice-demo-pending:'+identity);
            demoError=error.message+'. The request was rejected before the demo state changed.';
          } else demoError=error.message+' Check demo status before another request.';
        }
      }
      finally{if(current===generation)demoWriting=false;}
    },true);
  }
  async function refreshSandboxes(current = generation) {
    if(!demoActive() || sandboxLoading || !view.permissions(state.session).investigate || presenting)return;
    sandboxLoading=true;sandboxError='';render();
    try {
      const result=await api('/api/invoices/sandboxes');
      if(current!==generation)return;
      if(result.source!=='openshell-sandbox-inventory' || !Array.isArray(result.sandboxes) || !Number.isInteger(result.remaining_count))throw new Error('Sandbox inventory unavailable or busy. Refresh after the active operation.');
      sandboxInventory=result;
    } catch(error){if(current===generation)sandboxError=error.message;}
    finally{if(current===generation){sandboxLoading=false;render();}}
  }
  async function deleteSandbox(sandboxId) {
    const item=sandboxInventory?.sandboxes.find(entry=>entry.sandbox_id===sandboxId);
    if(!item?.deletable || pendingSandboxDeletes.has(sandboxId) || busy || presenting || !view.permissions(state.session).execute)return;
    if(!window.confirm('Delete stopped sandbox '+item.name+' ('+sandboxId+')? Verified evidence will be archived; SQL plans and receipts remain.'))return;
    await request(async current=>{
      pendingSandboxDeletes.add(sandboxId);
      sessionStorage.setItem('invoice-sandbox-deletes:'+identity,JSON.stringify([...pendingSandboxDeletes]));
      render();
      try {
        const result=await api('/api/invoices/sandboxes/'+sandboxId+'/delete',{method:'POST',body:JSON.stringify({sandbox_id:sandboxId})});
        if(current!==generation)return;
        if(result.sandbox_id!==sandboxId || result.state!=='deleted' || result.evidence_archived!==true)throw new Error('Deletion receipt differs');
        await refreshSandboxes(current);
      } catch(error){if(current===generation)sandboxError=error.message+' Deletion unconfirmed; refresh inventory, do not repeat.';}
    });
  }
  function selectMode(value) {
    if (busy || !['workflow','challenge'].includes(value)) return;
    if (value === 'challenge' && !view.permissions(state.session).investigate) return;
    mode = value; component = mode === 'challenge' ? 'sandbox' : view.chapters[step].focus; render(true);
  }
  async function checkRuntime() {
    await request(async current => {
      runtimeError = '';
      try {
        const result = await api('/api/live-workspace/check', { method: 'POST', body: '{}' });
        if (current !== generation) return;
        if (result.source !== 'live-azure-query' || !Number.isFinite(Date.parse(result.checkedAt))) throw new Error('Runtime observation is unconfirmed');
        runtimeSnapshot = result;
      } catch (error) { if (current === generation) { runtimeError = 'Runtime check failed. '+error.message; throw error; } }
    });
  }
  async function inspectEvidence() {
    const row = currentPlan(); if (!row) return;
    await request(async current => {
      const result = await api('/api/invoices/plans/' + row.plan_json.plan_id + '/evidence');
      if (current !== generation) return;
      if (result.source !== 'diagnostic-sql-observation' || result.plan_id !== row.plan_json.plan_id || result.plan_hash !== row.plan_hash) throw new Error('Evidence binding differs');
      observations.set(result.plan_id, result);
    });
  }
  async function pollProbe(current = generation) {
    if (!demoActive() || !probe || probePolling || !signedIn()) return;
    clearTimeout(probeTimer); probePolling = true; const identifier = probe.challenge_id;
    try {
      const result = await api('/api/invoices/challenges/' + identifier);
      if (current !== generation || probe?.challenge_id !== identifier) return;
      if (result.challenge_id !== identifier) throw new Error('Challenge receipt differs');
      if (!probe.kind && ['planning','execution'].includes(result.kind)) probeKind = result.kind;
      probe = result; render();
      if (['queued','running'].includes(result.state)) probeTimer = setTimeout(() => pollProbe(current), 3000);
    } catch (error) { if (current === generation) { probeMessage = error.message + ' Challenge observation unconfirmed; no replay.'; render(); } }
    finally { probePolling = false; }
  }
  async function startProbe(kind) {
    await request(async current => {
      const identifier = crypto.randomUUID().replaceAll('-','');
      probe = { challenge_id: identifier, kind, state: 'admission-unconfirmed', events: [] };
      sessionStorage.setItem('invoice-challenge:'+identity, identifier);
      const result = await api('/api/invoices/challenges', { method:'POST', body:JSON.stringify({ challenge_id:identifier, kind }) });
      if (current !== generation) return;
      if (result.challenge_id !== identifier) throw new Error('Challenge admission differs');
      probe.state = 'queued'; await pollProbe(current);
    });
  }
  async function requestSandboxTest(runId, sandboxId) {
    const record = job?.job_id === runId ? { job, events } : history.get(runId);
    const test = view.sandboxTest(record?.job, record?.events || [], pendingSandboxTests.has(runId));
    if (selectedRun() !== runId || test.sandboxId !== sandboxId || !test.available || !view.permissions(state.session).execute) return;
    await request(async current => {
      pendingSandboxTests.add(runId);
      sessionStorage.setItem('invoice-sandbox-tests:'+identity,JSON.stringify([...pendingSandboxTests].slice(-100)));
      render();
      const receipt = await api('/api/invoices/jobs/'+runId+'/sandbox-test',{method:'POST',body:JSON.stringify({sandbox_id:sandboxId})});
      if (current !== generation) return;
      if (receipt.job_id !== runId || receipt.sandbox_id !== sandboxId || receipt.state !== 'requested') throw new Error('Sandbox test request unconfirmed. Refresh this run; do not replay.');
      if (job?.job_id === runId) job.sandbox_test_requested = true;
      if (history.has(runId)) history.get(runId).job.sandbox_test_requested = true;
      if (mode === 'challenge') probeMessage = 'Test queued for the selected agent sandbox. No new sandbox created.';
      else message = 'Test queued for this agent sandbox. No new sandbox created.';
    });
  }
  const history = new Map(), loadingHistory = new Set();
  async function loadHistory(row, current = generation, force = false, onlyId = null) {
    if (!demoActive() || !row || approver()) return;
    const identifiers = [row.plan_json.planning_run_id, row.execution_run_id].filter(value => /^[a-f0-9]{32}$/.test(value || '') && (!onlyId || onlyId === value) && value !== job?.job_id && (force || !history.has(value)) && !loadingHistory.has(value));
    if (identifiers.length) historyError = '';
    for (const identifier of identifiers) {
      loadingHistory.add(identifier);
      try {
        const record = await api('/api/invoices/jobs/' + identifier);
        const activity = await api('/api/invoices/jobs/' + identifier + '/events?after=0');
        if (current !== generation) return;
        if (record.job_id !== identifier || activity.job_id !== identifier || !Array.isArray(activity.events)) throw new Error('Run evidence does not match');
        history.set(identifier, { job: record, events: activity.events, connected: true, observedAt: Date.now() });
      } catch (error) { if (current === generation) { historyError = error.message + ' Selected run observation could not be refreshed.'; const cached = history.get(identifier); if (cached) cached.connected = false; } }
      finally { if (current === generation) loadingHistory.delete(identifier); }
    }
    if (current === generation && identifiers.length) render();
  }
  const stages = ['Analyze', 'Propose', 'Approve', 'Execute', 'Verify'];
  const currentPlan = () => plans.find(row => row.plan_json.plan_id === selected);
  function selectedRun() {
    const row = currentPlan();
    return newInvestigation ? newJobId : step === 0 && job?.kind === 'planning' && !row ? job.job_id : row && (step >= 3 ? view.executionMatches(job, row) ? job.job_id : row.execution_run_id : row.plan_json.planning_run_id);
  }
  async function reconnect() {
    const identifier = selectedRun();
    if (!identifier || approver()) return;
    if (identifier === job?.job_id) await poll();
    else await loadHistory(currentPlan(), generation, true, identifier);
  }
  const button = (text, icon, handler, disabled = false) => { const element = node('button', text, 'button button-secondary'); element.type = 'button'; element.prepend(createIcon(icon)); element.disabled = disabled; element.addEventListener('click', handler); return element; };
  const iconButton = (label, icon, handler, disabled = false) => { const element = node('button', '', 'icon-button'); element.type = 'button'; element.title = label; element.setAttribute('aria-label', label); element.append(createIcon(icon)); element.disabled = disabled; element.addEventListener('click', handler); return element; };
  const signedIn = () => state.session?.status === 'authenticated';
  const approver = () => state.session?.persona === 'approver';
  function saveSelection() {
    if (identity && !approver()) sessionStorage.setItem('invoice-selection:' + identity, JSON.stringify({ mode: newInvestigation ? 'draft' : 'saved', planId: selected, draft }));
  }
  function openDraft() {
    if (busy || !view.permissions(state.session).investigate) return;
    navigation += 1; newInvestigation = true; newJobId = draft.jobId; selected = null;
    step = 0; mode = 'workflow'; component = 'planning'; message = ''; scenarioVariant = draft.variant;
    saveSelection(); render(true);
  }
  function beginInvestigation() {
    if (busy || !view.permissions(state.session).investigate) return;
    if (draft.creationRequested && draft.scenarioStatus?.state !== 'created') { openDraft(); scenarioError = 'Resolve this scenario request before replacing the draft. No creation will be replayed.'; render(); return; }
    if (draft.scenarioId && !window.confirm('Start a new draft? The current scenario will remain in SQL, but this draft selection will be replaced.')) return;
    draft = view.restoreSelection(null).draft;
    scenarioError = '';
    openDraft();
    find('invoiceScenarioSetup')?.scrollIntoView({ block: 'center' });
  }
  async function createScenario(variant) {
    if (draft.scenarioId) return;
    await request(async current => {
      const scenarioId = crypto.randomUUID().replaceAll('-','');
      draft = { scenarioId, name: variant === 'healthy' ? 'Healthy import' : 'Import retry after lost acknowledgement', variant, jobId: null, creationRequested: true,
        scenarioStatus: { scenario_id: scenarioId, source: 'owned-scenario-status', state: 'pending' } };
      saveSelection(); scenarioError = ''; render();
      try {
        const result = await api('/api/invoices/scenarios', { method:'POST', body:JSON.stringify({scenario_id:scenarioId,variant}) });
        if (current !== generation) return;
        if (result.scenario_id !== scenarioId || result.source !== 'owned-scenario-status' || result.state !== 'created') throw new Error('Scenario receipt differs');
        draft.scenarioStatus = result; newInvestigation = true; newJobId = null; selected = null; navigation += 1;
        message = 'Scenario created. No agent has run.';
      } catch (error) {
        if (current === generation) { draft.scenarioStatus.state = 'unconfirmed'; scenarioError = error.message + ' Check creation status. Do not repeat creation.'; }
      } finally { if (current === generation) saveSelection(); }
    });
  }
  async function checkScenario(current = generation) {
    const scenarioId = draft.scenarioId;
    if (!demoActive() || !/^[a-f0-9]{32}$/.test(scenarioId) || scenarioLoading || approver() || presenting) return;
    scenarioLoading = true; scenarioError = ''; render();
    try {
      const result = await api('/api/invoices/scenarios/' + scenarioId);
      if (current !== generation || draft.scenarioId !== scenarioId) return;
      if (result.scenario_id !== scenarioId || result.source !== 'owned-scenario-status' || !['created','unconfirmed'].includes(result.state)) throw new Error('Scenario status binding differs');
      draft.scenarioStatus = result;
      if (result.state === 'created') { draft.name = result.variant === 'healthy' ? 'Healthy import' : result.variant === 'lost-acknowledgement' ? 'Import retry after lost acknowledgement' : 'Existing scenario'; message = 'Scenario confirmed. No agent was started by this check.'; }
      else scenarioError = 'No owned creation is confirmed for this ID. Keep this request ID for inspection; do not replay creation.';
      saveSelection();
    } catch (error) { if (current === generation) scenarioError = 'Scenario status unavailable. ' + error.message; }
    finally { if (current === generation) { scenarioLoading = false; render(); } }
  }
  function remember() {
    if (identity && job) sessionStorage.setItem('invoice-job:' + identity, JSON.stringify({ job_id: job.job_id }));
  }
  async function request(action, sessionControl = false) {
    if (busy || presenting || !sessionControl && !demoActive()) return;
    const current = generation, requestMode = mode; busy = true;
    if (requestMode === 'challenge') probeMessage = ''; else message = '';
    render();
    try { await action(current); }
    catch (error) { if (current === generation) { if (requestMode === 'challenge') probeMessage = error.message; else message = error.message; } }
    finally { if (current === generation) { busy = false; render(); } }
  }
  async function refreshPlans(current = generation) {
    if(!demoActive())return false;
    const read = ++plansRead;
    plansLoading = true; plansError = ''; render();
    try {
      const result = await api('/api/invoices/plans');
      if (current !== generation || read !== plansRead) return false;
      if (result.source !== 'azure-sql' || !Array.isArray(result.plans)) throw new Error('Plan queue is unconfirmed');
      plans = result.plans;
      selected = view.selectPlan(plans, selected, newInvestigation);
      if (!selectionLoaded && currentPlan()) {
        step = approver() ? 2 : view.planStage(currentPlan()); component = view.chapters[step].focus;
      }
      selectionLoaded = true;
      if (!selected && !approver() && !newInvestigation) { newInvestigation = true; newJobId = draft.jobId; step = 0; component = 'planning'; }
      saveSelection();
      loadHistory(currentPlan(), current, true);
      return true;
    } catch (error) { if (current === generation && read === plansRead) plansError = 'Investigation list unavailable. ' + error.message + ' Refresh the list to check its current state.'; return false; }
    finally { if (current === generation && read === plansRead) { plansLoading = false; render(); } }
  }
  async function poll(current = generation) {
    if (polling) return;
    clearTimeout(timer);
    if (!demoActive() || !job || current !== generation || !signedIn()) return;
    polling = true;
    const jobId = job.job_id;
    const currentNavigation = navigation;
    try {
      const result = await api('/api/invoices/jobs/' + jobId);
      const activity = await api('/api/invoices/jobs/' + jobId + '/events?after=' + cursor);
      if (current !== generation || job?.job_id !== jobId) return;
      if (result.job_id !== job.job_id || activity.job_id !== job.job_id || !Array.isArray(activity.events)) throw new Error('Run evidence does not match');
      job = result; connected = true; observedAt = Date.now();
      for (const event of activity.events) { if (Number.isInteger(event.sequence) && event.sequence > cursor) { events.push(event); cursor = event.sequence; } }
      events = events.slice(-200); remember();
      history.set(job.job_id, { job, events: [...events], connected, observedAt });
      const belongsToSelection = view.completionBelongsToSelection(job, currentPlan(), newInvestigation, newJobId);
      if (job.state === 'finished' && currentNavigation === navigation && belongsToSelection) {
        if (job.result?.plan) {
          newInvestigation = false; selected = job.result.plan.plan_id;
          if (draft.jobId === jobId) { draft = view.restoreSelection(null).draft; newJobId = null; scenarioVariant = draft.variant; }
          saveSelection();
        }
        await refreshPlans(current);
        if (currentNavigation !== navigation || current !== generation) return;
        step = job.kind === 'planning' ? job.result?.plan ? view.planStage(currentPlan()) : 0 : 4;
        component = view.chapters[step].focus;
      }
      if (job.state === 'uncertain' && belongsToSelection) {
        await refreshPlans(current);
        if (current !== generation) return;
        message = view.observation(job,events,true,observedAt).title + ' No operation will be replayed. Inspect the persisted plan and receipts.';
      }
      render();
      if (['queued', 'running'].includes(job.state)) timer = setTimeout(() => poll(current), 3000);
    } catch (error) {
      if (current === generation) {
        if (state.session.authMode === 'local-demo' && error.status === 404) {
          job = null; events = []; cursor = 0; connected = true; observedAt = Date.now();
          sessionStorage.removeItem('invoice-job:' + identity);
          message = 'Local demo run was reset. You can start the approved execution.';
        } else {
          connected = false; message = error.message + ' Observation disconnected; the run was not restarted.';
        }
        render();
      }
    }
    finally { polling = false; }
  }
  async function start(kind, target, hash) {
    if (!/^[a-f0-9]{32}$/.test(target || '')) { message = 'Select a valid scenario or plan.'; render(); return; }
    if (kind === 'planning' && job?.kind === 'planning' && job.state === 'uncertain' && (job.target_id === target || draft.jobId === job.job_id)) {
      message = view.observation(job,events,true,observedAt).title + ' Reconnect observations; this attempt will not be replayed.'; render(); return;
    }
    const eligibility = kind === 'planning' ? view.scenarioStatus(draft.scenarioStatus) : view.executionStatus(currentPlan());
    if (!eligibility.allowed) { message = eligibility.label; render(); return; }
    await request(async current => {
      const pending = { job_id: crypto.randomUUID().replaceAll('-', ''), kind, target_id: target, plan_hash: hash || null };
      if (job) history.set(job.job_id, { job, events: [...events], connected, observedAt });
      if (kind === 'planning') { selected = null; newInvestigation = true; newJobId = pending.job_id; draft = { ...draft, scenarioId: target, jobId: pending.job_id }; saveSelection(); }
      job = { ...pending, state: 'admission-unconfirmed', created_at: new Date().toISOString() }; events = []; cursor = 0; connected = true; observedAt = Date.now(); remember();
      const result = await api('/api/invoices/jobs', { method: 'POST', body: JSON.stringify(pending) });
      if (current !== generation) return;
      if (result.job_id !== pending.job_id) throw new Error('Job admission does not match');
      job.state = 'queued';
      await poll(current);
    });
  }
  async function planAction(action, decision) {
    const row = currentPlan(); if (!row) return;
    if (action === 'submit' && !view.submissionStatus(row).allowed) { message = view.submissionStatus(row).label; render(); return; }
    if (decision && (!view.reviewStatus(row).allowed || decision === 'approve' && !row.diagnostics)) { message = 'A current submitted plan and its bound diagnostic snapshot are required.'; render(); return; }
    await request(async current => {
      const path = decision ? '/api/invoices/review/' + row.plan_json.plan_id : '/api/invoices/plans/' + row.plan_json.plan_id + '/' + action;
      const result = await api(path, { method: 'POST', body: JSON.stringify({ plan_hash: row.plan_hash, ...(decision ? { decision } : {}) }) });
      if (current !== generation) return;
      plansRead += 1; plansLoading = false;
      if (action !== 'reconcile' && (result.plan_id !== row.plan_json.plan_id || (decision ? result.decision !== decision : result.state !== (action === 'submit' ? 'submitted' : 'completed')))) throw new Error('Action receipt differs. Refresh persisted status; do not repeat the action.');
      if (action === 'submit') { row.state = 'submitted'; step = 2; component = 'human'; message = 'Plan submitted for independent review. No invoice data was changed.'; }
      if (decision) { row.state = decision === 'approve' ? 'approved' : 'rejected'; message = decision === 'approve' ? 'Plan approved. Switch to the original Operator account to continue.' : 'Plan rejected. No execution authority was granted.'; }
      if (action === 'complete') { row.state = 'completed'; message = 'Completion acknowledged.'; }
      if (action === 'reconcile') {
        if (result.replayed_steps !== 0 || result.plan_hash !== row.plan_hash) throw new Error('Reconciliation receipt differs');
        message = result.state === 'partial' ? result.receipts.length + ' of ' + row.plan_json.steps.length + ' steps recorded. Run remains halted; no step was replayed.' : 'All step receipts confirmed. Independent verification passed.';
        if (result.state !== 'partial') { row.state = result.state; row.verification_json = result.checks; step = 4; }
      }
      plans = plans.map(item => item.plan_json.plan_id === row.plan_json.plan_id && item.plan_hash === row.plan_hash ? row : item);
      render();
      await refreshPlans(current);
    });
  }
  function render(force = false) {
    if (!state.invoiceEnabled) return;
    if (!force && root.contains(document.activeElement) && document.activeElement.tagName === 'SELECT') { renderPending = true; return; }
    renderPending = false;
    const focusable = 'button,input,select,summary';
    const focusKey = element => element.tagName + ':' + (element.id || element.getAttribute('aria-label') || element.getAttribute('name') || element.textContent);
    const activeElement = root.contains(document.activeElement) && document.activeElement.matches(focusable) ? document.activeElement : null;
    const focused = activeElement && { key: focusKey(activeElement), index: [...root.querySelectorAll(focusable)].filter(element => focusKey(element) === focusKey(activeElement)).indexOf(activeElement) };
    if (!signedIn() || !demoActive()) presenting = false;
    const nextRenderContext = JSON.stringify([identity, mode, presenting, selectedRun() || selected || (mode === 'challenge' ? probe?.challenge_id : draft.scenarioId)]);
    const scrollSnapshot = renderContext === nextRenderContext ? window.invoiceScroll.capture(root, window) : null;
    renderContext = nextRenderContext;
    root.replaceChildren(); const row = currentPlan(), active = view.blocksNewWork(job) || probe && !['finished','uncertain'].includes(probe.state);
    root.classList.toggle('is-presenting', presenting);
    document.body.classList.toggle('invoice-audience', presenting);
    const access = view.permissions(state.session), chapter = view.chapters[step];
    const runtime = view.runtimeStatus(runtimeSnapshot);
    const runId = selectedRun();
    const matchingJob = Boolean(job && runId === job.job_id);
    const record = matchingJob ? { job, events } : history.get(runId);
    const relevant = record?.events || [];
    const observation = view.observation(record?.job, relevant, matchingJob ? connected : record?.connected === true, matchingJob ? observedAt : record?.observedAt);
    const planningJob = row ? history.get(row.plan_json.planning_run_id)?.job || job : newInvestigation && job?.job_id !== newJobId ? null : job;
    const reviewSnapshot = row?.diagnostics;
    const snapshot = approver() && reviewSnapshot?.source === 'invoice-diagnostic-api' && reviewSnapshot.scenario_id === row.plan_json.scenario_id && reviewSnapshot.evidence_hash === row.plan_json.evidence_hash ? reviewSnapshot : view.diagnostics(planningJob, row);
    const stored = record?.job.result?.database_observation;
    const currentEvidence = row ? view.latestEvidence(row, observations.get(row.plan_json.plan_id), stored && { ...stored, plan_id:row.plan_json.plan_id, plan_hash:row.plan_hash }) : null;
    const onTrace = sequence => { traceSelections.set(runId, sequence); render(); };
    if (presenting) {
      const header = node('header', '', 'audience-header'); header.append(node('strong', 'LearningNeMo'), node('span', 'AUDIENCE / READ ONLY'), button('Back to operator', 'minimize', () => { presenting = false; render(true); })); root.append(header);
      root.append(invoiceExperience.audience({ row, before:snapshot, observation:currentEvidence, job:record?.job, events:relevant, stage:step, sequence:traceSelections.get(runId), onSelect:onTrace, probe, probeKind, mode, runObservation:observation }));
      refreshIcons();
      window.invoiceScroll.restore(root, scrollSnapshot, window);
      if (focused) [...root.querySelectorAll(focusable)].filter(element => focusKey(element) === focused.key)[focused.index]?.focus({ preventScroll: true });
      return;
    }
    find('sessionNextAction').textContent = mode === 'challenge' ? view.sandboxTest(record?.job,relevant,pendingSandboxTests.has(runId)).title : view.nextAction(state.session, row, job, probe, newInvestigation, draft.scenarioId);
    if (mode === 'workflow' && newInvestigation && draft.scenarioId && !active && !(matchingJob && job.state === 'uncertain')) find('sessionNextAction').textContent = view.scenarioStatus(draft.scenarioStatus).label;
    const heading = node('header', '', 'mission-heading');
    const title = node('div'); title.append(node('h1', mode === 'challenge' ? 'Test the selected agent sandbox' : 'Invoice integrity'));
    const toolbar = node('div', '', 'invoice-toolbar');
    if (demoActive() && access.investigate && mode === 'workflow') {
      const create = button('New investigation', 'plus', beginInvestigation, busy); create.className = 'button button-primary'; toolbar.append(create);
    }
    if (signedIn() && demoActive()) {
      const more = node('details', '', 'invoice-action-menu');
      const summary = node('summary'); summary.setAttribute('aria-label', 'More options'); summary.append(createIcon('ellipsis'), node('span', 'More'));
      const options = node('div', '', 'invoice-action-menu-options');
      options.append(button('Audience view', 'maximize', () => { more.open = false; presenting = true; render(true); }));
      options.append(button('Architecture', 'network', () => { more.open = false; component = mode === 'challenge' ? 'sandbox' : step >= 3 ? 'execution' : 'planning'; inspectorTab = 'Authority'; revealArchitecture = true; render(); find('invoiceArchitecturePanel')?.scrollIntoView({ block: 'nearest' }); }));
      more.append(summary, options); toolbar.append(more);
    }
    heading.append(title, toolbar); root.append(heading);
    if(signedIn()) {
      const demo=node('section','','invoice-demo-session');demo.setAttribute('aria-label','Demo session');
      const title=demoPending?.action==='end'?'Demo end unconfirmed':demoActive()?'Demo active':demoSession?.state==='ending'?'Demo ending':demoSession?.state==='idle'?'Demo idle':'Demo status unavailable';
      demo.append(node('strong',title));
      if(demoActive())demo.append(node('time','Ends '+new Date(demoSession.expires_at).toUTCString()));
      if(demoSession?.state==='ending')demo.append(node('span',demoSession.inflight?'Waiting for admitted work: '+demoSession.inflight:'Final cleanup pending'));
      if(demoSession?.cleanup)demo.append(node('span','Final cleanup '+demoSession.cleanup));
      if(access.execute && demoSession?.state==='idle')demo.append(button('Start demo','play',()=>demoAction('start'),busy||Boolean(demoPending)||demoLoading));
      if(access.execute && demoActive() && demoSession.can_end) {
        const options=node('details','','invoice-session-menu');
        const summary=node('summary');summary.setAttribute('aria-label','Demo session options');summary.append(createIcon('ellipsis'));
        const actions=node('div','','invoice-session-menu-options');actions.append(button('End demo','square',()=>{options.open=false;demoAction('end');},busy||Boolean(demoPending)||demoLoading));
        options.append(summary,actions);demo.append(options);
      }
      if(demoError)demo.append(iconButton('Retry demo status','refresh-cw',()=>refreshDemo(),demoLoading||busy));
      if(demoError){const error=node('p',demoError,'mission-action-status');error.setAttribute('role','status');demo.append(error);}
      root.append(demo);
      if(!demoActive()) {
        find('sessionNextAction').textContent=demoSession?.state==='ending'?'Finishing admitted work and cleanup.':access.execute?'Start a demo to access invoice data.':'Waiting for an Operator to start the demo.';
        refreshIcons();window.invoiceScroll.restore(root,scrollSnapshot,window);
        if(focused)[...root.querySelectorAll(focusable)].filter(element=>focusKey(element)===focused.key)[focused.index]?.focus({preventScroll:true});
        return;
      }
    }
    if (access.investigate) {
      const modes = node('div', '', 'invoice-modes'); modes.setAttribute('role', 'tablist'); modes.setAttribute('aria-label', 'Demo mode');
      for (const [value, title, icon] of [['workflow','Invoice workflow','workflow'],['challenge','Sandbox challenge','shield-check']]) {
        const tab = button(title, icon, () => selectMode(value), busy); tab.id = 'invoice-mode-'+value;
        tab.setAttribute('role','tab'); tab.setAttribute('aria-selected', String(mode === value)); tab.setAttribute('aria-controls', value === 'workflow' ? 'invoiceFlowPanel' : 'invoiceChallengePanel'); tab.tabIndex = mode === value ? 0 : -1; modes.append(tab);
      }
      modes.addEventListener('keydown', event => { if (['ArrowLeft','ArrowRight','Home','End'].includes(event.key) && !busy) { event.preventDefault(); const value = ['ArrowLeft','Home'].includes(event.key) ? 'workflow' : 'challenge'; selectMode(value); find('invoice-mode-'+value)?.focus(); } }); root.append(modes);
    }
    if (access.investigate) {
      const availability = node('details', '', 'invoice-runtime', 'runtime-availability'); availability.setAttribute('aria-label', 'Runtime availability'); availability.append(node('summary', runtimeError ? 'Runtime check failed' : runtime.label));
      const status = node('div'); status.append(node('strong', runtimeError || runtime.label));
      if (runtimeSnapshot?.expiresAt) status.append(node('small', 'Observed host lease: '+new Date(runtimeSnapshot.expiresAt).toUTCString()));
      availability.append(status, button('Check runtime', 'refresh-cw', checkRuntime, busy)); root.append(availability);
      root.append(window.renderInvoiceSandboxInventory({inventory:sandboxInventory,loading:sandboxLoading,error:sandboxError,permitted:access.execute,busy:busy||active,pending:pendingSandboxDeletes,refresh:()=>refreshSandboxes(),remove:deleteSandbox}));
    }
    const flow = node('section'); flow.id = 'invoiceFlowPanel';
    if (access.investigate) { flow.setAttribute('role','tabpanel'); flow.setAttribute('aria-labelledby','invoice-mode-workflow'); }
    else { flow.setAttribute('role','region'); flow.setAttribute('aria-label','Invoice workflow'); }
    const rail = node('div', '', 'mission-rail'); rail.setAttribute('role', 'tablist'); rail.setAttribute('aria-label', 'Invoice incident sequence');
    stages.forEach((name, index) => { const allowed = approver() ? index === 2 : index === 0 || (row && index <= 2) || (row && ['approved','executing','verified','completed'].includes(row.state) && index === 3) || (row && ['verified','completed'].includes(row.state) && index === 4);
      const tab = button((index + 1) + '. ' + name, ['scan-search','file-pen-line','file-check-2','workflow','clipboard-check'][index], () => { step = index; component = view.chapters[index].focus; render(); }, !signedIn() || !allowed || busy);
      tab.id = 'invoice-tab-' + index; tab.tabIndex = index === step ? 0 : -1;
      tab.append(node('small', view.stageState(row, index)));
      tab.setAttribute('aria-controls','invoiceWork'); tab.setAttribute('role','tab'); tab.setAttribute('aria-selected', String(step === index)); rail.append(tab); });
    rail.addEventListener('keydown', event => { const tabs = [...rail.querySelectorAll('button:not(:disabled)')], position = tabs.indexOf(document.activeElement); const next = { ArrowRight: tabs[Math.min(position + 1, tabs.length - 1)], ArrowLeft: tabs[Math.max(position - 1, 0)], Home: tabs[0], End: tabs.at(-1) }[event.key]; if (next) { event.preventDefault(); const id = next.id; next.click(); find(id)?.focus(); } });
    flow.append(rail);
    const chapterHeading = node('div', '', 'invoice-chapter'); const chapterText = node('div'); chapterText.append(node('span', row ? row.plan_json.diagnosis : 'New investigation', 'investigation-title'));
    const executionJob = row && (job?.job_id === row.execution_run_id ? job : history.get(row.execution_run_id)?.job);
    chapterHeading.append(chapterText, node('span', row ? view.planStatus(row, executionJob) : newInvestigation ? 'New investigation' : 'No plan recorded', 'chapter-state')); flow.append(chapterHeading);
    if (row || snapshot) flow.append(invoiceExperience.outcomes(row, snapshot, currentEvidence));
    const monitor = node('div', '', 'invoice-monitor ' + observation.state); monitor.setAttribute('role','status');
    const monitorIcon = createIcon(observation.live ? 'activity' : ['uncertain','disconnected','stale'].includes(observation.state) ? 'circle-alert' : 'clock');
    const monitorText = node('div'); monitorText.append(node('small', observation.label), node('strong', observation.title));
    const selectedObservedAt = matchingJob ? observedAt : record?.observedAt;
    const freshness = node('span', selectedObservedAt ? 'Observed ' + new Date(selectedObservedAt).toLocaleTimeString() : 'No current run observation', 'invoice-freshness'); freshness.id = 'invoiceFreshness';
    monitor.append(monitorIcon, monitorText, freshness);
    const architecture = window.renderInvoiceScene({ stage: mode === 'challenge' ? probeKind === 'execution' ? 3 : 0 : step, session: state.session, row: mode === 'workflow' ? row : null, job: mode === 'workflow' ? record?.job : null, events: mode === 'workflow' ? relevant : [], observation: mode === 'workflow' ? observation : view.observation(null, [], false, null), selected: component, tab: inspectorTab, challenge,
      onSelect: key => { component = key; challenge = 0; render(); }, onTab: value => { inspectorTab = value; render(); }, onChallenge: value => { challenge = value; render(); } });
    const layout = node('div', '', 'mission-layout invoice-workbench'), panel = node('section', '', 'mission-active'); panel.id = 'invoiceWork'; panel.setAttribute('role','tabpanel'); panel.setAttribute('aria-labelledby','invoice-tab-'+step); panel.append(node('h2', ['Investigation','Resolution proposal','Independent decision','Approved operations','Database outcome'][step]));
    if (!signedIn()) panel.append(node('p', 'Sign in with your assigned Operator or Approver account.'), button('Sign in', 'log-in', () => find('sessionSignIn').click()));
    else if (state.session.persona === 'reader') panel.append(node('p', 'Operator identity required for an investigation.'));
    else {
      panel.append(iconButton('Refresh investigations', 'refresh-cw', () => refreshPlans(), plansLoading));
      if (plansLoading || plansError) { const listStatus = node('p', plansLoading ? 'Loading investigations. SQL may be resuming.' : plansError, 'invoice-list-status'); listStatus.setAttribute('role','status'); panel.append(listStatus); }
      if (matchingJob && job?.state === 'finished' && job.kind === 'planning' && job.result && !job.result.plan) {
        panel.append(node('h3', job.result.outcome.replaceAll('-', ' ')), node('p', job.result.diagnosis), node('p', job.result.rationale));
      }
      if (plans.length || !approver()) {
        const label = node('label', 'Investigation'); const picker = node('select'); picker.setAttribute('aria-label','Investigation');
        if (!approver()) { const option = node('option', 'Draft investigation / ' + (draft.scenarioId ? draft.name || 'Existing scenario selected' : 'No scenario selected')); option.value = 'draft'; picker.append(option); }
        plans.forEach(item => { const observed = job?.job_id === item.execution_run_id ? job : history.get(item.execution_run_id)?.job; const option = node('option', item.plan_json.diagnosis.slice(0,70) + ' / ' + view.planStatus(item, observed) + ' / ' + new Date(item.plan_json.created_at).toLocaleString()); option.value = item.plan_json.plan_id; picker.append(option); });
        picker.value = newInvestigation || !selected ? 'draft' : selected; picker.disabled = busy;
        picker.addEventListener('change', () => {
          if (picker.value === 'draft') { openDraft(); return; }
          navigation += 1; newInvestigation = false; newJobId = null; selected = picker.value; message = '';
          const chosen = currentPlan(); if (!chosen) { message = 'Plan list changed. Refresh plans.'; render(true); return; } step = approver() ? 2 : view.planStage(chosen);
          component = view.chapters[step].focus; saveSelection(); render(true); loadHistory(chosen);
        }); label.append(picker); panel.append(label);
      }
      if (step === 0 && !approver() && newInvestigation) {
        const setup = node('section'); setup.id = 'invoiceScenarioSetup'; setup.append(node('h3','Test data'));
        const variantLabel = node('label', 'Scenario to create'); const variant = node('select'); variant.setAttribute('aria-label','Scenario to create');
        for (const [value, text] of [['lost-acknowledgement','Import retry after lost acknowledgement'],['healthy','Healthy idempotent import']]) { const option=node('option',text);option.value=value;variant.append(option); }
        variant.value = scenarioVariant; variant.addEventListener('change', () => { scenarioVariant = variant.value; draft.variant = scenarioVariant; saveSelection(); });
        variant.disabled=busy||active; variantLabel.append(variant); setup.append(variantLabel);
        if (active) setup.append(node('p', 'Another run is active or its admission is unconfirmed. Reconnect observations before starting new work.'));
        const createFixture = button('Create invoice fixture','plus',() => createScenario(variant.value),active || !access.execute || Boolean(draft.scenarioId));
        createFixture.classList.replace('button-secondary','button-primary'); setup.append(createFixture);
        if (!draft.scenarioId) panel.append(setup);
        const scenario = draft.scenarioId;
        const eligibility = view.scenarioStatus(draft.scenarioStatus);
        const analyzing = matchingJob && job?.kind === 'planning';
        const analysisUnconfirmed = analyzing && job.state === 'uncertain';
        const chosen = node('div', '', 'selected-scenario'); chosen.append(createIcon('layers'), node('strong', scenario ? draft.name || 'Existing scenario selected' : 'No scenario selected'), node('small', analyzing ? observation.title : scenario ? eligibility.label : 'This draft has no scenario yet')); panel.append(chosen);
        if (scenario) {
          chosen.append(node('code',scenario));
          if (draft.scenarioStatus?.expires_at) chosen.append(node('small','Scenario expires '+new Date(draft.scenarioStatus.expires_at).toLocaleString()));
          panel.append(button(scenarioLoading ? 'Checking scenario status' : 'Check creation status','refresh-cw',()=>checkScenario(),busy || scenarioLoading));
          if (draft.scenarioStatus?.state === 'created' || !draft.creationRequested) panel.append(button('Change test data','layers',beginInvestigation,busy || active));
        }
        if (scenarioError) { const error = node('p',scenarioError,'mission-action-status'); error.setAttribute('role','status'); panel.append(error); }
        const advanced = node('details', '', 'invoice-advanced', 'existing-scenario'); advanced.append(node('summary','Use an existing scenario ID'));
        const label = node('label', 'Scenario ID', 'invoice-scenario'), input = node('input'); input.name = 'scenario'; input.maxLength = 32; input.placeholder = '32-character scenario ID'; input.value = scenario || ''; input.disabled = busy || active;
        const startPlanning = button(analysisUnconfirmed ? 'Analysis unavailable' : analyzing && ['queued','running'].includes(job.state) ? 'Planning in progress' : 'Analyze invoices', analysisUnconfirmed ? 'circle-alert' : analyzing && ['queued','running'].includes(job.state) ? 'clock' : 'play', () => start('planning', draft.scenarioId), busy || active || analysisUnconfirmed || runtime.blocked || !access.investigate || !eligibility.allowed);
        startPlanning.classList.replace('button-secondary','button-primary');
        label.append(input); advanced.append(label,button('Check existing scenario','search',()=>{
          const identifier = input.value.trim();
          if (!/^[a-f0-9]{32}$/.test(identifier)) { scenarioError = 'A scenario ID must contain 32 hexadecimal characters.'; render(); return; }
          draft = { ...draft, scenarioId: identifier, name: '', jobId: null, scenarioStatus: null }; newJobId = null; saveSelection(); checkScenario();
        },busy || active));
        if (!scenario) panel.append(advanced);
        if (scenario) panel.append(startPlanning);
      }
      if (step === 0 && row && !approver()) {
        panel.append(node('p', 'Scenario bound to this saved investigation. New scenario setup is separate.'));
        if (row.state === 'draft') panel.append(button('Review proposal', 'arrow-right', () => { step = 1; component = 'planning'; render(); }, busy));
        const reference = node('details', '', '', 'saved-scenario:'+row.plan_json.scenario_id); reference.append(node('summary','Saved scenario ID'), node('code',row.plan_json.scenario_id)); panel.append(reference);
        panel.append(button('Return to draft investigation', 'arrow-right', openDraft, busy));
      }
      if (row && step >= 1) {
        if (step < 4) {
          const receipts = relevant.filter(event => view.traceDetail(record?.job,relevant,row,step,event.sequence)?.receipt).map(event => event.step_id);
          panel.append(invoiceExperience.changes(row, snapshot, receipts));
        }
        else {
          const checks = view.verifiedChecks(row);
          if (checks) { const outcomes=node('dl','','verification-outcomes');for(const title of Object.values(view.checks))outcomes.append(node('dt',title),node('dd','Passed'));panel.append(outcomes); }
          else panel.append(node('p','Independent results are not confirmed.'));
          const artifact = node('details', '', '', 'verified-artifact:'+row.plan_json.plan_id); artifact.append(node('summary','Verified artifact'),node('pre',JSON.stringify({plan:row.plan_json,plan_hash:row.plan_hash},null,2),'','verified-json:'+row.plan_json.plan_id)); panel.append(artifact);
        }
        const stageActions = node('section', '', 'invoice-primary-action');
        if (step === 1 && row.state === 'draft') {
          const submission = view.submissionStatus(row);
          stageActions.setAttribute('aria-label', 'Submit proposal');
          stageActions.append(node('p', submission.label));
          if (submission.expires) stageActions.append(node('small', 'Submission deadline: ' + new Date(submission.expires).toLocaleString()));
          primaryAction(stageActions, 'Submit for approval', () => planAction('submit'), !access.submit || !submission.allowed);
        }
        if (step === 2 && !approver()) stageActions.append(node('p', row.state === 'submitted' ? view.reviewStatus(row).label + ' Use Change role above to continue as Approver.' : 'Independent review is required before execution.'));
        if (step === 2 && approver() && row.state === 'submitted') {
          const review = view.reviewStatus(row);
          if (snapshot) { const evidence = node('details', '', '', 'review-evidence:'+row.plan_json.plan_id); evidence.append(node('summary','Immutable diagnostic snapshot'),node('pre',JSON.stringify(snapshot,null,2),'','review-json:'+row.plan_json.plan_id)); stageActions.append(evidence); }
          else stageActions.append(node('p','Bound diagnostic snapshot unavailable. Approval is disabled.'));
          if (Number.isFinite(review.expires)) stageActions.append(node('p','Review deadline: '+new Date(review.expires).toLocaleString()));
          primaryAction(stageActions, 'Approve plan', () => planAction('decision','approve'), !access.approve || !snapshot || !review.allowed); stageActions.append(button('Reject plan','circle-x', () => planAction('decision','reject'), busy || !access.approve || !review.allowed));
        }
        if (step === 2 && approver() && ['approved','rejected'].includes(row.state)) stageActions.append(node('p', row.state === 'approved' ? 'Plan approved. Return to the sponsoring Operator account for execution.' : 'Plan rejected. No execution authority was granted.'));
        if (step === 3 && row.state === 'approved' && !approver()) { const eligibility = view.executionStatus(row); stageActions.append(node('p', eligibility.label)); if (Number.isFinite(eligibility.expires)) stageActions.append(node('small','Start execution before '+new Date(eligibility.expires).toLocaleString())); primaryAction(stageActions, 'Start Execution agent', () => start('execution', row.plan_json.plan_id, row.plan_hash), !eligibility.allowed || active || runtime.blocked || !access.execute); }
        if (step === 3 && row.state === 'executing' && !approver()) stageActions.append(button('Inspect execution receipts', 'scan-search', () => planAction('reconcile'), busy || active));
        if (stageActions.childElementCount) panel.append(stageActions);
        if (step === 4) { if (row.state === 'verified' && view.verifiedChecks(row)) panel.append(button('Acknowledge completion','check', () => planAction('complete'),busy || !access.execute)); else if (row.state === 'completed') panel.append(node('strong','Verified and completed')); }
      } else if (step !== 0) panel.append(node('p', approver() ? 'No submitted plans awaiting review.' : 'No plan returned for this account.'));
      if (row && !approver()) {
        const scenarioExpired = Date.parse(row.scenario_expires_at) <= Date.now();
        panel.append(button('Read database again', 'refresh-cw', inspectEvidence, busy || scenarioExpired));
        if (currentEvidence) { const ledger=node('details','','invoice-row-evidence','ledger-details:'+row.plan_json.plan_id);ledger.append(node('summary','Recorded invoice rows and execution receipts'),window.renderInvoiceEvidence(row,currentEvidence,snapshot));panel.append(ledger); }
      }
    }
    const status = node('p', message, 'mission-action-status'); status.setAttribute('role','status'); panel.insertBefore(status, panel.children[1] || null); layout.append(panel);
    const evidence = node('aside', '', 'mission-evidence');
    if (record) evidence.append(monitor);
    evidence.append(invoiceExperience.trace({job:record?.job,events:relevant,row,stage:step,sequence:traceSelections.get(runId),onSelect:onTrace}));
    if (runId && !approver()) evidence.append(button('Reconnect observations','refresh-cw', reconnect,busy || loadingHistory.has(runId)));
    if (record?.job && !approver()) evidence.append(window.renderInvoiceSandboxTest({job:record.job,events:relevant,pending:pendingSandboxTests.has(runId),permitted:access.execute,busy,request:requestSandboxTest}));
    if (historyError) evidence.append(node('p',historyError,'mission-action-status'));
    evidence.append(node('p',chapter.lesson,'invoice-stage-lesson'));
    if (signedIn()) layout.append(evidence);
    layout.classList.toggle('invoice-workbench-single', !signedIn()); flow.append(layout);
    if (mode === 'workflow') {
      if (probe && !['finished','uncertain'].includes(probe.state)) {
        const notice = node('div', '', 'invoice-mode-notice'); notice.append(node('p', 'A sandbox challenge is active or unconfirmed. New sandbox work is blocked.'), button('Open challenge status', 'arrow-right', () => selectMode('challenge'), busy)); flow.prepend(notice);
      }
      root.append(flow);
    }
    if (access.investigate) {
      const challenges = node('section'); challenges.id = 'invoiceChallengePanel'; challenges.setAttribute('role','tabpanel'); challenges.setAttribute('aria-labelledby','invoice-mode-challenge');
      challenges.append(window.renderInvoiceSandboxTest({job:record?.job,events:relevant,pending:pendingSandboxTests.has(runId),permitted:access.execute,busy,request:requestSandboxTest,reconnect}));
      if (!record?.job) challenges.append(button('Open invoice workflow','arrow-right',()=>selectMode('workflow'),busy));
      const isolated=node('details','','invoice-secondary','separate-policy-test');isolated.append(node('summary','Separate policy test in a new sandbox'),window.renderInvoiceChallenge({ result:probe, kind:probeKind, onKind:value=>{ probeKind=value; render(true); }, permitted:access.execute && !runtime.blocked, blocked:active, busy, start:startProbe, reconnect:()=>pollProbe() }));challenges.append(isolated);
      const status = node('p',probeMessage,'mission-action-status'); status.setAttribute('role','status'); challenges.append(status);
      if (mode === 'challenge') root.append(challenges);
    }
    const architecturePanel = node('details', '', 'invoice-secondary', 'architecture'); architecturePanel.id = 'invoiceArchitecturePanel';
    architecturePanel.append(node('summary', 'Architecture and permissions'), architecture); root.append(architecturePanel);
    refreshIcons();
    window.invoiceScroll.restore(root, scrollSnapshot, window);
    if (focused) [...root.querySelectorAll(focusable)].filter(element => focusKey(element) === focused.key)[focused.index]?.focus({ preventScroll: true });
    if (revealArchitecture) { architecturePanel.open = true; revealArchitecture = false; }
  }
  function primaryAction(panel, command, handler, disabled = false) {
    const action = button(command,'check', handler,busy || disabled); action.classList.replace('button-secondary','button-primary'); panel.append(action);
  }
  root.addEventListener('focusout', event => { if (event.target.tagName === 'SELECT') setTimeout(() => { if (renderPending) render(); }, 0); });
  window.addEventListener('console-session-changed', () => {
    root.hidden = !state.invoiceEnabled; if (!state.invoiceEnabled) return;
    find('missionWorkspace').hidden = true; find('identityWorkspace').hidden = true;
    const next = signedIn() ? state.session.persona + ':' + (state.session.storageKey || state.session.accountFingerprint) : null;
    const identityChanged = next !== identity;
    if (identityChanged) { demoSession=null;demoLoading=false;demoError='';demoPending=null;demoWriting=false;demoVersion+=1;clearTimeout(demoTimer);presenting = false; traceSelections.clear(); pendingSandboxTests.clear(); pendingSandboxDeletes.clear(); sandboxInventory=null; sandboxLoading=false; sandboxError=''; mode = 'workflow'; probeMessage = ''; selectionLoaded = false; probeKind = 'planning'; runtimeSnapshot = null; runtimeError = ''; plansRead += 1; plansLoading = false; plansError = ''; historyError = ''; scenarioLoading = false; scenarioError = ''; }
    if (identityChanged) { identity = next; generation += 1; navigation += 1; newInvestigation = false; newJobId = null; draft = view.restoreSelection(null).draft; clearTimeout(timer); clearTimeout(probeTimer); observations.clear(); probe=null; history.clear(); loadingHistory.clear(); plans = []; selected = null; job = null; events = []; cursor = 0; busy = false; message = ''; connected = false; observedAt = null; step = approver() ? 2 : 0; component = view.chapters[step].focus;
      if (identity) {
        const legacyIdentity = state.session.persona + ':' + state.session.accountFingerprint;
        if (legacyIdentity !== identity) for (const prefix of ['invoice-selection:','invoice-job:','invoice-challenge:']) {
          const previous = sessionStorage.getItem(prefix + legacyIdentity);
          if (!sessionStorage.getItem(prefix + identity) && previous) sessionStorage.setItem(prefix + identity, previous);
        }
        if (!approver()) {
          try {const saved=JSON.parse(sessionStorage.getItem('invoice-sandbox-deletes:'+identity)||'[]');if(Array.isArray(saved))for(const identifier of saved)if(/^[a-f0-9]{32}$/.test(identifier))pendingSandboxDeletes.add(identifier);}catch{}
          try { const saved = JSON.parse(sessionStorage.getItem('invoice-sandbox-tests:'+identity)||'[]'); if (Array.isArray(saved)) for(const runId of saved.slice(-100)) if (/^[a-f0-9]{32}$/.test(runId)) pendingSandboxTests.add(runId); } catch {}
          let saved; try { saved = JSON.parse(sessionStorage.getItem('invoice-selection:' + identity)); } catch { }
          const selection = view.restoreSelection(saved); draft = selection.draft; selected = selection.planId; newInvestigation = selection.mode === 'draft'; newJobId = newInvestigation ? draft.jobId : null; scenarioVariant = draft.variant;
        }
        try { const saved = JSON.parse(sessionStorage.getItem('invoice-job:' + identity)); if (/^[a-f0-9]{32}$/.test(saved?.job_id)) job = saved; } catch {}
        const identifier = sessionStorage.getItem('invoice-challenge:'+identity);
        if (!approver() && /^[a-f0-9]{32}$/.test(identifier||'')) { probe={challenge_id:identifier,state:'admission-unconfirmed',events:[]}; }
        try {demoPending=JSON.parse(sessionStorage.getItem('invoice-demo-pending:'+identity)||'null');}catch{demoPending={action:'unknown'};}
        refreshDemo();
      }
    } render(identityChanged);
  });
  setInterval(() => {
    if (!state.invoiceEnabled) return;
    const deadlines = JSON.stringify([view.scenarioStatus(draft.scenarioStatus).label, view.submissionStatus(currentPlan()).label, view.reviewStatus(currentPlan()).label, view.executionStatus(currentPlan()).label]);
    if (deadlines !== deadlineSignature) { deadlineSignature = deadlines; if (!presenting) render(); }
    const identifier = selectedRun(), selectedRecord = identifier === job?.job_id ? { job, events, connected, observedAt } : history.get(identifier);
    if (!selectedRecord?.observedAt) return;
    if (presenting) {
      const status = root.querySelector('.audience-run-state');
      if (status?.dataset.runId === identifier) {
        const current = view.observation(selectedRecord.job, selectedRecord.events, selectedRecord.connected, selectedRecord.observedAt);
        status.querySelector('strong').textContent = current.title;
        status.querySelector('span').textContent = current.label;
      }
      return;
    }
    const target = find('invoiceFreshness');
    if (!target || target.textContent === 'No current run observation') return;
    const age = Math.max(0, Math.floor((Date.now() - selectedRecord.observedAt) / 1000));
    target.textContent = (selectedRecord.connected ? 'Last observed ' : 'Disconnected / last observed ') + age + 's ago';
    if (age > 15) for (const element of root.querySelectorAll('.is-observed')) element.classList.remove('is-observed');
    if (age > 15 && selectedRecord.connected && ['queued','running'].includes(selectedRecord.job?.state)) {
      const monitor = root.querySelector('.invoice-monitor');
      if (monitor && monitor.classList.contains(selectedRecord.job.state)) { monitor.classList.add('stale'); monitor.querySelector('small').textContent = 'Last known state'; monitor.querySelector('strong').textContent = 'Awaiting a fresh observation'; }
    }
  }, 1000);
})();