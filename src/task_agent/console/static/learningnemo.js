const state = {
  agent: null,
  agentUrl: "",
  hosting: "local",
  reviewEnabled: false,
  session: null,
  system: null,
  plan: null,
  csrfToken: null,
  authGeneration: 0,
  activities: [],
  authWaiter: null,
  scenarioRunning: false,
  manualRunning: false,
  baselineTaskStatus: null,
};

const elements = {
  agentStatusDot: document.querySelector("#agentStatusDot"),
  agentStatusLabel: document.querySelector("#agentStatusLabel"),
  agentEndpoint: document.querySelector("#agentEndpoint"),
  refreshButton: document.querySelector("#refreshButton"),
  currentSession: document.querySelector("#currentSession"),
  currentUserIcon: document.querySelector("#currentUserIcon"),
  currentUserHeading: document.querySelector("#currentUserHeading"),
  currentUserStatus: document.querySelector("#currentUserStatus"),
  currentScopeList: document.querySelector("#currentScopeList"),
  currentRoleList: document.querySelector("#currentRoleList"),
  signInButton: document.querySelector("#signInButton"),
  signOutButton: document.querySelector("#signOutButton"),
  runScenarioButton: document.querySelector("#runScenarioButton"),
  progressLabel: document.querySelector("#progressLabel"),
  progressCount: document.querySelector("#progressCount"),
  progressBar: document.querySelector("#progressBar"),
  scenarioSteps: document.querySelector("#scenarioSteps"),
  systemTab: document.querySelector("#systemTab"),
  promptTab: document.querySelector("#promptTab"),
  activityTab: document.querySelector("#activityTab"),
  systemPanel: document.querySelector("#systemPanel"),
  promptPanel: document.querySelector("#promptPanel"),
  activityPanel: document.querySelector("#activityPanel"),
  activityCount: document.querySelector("#activityCount"),
  activityList: document.querySelector("#activityList"),
  clearActivityButton: document.querySelector("#clearActivityButton"),
  promptForm: document.querySelector("#promptForm"),
  promptInput: document.querySelector("#promptInput"),
  promptMeta: document.querySelector("#promptMeta"),
  promptPersona: document.querySelector("#promptPersona"),
  sendButton: document.querySelector("#sendButton"),
  manualResponse: document.querySelector("#manualResponse"),
  authDialog: document.querySelector("#authDialog"),
  authDialogIcon: document.querySelector("#authDialogIcon"),
  deviceCodeBlock: document.querySelector("#deviceCodeBlock"),
  deviceCode: document.querySelector("#deviceCode"),
  copyCodeButton: document.querySelector("#copyCodeButton"),
  localAccountChoices: document.querySelector("#localAccountChoices"),
  localOperatorButton: document.querySelector("#localOperatorButton"),
  localApproverButton: document.querySelector("#localApproverButton"),
  authScopeList: document.querySelector("#authScopeList"),
  authWaiting: document.querySelector("#authWaiting"),
  authStatusText: document.querySelector("#authStatusText"),
  authCountdown: document.querySelector("#authCountdown"),
  openMicrosoftButton: document.querySelector("#openMicrosoftButton"),
  closeAuthButton: document.querySelector("#closeAuthButton"),
  toastRegion: document.querySelector("#toastRegion"),
  capabilityCount: document.querySelector("#capabilityCount"),
  guardrailCount: document.querySelector("#guardrailCount"),
  toolCount: document.querySelector("#toolCount"),
  capabilityList: document.querySelector("#capabilityList"),
  guardrailList: document.querySelector("#guardrailList"),
  toolRows: document.querySelector("#toolRows"),
  requestJourney: document.querySelector("#requestJourney"),
  evidenceLegend: document.querySelector("#evidenceLegend"),
  accountMatchBadge: document.querySelector("#accountMatchBadge"),
  accountMatchDetail: document.querySelector("#accountMatchDetail"),
  identityModelSummary: document.querySelector("#identityModelSummary"),
  identityClientName: document.querySelector("#identityClientName"),
  identityClientScopes: document.querySelector("#identityClientScopes"),
  identityProfileBranches: document.querySelector("#identityProfileBranches"),
  identityProductionNote: document.querySelector("#identityProductionNote"),
};

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons({ attrs: { "stroke-width": 1.8 } });
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = options.body
    ? { "Content-Type": "application/json", ...(options.headers || {}) }
    : { ...(options.headers || {}) };
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && state.csrfToken) {
    headers["X-LearningNeMo-CSRF"] = state.csrfToken;
  }
  const response = await fetch(path, {
    ...options,
    headers,
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    const requestId = response.headers.get("X-Request-ID");
    const correlation = /^[a-f0-9]{32}$/.test(requestId || "") ? ` / request ${requestId}` : "";
    const detail = typeof payload.detail === "string"
      ? payload.detail
      : Array.isArray(payload.detail)
      ? "Request validation failed"
      : `Request failed with HTTP ${response.status}`;
    const error = new Error(detail + correlation);
    error.status = response.status;
    error.requestId = correlation ? requestId : null;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function createIcon(name, className = "") {
  const icon = document.createElement("i");
  icon.dataset.lucide = name;
  if (className) icon.className = className;
  return icon;
}

function scopeElement(scope) {
  const item = document.createElement("span");
  item.className = `scope ${scope === "tasks.execute" ? "scope-execute" : "scope-read"}`;
  item.textContent = scope;
  return item;
}

function roleElement(role) {
  const item = document.createElement("span");
  item.className = `role-claim ${role === "Task.Operator" ? "role-operator" : ""}`;
  item.textContent = role;
  return item;
}

function statusLabel(status) {
  return {
    signed_out: "Signed out",
    pending: "Waiting for sign-in",
    authenticated: "Authenticated",
    expired: "Session expired",
    error: "Sign-in failed",
  }[status] || "Unknown";
}

function statusClass(status) {
  if (status === "authenticated") return "status-online";
  if (status === "pending") return "status-pending";
  if (status === "error" || status === "expired") return "status-error";
  return "status-neutral";
}

function personaLabel(persona) {
  return { reader: "Reader", operator: "Operator", approver: "Approver" }[persona] || "Unknown role";
}

function renderAgent() {
  const agent = state.agent || { status: "offline" };
  if (state.invoiceEnabled) {
    elements.refreshButton.hidden = true;
    elements.agentStatusDot.className = 'status-dot status-neutral';
    elements.agentStatusLabel.textContent = 'Invoice console';
    elements.agentEndpoint.textContent = state.hosting === 'azure' ? 'Azure hosted' : 'Local preview';
    return;
  }
  elements.refreshButton.hidden = false;
  elements.agentStatusDot.className = `status-dot ${agent.status === "online" ? "status-online" : "status-offline"}`;
  if (["not_authenticated", "not_permitted"].includes(agent.status)) elements.agentStatusDot.className = "status-dot status-neutral";
  elements.agentStatusLabel.textContent = agent.status === "not_permitted"
    ? "Approver / no agent access"
    : agent.status === "not_authenticated"
    ? "Cloud agent: sign in to check"
    : agent.status === "online"
    ? `${state.hosting === "azure" ? "Cloud" : "Local"} agent online · ${agent.durationMs} ms`
    : `${state.hosting === "azure" ? "Cloud" : "Local"} agent unavailable`;
  elements.agentEndpoint.textContent = state.agentUrl.replace(/^https?:\/\//, "");
}

let sessionViewportKey = null;
function renderSession() {
  const session = state.session || { status: "signed_out", grantedScopes: [], grantedRoles: [] };
  const nextViewportKey = JSON.stringify([state.invoiceEnabled, session.status, session.persona, session.storageKey || session.accountFingerprint]);
  const viewport = state.invoiceEnabled && sessionViewportKey === nextViewportKey ? { left: window.scrollX, top: window.scrollY } : null;
  sessionViewportKey = nextViewportKey;
  const authenticated = session.status === "authenticated";
  const approver = authenticated && session.persona === "approver";
  const label = authenticated ? personaLabel(session.persona) : "Not signed in";
  const dot = elements.currentUserStatus.querySelector(".status-dot");
  const fingerprint = elements.currentSession.querySelector(".account-fingerprint");
  elements.currentUserHeading.textContent = authenticated ? `${label} detected` : label;
  elements.currentUserStatus.lastChild.textContent = statusLabel(session.status);
  dot.className = `status-dot ${statusClass(session.status)}`;
  elements.currentUserIcon.className = `role-icon ${authenticated ? `${session.persona}-icon` : ""}`;
  elements.currentUserIcon.replaceChildren(createIcon(approver ? "file-check-2" : authenticated && session.persona === "operator" ? "terminal-square" : "user-round"));
  elements.signInButton.hidden = authenticated;
  elements.signOutButton.hidden = !authenticated;
  fingerprint.hidden = !session.accountFingerprint;
  fingerprint.textContent = session.accountFingerprint ? `account proof ${session.accountFingerprint}` : "";
  const scopes = session.grantedScopes?.length ? session.grantedScopes : session.expectedScopes || [];
  elements.currentScopeList.replaceChildren(...scopes.map(scopeElement));
  if (session.grantedRoles?.length) {
    elements.currentRoleList.replaceChildren(...session.grantedRoles.map(roleElement));
  } else {
    const empty = document.createElement("span");
    empty.className = "empty-claim";
    empty.textContent = "Detected after sign-in";
    elements.currentRoleList.replaceChildren(empty);
  }
  elements.promptPersona.textContent = authenticated
    ? `${label} · ${session.grantedRoles.join(", ")}`
    : "Sign in to detect access";
  elements.runScenarioButton.disabled = approver || state.scenarioRunning;
  elements.sendButton.disabled = approver || state.manualRunning || state.scenarioRunning;
  elements.promptInput.disabled = approver;
  renderIdentityStatus();
  refreshIcons();
  window.dispatchEvent(new CustomEvent("console-session-changed", { detail: session }));
  if (viewport) window.scrollTo({ ...viewport, behavior: 'instant' });
}

async function bootstrap() {
  const authGeneration = state.authGeneration;
  elements.refreshButton.disabled = true;
  try {
    const payload = await api("/api/bootstrap");
    if (authGeneration !== state.authGeneration) return;
    state.agent = payload.agent;
    state.agentUrl = payload.agentUrl;
    state.hosting = payload.hosting || "local";
    state.reviewEnabled = payload.reviewEnabled === true;
    state.invoiceEnabled = payload.invoiceEnabled === true;
    state.session = payload.session;
    state.csrfToken = payload.csrfToken;
    renderAgent();
    renderSession();
    if (state.hosting !== "azure" || state.session?.status === "authenticated") {
      state.system = state.system || await api("/api/capabilities");
    }
    renderSystem();
  } catch (error) {
    state.agent = { status: "offline" };
    renderAgent();
    toast(error.message, "error");
  } finally {
    elements.refreshButton.disabled = false;
  }
}

function renderSystem() {
  if (!state.system) return;
  elements.capabilityCount.textContent = String(state.system.capabilities.length);
  elements.guardrailCount.textContent = String(state.system.guardrails.length);
  elements.toolCount.textContent = String(state.system.tools.length);
  elements.identityModelSummary.textContent = state.system.identityModel.summary;
  elements.identityProductionNote.textContent = state.system.identityModel.productionNote;
  elements.identityClientName.textContent = state.system.identityModel.client.name;
  elements.identityClientScopes.replaceChildren(...state.system.identityModel.client.scopes.map(scopeElement));

  elements.identityProfileBranches.replaceChildren(...state.system.identityModel.profiles.map((profile) => {
    const branch = document.createElement("article");
    branch.className = `identity-branch ${profile.role}-branch`;
    const account = document.createElement("strong");
    account.textContent = profile.account;
    const roles = document.createElement("div");
    roles.className = "scope-list";
    roles.replaceChildren(...profile.roles.map(roleElement));
    const meaning = document.createElement("span");
    meaning.textContent = profile.meaning;
    branch.append(account, roles, meaning);
    return branch;
  }));

  elements.capabilityList.replaceChildren(...state.system.capabilities.map((capability) => {
    const item = document.createElement("details");
    item.className = "capability-item";
    const summary = document.createElement("summary");
    const icon = document.createElement("span");
    icon.className = "capability-icon";
    icon.append(createIcon(capability.icon));
    const copy = document.createElement("span");
    copy.className = "capability-summary";
    const name = document.createElement("strong");
    name.textContent = capability.name;
    const description = document.createElement("span");
    description.textContent = capability.summary;
    copy.append(name, description);
    summary.append(icon, copy, createIcon("chevron-down", "capability-chevron"));
    const details = document.createElement("ul");
    details.className = "capability-details";
    capability.details.forEach((detail) => {
      const row = document.createElement("li");
      row.textContent = detail;
      details.append(row);
    });
    item.append(summary, details);
    return item;
  }));

  elements.guardrailList.replaceChildren(...state.system.guardrails.map((rail) => {
    const item = document.createElement("article");
    item.className = "guardrail-item";
    const title = document.createElement("div");
    title.className = "guardrail-title";
    const name = document.createElement("strong");
    name.textContent = rail.name;
    const engine = document.createElement("span");
    engine.textContent = rail.engine;
    title.append(name, engine);
    const effect = document.createElement("p");
    effect.textContent = rail.effect;
    const probe = document.createElement("button");
    probe.className = "probe-button";
    probe.type = "button";
    probe.textContent = rail.probeLabel;
    probe.addEventListener("click", () => prepareProbe(rail.probePrompt));
    item.append(title, effect, probe);
    return item;
  }));

  elements.toolRows.replaceChildren(...state.system.tools.map((tool) => {
    const row = document.createElement("div");
    row.className = "tool-row";
    row.setAttribute("role", "row");
    const name = document.createElement("span");
    name.setAttribute("role", "cell");
    name.textContent = tool.name;
    if (tool.mutatesState) {
      const mutation = document.createElement("span");
      mutation.className = "mutation-mark";
      mutation.textContent = "writes";
      name.append(mutation);
    }
    row.append(name, scopeElement(tool.requiredScope), roleElement(tool.requiredRole));
    return row;
  }));

  elements.requestJourney.replaceChildren(...state.system.requestStages.map((stage) => {
    const row = document.createElement("article");
    row.className = "journey-stage";
    const icon = document.createElement("span");
    icon.className = "journey-icon";
    icon.append(createIcon(stage.icon));
    const copy = document.createElement("div");
    copy.className = "journey-copy";
    const title = document.createElement("div");
    title.className = "journey-title";
    const name = document.createElement("strong");
    name.textContent = stage.name;
    const owner = document.createElement("span");
    owner.textContent = stage.owner;
    title.append(name, owner);
    const action = document.createElement("p");
    action.textContent = stage.action;
    const evidence = document.createElement("small");
    evidence.textContent = stage.evidence;
    copy.append(title, action, evidence);
    row.append(icon, copy);
    return row;
  }));

  elements.evidenceLegend.replaceChildren(...Object.entries(state.system.evidenceLevels).map(([level, explanation]) => {
    const row = document.createElement("div");
    row.className = "legend-row";
    const badge = document.createElement("span");
    badge.className = `evidence-badge evidence-${level}`;
    badge.textContent = level[0].toUpperCase() + level.slice(1);
    const copy = document.createElement("p");
    copy.textContent = explanation;
    row.append(badge, copy);
    return row;
  }));
  renderIdentityStatus();
  refreshIcons();
}

function renderIdentityStatus() {
  if (!elements.accountMatchBadge || !elements.accountMatchDetail) return;
  if (state.session?.status !== "authenticated") {
    elements.accountMatchBadge.className = "evidence-badge evidence-configured";
    elements.accountMatchBadge.textContent = "Awaiting sign-in";
    elements.accountMatchDetail.textContent = "One client · persona derived from app roles";
    return;
  }
  const label = personaLabel(state.session.persona);
  elements.accountMatchBadge.className = "evidence-badge evidence-observed";
  elements.accountMatchBadge.textContent = `${label} detected`;
  elements.accountMatchDetail.textContent = `${state.session.grantedRoles.join(", ")} · proof ${state.session.accountFingerprint}`;
}

function updateAuthDialog(session) {
  const local = session.authMode === "local-demo";
  document.querySelector("#authDialog .eyebrow").textContent = local ? "LOCAL DEMO ACCOUNTS" : "Microsoft Entra ID";
  elements.authDialog.querySelector("h1").textContent = local && state.session?.status === "authenticated" ? "Change demo role" : local ? "Choose a demo role" : "Sign in to LearningNeMo";
  document.querySelector("#authRoleHint").textContent = local
    ? "Choose the role needed for the next step. Your investigation remains available."
    : "Use your assigned Microsoft account. Your verified role determines access; Approvers cannot execute changes.";
  elements.closeAuthButton.title = local ? "Close role chooser" : "Cancel sign-in";
  elements.closeAuthButton.querySelector(".sr-only").textContent = elements.closeAuthButton.title;
  elements.localOperatorButton.classList.toggle("is-current", local && session.status === "authenticated" && session.persona === "operator");
  elements.localApproverButton.classList.toggle("is-current", local && session.status === "authenticated" && session.persona === "approver");
  elements.deviceCodeBlock.hidden = local;
  elements.localAccountChoices.hidden = !local;
  elements.authWaiting.hidden = local;
  elements.openMicrosoftButton.hidden = local;
  elements.authScopeList.hidden = local;
  if (local) {
    elements.authStatusText.textContent = "Choose a local demo role";
    refreshIcons();
    return;
  }
  elements.authStatusText.textContent = "Waiting for Microsoft sign-in";
  elements.deviceCode.textContent = session.userCode || "•••••••••";
  elements.openMicrosoftButton.href = session.verificationUri || "https://microsoft.com/devicelogin";
  elements.authScopeList.replaceChildren(...session.expectedScopes.map(scopeElement));
  updateCountdown(session.expiresAt);
  refreshIcons();
}

function updateCountdown(expiresAt) {
  if (!expiresAt) {
    elements.authCountdown.textContent = "--:--";
    return;
  }
  const remaining = Math.max(0, Math.floor(expiresAt - Date.now() / 1000));
  elements.authCountdown.textContent = `${String(Math.floor(remaining / 60)).padStart(2, "0")}:${String(remaining % 60).padStart(2, "0")}`;
}

async function authenticate(reviewAccess = false) {
  state.authGeneration += 1;
  const current = await api("/api/auth");
  state.session = current;
  renderSession();
  if (current.status === "authenticated" && (!reviewAccess || current.grantedScopes?.includes("plans.review"))) return current;
  if (current.authMode === "local-demo") {
    updateAuthDialog(current);
    if (!elements.authDialog.open) elements.authDialog.showModal();
    return current;
  }
  const session = current.status === "pending" ? current : await api(reviewAccess ? "/api/auth/review" : "/api/auth/start", { method: "POST" });
  state.session = session;
  renderSession();
  if (session.status === "authenticated") return session;
  updateAuthDialog(session);
  if (!elements.authDialog.open) elements.authDialog.showModal();
  if (state.authWaiter) return state.authWaiter.promise;
  const waiter = {};
  waiter.promise = new Promise((resolve, reject) => {
    waiter.resolve = resolve;
    waiter.reject = reject;
  });
  state.authWaiter = waiter;
  pollAuthentication();
  return waiter.promise;
}

async function signInLocal(persona) {
  const session = await api("/api/auth/start", { method: "POST", body: JSON.stringify({ persona }) });
  state.session = session;
  elements.authDialog.close();
  toast(`${personaLabel(session.persona)} demo account selected`);
  await bootstrap();
  return session;
}

async function pollAuthentication() {
  const waiter = state.authWaiter;
  if (!waiter) return;
  try {
    const session = await api("/api/auth");
    if (state.authWaiter !== waiter) return;
    state.session = session;
    renderSession();
    updateCountdown(session.expiresAt);
    if (session.status === "authenticated") {
      state.authWaiter = null;
      elements.authDialog.close();
      waiter.resolve(session);
      toast(`${personaLabel(session.persona)} access detected from verified roles`);
      if (state.hosting === "azure") await bootstrap();
      return;
    }
    if (session.status === "error" || session.status === "expired") {
      throw new Error(session.error || statusLabel(session.status));
    }
    window.setTimeout(pollAuthentication, 1000);
  } catch (error) {
    if (state.authWaiter !== waiter) return;
    state.authWaiter = null;
    elements.authStatusText.textContent = error.message;
    waiter.reject(error);
  }
}

async function clearAuthentication() {
  state.authGeneration += 1;
  const waiter = state.authWaiter;
  state.authWaiter = null;
  waiter?.reject(new Error("Sign-in cancelled"));
  state.session = await api("/api/auth", { method: "DELETE" });
  if (state.hosting === "azure") {
    state.agent = { status: "not_authenticated" };
    renderAgent();
  }
  state.plan = null;
  renderSession();
  resetScenario();
  toast("Current session cleared");
}

function cancelAuthentication() {
  const waiter = state.authWaiter;
  if (waiter || state.session?.status === "pending") {
    const generation = ++state.authGeneration;
    state.authWaiter = null;
    api("/api/auth", { method: "DELETE" })
      .then((session) => {
        if (generation !== state.authGeneration) return;
        state.session = session;
        renderSession();
      })
      .catch((error) => toast(error.message, "error"));
    waiter?.reject(new Error("Sign-in cancelled"));
  }
  elements.authDialog.close();
}

function prepareProbe(prompt) {
  selectTab("prompt");
  elements.promptInput.value = prompt;
  elements.promptInput.focus();
  toast("Guardrail probe loaded. Sign in and send when ready.");
}

async function callAgent(prompt, source) {
  await authenticate();
  if (state.session?.persona === "approver") throw new Error("Approver access does not permit agent tasks.");
  const identity = sessionIdentity();
  const startedAt = new Date();
  try {
    const reply = await api("/api/chat", { method: "POST", body: JSON.stringify({ prompt }) });
    if (identity !== sessionIdentity()) throw new Error("Account changed. Previous response discarded.");
    addActivity({ persona: reply.persona, prompt, response: reply.content, time: startedAt, durationMs: reply.durationMs, httpStatus: reply.httpStatus, ok: true, source });
    return reply;
  } catch (error) {
    if (identity === sessionIdentity()) addActivity({ persona: state.session?.persona || "unknown", prompt, response: error.message, time: startedAt, durationMs: null, ok: false, source });
    throw error;
  }
}

async function callWalkthroughStep(step) {
  const identity = sessionIdentity();
  const startedAt = new Date();
  try {
    const reply = await api(`/api/walkthrough/${step.id}`, { method: "POST" });
    if (identity !== sessionIdentity()) throw new Error("Account changed during the test");
    addActivity({ persona: reply.persona, prompt: reply.prompt, response: reply.content, time: startedAt, durationMs: reply.durationMs, httpStatus: reply.httpStatus, ok: true, source: "walkthrough" });
    return reply;
  } catch (error) {
    if (identity !== sessionIdentity()) throw new Error("Account changed during the test");
    if (error.status === 403 && error.payload?.accessDecision) {
      const reply = error.payload;
      addActivity({ persona: reply.persona, prompt: reply.prompt, response: reply.content, time: startedAt, durationMs: reply.durationMs, httpStatus: 403, ok: true, source: "walkthrough" });
      return reply;
    }
    addActivity({ persona: state.session?.persona || "unknown", prompt: step.title, response: error.message, time: startedAt, durationMs: null, ok: false, source: "walkthrough" });
    throw error;
  }
}

function sessionIdentity() {
  return state.session?.status === "authenticated" ? `${state.session.persona}:${state.session.accountFingerprint}` : null;
}

function notifyOperation(id, busy, message) {
  window.dispatchEvent(new CustomEvent("console-operation-changed", { detail: { id, busy, message } }));
}

function renderWalkthroughPlaceholder() {
  const item = document.createElement("li");
  item.className = "scenario-empty";
  item.append(createIcon("log-in"));
  const copy = document.createElement("span");
  copy.textContent = "Sign in once to generate a walkthrough from the current user's verified roles.";
  item.append(copy);
  elements.scenarioSteps.replaceChildren(item);
  refreshIcons();
}

function accessDecisionElement(decision) {
  const box = document.createElement("div");
  box.className = `access-decision ${decision.allowed ? "decision-allow" : "decision-deny"}`;
  const title = document.createElement("strong");
  title.textContent = decision.allowed ? "Expected access: ALLOW" : "Expected access: DENY";
  const checks = document.createElement("span");
  checks.textContent = decision.allowed
    ? `${decision.required.scopes.join(", ")} + ${decision.required.roles.join(", ")}`
    : decision.reason;
  const enforcement = document.createElement("small");
  enforcement.textContent = decision.enforcedBy;
  box.append(title, checks, enforcement);
  return box;
}

function renderPlan(plan) {
  state.plan = plan;
  elements.scenarioSteps.replaceChildren(...plan.steps.map((step, index) => {
    const item = document.createElement("li");
    item.className = "scenario-step";
    item.dataset.step = String(index);
    item.dataset.state = "idle";
    const marker = document.createElement("div");
    marker.className = "step-state";
    const number = document.createElement("span");
    number.textContent = String(index + 1);
    marker.append(number, createIcon("check"));
    const body = document.createElement("div");
    body.className = "step-copy";
    const heading = document.createElement("div");
    heading.className = "step-title-row";
    const title = document.createElement("h2");
    title.textContent = step.title;
    const badge = document.createElement("span");
    badge.className = `role-badge ${plan.persona}-badge`;
    badge.textContent = personaLabel(plan.persona);
    heading.append(title, badge);
    const expected = document.createElement("p");
    expected.className = "step-expected";
    expected.textContent = step.expected;
    const explanation = document.createElement("div");
    explanation.className = "step-explanation";
    [["Runs", step.runs], ["Proves", step.proves]].forEach(([label, value]) => {
      const row = document.createElement("p");
      const key = document.createElement("span");
      key.textContent = label;
      const text = label === "Runs" ? document.createElement("code") : document.createElement("span");
      text.textContent = value;
      row.append(key, text);
      explanation.append(row);
    });
    const result = document.createElement("details");
    result.className = "step-result";
    result.hidden = true;
    const summary = document.createElement("summary");
    summary.textContent = "Response";
    result.append(summary, document.createElement("pre"));
    body.append(heading, expected, accessDecisionElement(step.accessDecision), explanation, result);
    const verdict = document.createElement("span");
    verdict.className = "step-verdict";
    verdict.textContent = "Not run";
    item.append(marker, body, verdict);
    return item;
  }));
  elements.progressCount.textContent = `0 / ${plan.steps.length}`;
  refreshIcons();
}

function resetScenario() {
  state.baselineTaskStatus = null;
  state.plan = null;
  updatePreflight("idle", "Sign in to inspect delegated scopes and assigned app roles");
  elements.progressLabel.textContent = "Ready";
  elements.progressCount.textContent = "0 / —";
  elements.progressBar.value = 0;
  renderWalkthroughPlaceholder();
}

function updatePreflight(status, detail) {
  const preflight = document.querySelector("#scenarioPreflight");
  preflight.dataset.state = status;
  document.querySelector("#preflightDetail").textContent = detail;
  document.querySelector("#preflightVerdict").textContent = {
    idle: state.session?.status === "authenticated" ? "Preview" : "Not run",
    running: "Inspecting",
    pass: "Detected",
    fail: "Review",
  }[status];
}

function updateStep(index, status, response = "") {
  const step = elements.scenarioSteps.querySelector(`[data-step="${index}"]`);
  step.dataset.state = status;
  step.querySelector(".step-verdict").textContent = {
    idle: "Not run", running: "In progress", pass: "Verified", fail: "Failed", blocked: "Not run",
  }[status];
  if (response) {
    const result = step.querySelector(".step-result");
    result.hidden = false;
    result.querySelector("pre").textContent = response;
  }
}

function taskStatus(content, taskId = "task-1") {
  const match = content.match(new RegExp(`${taskId}[\\s\\S]{0,160}?\\b(pending|completed)\\b`, "i"));
  return match ? match[1].toLowerCase() : null;
}

function assessStep(step, reply) {
  const content = reply.content.trim();
  if (!content) return false;
  switch (step.assertion) {
    case "baseline":
      state.baselineTaskStatus = taskStatus(content);
      return /task-1/i.test(content) && /task-2/i.test(content) && Boolean(state.baselineTaskStatus);
    case "denied":
      return reply.accessDecision.allowed === false
        && reply.accessDecision.missing.scopes.length === 0
        && reply.accessDecision.missing.roles.includes("Task.Operator")
        && /(permission|role|not authorized|unauthorized|cannot|can't|denied|unable)/i.test(content);
    case "unchanged":
      return taskStatus(content) === state.baselineTaskStatus;
    case "reset":
    case "pending":
      return taskStatus(content, "task-1") === "pending" && taskStatus(content, "task-2") === "pending";
    case "execution-succeeded":
      return reply.accessDecision.allowed === true
        && !/already (?:been )?completed/i.test(content)
        && /(completed successfully|has been completed|task completed)/i.test(content);
    case "completed":
      if (step.tool === "execute_task") {
        return reply.accessDecision.allowed === true
          && !/already (?:been )?completed/i.test(content)
          && /(completed successfully|has been completed|task completed)/i.test(content);
      }
      return taskStatus(content) === "completed";
    default:
      return false;
  }
}

function blockRemaining(startIndex) {
  for (let index = startIndex; index < state.plan.steps.length; index += 1) updateStep(index, "blocked");
}

async function runScenario() {
  if (state.scenarioRunning || state.manualRunning) return;
  state.scenarioRunning = true;
  elements.runScenarioButton.disabled = true;
  elements.runScenarioButton.querySelector("span").textContent = "Running";
  let runIdentity = null;
  let finishMessage = "Role test stopped. No successful result is assumed.";
  notifyOperation("walkthrough", true, "Preparing the role test. No task request has run yet.");
  try {
    elements.progressLabel.textContent = "Authenticate current user";
    await authenticate();
    if (state.session?.persona === "approver") {
      document.getElementById("identityViewTab").click();
      finishMessage = "Approver review service is not connected. No task test was run.";
      return;
    }
    runIdentity = sessionIdentity();
    updatePreflight("running", "Evaluating verified scp and roles claims");
    const plan = await api("/api/walkthrough");
    if (runIdentity !== sessionIdentity()) return;
    renderPlan(plan);
    state.baselineTaskStatus = null;
    elements.progressBar.value = 0;
    updatePreflight(
      "pass",
      `${personaLabel(plan.persona)} path · roles: ${plan.grantedRoles.join(", ")} · scopes: ${plan.grantedScopes.length}`,
    );
    let passed = 0;
    for (let index = 0; index < plan.steps.length; index += 1) {
      if (runIdentity !== sessionIdentity()) return;
      const step = plan.steps[index];
      updateStep(index, "running");
      elements.progressLabel.textContent = `Step ${index + 1}: ${step.title}`;
      notifyOperation("walkthrough", true, `${personaLabel(plan.persona)} test ${index + 1}/${plan.steps.length}: ${step.title}. Request in progress.`);
      let outcome = false;
      let response = "";
      try {
        const reply = await callWalkthroughStep(step);
        response = reply.content;
        outcome = assessStep(step, reply);
      } catch (error) {
        response = error.message;
      }
      if (runIdentity !== sessionIdentity()) return;
      updateStep(index, outcome ? "pass" : "fail", response);
      if (outcome && step.assertion === "denied") elements.scenarioSteps.querySelector(`[data-step="${index}"] .step-verdict`).textContent = "Denied as expected";
      if (outcome) passed += 1;
      elements.progressCount.textContent = `${index + 1} / ${plan.steps.length}`;
      elements.progressBar.value = ((index + 1) / plan.steps.length) * 100;
      if (!outcome) {
        blockRemaining(index + 1);
        break;
      }
    }
    elements.progressLabel.textContent = passed === plan.steps.length
      ? `${personaLabel(plan.persona)} walkthrough passed`
      : `Stopped · ${passed} of ${plan.steps.length} passed`;
    finishMessage = passed === plan.steps.length
      ? plan.persona === "reader" ? "Reader test finished: write denied and task state unchanged." : "Operator test finished: authorized change confirmed by read-back. No approval request was created."
      : `Test stopped: ${passed}/${plan.steps.length} checks verified. Inspect the failed step; later steps did not run.`;
    toast(
      passed === plan.steps.length ? `${personaLabel(plan.persona)} walkthrough passed` : "Walkthrough needs review",
      passed === plan.steps.length ? "success" : "error",
    );
  } catch (error) {
    if (!runIdentity || runIdentity === sessionIdentity()) {
      updatePreflight("fail", error.message);
      elements.progressLabel.textContent = "Walkthrough stopped";
      finishMessage = `Role test failed: ${error.message}`;
      toast(error.message, "error");
    }
  } finally {
    state.scenarioRunning = false;
    elements.runScenarioButton.disabled = state.session?.persona === "approver";
    elements.runScenarioButton.querySelector("span").textContent = `Start ${personaLabel(state.session?.persona)} test`;
    notifyOperation("walkthrough", false, runIdentity && runIdentity !== sessionIdentity()
      ? "Account changed. Previous test stopped; no result is attached to this session." : finishMessage);
  }
}

function addActivity(activity) {
  state.activities.unshift(activity);
  elements.activityCount.textContent = String(state.activities.length);
  renderActivities();
}

function renderActivities() {
  if (!state.activities.length) {
    const empty = document.createElement("div");
    empty.className = "activity-empty";
    empty.append(createIcon("inbox"));
    const text = document.createElement("span");
    text.textContent = "No requests yet";
    empty.append(text);
    elements.activityList.replaceChildren(empty);
    refreshIcons();
    return;
  }
  elements.activityList.replaceChildren(...state.activities.map((activity) => {
    const item = document.createElement("article");
    item.className = "activity-item";
    const header = document.createElement("header");
    const title = document.createElement("strong");
    title.textContent = activity.prompt;
    const timestamp = document.createElement("time");
    timestamp.textContent = activity.time.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    header.append(title, timestamp);
    const response = document.createElement("p");
    response.textContent = activity.response;
    const metadata = document.createElement("span");
    metadata.className = "activity-meta";
    metadata.textContent = `${activity.persona} · ${activity.ok ? `HTTP ${activity.httpStatus}` : "error"}${activity.durationMs ? ` · ${activity.durationMs} ms` : ""}`;
    item.append(header, response, metadata);
    return item;
  }));
}

function renderManualResponse(reply) {
  const wrapper = document.createElement("div");
  wrapper.className = "response-content";
  const metadata = document.createElement("div");
  metadata.className = "response-meta";
  metadata.textContent = `${personaLabel(reply.persona)} · HTTP ${reply.httpStatus} · ${reply.durationMs} ms · request ${reply.requestId || "n/a"}`;
  const content = document.createElement("p");
  content.textContent = reply.content;
  const evidenceTitle = document.createElement("h2");
  evidenceTitle.className = "response-evidence-title";
  evidenceTitle.textContent = "What this response proves";
  const evidence = document.createElement("div");
  evidence.className = "response-evidence";
  (reply.evidence || []).forEach((entry) => {
    const row = document.createElement("div");
    row.className = "response-evidence-row";
    const badge = document.createElement("span");
    badge.className = `evidence-badge evidence-${entry.level}`;
    badge.textContent = entry.level[0].toUpperCase() + entry.level.slice(1);
    const copy = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = entry.name;
    const detail = document.createElement("span");
    detail.textContent = entry.detail;
    copy.append(name, detail);
    row.append(badge, copy);
    evidence.append(row);
  });
  wrapper.append(metadata, content, evidenceTitle, evidence);
  elements.manualResponse.replaceChildren(wrapper);
}

function renderManualError(message) {
  const wrapper = document.createElement("div");
  wrapper.className = "response-content";
  const metadata = document.createElement("div");
  metadata.className = "response-meta";
  metadata.textContent = "Request failed";
  const content = document.createElement("p");
  content.textContent = message;
  wrapper.append(metadata, content);
  elements.manualResponse.replaceChildren(wrapper);
}

function selectTab(tab) {
  const tabs = { system: [elements.systemTab, elements.systemPanel], prompt: [elements.promptTab, elements.promptPanel], activity: [elements.activityTab, elements.activityPanel] };
  Object.entries(tabs).forEach(([name, [button, panel]]) => {
    const selected = name === tab;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
    panel.hidden = !selected;
  });
}

function toast(message, type = "success") {
  const item = document.createElement("div");
  item.className = `toast ${type === "error" ? "error" : ""}`;
  item.append(createIcon(type === "error" ? "circle-alert" : "circle-check"));
  const text = document.createElement("span");
  text.textContent = message;
  item.append(text);
  elements.toastRegion.append(item);
  refreshIcons();
  window.setTimeout(() => item.remove(), 4200);
}

elements.signInButton.addEventListener("click", () => authenticate().catch((error) => toast(error.message, "error")));
elements.signOutButton.addEventListener("click", () => clearAuthentication().catch((error) => toast(error.message, "error")));
elements.refreshButton.addEventListener("click", bootstrap);
elements.runScenarioButton.addEventListener("click", runScenario);
elements.systemTab.addEventListener("click", () => selectTab("system"));
elements.promptTab.addEventListener("click", () => selectTab("prompt"));
elements.activityTab.addEventListener("click", () => selectTab("activity"));
elements.clearActivityButton.addEventListener("click", () => {
  state.activities = [];
  elements.activityCount.textContent = "0";
  renderActivities();
});

document.querySelectorAll(".prompt-presets button").forEach((button) => {
  button.addEventListener("click", () => {
    elements.promptInput.value = button.dataset.prompt;
    elements.promptInput.focus();
  });
});

elements.promptForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const prompt = elements.promptInput.value.trim();
  if (!prompt || state.manualRunning || state.scenarioRunning) return;
  state.manualRunning = true;
  let requestIdentity = sessionIdentity();
  notifyOperation("prompt", true, "Sending a manual agent request. This is separate from the role test.");
  elements.sendButton.disabled = true;
  elements.sendButton.querySelector("span").textContent = "Sending";
  try {
    const reply = await callAgent(prompt, "manual");
    if (!requestIdentity) requestIdentity = sessionIdentity();
    if (requestIdentity === sessionIdentity()) renderManualResponse(reply);
  } catch (error) {
    if (!requestIdentity || requestIdentity === sessionIdentity()) renderManualError(error.message);
    toast(error.message, "error");
  } finally {
    state.manualRunning = false;
    elements.sendButton.disabled = state.session?.persona === "approver";
    elements.sendButton.querySelector("span").textContent = "Send";
    notifyOperation("prompt", false, "Manual request finished. Inspect its response; no workspace or approval step ran.");
  }
});

elements.copyCodeButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(elements.deviceCode.textContent);
    toast("Device code copied");
  } catch {
    toast("Clipboard access was blocked", "error");
  }
});

elements.localOperatorButton.addEventListener("click", () => signInLocal("operator").catch(error => toast(error.message, "error")));
elements.localApproverButton.addEventListener("click", () => signInLocal("approver").catch(error => toast(error.message, "error")));
elements.closeAuthButton.addEventListener("click", cancelAuthentication);
elements.authDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  cancelAuthentication();
});

resetScenario();
selectTab("system");
refreshIcons();
document.addEventListener("DOMContentLoaded", bootstrap, { once: true });
window.setInterval(bootstrap, 30000);