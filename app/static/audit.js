const emptyState = document.querySelector("#audit-empty");
const content = document.querySelector("#audit-content");
const validationBadge = document.querySelector("#audit-validation-status");
const eligibilityValue = document.querySelector("#audit-export-eligibility");
const blockingList = document.querySelector("#audit-blocking-list");
const regionList = document.querySelector("#audit-region-list");
const assumptionList = document.querySelector("#audit-assumption-list");
const exportButton = document.querySelector("#audit-export-button");
const actionStatus = document.querySelector("#audit-action-status");

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function conditionSummary(condition) {
  const values = Object.entries(condition)
    .filter(([key]) => !["type", "region_ref"].includes(key))
    .map(([key, value]) => `${key}: ${JSON.stringify(value)}`)
    .join("; ");
  return values ? `${condition.type} — ${values}` : condition.type;
}

function actionButton(label, action, kind, id) {
  const button = element("button", `audit-action ${action}`, label);
  button.type = "button";
  button.dataset.action = action;
  button.dataset.kind = kind;
  button.dataset.id = id;
  return button;
}

function renderRegion(region) {
  const card = element("article", `audit-card region-card ${region.status}`);
  const heading = element("div", "audit-card-heading");
  heading.append(
    element("strong", "audit-card-title", region.id),
    element("span", `audit-status ${region.status}`, region.status),
  );
  card.append(heading);

  const ids = region.entity_ids.map((id) => String(id)).join(", ");
  const facts = element("dl", "audit-facts");
  for (const [label, value] of [
    ["Entities", ids],
    ["Source", region.source_instruction],
    ["Method", region.selection_method],
    ["Confidence", Number(region.confidence).toFixed(3)],
  ]) {
    facts.append(element("dt", "", label), element("dd", "", value));
  }
  card.append(facts);

  const conditions = [...region.boundary_conditions, ...region.loads];
  const conditionList = element("ul", "audit-condition-list");
  if (conditions.length === 0) {
    conditionList.append(element("li", "muted", "No associated BC or load."));
  } else {
    for (const condition of conditions) {
      conditionList.append(element("li", "", conditionSummary(condition)));
    }
  }
  card.append(conditionList);

  if (region.status === "proposed") {
    const controls = element("div", "audit-actions");
    controls.append(
      actionButton("Confirm", "confirm", "region", region.id),
      actionButton("Reject", "reject", "region", region.id),
    );
    card.append(controls);
  }
  return card;
}

function renderAssumption(assumption) {
  const card = element("article", `audit-card assumption-card ${assumption.status}`);
  const heading = element("div", "audit-card-heading");
  heading.append(
    element(
      "span",
      `criticality ${assumption.criticality}`,
      assumption.criticality === "unit_critical" ? "Unit-critical" : "Noncritical",
    ),
    element("span", `audit-status ${assumption.status}`, assumption.status),
  );
  card.append(heading, element("p", "assumption-text", assumption.text));
  if (assumption.status === "pending") {
    const controls = element("div", "audit-actions");
    controls.append(
      actionButton("Accept", "accept", "assumption", assumption.id),
      actionButton("Reject", "reject", "assumption", assumption.id),
    );
    card.append(controls);
  }
  return card;
}

async function responseMessage(response) {
  try {
    const payload = await response.json();
    if (typeof payload.detail === "string") return payload.detail;
    return payload.message || `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

export function createAuditPanel({ onHighlight, onStatus }) {
  let modelId = null;
  let latestAudit = null;

  function showUnavailable(message) {
    latestAudit = null;
    emptyState.textContent = message;
    emptyState.hidden = false;
    content.hidden = true;
    validationBadge.textContent = "Unvalidated";
    validationBadge.dataset.status = "unvalidated";
    exportButton.disabled = true;
  }

  function render(audit) {
    latestAudit = audit;
    emptyState.hidden = true;
    content.hidden = false;
    validationBadge.textContent = audit.validation_status;
    validationBadge.dataset.status = audit.validation_status;
    eligibilityValue.textContent = audit.export_eligible ? "Eligible" : "Blocked";
    eligibilityValue.dataset.eligible = String(audit.export_eligible);
    exportButton.disabled = !audit.export_eligible;

    blockingList.replaceChildren();
    const blockingIssues = audit.validation_report.issues.filter((issue) => issue.blocks_export);
    if (blockingIssues.length === 0) {
      blockingList.append(element("li", "clear", "No blocking issues."));
    } else {
      for (const issue of blockingIssues) {
        const item = element("li", issue.severity);
        item.append(
          element("code", "", issue.code),
          document.createTextNode(` ${issue.message}`),
        );
        blockingList.append(item);
      }
    }

    regionList.replaceChildren(...audit.regions.map(renderRegion));
    assumptionList.replaceChildren(...audit.assumptions.map(renderAssumption));
    if (audit.assumptions.length === 0) {
      assumptionList.append(element("p", "audit-empty-inline", "No assumptions recorded."));
    }

    for (const region of audit.regions) {
      if (!region.entity_ids.every((id) => Number.isInteger(Number(id)))) continue;
      onHighlight({
        entity_ids: region.entity_ids.map(Number),
        style: region.status === "rejected" ? "base" : region.status,
      });
    }
  }

  async function refresh() {
    if (!modelId) return;
    const response = await fetch(`/session/${modelId}/audit`);
    if (response.status === 409) {
      showUnavailable("This model has no saved simulation intent yet.");
      return;
    }
    if (!response.ok) throw new Error(await responseMessage(response));
    render(await response.json());
  }

  async function runAction(kind, action, id) {
    if (!modelId) return;
    const encodedId = encodeURIComponent(id);
    const path = kind === "region"
      ? `/session/${modelId}/${action}_region`
      : `/session/${modelId}/assumptions/${encodedId}/${action}`;
    const options = { method: "POST" };
    if (kind === "region") {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify({ region_id: id });
    }
    const response = await fetch(path, options);
    if (!response.ok) throw new Error(await responseMessage(response));
    await refresh();
    actionStatus.textContent = `${kind} ${id} updated to ${action}.`;
    onStatus(actionStatus.textContent);
  }

  content.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    button.disabled = true;
    try {
      await runAction(button.dataset.kind, button.dataset.action, button.dataset.id);
    } catch (error) {
      actionStatus.textContent = error.message || "Audit action failed.";
      onStatus(actionStatus.textContent, true);
      button.disabled = false;
    }
  });

  exportButton.addEventListener("click", async () => {
    if (!modelId || !latestAudit?.export_eligible) return;
    exportButton.disabled = true;
    try {
      const response = await fetch(`/session/${modelId}/export-gate`, { method: "POST" });
      if (!response.ok) throw new Error(await responseMessage(response));
      const readiness = await response.json();
      actionStatus.textContent = readiness.message;
      onStatus(readiness.message);
    } catch (error) {
      actionStatus.textContent = error.message || "Readiness check failed.";
      onStatus(actionStatus.textContent, true);
    } finally {
      exportButton.disabled = !latestAudit?.export_eligible;
    }
  });

  return {
    async setModel(nextModelId) {
      modelId = nextModelId;
      actionStatus.textContent = "";
      try {
        await refresh();
      } catch (error) {
        showUnavailable(error.message || "Audit state could not be loaded.");
        onStatus(error.message || "Audit state could not be loaded.", true);
      }
    },
    refresh,
  };
}
