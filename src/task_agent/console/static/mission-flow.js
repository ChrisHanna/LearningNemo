(() => {
  function progressLabels({ step, workspace, incident, busy = false }) {
    if (incident?.planId) return { title: `${incident.taskId} / ${incident.planState}`, evidence: 'Persisted investigation' };
    if (step === 1) {
      if (busy || workspace?.busy) return { title: 'Checking workspace', evidence: 'Awaiting result' };
      if (workspace?.result?.receipt) return { title: workspace.result.receipt.passed ? 'Containment passed' : 'Containment failed', evidence: 'Sandbox proof recorded' };
      if (workspace?.cloud && !workspace.cloud.readyForProbe) {
        const reason = workspace.cloud.vm !== 'running' ? 'Workspace stopped' : workspace.cloud.lease !== 'valid' ? 'Workspace lease expired' : 'Workspace prerequisites blocked';
        return { title: reason, evidence: workspace.stale ? 'Last observation / recheck' : 'Proof not run' };
      }
      return { title: 'Containment proof pending', evidence: 'No sandbox command completed' };
    }
    return { title: 'Incident not started', evidence: 'No live receipt' };
  }

  function workspaceBlocker(cloud) {
    if (cloud.vm !== 'running') return 'The SAW VM is stopped. An Operator sign-in cannot start it. The workspace must be restored before this proof can run; retained disks have not been deleted.';
    if (cloud.lease !== 'valid') return 'The workspace lease has expired or is too short. Renew the runtime lease before running the proof.';
    if (cloud.runtimeLock !== 'deployed') return 'The workspace network policy is not verified. Execution is blocked until its protections are restored.';
    if (!['absent', 'runtime-verified'].includes(cloud.nat)) return 'The outbound network configuration is not verified. Execution remains blocked.';
    return 'The workspace did not pass its readiness checks. No sandbox command was sent.';
  }

  function nextAction({ step, session, workspace, incident, review, execution, busy = false, now = Date.now() }) {
    const action = (id, label, message, disabled = false) => ({ id, label, message, disabled });
    if (busy || workspace?.busy) return action('wait', 'Request in progress', 'Waiting for the current request. No additional action will be sent.', true);
    if (session?.status !== 'authenticated') return action('signin', session?.status === 'pending' ? 'Continue sign-in' : 'Sign in to begin', session?.status === 'pending' ? 'Reopen the sign-in dialog to continue the existing request.' : 'Sign in with your assigned account. No demo action has started.');
    const role = session.persona;
    if (step === 0) return action('continue', role === 'approver' ? 'Continue to review' : 'Continue to workspace', 'Identity verified. The next step checks the environment, not your role again.');
    if (step === 1) {
      if (role !== 'operator') return action('switch', 'Switch to Operator', 'This step requires an Operator account. No workspace command has been sent.');
      if (!workspace?.enabled) return action('unavailable', 'Workspace connection unavailable', workspace?.configurationError || 'The console has not established its workspace connection.', true);
      const cloud = workspace.cloud;
      const fresh = cloud && !workspace.stale && Number.isFinite(Date.parse(cloud.checkedAt)) && now - Date.parse(cloud.checkedAt) < cloud.freshForSeconds * 1000 && now >= Date.parse(cloud.checkedAt);
      if (!fresh) return action('check', 'Check workspace readiness', workspace?.error || (cloud && !cloud.readyForProbe
        ? `Last observation: ${workspaceBlocker(cloud)} Recheck to confirm the current state.`
        : 'Check the VM, lease, and network policy before sending a sandbox command.'));
      if (!cloud.readyForProbe) return action('check', 'Recheck workspace', workspaceBlocker(cloud));
      if (!Number.isFinite(Date.parse(cloud.expiresAt)) || Date.parse(cloud.expiresAt) <= now + 300000) return action('check', 'Recheck workspace lease', 'The workspace lease is missing, expired, or too short. No sandbox command will be sent.');
      if (workspace.result?.receipt?.passed) return action('continue', 'Continue to investigation', 'Containment passed. The next step needs a persisted investigation; this proof is not an incident run.');
      if (workspace.result?.receipt) return action('run', 'Retry containment proof', `The sandbox returned GET ${workspace.result.receipt.readHttpStatus || 'unreachable'} and POST ${workspace.result.receipt.writeHttpStatus || 'unreachable'}. Expected 401 and 403. This step has not passed.`);
      return action('run', 'Run containment proof', workspace.error || 'Workspace prerequisites passed. Run the fixed read/write and non-root checks.');
    }
    if (step === 2) {
      if (role !== 'operator') return action('switch', 'Switch to Operator', 'Investigations belong to their verified sponsor.');
      if (!incident?.connected) return action('incidents', 'Load investigation', 'Check for a persisted investigation belonging to this account.');
      if (!incident.count) return incident.initiationAvailable
        ? action('initiate', incident.pendingRequest ? 'Check investigation request' : 'Start investigation', incident.initiationError || 'Start the controlled SQL investigation, cancel its owned query, and record a draft for independent review. No remediation will execute.')
        : action('unavailable', 'Investigation not available', 'Incident initiation is not enabled on the private service. No plan is inferred from the containment proof.', true);
      if (incident.expired) return action('unavailable', 'Investigation expired', 'A new investigation is required before review.', true);
      if (incident.planState === 'draft') return action('plan', 'Review the plan below', 'Inspect the exact plan, then confirm its submission below. Submission does not execute remediation.', true);
      if (['approved', 'consumed'].includes(incident.planState)) return action('execution-step', 'Continue to execution', 'The independent decision is recorded. Inspect execution status before proceeding.');
      return action('continue', 'Continue to review', 'The plan is recorded. Review requires a different person.');
    }
    if (step === 3) {
      if (role === 'operator' && ['approved', 'consumed'].includes(incident?.planState)) return action('execution-step', 'Continue to execution', 'The independent decision is recorded for this plan.');
      if (role !== 'approver') return action('switch', 'Switch to reviewer account', 'The Operator cannot approve their own plan. Sign in as the independent Approver.');
      if (review?.authorizationRequired) return action('authorize', 'Authorize review access', 'Authorize the review-specific scope with this same Approver account.');
      if (review?.connected && review.count) return action('plan', 'Review the plan below', 'Inspect the plan and make an explicit approve or reject decision below.', true);
      return action('reviews', 'Refresh review queue', review?.connected ? 'No plans are awaiting this reviewer. An Operator must submit a plan first.' : 'Read the independent review queue. No approval is submitted by this check.');
    }
    if (step >= 4) {
      if (role !== 'operator') return action('switch', 'Switch to Operator', 'Only the sponsor can execute and acknowledge completion.');
      if (!incident?.planId) return action('incidents', 'Load investigation', 'Select the sponsor-owned plan before execution.');
      if (!execution?.connected || execution.planId !== incident.planId) return action('execution-status', 'Check execution status', execution?.error || 'Read persisted execution evidence before sending a command.');
      const record = execution.record;
      if (record?.state === 'completed') return action('done', 'Incident completed', 'The broker change, independent verification, and sponsor acknowledgement are recorded.', true);
      if (record?.state === 'verification') return step === 4
        ? action('continue', 'Inspect verified result', 'Execution and independent verification are persisted. Review the checks before completion.')
        : action('complete', 'Acknowledge completion', 'Confirm completion of this exact independently verified execution.');
      if (record) return action('reconcile', 'Reconcile execution', 'Read the committed broker outcome and repeat only independent verification. Remediation is never replayed.');
      if (step === 5) return action('unavailable', 'Verification not recorded', 'An approved execution must finish before completion.', true);
      if (incident.expired || incident.planState !== 'approved') return action('incidents', 'Refresh approval', 'A current independent approval is required for this exact plan.');
      return action('execute', 'Execute approved plan', 'Apply the exact approved cycle-safe query once, then run the independent verifier.');
    }
    return action('unavailable', 'Unavailable', 'No action available.', true);
  }

  function unlockedSteps({ session, workspace, incident, execution }) {
    if (session?.status !== 'authenticated') return [0];
    if (session.persona === 'approver') return [0, 3];
    const steps = [0, 1];
    if (workspace?.result?.receipt?.passed || incident?.planId) steps.push(2);
    if (incident?.planId && !incident.expired && incident.planState !== 'draft') steps.push(3);
    if (incident?.planId && (['approved', 'consumed'].includes(incident.planState) || execution?.record)) steps.push(4);
    if (execution?.planId === incident?.planId && ['verification', 'completed'].includes(execution?.record?.state)) steps.push(5);
    return steps;
  }
  const flow = { nextAction, unlockedSteps, workspaceBlocker, progressLabels };
  if (typeof module !== 'undefined' && module.exports) module.exports = flow;
  else window.missionFlow = flow;
})();