(() => {
  const find = id => document.getElementById(id);
  const text = (tag, value, className = "") => {
    const item = document.createElement(tag);
    item.textContent = value;
    item.className = className;
    return item;
  };
  const boundaries = {
    identity: {
      label: "01 / Human authorization", title: "A valid sign-in is the beginning, not a blank cheque.",
      risk: "A user can authenticate successfully but still lack authority to mutate a task or trigger a workspace action.",
      control: "Check both the delegated scope and the assigned app role at the server. The interface never chooses the user's role.",
      proof: "Reader denial plus unchanged task state; Operator execution plus authoritative read-back.",
      status: "Reader/Operator controls implemented. A full cloud persona rehearsal remains incomplete.",
    },
    agent: {
      label: "02 / Delegated agent authority", title: "The agent is not the human who launched it.",
      risk: "Passing all of an Operator's authority into an autonomous process lets a task expand into unrelated operations.",
      control: "Target design: bind a short-lived AgentRunner capability to its sponsor, task, workspace, tools, and expiry. Delegation can only narrow authority.",
      proof: "Reject a capability for another task/workspace, an expired capability, and an attempt to approve its own plan.",
      status: "Target design. The current fixed workspace proof is controller-mediated, not this delegated AgentRunner workflow.",
    },
    workspace: {
      label: "03 / Runtime containment", title: "Constrain what the process can do, even when the model is wrong.",
      risk: "Untrusted instructions or generated code can attempt file access, unexpected network calls, or privilege escalation.",
      control: "SAW supplies the outer Azure engagement boundary. OpenShell applies distinct non-root process and route policies inside it. Guardrails inspect content before the model acts.",
      proof: "Execute inside Planning, observe its UID, test protected writes, and compare an allowed method/path with a denied one.",
      status: "Sandbox bootstrap and non-root probes observed. Approved API connectivity under runtime lockdown remains unresolved.",
    },
    approval: {
      label: "04 / Consequential action", title: "A proposed fix cannot authorize itself.",
      risk: "An apparently reasonable plan can change after approval, be approved by its own author, or be replayed.",
      control: "Bind approval to the exact plan hash and a distinct actor. The deterministic broker accepts only registered, bounded operations, not arbitrary SQL.",
      proof: "Deny self-approval, a changed plan, and a reused execution grant. Show a separate valid approval before execution.",
      status: "Approval-bound service cycle has separate evidence. Interactive Approver and sandbox-to-broker integration are pending.",
    },
    azure: {
      label: "05 / Independent verification", title: "The result comes from the system, not the agent's final sentence.",
      risk: "A fluent success message can hide an unchanged database, partial execution, or a failed security gate.",
      control: "Dedicated workload identities perform narrow service operations. A separate verifier reads authoritative state and binds evidence to the run.",
      proof: "Compare before/after state, operation receipts, identity decisions, and verification results under one engagement ID.",
      status: "Azure services are deployed; the complete correlated incident journey has not been demonstrated through this dashboard.",
    },
  };
  const personas = [
    { id: "reader", name: "Reader", subtitle: "Visibility without mutation", icon: "eye", allowed: "Inspect incident status and redacted evidence.", denied: "Operate the workspace, execute remediation, or approve a critical plan.", why: "Read access should not silently become operational authority.", state: "Reader role exists. The current task lab demonstrates a subset of this target incident role." },
    { id: "operator", name: "Operator", subtitle: "Sponsor and operate", icon: "user-round-cog", allowed: "Sponsor investigation, propose a plan, and trigger an approved operation.", denied: "Approve the same critical plan they will execute, or connect directly to SQL.", why: "Operational responsibility is separate from critical-change approval.", state: "Operator task and fixed-probe controls exist. Full incident sponsorship/delegation is not yet connected." },
    { id: "approver", name: "Approver", subtitle: "An independent decision", icon: "file-check-2", allowed: "Target: review and approve or reject the exact permanent-fix plan.", denied: "Execute remediation or complete the Operator's task.", why: "Approval is a separate actor's decision, not another step the agent can grant itself.", state: "Dedicated Approver sign-in and restricted view are implemented. The plan queue and approval submission service are not connected yet." },
    { id: "agent", name: "AgentRunner", subtitle: "A distinct non-human actor", icon: "bot", allowed: "Analyze bounded evidence and use delegated tools for one task and workspace.", denied: "Approve itself, widen its policy, inherit all human roles, or access SQL credentials.", why: "Task-specific machine authority limits what an autonomous process can do on a human's behalf.", state: "Target capability model, not the identity of the current cloud task agent or fixed probe." },
  ];

  function renderBoundary(id) {
    const boundary = boundaries[id];
    document.querySelectorAll('.pattern-map [role="tab"]').forEach(button => {
      const selected = button.id === `boundary-${id}`;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    const panel = find("patternBoundaryDetail");
    panel.setAttribute("aria-labelledby", `boundary-${id}`);
    const heading = text("div", "", "pattern-detail-lead");
    heading.append(text("span", boundary.label, "pattern-eyebrow"), text("h3", boundary.title));
    const facts = text("dl", "", "pattern-detail-facts");
    for (const [name, value] of [["The risk", boundary.risk], ["The decision", boundary.control], ["What would prove it", boundary.proof]]) {
      const row = text("div", "");
      row.append(text("dt", name), text("dd", value));
      facts.append(row);
    }
    panel.replaceChildren(heading, facts, text("p", boundary.status, "pattern-detail-status"));
  }

  function renderPersona(id) {
    const persona = personas.find(item => item.id === id);
    document.querySelectorAll('#patternPersonaTabs [role="tab"]').forEach(button => {
      const selected = button.id === `persona-design-${id}`;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    const panel = find("patternPersonaDetail");
    panel.setAttribute("aria-labelledby", `persona-design-${id}`);
    const heading = text("div", "", "pattern-persona-lead");
    heading.append(text("span", "PERSONA DESIGN", "pattern-eyebrow"), text("h3", persona.subtitle), text("p", persona.why));
    const permissions = text("div", "", "pattern-permission-pair");
    for (const [label, value, kind] of [["Permitted by design", persona.allowed, "allow"], ["Outside its authority", persona.denied, "deny"]]) {
      const item = text("div", "", `pattern-permission-${kind}`);
      item.append(text("h4", label), text("p", value));
      permissions.append(item);
    }
    panel.replaceChildren(heading, permissions, text("p", persona.state, "pattern-detail-status"));
  }

  function keyboard(container) {
    container.addEventListener("keydown", event => {
      const tabs = [...container.querySelectorAll('[role="tab"]')];
      const current = tabs.indexOf(document.activeElement);
      if (current < 0) return;
      const target = event.key === "ArrowRight" ? (current + 1) % tabs.length
        : event.key === "ArrowLeft" ? (current + tabs.length - 1) % tabs.length
        : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
      if (target === null) return;
      event.preventDefault();
      tabs[target].click();
      tabs[target].focus({ preventScroll: true });
    });
  }
  Object.keys(boundaries).forEach(id => find(`boundary-${id}`).addEventListener("click", () => renderBoundary(id)));
  find("patternPersonaTabs").replaceChildren(...personas.map((persona, index) => {
    const button = text("button", "", "pattern-persona-tab");
    button.id = `persona-design-${persona.id}`;
    button.type = "button";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", "patternPersonaDetail");
    button.setAttribute("aria-selected", String(index === 0));
    button.tabIndex = index === 0 ? 0 : -1;
    const icon = document.createElement("i");
    icon.dataset.lucide = persona.icon;
    icon.setAttribute("aria-hidden", "true");
    button.append(icon, text("span", persona.name));
    button.addEventListener("click", () => renderPersona(persona.id));
    return button;
  }));
  document.querySelectorAll("[data-pattern-view]").forEach(button => button.addEventListener("click", () => {
    find(button.dataset.patternView).click();
    find(button.dataset.patternView).focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: "instant" });
  }));
  keyboard(document.querySelector(".pattern-map"));
  keyboard(find("patternPersonaTabs"));
  renderBoundary("identity");
  renderPersona("reader");
  window.lucide?.createIcons({ attrs: { "stroke-width": 1.7 } });
})();