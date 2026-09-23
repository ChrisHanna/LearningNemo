(() => {
  function availableSteps({ session, analysis, incident, execution }) {
    if (session?.status !== 'authenticated') return [0];
    if (session.persona === 'approver') return [2];
    const steps = [0];
    if (analysis?.receipt || incident?.planId) steps.push(1);
    if (incident?.planId && incident.planState !== 'draft') steps.push(2);
    if (incident?.planId && ['approved', 'consumed'].includes(incident.planState)) steps.push(3);
    if (execution?.planId === incident?.planId && ['verification', 'completed'].includes(execution?.record?.state)) steps.push(4);
    return steps;
  }
  function nextAction({ step, session, analysis, incident, review, execution, busy, now = Date.now() }) {
    const action = (id, label, message, disabled = false) => ({ id, label, message, disabled });
    if (busy) return action('wait', 'Request in progress', 'Waiting for the recorded outcome.', true);
    if (session?.status !== 'authenticated') return action('signin', session?.status === 'pending' ? 'Continue sign-in' : 'Sign in', 'Use your assigned account.');
    if (step === 2) {
      if (session.persona !== 'approver') return ['approved', 'consumed'].includes(incident?.planState)
        ? action('execute-step', 'Continue to execution', 'Independent approval recorded.')
        : action('switch', 'Switch to Approver', 'The sponsor cannot approve their own plan.');
      if (review?.authorizationRequired) return action('authorize', 'Authorize review access', 'This older session needs the review scope.');
      if (review?.connected && review.count) return action('plan', 'Review the plan', 'Confirm the exact plan below.', true);
      return action('reviews', 'Refresh review queue', review?.error ? 'Review service unavailable.' : 'No submitted plan is awaiting review.');
    }
    if (session.persona !== 'operator') return action('switch', 'Switch to Operator', 'An Operator can analyze and propose; the Approver makes the independent decision.');
    if (step === 0) {
      if (incident?.planId && (incident.planState === 'consumed' || incident.planState === 'approved' && !incident.expired)) return action('execute-step', 'Continue to execution', 'Inspect the approved plan and its persisted execution state. No new proposal is required.');
      if (incident?.planId && !incident.expired) return action('propose-step', 'Inspect saved plan', 'Continue with your recorded plan. No new analysis is required.');
      if (analysis?.receipt) return action('propose-step', 'Inspect proposal', 'Diagnostics retrieved. No workload changed and no query cancelled.');
      return action('analyze', 'Analyze database', analysis?.error || 'Read current diagnostic data. No query starts, cancellations, or database configuration changes.');
    }
    if (step === 1) {
      if (incident?.planId && !incident.expired) {
        if (incident.planState === 'draft') return action('plan', 'Inspect draft', 'Confirm the exact draft below before submitting it.', true);
        return ['approved', 'consumed'].includes(incident.planState)
          ? action('execute-step', 'Continue to execution', 'Independent approval recorded.')
          : action('approve-step', 'Continue to approval', 'A different person must review this plan.');
      }
      const receipt = analysis?.receipt;
      const age = now - Date.parse(receipt?.observed_at);
      if (!receipt || !Number.isFinite(age) || age < 0 || age > 900000) return action('analyze-step', 'Refresh analysis', 'Fresh evidence is required before proposing a change.');
      if (receipt.snapshot.active_query_version !== 'cycle-unsafe-v1') return action('analyze-step', 'Back to analysis', 'No registered change is proposed for the observed query version.');
      if (Object.values(receipt.snapshot.query_run_states).some(value => ['starting', 'running', 'cancel_requested'].includes(value)))
        return action('analyze-step', 'Recheck database', 'An active query prevents this proposal. It will not be cancelled.');
      return action('propose', 'Create draft proposal', analysis?.error || 'Propose cycle-safe-v1 for independent review. The database remains unchanged.');
    }
    if (!execution?.connected || execution.planId !== incident?.planId) return action('execution-status', 'Check execution availability', execution?.error || 'Read persisted execution state before sending a command.');
    const record = execution.record;
    if (record?.state === 'completed') return action('done', 'Incident completed', 'Verified outcome and sponsor acknowledgement recorded.', true);
    if (record?.state === 'verification') return step === 3 ? action('verify-step', 'Inspect verification', 'Independent checks are recorded.') : action('complete', 'Acknowledge completion', 'Acknowledge this exact verified outcome.');
    if (record) return action('reconcile', 'Reconcile execution', 'Read committed evidence and repeat only verification. No remediation is replayed.');
    if (incident?.expired || incident?.planState !== 'approved') return action('incidents', 'Refresh approval', 'A live independent approval is required.');
    return action('execute', 'Execute approved plan', 'Apply only the exact approved operation once. No query cancellation is permitted.');
  }
  const flow = { availableSteps, nextAction };
  if (typeof module !== 'undefined' && module.exports) module.exports = flow;
  else window.incidentFlow = flow;
})();