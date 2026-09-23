(() => {
  const model = { record: null, stage: 0, sandbox: 0, check: 0, decision: 0, loading: false };
  const find = (id) => document.getElementById(id);
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const icon = (name) => {
    const element = node("i");
    element.dataset.lucide = name;
    element.setAttribute("aria-hidden", "true");
    return element;
  };
  const icons = () => window.lucide?.createIcons({ attrs: { "stroke-width": 1.7 } });
  const badge = (text, kind = "") => node("span", `demo-badge ${kind ? `demo-badge-${kind}` : ""}`, text);

  function keyboardTabs(container, vertical = false) {
    container.addEventListener("keydown", (event) => {
      const tabs = [...container.querySelectorAll('[role="tab"]')].filter(tab => !tab.hidden);
      const current = tabs.indexOf(document.activeElement);
      if (current < 0) return;
      let target;
      if (event.key === (vertical ? "ArrowDown" : "ArrowRight")) target = (current + 1) % tabs.length;
      if (event.key === (vertical ? "ArrowUp" : "ArrowLeft")) target = (current - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") target = 0;
      if (event.key === "End") target = tabs.length - 1;
      if (target === undefined) return;
      event.preventDefault();
      tabs[target].click();
      const refreshed = [...container.querySelectorAll('[role="tab"]')].filter(tab => !tab.hidden);
      refreshed[target].focus();
    });
  }

  const views = [
    ["patternViewTab", "patternWorkspace"],
    ["liveViewTab", "liveWorkspace"],
    ["workspaceViewTab", "showcaseWorkspace"],
    ["identityViewTab", "identityWorkspace"],
    ["buildViewTab", "buildWorkspace"],
  ];
  views.forEach(([tabId, panelId]) => {
    find(tabId).addEventListener("click", () => {
      if (window.missionNavigate) { window.missionNavigate(tabId); return; }
      views.forEach(([otherTab, otherPanel]) => {
        const active = panelId === otherPanel;
        find(otherPanel).hidden = !active;
        find(otherTab).setAttribute("aria-selected", String(active));
        find(otherTab).tabIndex = active ? 0 : -1;
      });
      if (panelId === "identityWorkspace") window.dispatchEvent(new Event("resize"));
    });
  });
  keyboardTabs(document.querySelector(".demo-nav"));

  function selectable(label, selected, id, controls, action) {
    const button = node("button", "", label);
    button.type = "button";
    button.id = id;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(selected));
    button.setAttribute("aria-controls", controls);
    button.tabIndex = selected ? 0 : -1;
    button.addEventListener("click", action);
    return button;
  }

  function renderJourney() {
    const focusedId = find("demoJourney").contains(document.activeElement) ? document.activeElement.id : null;
    const stages = model.record.journey;
    find("demoJourney").replaceChildren(...stages.map((stage, index) => {
      const button = selectable(undefined, model.stage === index, `stage-${stage.id}`, "demoStage", () => {
        model.stage = index;
        renderJourney();
      });
      button.className = "demo-journey-button";
      button.append(node("span", "demo-stage-number", String(index + 1).padStart(2, "0")), icon(stage.icon), node("strong", "", stage.name), node("span", "demo-stage-owner", stage.owner));
      return button;
    }));
    const stage = stages[model.stage];
    find("demoStepCount").textContent = `${model.stage + 1} / ${stages.length}`;
    find("demoPrevious").disabled = model.stage === 0;
    find("demoNext").disabled = model.stage === stages.length - 1;
    const copy = node("div", "demo-stage-copy");
    copy.append(badge(stage.status, stage.status === "Failed" ? "failed" : ""), node("h3", "", stage.heading), node("p", "", stage.body));
    const evidence = node("div", "demo-stage-proof");
    evidence.append(node("span", "demo-field-label", "Evidence"), node("p", "", stage.proof), node("span", "demo-field-label", "Claim boundary"), node("p", "demo-limit", stage.limit));
    find("demoStage").replaceChildren(copy, evidence);
    find("demoStage").setAttribute("aria-labelledby", `stage-${stage.id}`);
    icons();
    if (focusedId) find(focusedId)?.focus({ preventScroll: true });
  }

  function renderSandbox() {
    const focusedId = find("demoSandboxes").contains(document.activeElement) ? document.activeElement.id : null;
    find("demoSandboxes").replaceChildren(...model.record.sandboxes.map((sandbox, index) => {
      const button = selectable(undefined, model.sandbox === index, `sandbox-${sandbox.id}`, "demoInspector", () => {
        model.sandbox = index;
        model.check = 0;
        renderSandbox();
      });
      button.className = `demo-sandbox demo-sandbox-${sandbox.id}`;
      button.append(icon(sandbox.icon), node("strong", "", sandbox.name), node("span", "", sandbox.role), node("code", "", sandbox.method));
      return button;
    }));
    const sandbox = model.record.sandboxes[model.sandbox];
    const facts = node("div", "demo-policy-facts");
    facts.append(node("span", "", sandbox.destination), node("span", "", sandbox.process));
    const route = node("div", "demo-route");
    route.append(badge(sandbox.method), node("code", "", sandbox.path));
    find("demoPolicy").replaceChildren(node("h3", "", `${sandbox.name} policy`), facts, route);
    find("demoInspector").setAttribute("aria-labelledby", `sandbox-${sandbox.id}`);
    const tradeoff = node("details", "demo-tradeoff");
    tradeoff.append(node("summary", "", "Tradeoff"), node("p", "", sandbox.tradeoff));
    find("demoRationale").replaceChildren(node("span", "demo-field-label", "Why this boundary"), node("h3", "", sandbox.purpose), node("p", "", sandbox.reason), tradeoff);
    renderChecks();
    icons();
    if (focusedId) find(focusedId)?.focus({ preventScroll: true });
  }

  function renderChecks() {
    const focusedId = find("demoChecks").contains(document.activeElement) ? document.activeElement.id : null;
    const sandbox = model.record.sandboxes[model.sandbox];
    find("demoChecks").replaceChildren(...sandbox.checks.map((check, index) => {
      const button = node("button", `demo-check ${model.check === index ? "is-selected" : ""}`);
      button.type = "button";
      button.id = `check-${sandbox.id}-${index}`;
      button.setAttribute("aria-pressed", String(model.check === index));
      button.append(icon(check.kind === "allowed" ? "arrow-up-right" : "shield-ban"), node("span", "", check.label), node("code", "", check.observed), icon("chevron-right"));
      button.addEventListener("click", () => { model.check = index; renderChecks(); });
      return button;
    }));
    const check = sandbox.checks[model.check];
    const status = node("div", "demo-result-heading");
    status.append(badge(check.verdict), node("span", "demo-quiet", "Historical summary / not executed now"));
    const comparison = node("div", "demo-comparison");
    for (const [label, value] of [["Expected", check.expected], ["Previously observed", check.observed]]) {
      const item = node("div");
      item.append(node("span", "demo-field-label", label), node("strong", "", value));
      comparison.append(item);
    }
    const receipt = node("details", "demo-receipt");
    receipt.append(node("summary", "", "Evidence source"), node("p", "", `Engineering record / ${model.record.recordedOn}. Curated summary, not an imported raw trace.`), node("code", "", sandbox.receipt));
    find("demoResult").replaceChildren(status, node("code", "demo-request", check.request), comparison, node("p", "", check.detail), receipt);
    icons();
    if (focusedId) find(focusedId)?.focus({ preventScroll: true });
  }

  function renderDecisions() {
    const focusedId = find("demoDecisions").contains(document.activeElement) ? document.activeElement.id : null;
    find("demoDecisions").replaceChildren(...model.record.decisions.map((decision, index) => {
      const button = selectable(undefined, model.decision === index, `decision-${decision.id}`, "demoDecision", () => {
        model.decision = index;
        renderDecisions();
      });
      button.className = "demo-decision-button";
      button.append(icon(decision.icon), node("span", "", decision.title), icon("chevron-right"));
      return button;
    }));
    const decision = model.record.decisions[model.decision];
    const emblem = node("div", "demo-decision-emblem");
    emblem.append(icon(decision.icon));
    const detail = find("demoDecision");
    detail.setAttribute("aria-labelledby", `decision-${decision.id}`);
    detail.replaceChildren(emblem, node("span", "demo-kicker", "ARCHITECTURE DECISION"), node("h2", "", decision.title));
    for (const [label, value] of [["The choice", decision.choice], ["Why it matters", decision.reason], ["The tradeoff", decision.cost], ["Supporting evidence", decision.evidence]]) {
      const section = node("div", "demo-decision-fact");
      section.append(node("h3", "", label), node("p", "", value));
      detail.append(section);
    }
    icons();
    if (focusedId) find(focusedId)?.focus({ preventScroll: true });
  }

  async function openDocument(document) {
    find("demoDocumentTitle").textContent = document.title;
    find("demoDocumentBody").textContent = "Loading document...";
    find("demoDocumentDialog").showModal();
    try {
      const response = await fetch(`/api/showcase/documents/${encodeURIComponent(document.id)}`, { signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw new Error("Document unavailable in this installation.");
      find("demoDocumentBody").textContent = await response.text();
      find("demoDocumentBody").scrollTop = 0;
    } catch {
      find("demoDocumentBody").textContent = "Document unavailable in this installation. The repository docs directory contains the build guide, demo runbook, and diagnostic record.";
    }
  }

  async function load() {
    if (model.loading) return;
    model.loading = true;
    find("demoLoading").hidden = false;
    find("demoError").hidden = true;
    try {
      const response = await fetch("/api/showcase", { signal: AbortSignal.timeout(10000) });
      if ([401, 403].includes(response.status)) {
        find("demoSource").textContent = "Sign-in required";
        find("demoLoading").textContent = "Sign in with an assigned demo account from the Live run view.";
        find("buildLoading").textContent = "Sign in with an assigned demo account from the Live run view.";
        return;
      }
      if (!response.ok) throw new Error("Record unavailable");
      const record = await response.json();
      if (record.schemaVersion !== 1 || record.source !== "recorded-summary" || !record.sandboxes?.length || !record.journey?.length) throw new Error("Unsupported record");
      model.record = record;
      find("demoSource").textContent = "Recorded engineering summary";
      find("demoDate").textContent = record.recordedOn;
      find("demoDate").dateTime = record.recordedOn;
      find("demoQualification").textContent = record.qualification;
      find("demoMilestones").replaceChildren(...record.milestones.map((milestone) => {
        const item = node("div", `demo-milestone demo-milestone-${milestone.state}`);
        item.append(icon(milestone.icon), node("span", "", milestone.label), node("strong", "", milestone.value));
        return item;
      }));
      renderJourney();
      renderSandbox();
      renderDecisions();
      const table = node("table");
      const head = node("thead");
      const headings = node("tr");
      for (const label of ["Component", "Contract", "Evidence"]) headings.append(node("th", "", label));
      head.append(headings);
      const body = node("tbody");
      record.build.forEach((item) => {
        const row = node("tr");
        const evidence = node("td");
        evidence.append(badge(item.kind));
        row.append(node("th", "", item.label), node("td", "", item.value), evidence);
        body.append(row);
      });
      table.append(head, body);
      find("demoBuild").replaceChildren(table);
      find("demoDocuments").replaceChildren(...record.documents.map((document) => {
        const button = node("button", "demo-document-link");
        button.type = "button";
        const copy = node("span");
        copy.append(node("strong", "", document.title), node("span", "", document.description));
        button.append(icon(document.icon), copy, icon("arrow-up-right"));
        button.addEventListener("click", () => openDocument(document));
        return button;
      }));
      find("demoContent").hidden = false;
      find("buildContent").hidden = false;
      find("buildLoading").hidden = true;
      icons();
    } catch {
      find("demoError").hidden = false;
      find("demoSource").textContent = "Record unavailable";
      find("buildLoading").textContent = "Engineering record unavailable. Retry from the Workspace view.";
    } finally {
      find("demoLoading").hidden = find("demoSource").textContent !== "Sign-in required";
      model.loading = false;
    }
  }

  find("demoPrevious").addEventListener("click", () => { model.stage = Math.max(0, model.stage - 1); renderJourney(); });
  find("demoNext").addEventListener("click", () => { model.stage = Math.min(model.record.journey.length - 1, model.stage + 1); renderJourney(); });
  find("demoDocumentClose").addEventListener("click", () => find("demoDocumentDialog").close());
  find("demoDocumentDialog").addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      find("demoDocumentDialog").close();
    }
  });
  find("demoRetry").addEventListener("click", load);
  keyboardTabs(find("demoJourney"));
  keyboardTabs(find("demoSandboxes"));
  keyboardTabs(find("demoDecisions"), true);
  find("demoExport").addEventListener("click", () => {
    const sandbox = model.record.sandboxes[model.sandbox];
    const payload = {
      schemaVersion: 1, source: model.record.source, recordedOn: model.record.recordedOn,
      currentHealth: model.record.currentHealth, qualification: model.record.qualification,
      sandbox: sandbox.name, observation: sandbox.checks[model.check],
      milestone: sandbox.receipt, evidenceSource: "Curated engineering record; not a raw trace",
    };
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
    const link = node("a");
    link.href = url;
    link.download = `learningnemo-${sandbox.id}-recorded-summary.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  window.addEventListener("console-session-changed", event => {
    if (state.hosting !== "azure") return;
    if (event.detail.status === "authenticated") { if (!model.record) load(); }
    else {
      model.record = null;
      find("demoContent").hidden = true;
      find("buildContent").hidden = true;
      find("buildLoading").hidden = false;
      find("demoSource").textContent = "Sign-in required";
    }
  });
  load();
})();