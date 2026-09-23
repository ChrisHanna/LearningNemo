(() => {
  const find = id => document.getElementById(id);
  const roles = {
    reader: {
      title: "Reader: prove that a denied action changes nothing",
      purpose: "Capture task state, attempt a write, then compare the state. A denied write is the expected result.",
      can: "Read demo tasks. Run the Reader denial test. Inspect available workspace status.",
      cannot: "Complete or reset tasks. Approve plans. A write attempt in this test must be denied.",
      next: "Review your three test steps, then start the Reader test.",
      action: "Start Reader test",
    },
    operator: {
      title: "Operator: make a permitted change and verify it",
      purpose: "Reset the disposable tasks, confirm the baseline, complete task-1, then read its state back.",
      can: "Read, reset, and complete demo tasks. Run the fixed workspace proof when its live checks permit it.",
      cannot: "Approve your own critical plan. Treat a task test as an approved SQL remediation.",
      next: "Review the reset and task change below before starting the Operator test.",
      action: "Start Operator test",
    },
    approver: {
      title: "Approver: independent review, not execution",
      purpose: "Your reviewer identity is recognized. The plan queue and approval submission service are not connected yet.",
      can: "Check review-service availability and inspect explanatory material.",
      cannot: "Run task or workspace operations. Submit an approval in this build.",
      next: "Review service is not connected. There is no plan awaiting your decision here.",
      action: "Approval unavailable",
    },
  };
  let key = null;
  let generation = 0;
  let switching = false;
  let loading = false;
  const operations = new Set();
  let phase = "idle";
  let review = null;
  let incident = null;

  function incidentStatus() {
    if (state.session?.persona !== "operator" || !incident) return;
    if (!incident.connected) {
      find("sessionTitle").textContent = roles.operator.title;
      find("sessionPurpose").textContent = roles.operator.purpose;
      find("sessionCan").textContent = roles.operator.can;
      find("sessionCannot").textContent = roles.operator.cannot;
      document.querySelector('[data-session-role="operator"] span').textContent = "Reset / execute / verify changed";
      if (phase === "idle") {
        find("sessionNextAction").textContent = "Incident handoff unavailable. Only the separate demo-task test is available.";
        find("sessionMode").textContent = "Incident handoff unavailable";
      }
      return;
    }
    find("sessionTitle").textContent = "Operator: submit a recorded investigation for review";
    find("sessionPurpose").textContent = "Inspect your incident's exact plan and evidence references, then hand it to an independent Approver.";
    find("sessionCan").textContent = "Read your persisted investigations and submit an eligible draft for review.";
    find("sessionCannot").textContent = "Approve your own plan or infer execution success from an approval.";
    document.querySelector('[data-session-role="operator"] span').textContent = "Investigate / inspect / submit for review";
    if (phase === "idle") {
      find("sessionNextAction").textContent = !incident.count ? "No investigation returned for this account. The trusted producer must record one first."
        : incident.expired ? "This investigation has expired. A new bounded investigation is required."
        : incident.planState === "awaiting_approval" ? "Plan submitted. Await an independent Approver decision, then refresh this investigation."
        : incident.planState === "approved" || incident.planState === "consumed" ? "Approval is recorded. No execution or recovery result is established by this view."
        : incident.planState === "rejected" ? "Plan rejected. A revised investigation and a new review are required."
        : incident.canSubmit === false ? "This investigation is not eligible for submission. Refresh its recorded state."
        : "Inspect the investigation below and submit its exact plan for review.";
      find("sessionMode").textContent = "Incident handoff / Azure SQL";
    }
  }

  function reviewStatus() {
    if (state.session?.persona !== "approver") return;
    if (state.reviewEnabled && !state.session?.grantedScopes?.includes("plans.review")) review = { authorizationRequired: true };
    if (!review) return;
    if (review.authorizationRequired) {
      find("sessionPurpose").textContent = "Your Approver identity is recognized. Authorize the review-specific permission to access submitted plans.";
      find("sessionCan").textContent = "Request review authorization for this assigned account.";
      find("sessionCannot").textContent = "Submit decisions before authorization, execute tasks, or operate the workspace.";
      find("sessionNextAction").textContent = "Choose Authorize review access and finish Microsoft sign-in with the same Approver account.";
      find("sessionMode").textContent = "Review authorization required";
      return;
    }
    if (review.loading) {
      find("sessionNextAction").textContent = "Checking the review service. No decision is being submitted.";
      find("sessionMode").textContent = "Checking review availability";
      return;
    }
    const connected = review.connected;
    document.querySelector('[data-session-role="approver"] span').textContent = connected ? "Independent reviewer / exact-plan decision" : review.error ? "Independent reviewer / service unavailable" : "Separate reviewer / service not connected";
    find("sessionPurpose").textContent = connected ? "Review a persisted plan and its exact parameters. A decision never executes the change." : review.error ? "Review service could not be verified. No decision or empty queue is assumed." : roles.approver.purpose;
    find("sessionCan").textContent = connected ? "Inspect submitted plans. Approve or reject an eligible plan authored by a different identity." : roles.approver.can;
    find("sessionCannot").textContent = connected ? "Execute tasks or workspace operations. Approve your own, changed, or expired plan." : roles.approver.cannot;
    if (phase === "idle") {
      find("sessionNextAction").textContent = connected ? review.count ? "Inspect a submitted plan, confirm its contents, then approve or reject it." : "No reviewable plans returned. An Operator must submit a plan before review." : review.error ? "Review service unavailable. Recheck service status before deciding." : roles.approver.next;
      find("sessionMode").textContent = connected ? "Live review / Azure SQL" : review.error ? "Review unavailable" : "Review service not connected";
    }
  }

  function controls() {
    const signedIn = state.session?.status === "authenticated";
    const working = operations.size > 0 || state.scenarioRunning;
    for (const id of ["sessionSwitch", "sessionSignOut", "signOutButton"]) find(id).disabled = switching || working;
    find("sessionSignIn").disabled = (switching && state.session?.status !== "pending") || working || !state.csrfToken;
    find("sessionSignIn").hidden = signedIn;
    for (const id of ["sessionSwitch", "sessionSignOut", "sessionOpen"]) find(id).hidden = !signedIn;
    const incidentMode = state.session?.persona === "operator" && incident?.connected;
    find("runScenarioButton").disabled = !signedIn || state.session?.persona === "approver" || incidentMode || working || loading;
    find("sendButton").disabled = !signedIn || state.session?.persona === "approver" || incidentMode || working;
  }

  async function preview(requestGeneration) {
    loading = true;
    find("sessionMode").textContent = "Loading test steps";
    controls();
    try {
      const plan = await api("/api/walkthrough");
      if (requestGeneration !== generation || state.scenarioRunning) return;
      if (plan.persona !== state.session?.persona || !plan.steps?.length) throw new Error("No matching role test is available");
      renderPlan(plan);
      find("progressLabel").textContent = "Preview only / no task requests sent";
      updatePreflight("idle", "Expected access decisions shown below. Start the test to observe actual results.");
      find("sessionMode").textContent = "Ready / no action running";
    } catch (error) {
      if (requestGeneration !== generation) return;
      phase = "error";
      find("sessionMode").textContent = "Test steps unavailable";
      find("progressLabel").textContent = error.message;
      find("sessionNextAction").textContent = "Could not load the role test. Retry with Start, or sign in again if your session expired.";
      find("scenarioSteps").textContent = "No test steps loaded. No task request was sent.";
      updatePreflight("fail", error.message);
    } finally {
      if (requestGeneration === generation) { loading = false; controls(); incidentStatus(); }
    }
  }

  function update(session) {
    const signedIn = session?.status === "authenticated";
    const next = signedIn ? `${session.persona}:${session.accountFingerprint}` : null;
    const changed = next !== key;
    const role = signedIn ? roles[session.persona] : null;
    if (changed) {
      key = next;
      generation += 1;
      loading = false;
      phase = "idle";
      review = null;
      incident = null;
      document.querySelector('[data-session-role="operator"] span').textContent = "Reset / execute / verify changed";
      document.querySelector('[data-session-role="approver"] span').textContent = "Independent reviewer / check service availability";
      resetScenario();
      state.activities = [];
      find("activityCount").textContent = "0";
      renderActivities();
      find("manualResponse").textContent = "No response in this account session.";
      document.querySelectorAll(".session-technical").forEach(panel => { panel.open = false; });
      find("promptInput").value = "";
      find("sessionMode").textContent = role?.action === "Approval unavailable" ? "Review service not connected" : "No action running";
      if (signedIn) {
        find("identityViewTab").click();
        if (session.persona !== "approver" && !find('missionWorkspace')) preview(generation);
      }
    }
    find("sessionRoleName").textContent = signedIn ? personaLabel(session.persona) : statusLabel(session?.status || "signed_out");
    find("sessionAccountProof").textContent = signedIn ? `Account ${session.accountFingerprint}` : "No role selected";
    find("sessionSwitch").querySelector("span").textContent = session?.authMode === "local-demo" ? "Change role" : "Switch account";
    find("sessionTitle").textContent = role?.title || "Choose an assigned account to begin";
    find("sessionPurpose").textContent = role?.purpose || "Reader tests denied changes. Operator tests permitted changes. Approver review is not connected yet.";
    find("sessionCan").textContent = role?.can || "Sign-in and architecture review.";
    find("sessionCannot").textContent = role?.cannot || "No task or workspace actions before sign-in.";
    if (phase === "idle") find("sessionNextAction").textContent = role?.next || (session?.status === "pending"
      ? "Waiting for Microsoft sign-in. No role test has started."
      : session?.status === "expired" ? "Session expired. Sign in again to continue."
      : session?.status === "error" ? "Sign-in failed. Retry with an assigned demo account."
      : "Sign in with your assigned demo account.");
    if (!state.scenarioRunning) find("runScenarioButton").querySelector("span").textContent = role?.action || "Sign in to start";
    find("walkthroughPurpose").textContent = role?.purpose || "No tasks execute until you start a test.";
    document.querySelectorAll("[data-session-role]").forEach(item => {
      item.classList.toggle("is-current", signedIn && item.dataset.sessionRole === session.persona);
      item.setAttribute("aria-label", `${item.dataset.sessionRole}${signedIn && item.dataset.sessionRole === session.persona ? ' / your current role' : ' / different account'}`);
    });
    controls();
    reviewStatus();
    incidentStatus();
  }

  find("sessionOpen").addEventListener("click", () => find("identityViewTab").click());
  find("sessionSignIn").addEventListener("click", () => authenticate().catch(error => toast(error.message, "error")));
  find("sessionSignOut").addEventListener("click", () => clearAuthentication().catch(error => toast(error.message, "error")));
  find("sessionSwitch").addEventListener("click", async () => {
    if (switching || operations.size || state.scenarioRunning) return;
    if (state.session?.authMode === "local-demo") {
      updateAuthDialog(state.session);
      if (!elements.authDialog.open) elements.authDialog.showModal();
      return;
    }
    switching = true;
    controls();
    try {
      await clearAuthentication();
      find("identityViewTab").click();
      await authenticate();
    } catch (error) { toast(error.message, "error"); }
    finally { switching = false; controls(); }
  });
  window.addEventListener("console-session-changed", event => update(event.detail));
  window.addEventListener("console-incident-changed", event => {
    incident = event.detail;
    if (!operations.size) phase = "idle";
    incidentStatus();
    controls();
  });
  window.addEventListener("console-review-changed", event => {
    review = event.detail;
    if (!operations.has("review")) phase = "idle";
    reviewStatus();
  });
  window.addEventListener("console-operation-changed", event => {
    const operation = event.detail;
    if (operation.busy) operations.add(operation.id); else operations.delete(operation.id);
    if (operation.id === "walkthrough") {
      phase = operation.busy ? "running" : "result";
      find("sessionMode").textContent = operation.busy ? "Live test running" : "Live test finished";
    }
    if (operation.id !== "walkthrough") phase = operation.busy ? "running" : "result";
    find("sessionNextAction").textContent = operation.message;
    controls();
  });
  update(state.session);
})();