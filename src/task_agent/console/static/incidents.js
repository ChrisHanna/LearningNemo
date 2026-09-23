(() => {
  const find = id => document.getElementById(id);
  let identity = null;
  let generation = 0;
  let busy = false;
  let records = [];
  let selected = null;
  let expiryTimer = null;
  let connected = false;
  let initiationAvailable = false;
  let pendingRequest = null;
  let initiationError = '';
  let externalBusy = false;
  const node = (tag, text, className = "") => {
    const element = document.createElement(tag);
    element.textContent = text;
    element.className = className;
    return element;
  };

  function emit(detail) {
    find("taskExperience").hidden = state.session?.persona === "approver" || Boolean(identity && detail.connected);
    window.dispatchEvent(new CustomEvent("console-incident-changed", { detail: { ...detail, initiationAvailable, pendingRequest, initiationError } }));
  }

  function render() {
    clearTimeout(expiryTimer);
    const container = find("incidentPlans");
    container.replaceChildren();
    find("incidentRefresh").disabled = busy || externalBusy || !identity;
    if (!records.length) return;
    if (!records.some(record => record.plan.plan_id === selected)) selected = records[0].plan.plan_id;
    const label = node("label", "Investigation", "review-picker");
    const select = node("select", "");
    select.setAttribute("aria-label", "Investigation to submit");
    select.disabled = busy || externalBusy;
    for (const record of records) {
      const option = node("option", `${record.plan.content.task_id} / ${record.plan.state}`);
      option.value = record.plan.plan_id;
      select.append(option);
    }
    select.value = selected;
    select.addEventListener("change", () => { selected = select.value; render(); });
    label.append(select);
    container.append(label);
    const record = records.find(item => item.plan.plan_id === selected);
    const plan = record.plan;
    const expired = Date.parse(record.expires_at) <= Date.now();
    if (!busy && !externalBusy) emit({ connected, count: records.length, planState: plan.state, expired, canSubmit: record.canSubmit, planId: plan.plan_id, planHash: plan.plan_hash, planVersion: plan.version, taskId: plan.content.task_id });
    if (!expired) expiryTimer = setTimeout(render, Math.min(Date.parse(record.expires_at) - Date.now() + 25, 2147483647));
    const article = node("article", "", "review-plan");
    article.append(node("h3", "Activate the cycle-safe query"), node("p", `${plan.content.task_id} / ${plan.plan_id}`));
    const stages = node("ol", "", "review-stages");
    for (const [title, status] of [
      ["Investigation", "Recorded by trusted service"],
      ["Operator handoff", plan.state === "draft" ? "Not submitted" : "Submitted"],
      ["Independent review", plan.state === "draft" ? "Not requested" : plan.state === "awaiting_approval" ? "Awaiting Approver" : plan.state === "rejected" ? "Rejected" : "Approved"],
    ]) {
      const item = node("li", "");
      item.append(node("strong", title), node("span", status));
      stages.append(item);
    }
    article.append(stages);
    const fields = node("dl", "", "review-fields");
    for (const [title, value] of [["Workspace", plan.content.workspace_id], ["Agent", plan.content.logical_agent_id],
      ["Registered operation", plan.content.operation_id], ["Target", plan.content.target_resource],
      ["Proposed version", plan.content.safe_query_version], ["Rollback", plan.content.rollback_version],
      ["Plan hash", plan.plan_hash], ["Investigation version", String(record.investigation_version)],
      ["Diagnosis receipt hash", record.diagnosis_receipt_hash], ["Safety evidence hash", record.containment_receipt_hash],
      ["Deadline", record.expires_at]]) {
      const row = node("div", "");
      row.append(node("dt", title), node("dd", value));
      fields.append(row);
    }
    article.append(fields);
    const details = node("details", "");
    details.append(node("summary", "Exact parameters"), node("pre", JSON.stringify(plan.content.parameters, null, 2)));
    article.append(details);
    if (record.canSubmit && plan.state === "draft" && !expired) {
      const button = node("button", "Submit for review", "button button-primary");
      button.prepend(createIcon("send"));
      button.type = "button";
      button.disabled = busy || externalBusy;
      button.addEventListener("click", () => submit(record));
      article.append(button);
    } else {
      article.append(node("p", expired ? "Investigation expired. No submission is permitted."
        : plan.state === "awaiting_approval" ? "Submitted. The independent Approver can review this plan."
        : plan.state === "rejected" ? "Rejected. A new investigation and plan are required."
        : plan.state === "approved" || plan.state === "consumed" ? "Approval recorded. Execution and recovery must be verified separately."
        : "This investigation is not eligible for submission.", "live-detail"));
    }
    article.append(node("p", "Receipt references are from the investigation record. This view does not establish current sandbox liveness or verified recovery.", "live-detail"));
    container.append(article);
    refreshIcons();
  }

  async function refresh() {
    if (!identity || busy || externalBusy) return;
    const requestGeneration = generation;
    busy = true;
    records = [];
    render();
    find("incidentStatus").textContent = "Reading your persisted investigations. No incident is being started.";
    try {
      const result = await api("/api/incidents");
      if (requestGeneration !== generation) return;
      connected = result.availability === "connected" && result.source === "azure-sql" && Array.isArray(result.incidents);
      if (!connected && !(result.availability === "not-configured" && result.source === "service-configuration")) throw new Error("Unrecognized incident service response");
      records = connected ? result.incidents : [];
      initiationAvailable = connected && result.initiation_available === true && result.producer === 'trusted-fixed-investigation-v1';
      find("incidentAvailability").textContent = connected ? "Connected / Azure SQL" : "Not connected";
      find("incidentStatus").textContent = connected ? records.length ? "Investigation records loaded. Select a plan to inspect its state." : "No investigations returned for this sponsor. No incident was created by this request." : result.detail;
      emit({ connected, count: records.length });
    } catch (error) {
      if (requestGeneration !== generation) return;
      records = [];
      connected = false;
      initiationAvailable = false;
      find("incidentAvailability").textContent = "Unavailable / not verified";
      find("incidentStatus").textContent = `${error.message}. No empty queue or successful submission is assumed.`;
      emit({ connected: false, error: true });
    } finally {
      if (requestGeneration === generation) { busy = false; render(); }
    }
  }

  async function submit(record) {
    if (!identity || busy || externalBusy || !record.canSubmit || Date.parse(record.expires_at) <= Date.now()) return;
    const requestGeneration = generation;
    busy = true;
    render();
    notifyOperation("incident", true, "Submitting the recorded plan for independent review. No remediation is executing.");
    let message = "Submission unconfirmed. Refresh before retrying.";
    find("incidentStatus").textContent = "Submitting exact plan...";
    try {
      const result = await api(`/api/incidents/${encodeURIComponent(record.plan.plan_id)}/submit`, {
        method: "POST", body: JSON.stringify({ plan_hash: record.plan.plan_hash, investigation_version: record.investigation_version }),
      });
      if (requestGeneration !== generation) return;
      if (result.planId !== record.plan.plan_id || result.planHash !== record.plan.plan_hash || result.state !== "awaiting_approval") throw new Error("Unverified submission response");
      message = "Submitted for independent review. An Approver must decide this exact plan. No remediation was executed.";
    } catch (error) {
      message = `${error.message}. Refresh before retrying; no successful submission is assumed.`;
    } finally {
      if (requestGeneration === generation) {
        busy = false;
        records = [];
        render();
        find("incidentStatus").textContent = message;
      }
      notifyOperation("incident", false, requestGeneration === generation ? message : "Account changed. Previous submission response discarded.");
    }
    if (requestGeneration === generation) await refresh();
  }

  async function start() {
    if (!identity || busy || externalBusy || !initiationAvailable || records.length) return;
    const requestGeneration = generation;
    pendingRequest ||= crypto.randomUUID();
    const requestId = pendingRequest;
    busy = true;
    initiationError = '';
    notifyOperation('incident', true, 'Investigating the controlled SQL query and recording a draft. No remediation is executing.');
    render();
    try {
      const result = await api('/api/incidents/start', { method: 'POST', body: JSON.stringify({ request_id: requestId }) });
      if (requestGeneration !== generation) return;
      if (result.requestId !== requestId || result.state !== 'draft' || !/^plan-[a-f0-9]{32}$/.test(result.planId)) throw new Error('Unverified investigation response');
      selected = result.planId;
      pendingRequest = null;
    } catch (error) {
      if (requestGeneration === generation) initiationError = `${error.message} Request ${requestId}. No successful investigation is assumed.`;
    } finally {
      if (requestGeneration === generation) busy = false;
      notifyOperation('incident', false, requestGeneration === generation ? initiationError || 'Draft recorded. Inspect the exact plan before submission.' : 'Account changed. Investigation response discarded.');
    }
    if (requestGeneration === generation) await refresh();
  }

  function update(session) {
    if (state.invoiceEnabled) { identity = null; generation += 1; find('incidentWorkspace').hidden = true; return; }
    const next = session?.status === "authenticated" && session.persona === "operator" ? session.accountFingerprint : null;
    const changed = identity !== next;
    find("incidentWorkspace").hidden = !next;
    find("taskExperience").hidden = session?.persona === "approver" || Boolean(next && connected);
    if (changed) {
      identity = next;
      generation += 1;
      records = [];
      selected = null;
      connected = false;
      initiationAvailable = false;
      pendingRequest = null;
      initiationError = '';
      busy = false;
      find("incidentAvailability").textContent = "Not checked";
      find("incidentStatus").textContent = "No incident request has been sent for this account.";
      render();
      if (next) refresh();
    }
  }

  find("incidentRefresh").addEventListener("click", refresh);
  window.addEventListener('console-incident-proposed', event => {
    if (!identity || !/^plan-[a-f0-9]{32}$/.test(event.detail?.planId)) return;
    selected = event.detail.planId;
    refresh();
  });
  window.addEventListener('console-incident-requested', () => {
    emit({ connected, count: records.length });
    render();
  });
  window.addEventListener("console-session-changed", event => update(event.detail));
  const running = new Set();
  window.addEventListener("console-operation-changed", event => {
    if (event.detail.id === "incident") return;
    if (event.detail.busy) running.add(event.detail.id); else running.delete(event.detail.id);
    externalBusy = running.size > 0;
    render();
  });
  update(state.session);
})();