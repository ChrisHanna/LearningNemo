(() => {
  let identity = null;
  let generation = 0;
  let selected = null;
  let status = null;
  let busy = false;
  let message = '';
  const emit = () => window.dispatchEvent(new CustomEvent('console-execution-changed', { detail: { planId: selected?.planId, connected: status?.availability === 'connected', record: status?.execution, error: message } }));

  async function refresh() {
    if (!identity || !selected?.planId || busy) return;
    const current = generation;
    busy = true;
    notifyOperation('execution', true, 'Reading persisted execution status. No write is being replayed.');
    try {
      const result = await api(`/api/executions/${encodeURIComponent(selected.planId)}`);
      if (current !== generation) return;
      if (result.availability !== 'connected' || result.source !== 'azure-sql' || !('execution' in result)) throw new Error('Execution service not connected');
      if (result.execution && (result.execution.plan_id !== selected.planId || result.execution.plan_hash !== selected.planHash)) throw new Error('Execution evidence belongs to another plan');
      status = result;
      message = '';
    } catch (error) {
      if (current === generation) { status = null; message = error.message; }
    } finally {
      if (current === generation) { busy = false; emit(); }
      notifyOperation('execution', false, 'Execution status read finished.');
    }
  }

  async function mutate(complete) {
    if (!identity || busy || status?.availability !== 'connected' || !selected?.planId) return;
    if (complete ? status.execution?.state !== 'verification' : status.execution || selected.planState !== 'approved' || selected.expired) return;
    const current = generation;
    const path = complete ? `/api/executions/${status.execution.execution_id}/complete` : `/api/executions/${selected.planId}`;
    const body = complete ? { plan_hash: selected.planHash } : { plan_hash: selected.planHash, plan_version: selected.planVersion };
    busy = true;
    notifyOperation('execution', true, complete ? 'Recording completion acknowledgement.' : 'Executing the exact approved operation and verifying its result.');
    try {
      await api(path, { method: 'POST', body: JSON.stringify(body) });
    } catch (error) {
      if (current === generation) message = `${error.message} Check persisted status before continuing.`;
    } finally {
      if (current === generation) busy = false;
      notifyOperation('execution', false, 'Request returned. Checking the persisted outcome.');
    }
    if (current === generation) await refresh();
  }

  window.addEventListener('console-execution-requested', refresh);
  window.addEventListener('console-execute-approved', () => mutate(false));
  window.addEventListener('console-complete-execution', () => mutate(true));
  window.addEventListener('console-reconcile-execution', async () => {
    if (!identity || busy || !status?.execution || !['claimed', 'broker'].includes(status.execution.state)) return;
    const current = generation;
    busy = true;
    notifyOperation('execution', true, 'Reconciling committed broker evidence; remediation will not be replayed.');
    try { await api(`/api/executions/${selected.planId}/reconcile`, { method: 'POST' }); }
    catch (error) { if (current === generation) message = error.message; }
    finally { if (current === generation) busy = false; notifyOperation('execution', false, 'Reconciliation request returned.'); }
    if (current === generation) await refresh();
  });
  window.addEventListener('console-incident-changed', event => {
    const next = event.detail;
    if (next.planId && next.planId !== selected?.planId) { generation += 1; status = null; message = ''; busy = false; }
    if (next.planId) selected = next;
    emit();
  });
  window.addEventListener('console-session-changed', event => {
    const next = event.detail?.status === 'authenticated' && event.detail.persona === 'operator' ? event.detail.accountFingerprint : null;
    if (next !== identity) { identity = next; generation += 1; selected = null; status = null; busy = false; message = ''; emit(); }
  });
})();