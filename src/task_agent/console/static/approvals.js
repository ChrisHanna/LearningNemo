(() => {
  const find = id => document.getElementById(id);
  let identity = null;
  let generation = 0;
  let busy = false;
  let plans = [];
  let selectedPlan = null;
  let expiryTimer = null;
  const container = document.createElement("div");
  container.id = "reviewPlans";
  find("approvalsWorkspace").append(container);
  const authorize = document.createElement("button");
  authorize.type = "button";
  authorize.className = "button button-primary";
  authorize.textContent = "Authorize review access";
  authorize.hidden = true;
  find("approverStatus").after(authorize);
  authorize.addEventListener("click", () => authenticate(true).catch(error => toast(error.message, "error")));
  const element = (tag, text, className = "") => {
    const node = document.createElement(tag);
    node.textContent = text;
    node.className = className;
    return node;
  };

  function renderPlans() {
    clearTimeout(expiryTimer);
    container.replaceChildren();
    if (!plans.length) return;
    if (!plans.some(record => record.plan.plan_id === selectedPlan)) selectedPlan = plans[0].plan.plan_id;
    const heading = element("div", "", "review-heading");
    heading.append(element("h2", "Plan review"));
    const pickerLabel = element("label", "Plan", "review-picker");
    const picker = element("select", "");
    picker.disabled = busy;
    picker.setAttribute("aria-label", "Plan to review");
    for (const record of plans) {
      const option = element("option", `${record.plan.content.task_id} / ${record.plan.state}`);
      option.value = record.plan.plan_id;
      picker.append(option);
    }
    picker.value = selectedPlan;
    picker.addEventListener("change", () => { selectedPlan = picker.value; renderPlans(); });
    pickerLabel.append(picker);
    heading.append(pickerLabel);
    container.append(heading);
    for (const record of plans.filter(record => record.plan.plan_id === selectedPlan)) {
      const plan = record.plan;
      const expired = Date.parse(record.expires_at) <= Date.now();
      if (!expired) expiryTimer = setTimeout(renderPlans, Math.min(Date.parse(record.expires_at) - Date.now() + 25, 2147483647));
      const article = element("article", "", "review-plan");
      article.append(element("h3", "Activate the cycle-safe query"), element("p", `${plan.content.task_id} / ${plan.plan_id} / version ${plan.version}${expired ? ' / expired' : ''}`));
      const stage = element("ol", "", "review-stages");
      for (const [name, status] of [["Submitted plan", "Persisted"], ["Human decision", plan.state === 'awaiting_approval' ? 'Awaiting review' : plan.state === 'rejected' ? 'Rejected' : 'Approved'], ["Broker consumption", plan.state === 'consumed' ? 'Recorded' : 'Not recorded']]) {
        const item = element("li", "");
        item.append(element("strong", name), element("span", status));
        stage.append(item);
      }
      article.append(stage, element("p", "Consumption is not independent verification. No execution or recovery result is inferred from this plan record.", "live-detail"));
      const fields = element("dl", "", "review-fields");
      for (const [label, value] of [
        ["Incident", plan.content.task_id], ["Workspace", plan.content.workspace_id],
        ["Agent", plan.content.logical_agent_id], ["Registered operation", plan.content.operation_id],
        ["Target", plan.content.target_resource], ["Proposed version", plan.content.safe_query_version],
        ["Rollback version", plan.content.rollback_version], ["Plan hash", plan.plan_hash],
        ["Author identity hash", plan.created_by_hash], ["Review deadline", record.expires_at],
      ]) {
        const row = element("div", "");
        row.append(element("dt", label), element("dd", value));
        fields.append(row);
      }
      article.append(fields);
      const parameters = element("details", "");
      parameters.append(element("summary", "Exact parameters"), element("pre", JSON.stringify(plan.content.parameters, null, 2)));
      article.append(parameters);
      if (record.canDecide && plan.state === "awaiting_approval" && !expired) {
        const actions = element("div", "", "review-actions");
        for (const decision of ["approve", "reject"]) {
          const button = element("button", decision === "approve" ? "Approve exact plan" : "Reject plan", `button button-${decision === "approve" ? 'primary' : 'secondary'}`);
          button.type = "button";
          button.prepend(createIcon(decision === "approve" ? "check" : "x"));
          button.disabled = busy;
          button.addEventListener("click", () => decide(record, decision));
          actions.append(button);
        }
        article.append(actions);
      } else {
        article.append(element("p", expired ? "Review deadline passed. No decision can be submitted." : plan.state !== "awaiting_approval" ? "This plan is no longer awaiting a decision." : "This identity cannot decide this plan.", "live-detail"));
      }
      container.append(article);
    }
    refreshIcons();
  }

  async function decide(record, decision) {
    if (!identity || busy || !record.canDecide || Date.parse(record.expires_at) <= Date.now()) return;
    const requestGeneration = generation;
    busy = true;
    find("approverRefresh").disabled = true;
    container.querySelectorAll("button, input, select").forEach(control => { control.disabled = true; });
    notifyOperation("review", true, "Submitting an exact-plan decision. No remediation is being executed.");
    find("approverStatus").textContent = "Submitting decision to the review service...";
    let resultMessage = "Decision could not be confirmed. Refresh the queue before retrying.";
    try {
      const result = await api(`/api/approvals/${encodeURIComponent(record.plan.plan_id)}/decision`, {
        method: "POST", body: JSON.stringify({ decision, plan_hash: record.plan.plan_hash, plan_version: record.plan.version }),
      });
      if (requestGeneration !== generation) return;
      const expected = decision === "approve" ? "approved" : "rejected";
      if (result.planId !== record.plan.plan_id || result.planHash !== record.plan.plan_hash || result.state !== expected) throw new Error("Decision response did not match the reviewed plan");
      resultMessage = `Plan ${expected}. No remediation was executed. ${decision === 'approve' ? 'Operator execution remains a separate authorized action.' : 'A revised plan needs a new review.'}`;
    } catch (error) {
      if (requestGeneration !== generation) return;
      resultMessage = `${error.message}. Refresh before retrying; no successful decision is assumed.`;
    } finally {
      if (requestGeneration === generation) {
        busy = false;
        plans = [];
        renderPlans();
        find("approverRefresh").disabled = false;
        find("approverStatus").textContent = resultMessage;
      }
      notifyOperation("review", false, requestGeneration === generation ? resultMessage : "Account changed. Previous review response discarded.");
    }
  }

  async function refresh() {
    if (!identity || busy) return;
    if (state.reviewEnabled && !state.session?.grantedScopes?.includes("plans.review")) {
      authorize.hidden = false;
      find("approverStatus").textContent = "Approver identity recognized. Authorize the review-specific scope to access submitted plans.";
      find("approverAvailability").textContent = "Review authorization required";
      find("approvalsWorkspace").querySelector(".session-approval-banner").hidden = true;
      window.dispatchEvent(new CustomEvent("console-review-changed", { detail: { authorizationRequired: true } }));
      return;
    }
    const requestGeneration = generation;
    busy = true;
    plans = [];
    renderPlans();
    find("approverRefresh").disabled = true;
    find("approverStatus").textContent = "Checking plan review service availability...";
    window.dispatchEvent(new CustomEvent("console-review-changed", { detail: { loading: true } }));
    try {
      const result = await api("/api/approvals");
      if (requestGeneration !== generation) return;
      if (result.persona !== "approver") {
        throw new Error("Unrecognized approval service response");
      }
      const connected = result.source === "azure-sql" && result.availability === "connected" && result.canSubmitDecision === true && Array.isArray(result.plans);
      const unavailable = result.source === "service-configuration" && result.availability === "not-configured" && result.canSubmitDecision === false;
      if (!connected && !unavailable) throw new Error("Unrecognized approval service response");
      find("approverStatus").textContent = result.detail;
      find("approverAvailability").textContent = connected ? "Connected / Azure SQL" : "Not connected";
      find("approvalsWorkspace").querySelector(".session-approval-banner").hidden = connected;
      if (connected) {
        plans = result.plans;
        renderPlans();
        if (!plans.length) container.append(element("p", "No reviewable plans returned by the service.", "live-detail"));
      }
      window.dispatchEvent(new CustomEvent("console-review-changed", { detail: { connected, count: plans.length } }));
    } catch (error) {
      if (requestGeneration !== generation) return;
      find("approverStatus").textContent = `Approval status unavailable: ${error.message}`;
      find("approverAvailability").textContent = "Unavailable / not verified";
      plans = [];
      renderPlans();
      window.dispatchEvent(new CustomEvent("console-review-changed", { detail: { connected: false, error: true } }));
    } finally {
      if (requestGeneration === generation) {
        busy = false;
        find("approverRefresh").disabled = !identity;
        if (plans.length) renderPlans();
      }
    }
  }

  function update(session) {
    if (state.invoiceEnabled) { identity = null; generation += 1; find('approvalsWorkspace').hidden = true; return; }
    const next = session?.status === "authenticated" && session.persona === "approver" ? session.accountFingerprint : null;
    const changed = next !== identity;
    if (changed) {
      generation += 1;
      busy = false;
      identity = next;
      selectedPlan = null;
      plans = [];
      renderPlans();
      find("approvalsWorkspace").querySelector(".session-approval-banner").hidden = false;
      find("approverAvailability").textContent = "Not checked";
      find("approverStatus").textContent = next ? "Approver recognized. Checking review service..." : "Sign in with the dedicated Approver account.";
    }
    find("approverIdentity").textContent = next ? `Approver / ${next}` : "Not signed in";
    authorize.hidden = !(next && state.reviewEnabled && !session.grantedScopes?.includes("plans.review"));
    find("approverRefresh").disabled = !next || busy;
    find("approvalsWorkspace").hidden = !next;
    find("taskExperience").hidden = Boolean(next);
    if (changed && next) {
      refresh();
    }
  }

  find("approverRefresh").addEventListener("click", refresh);
  window.addEventListener("console-session-changed", event => update(event.detail));
  update(state.session);
})();