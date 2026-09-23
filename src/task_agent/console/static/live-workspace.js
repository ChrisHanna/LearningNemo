(() => {
  const find = id => document.getElementById(id);
  const live = { enabled: false, busy: false, cloud: null, cloudStale: false, result: null, identityKey: null, generation: 0 };
  const element = (tag, text, className = "") => {
    const item = document.createElement(tag);
    item.textContent = text;
    item.className = className;
    return item;
  };
  const labels = {
    running: "Running", "not-running": "Not running", deployed: "Rules deployed",
    blocked: "Blocked", absent: "Absent", attached: "Attached", valid: "Valid",
    "expired-or-too-short": "Expired or less than 5 minutes remain", "not-checked": "Not checked",
    "runtime-verified": "Runtime NAT verified / bootstrap NAT absent",
  };
  const initialSteps = [
    ["Entra identity", "Not verified for this action", "unknown"],
    ["Operator permission", "Not evaluated", "unknown"],
    ["SAW and runtime lock", "Not queried", "unknown"],
    ["Planning MicroVM", "No command executed", "unknown"],
    ["Protected API route", "No request executed", "unknown"],
  ];

  function steps(items) {
    find("liveSteps").replaceChildren(...items.map(([name, detail, outcome], index) => {
      const item = element("li", "", `live-step live-${outcome}`);
      item.append(element("span", String(index + 1).padStart(2, "0"), "live-number"));
      const copy = element("div", "");
      copy.append(element("strong", name), element("span", detail));
      item.append(copy, element("span", outcome, "demo-badge"));
      return item;
    }));
  }

  function renderState() {
    const snapshot = live.cloud;
    const observation = window.workspaceObservation.observationStatus(snapshot, live.cloudStale);
    const { stale, leaseExpired: expired, source } = observation;
    const rows = [
      [state.hosting === "azure" ? "Azure dashboard" : "Local console", "Responding", "This browser session"],
      [state.hosting === "azure" ? "Cloud NeMo API" : "Local NeMo API", state.session?.persona === "approver" ? "Not permitted for Approver" : state.agent?.status === "not_authenticated" ? "Sign in to check" : state.agent?.status === "online" ? "Online" : "Unavailable / unchecked", "Separate agent health check"],
      ["SAW VM", labels[snapshot?.vm] || "Not checked", source],
      ["Runtime network lock", labels[snapshot?.runtimeLock] || "Not checked", source],
      ["Outbound translation", labels[snapshot?.nat] || "Not checked", source],
      ["OpenShell / Planning process", live.result?.receipt ? `Ran as UID ${live.result.receipt.uid}` : "Not checked inside the guest", live.result?.completedAt || "Requires workspace proof"],
      ["Database analysis", "Separate read-only action", "Available in Analyze; no sandbox proof required"],
    ];
    find("liveComponents").replaceChildren(...rows.map(values => {
      const row = document.createElement("tr");
      values.forEach((value, index) => row.append(element(index === 0 ? "th" : "td", value)));
      return row;
    }));
    find("liveCheckedAt").textContent = observation.label;
    find("liveCheckedAt").className = `demo-badge ${stale ? "demo-badge-failed" : ""}`;
    find("liveLease").textContent = snapshot ? `Workspace expiry: ${snapshot.expiresAt || "unknown"} / ${expired ? "Expired" : labels[snapshot.lease]}` : "Workspace lease: not checked";
    window.dispatchEvent(new CustomEvent("console-workspace-changed", { detail: { enabled: live.enabled, configurationError: live.configurationError, error: live.error, cloud: live.cloud, stale, result: live.result, busy: live.busy } }));
  }

  function renderIdentity() {
    const session = state.session;
    const signedIn = session?.status === "authenticated";
    const approver = signedIn && session.persona === "approver";
    find("liveEyebrow").textContent = approver ? "WORKSPACE / ACCOUNT ACCESS" : "WORKSPACE / STATUS AND PROOF";
    find("liveTitle").textContent = approver ? "Workspace access restricted" : "Planning sandbox isolation check";
    find("liveSummary").textContent = approver
      ? "Approver has no workspace permissions. This is an account boundary, not a failed run."
      : "Check current workspace state or request a fixed execution proof.";
    for (const id of ["liveScope", "liveCheck", "liveRun", "liveObservedState", "liveRunResults"]) find(id).hidden = approver;
    find("liveReviewStatus").hidden = !approver;
    const key = signedIn ? `${session.accountFingerprint}:${session.persona}` : null;
    if (key !== live.identityKey) {
      live.identityKey = key;
      live.generation += 1;
      live.result = null;
      live.cloud = null;
      live.cloudStale = false;
      live.error = null;
      find("liveReceipt").textContent = "";
      find("liveOperation").textContent = "No cloud query or workspace command has run for this account session.";
      find("liveReceipt").hidden = true;
      find("liveExport").disabled = true;
      steps(initialSteps);
    }
    find("liveIdentity").textContent = signedIn ? `${session.persona} / ${session.accountFingerprint}` : "Not signed in";
    find("liveAccess").textContent = signedIn ? "JWT signature, age, client, scopes, and roles are rechecked before execution." : "Execution requires agent.invoke, tasks.execute, Task.Reader, and Task.Operator.";
    find("liveSignIn").hidden = signedIn;
    find("liveRun").disabled = approver || !signedIn || !live.enabled || live.busy;
    find("liveCheck").disabled = approver || !live.enabled || live.busy || (state.hosting === "azure" && !signedIn);
    if (approver) {
      find("liveAccess").textContent = "Task.Approver / review authority only";
      find("liveOperation").textContent = "No Azure query or workspace command was sent. Workspace status requires a Reader or Operator account; execution requires Operator.";
    }
    find("liveMode").textContent = approver ? "Access restricted" : live.enabled
      ? state.hosting === "azure" ? "Cloud managed-identity transport" : "Live operator transport enabled"
      : "Live transport disabled";
    document.querySelector(".live-scope p").textContent = state.hosting === "azure"
      ? "Fixed sandbox proof, not the full SQL incident demo. No model call or SQL remediation. Cloud commands use a separate managed-identity controller; your Entra token never enters the sandbox."
      : "Fixed sandbox proof, not the full SQL incident demo. No model call or SQL remediation. Azure commands use the local operator's credentials; your Entra token stays in the console.";
    const location = document.querySelector(".demo-location");
    if (state.hosting === "azure") location.textContent = "Azure dashboard / private workspace";
    renderState();
  }

  function showResult(result) {
    live.result = result;
    if (result.cloud) { live.cloud = result.cloud; live.cloudStale = false; }
    const denied = result.status === "denied";
    const receipt = result.receipt;
    steps([
      ["Entra identity", `JWT verified / account ${result.identity.accountFingerprint}`, "passed"],
      ["Operator permission", denied ? "Denied before Azure execution. No workspace command sent." : "Required scopes and roles present", denied ? "denied" : "passed"],
      ["SAW and runtime lock", result.cloud ? `VM: ${result.cloud.vm}; lock: ${result.cloud.runtimeLock}; lease: ${result.cloud.lease}` : "Not queried", result.cloud?.readyForProbe ? "passed" : "blocked"],
      ["Planning MicroVM", receipt ? `Command returned UID ${receipt.uid}; setuid(0) ${receipt.privilegeEscalation}` : "No proof receipt returned", receipt ? "passed" : "blocked"],
      ["Protected API route", receipt ? `GET: ${receipt.readHttpStatus || "unreachable"}; POST: ${receipt.writeHttpStatus || "unreachable"}. Expected 401 / 403.` : "Not executed", receipt?.passed ? "passed" : receipt ? "failed" : "blocked"],
    ]);
    find("liveOperation").textContent = denied ? "Live authorization denial. Nothing executed on the workspace." : receipt?.passed ? "Live workspace proof completed. This was a fixed probe, not a SQL incident or model run." : receipt ? "The command ran inside Planning, but the API connectivity checks failed. The full flow did not succeed." : result.detail || "The live run was blocked.";
    find("liveReceipt").textContent = JSON.stringify(result, null, 2);
    find("liveReceipt").hidden = false;
    find("liveExport").disabled = false;
    renderState();
  }

  async function perform(operation) {
    if (live.busy || state.session?.persona === "approver") return;
    if (operation === 'run' && (state.session?.persona !== 'operator' || !live.cloud?.readyForProbe || live.cloudStale || Date.now() - Date.parse(live.cloud.checkedAt) >= live.cloud.freshForSeconds * 1000 || Date.parse(live.cloud.expiresAt) <= Date.now() + 300000)) {
      live.error = 'A fresh successful workspace check and an Operator account are required before execution.';
      renderState();
      return;
    }
    live.error = null;
    live.busy = true;
    const generation = live.generation;
    if (operation === "run") {
      live.result = null;
      find("liveReceipt").hidden = true;
      find("liveExport").disabled = true;
      steps(initialSteps);
    }
    renderIdentity();
    find("liveOperation").textContent = operation === "check" ? "Querying Azure VM, lease, NSG rules, and NAT association..." : "Verifying identity and workspace prerequisites, then executing the fixed probe. No result yet. This may take several minutes.";
    notifyOperation("workspace", true, operation === "check" ? "Checking Azure resource state. No guest command is running." : "Fixed workspace proof in progress. This is separate from the task test and approval service.");
    try {
      const result = await api(`/api/live-workspace/${operation}`, { method: "POST" });
      if (generation !== live.generation) return;
      if (operation === "check") {
        live.cloud = result;
        live.cloudStale = false;
        find("liveOperation").textContent = result.readyForProbe ? "Azure prerequisites checked. Gateway and sandbox process still require an execution proof." : "Azure prerequisites block the probe. No guest command was run.";
      } else showResult(result);
    } catch (error) {
      if (generation !== live.generation) return;
      live.cloudStale = true;
      live.error = error.message;
      if (error.payload?.status === "denied") showResult(error.payload);
      else find("liveOperation").textContent = `Live ${operation} failed: ${error.message}. No successful result is assumed.`;
    } finally {
      live.busy = false;
      renderIdentity();
      notifyOperation("workspace", false, find("liveOperation").textContent);
    }
  }

  find("liveCheck").addEventListener("click", () => perform("check"));
  find("liveRun").addEventListener("click", () => perform("run"));
  find("liveSignIn").addEventListener("click", () => find("signInButton").click());
  find("liveReviewStatus").addEventListener("click", () => find("identityViewTab").click());
  find("liveExport").addEventListener("click", () => {
    if (!live.result) return;
    const link = document.createElement("a");
    const url = URL.createObjectURL(new Blob([JSON.stringify(live.result, null, 2)], { type: "application/json" }));
    link.href = url;
    link.download = `workspace-live-${live.result.runId}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  window.addEventListener("console-session-changed", renderIdentity);
  window.addEventListener('console-workspace-requested', renderState);
  steps(initialSteps);
  renderIdentity();
  api("/api/live-workspace").then(config => {
    live.enabled = config.enabled;
    find("liveMode").textContent = config.enabled ? "Live operator transport enabled" : "Live transport disabled";
    if (!config.enabled) find("liveOperation").textContent = "Cloud execution is disabled in this console. Recorded evidence is a separate tab.";
    renderIdentity();
  }).catch(() => {
    live.configurationError = 'Workspace transport could not be loaded. Refresh the connection after the service is available.';
    renderState();
    if (state.session?.persona === "approver") {
      renderIdentity();
      return;
    }
    find("liveMode").textContent = "Live transport unavailable";
    find("liveOperation").textContent = "The console backend does not expose a live workspace transport. No action is available.";
  });
  setInterval(renderState, 5000);
})();