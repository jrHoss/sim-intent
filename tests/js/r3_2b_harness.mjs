import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import path from "node:path";
import {
  actionButton,
  buildDocument,
  button,
  deferred,
  dispatch,
  tick,
  waitFor,
} from "./fake-dom.mjs";

function intent(label) {
  return {
    schema_version: 3,
    analysis: {
      type: "static_structural",
      units: { length: "mm", force: "N", stress: "MPa" },
      dimensionality: "3d_solid",
      solver_target: "calculix",
      coordinate_system: "global_cartesian",
    },
    materials: [{
      name: `material-${label}`,
      model: "linear_elastic_isotropic",
      authority: "system_proposed",
      proposal_assumption_ref: `assumption-${label}`,
      E_MPa: 70000,
      nu: 0.3,
      density_tonne_per_mm3: null,
      youngs_modulus_original: { value: 70, unit: "GPa" },
      density_original: null,
    }],
    regions: [{
      id: `region-${label}`,
      entity_type: "cad_face",
      // v3 CAD evidence: a durable target, never an entity_ids projection.
      cad_face_target: {
        resolution: "resolved",
        model_version_id: "version-A",
        artifact_sha256: "a".repeat(64),
        stable_identities: [`gfi1:${String(label).repeat(4)}`],
        source_face_tags: [label === "A" ? 1 : 2],
      },
      selection_method: "user_click",
      confidence: 1,
      source_instruction: `region ${label}`,
      status: "proposed",
    }],
    bcs: [],
    loads: [],
    assumptions: [{
      id: `assumption-${label}`,
      text: `material ${label}`,
      status: "pending",
    }],
    mesh_settings: {
      global_element_size_mm: 5,
      element_type: "tetrahedral",
      element_order: "first_order",
      mesher: "gmsh",
      mesher_preset: "gmsh_tet_v1",
      target_size_original: { value: 5, unit: "mm" },
    },
    solver_settings: {
      target: "calculix",
      analysis_profile: "linear_static_v1",
      requested_results: ["displacement", "stress"],
    },
    validation_status: "unvalidated",
  };
}

function revision(setupId, number, setupIntent, requestId = `request-${number}`) {
  return {
    schema_version: 1,
    id: `${setupId}-revision-${number}`,
    setup_id: setupId,
    revision: number,
    parent_revision_id: number > 1 ? `${setupId}-revision-${number - 1}` : null,
    simulation_intent_schema_version: 3,
    intent_sha256: `${setupId}-${number}`,
    mutation_type: number === 1 ? "create" : "update",
    request_id: requestId,
    created_at: `2026-01-${String(number).padStart(2, "0")}T00:00:00Z`,
    intent: structuredClone(setupIntent),
    validation: {
      readiness_status: "needs_review",
      issues: [],
      load_summary: {
        explicit_force_vector_sum_N: [0, 0, 0],
        distributed_load_count: 0,
        gravity_density_required: false,
        gravity_density_available: false,
        unresolved_resultants: [],
      },
    },
    selected_entities: {},
    highlight_state: {},
    engineering_ready: false,
    artifact_capability: { supported: false, blocking_issue_codes: [] },
    export_eligible: false,
  };
}

function makeApi(ApiError) {
  const projects = [{ id: "project", name: "Project" }, { id: "other", name: "Other" }];
  const setups = new Map();
  for (const label of ["A", "B"]) {
    const setup = {
      id: `setup-${label}`,
      project_id: "project",
      model_id: "model-A",
      model_version_id: "version-A",
      current_revision: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      model_version_is_current: true,
      is_stale: false,
      stale_reason: null,
      stale_at: null,
    };
    setups.set(setup.id, { setup, history: [revision(setup.id, 1, intent(label))] });
  }
  const otherSetup = {
    id: "setup-C", project_id: "other", model_id: "model-C", model_version_id: "version-C",
    current_revision: 1, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    model_version_is_current: true, is_stale: false, stale_reason: null, stale_at: null,
  };
  setups.set("setup-C", { setup: otherSetup, history: [revision("setup-C", 1, intent("C"))] });

  const calls = [];
  let failure = null;
  const current = (setupId) => setups.get(setupId).history.at(-1);
  const mutate = (setupId, body, transform = null) => {
    calls.push({ kind: "mutation", setupId, body: structuredClone(body) });
    if (failure) {
      const pending = failure;
      failure = null;
      throw pending;
    }
    const record = setups.get(setupId);
    const nextIntent = body.intent ? structuredClone(body.intent) : structuredClone(current(setupId).intent);
    transform?.(nextIntent);
    const next = revision(setupId, record.history.length + 1, nextIntent, body.request_id);
    record.history.push(next);
    record.setup.current_revision = next.revision;
    return structuredClone(next);
  };

  return {
    projects,
    setups,
    calls,
    failNetwork() {
      const error = new Error("offline");
      error.name = "NetworkError";
      failure = error;
    },
    failConflict() {
      failure = new ApiError(
        { status: 409 },
        { code: "setup_revision_conflict", detail: "stale revision", errors: [] },
      );
    },
    listProjects: async () => structuredClone(projects),
    readProject: async (id) => structuredClone(projects.find((item) => item.id === id)),
    listSetups: async (projectId) => [...setups.values()]
      .map((item) => item.setup)
      .filter((item) => item.project_id === projectId)
      .map((item) => structuredClone(item)),
    readSetup: async (id) => ({
      setup: structuredClone(setups.get(id).setup),
      current: structuredClone(current(id)),
    }),
    readModelVersion: async (id) => {
      const label = id.slice(-1);
      return { id, model_id: `model-${label}`, source_name: `${label}.step`, model_kind: "step", version: 1 };
    },
    listModelVersions: async (modelId) => [{
      id: `version-${modelId.slice(-1)}`, model_id: modelId, source_name: `${modelId.slice(-1)}.step`,
      model_kind: "step", version: 1, is_current: true,
    }],
    readInventory: async () => ({ faces: [] }),
    readGeometryIdentity: async (versionId) => ({
      artifact_sha256: "a".repeat(64),
      model_version_id: versionId,
      faces: [],
      collision_groups: [],
    }),
    listRevisions: async (setupId) => structuredClone(setups.get(setupId).history),
    createRevision: async (setupId, body) => mutate(setupId, body),
    decideRegion: async (setupId, regionId, action, body) => mutate(setupId, body, (nextIntent) => {
      nextIntent.regions.find((region) => region.id === regionId).status = action === "confirm" ? "confirmed" : "rejected";
    }),
    decideAssumption: async (setupId, assumptionId, action, body) => mutate(setupId, body, (nextIntent) => {
      nextIntent.assumptions.find((item) => item.id === assumptionId).status = action === "accept" ? "accepted" : "rejected";
    }),
    interpretVersion: async () => ({ grounding: { results: [] }, intent: intent("proposal") }),
    createProject: async (name) => ({ id: "created", name }),
  };
}

const root = process.env.SIM_INTENT_ROOT;
globalThis.document = buildDocument();
globalThis.localStorage = {
  values: new Map(),
  getItem(key) { return this.values.get(key) ?? null; },
  setItem(key, value) { this.values.set(key, value); },
};

const engineeringModule = await import(pathToFileURL(path.join(root, "app/static/engineering.js")));
const apiModule = await import(pathToFileURL(path.join(root, "app/static/durable-api.js")));
const groundingModule = await import(pathToFileURL(path.join(root, "app/static/grounding-highlights.js")));
const localGroundingCommands = [];
groundingModule.applyReturnedGrounding({
  results: [
    { clarification: { candidate_sets: [{ entity_ids: [1, 2] }] } },
    { region: { entity_ids: [3] }, bc: { type: "fixed_displacement" } },
  ],
}, (command) => localGroundingCommands.push(command));
assert.deepEqual(localGroundingCommands, [
  { reset: true },
  { entity_ids: [1, 2], style: "candidate" },
  { entity_ids: [3], style: "proposed" },
  { entity_ids: [3], style: "fixed_boundary_condition" },
]);
const api = makeApi(apiModule.ApiError);
const loadDeferred = new Map();
let viewerVersion = null;
let viewerSetup = null;
let viewerGeometry = null;
const loadVersion = (version, context) => new Promise((resolve) => {
  const geometry = Object.freeze({
    setupId: context.setupId,
    versionId: version.id,
    epoch: context.epoch,
  });
  loadDeferred.set(context.setupId, () => {
    loadDeferred.delete(context.setupId);
    if (context.isCurrent()) {
      viewerVersion = version.id;
      viewerSetup = context.setupId;
      viewerGeometry = geometry;
    }
    resolve();
  });
});
const highlightCommands = [];
const statusUpdates = [];
let idCounter = 0;
const workspace = engineeringModule.createEngineeringWorkspace({
  api,
  makeRequestId: (prefix) => `${prefix}-${++idCounter}`,
  onLoadVersion: loadVersion,
  onHighlight: (command) => highlightCommands.push(structuredClone(command)),
  onStatus: (message, error = false) => statusUpdates.push({ message, error }),
});

await workspace.initialize();
await workspace.initialize();
const projectSelect = document.querySelector("#project-select");
projectSelect.value = "project";
dispatch(projectSelect, "change");
await waitFor(() => document.querySelector("#setup-select").options.length === 3, "project did not open");

const setupSelect = document.querySelector("#setup-select");
setupSelect.value = "setup-A";
dispatch(setupSelect, "change");
await waitFor(() => loadDeferred.has("setup-A"), "setup A did not reach deferred viewer load");
setupSelect.value = "setup-B";
dispatch(setupSelect, "change");
await waitFor(() => loadDeferred.has("setup-B"), "setup B did not reach deferred viewer load");
loadDeferred.get("setup-B")();
await waitFor(() => document.querySelector("#setup-select").value === "setup-B", "setup B did not render");
loadDeferred.get("setup-A")();
await tick();
assert.equal(viewerVersion, "version-A");
assert.equal(viewerSetup, "setup-B");
assert.equal(document.querySelector("#setup-select").value, "setup-B");
assert.match(document.querySelector("#revision-history-list").textContent, /request-1/);

// Display A, then prove a delayed A /select response cannot enter B even
// though both setups intentionally share the same model version.
setupSelect.value = "setup-A";
dispatch(setupSelect, "change");
await waitFor(() => loadDeferred.has("setup-A"), "setup A reopen did not reach viewer load");
loadDeferred.get("setup-A")();
await waitFor(() => viewerSetup === "setup-A" && workspace.currentSetup()?.id === "setup-A", "setup A did not display");
const staleEntityId = 1;
const staleSelectionResponse = deferred();
const selectedClicks = new Set();
const activity = [];
let selectionValue = "None";
let selectionRequestCount = 0;
const selectOperation = (entityId, requestSelection) => {
  const contextToken = workspace.captureViewerSelectionContext(entityId, viewerGeometry);
  return engineeringModule.runFaceSelectionOperation({
    entityId,
    contextToken,
    workspace,
    getViewerSourceId: () => viewerGeometry,
    requestSelection: () => {
      selectionRequestCount += 1;
      return requestSelection();
    },
    responseError: async (response) => `Selection failed (${response.status})`,
    onAccepted(selection) {
      selectedClicks.add(entityId);
      selectionValue = `face_${entityId}`;
      activity.push(selection.node_name);
      statusUpdates.push({ message: `${selection.node_name} recorded by the server.`, error: false });
    },
    onError(error) {
      statusUpdates.push({
        message: error.message || "Selection could not be recorded.",
        error: true,
      });
    },
  });
};

setupSelect.value = "setup-B";
dispatch(setupSelect, "change");
await waitFor(() => loadDeferred.has("setup-B"), "shared-version setup B did not reach deferred viewer load");
const staleSelection = selectOperation(staleEntityId, () => staleSelectionResponse.promise);
assert.equal(selectionRequestCount, 1);
assert.equal(selectionValue, "None");
assert.deepEqual([...selectedClicks], []);
loadDeferred.get("setup-B")();
await waitFor(() => viewerSetup === "setup-B" && workspace.currentSetup()?.id === "setup-B", "shared-version setup B did not open");

const mutationsBeforeStaleResponse = api.calls.length;
const historyBeforeStaleResponse = api.setups.get("setup-B").history.length;
const historyMarkupBeforeStaleResponse = document.querySelector("#revision-history-list").textContent;
const statusesBeforeStaleResponse = structuredClone(statusUpdates);
const highlightsBeforeStaleResponse = structuredClone(highlightCommands);
staleSelectionResponse.resolve({
  ok: true,
  json: async () => ({ node_name: `face_${staleEntityId}` }),
});
assert.equal(await staleSelection, false);
await tick();

assert.equal(projectSelect.value, "project");
assert.equal(setupSelect.value, "setup-B");
assert.equal(workspace.currentSetup().id, "setup-B");
assert.equal(viewerSetup, "setup-B");
assert.equal(viewerVersion, "version-A");
assert.equal(workspace.selectedClicks().includes(staleEntityId), false);
assert.equal(selectedClicks.has(staleEntityId), false);
assert.equal(selectionValue, "None");
assert.deepEqual(activity, []);
assert.deepEqual(statusUpdates, statusesBeforeStaleResponse);
assert.deepEqual(highlightCommands, highlightsBeforeStaleResponse);
assert.equal(document.querySelector("#bc-viewer-selection").textContent, "None");
assert.equal(document.querySelector("#load-viewer-selection").textContent, "None");
assert.equal(document.querySelector("#bc-target").options.some((option) => option.value === "viewer"), false);
assert.equal(document.querySelector("#load-target").options.some((option) => option.value === "viewer"), false);
assert.equal(api.calls.length, mutationsBeforeStaleResponse);
assert.equal(api.setups.get("setup-B").history.length, historyBeforeStaleResponse);
assert.equal(document.querySelector("#revision-history-list").textContent, historyMarkupBeforeStaleResponse);

// Positive control: a click made in stable B is accepted normally.
const acceptedEntityId = 2;
assert.equal(await selectOperation(acceptedEntityId, async () => ({
  ok: true,
  json: async () => ({ node_name: `face_${acceptedEntityId}` }),
})), true);
assert.deepEqual(workspace.selectedClicks(), [acceptedEntityId]);
assert.deepEqual([...selectedClicks], [acceptedEntityId]);
assert.equal(selectionValue, `face_${acceptedEntityId}`);
assert.deepEqual(activity, [`face_${acceptedEntityId}`]);
assert.equal(document.querySelector("#bc-viewer-selection").textContent, `face_${acceptedEntityId}`);
assert.equal(document.querySelector("#load-viewer-selection").textContent, `face_${acceptedEntityId}`);
assert.equal(document.querySelector("#bc-target").options.some((option) => option.value === "viewer"), true);
assert.equal(document.querySelector("#load-target").options.some((option) => option.value === "viewer"), true);

// Isolate setup-ID and operation-epoch rejection with every other field current.
const validContextToken = workspace.captureViewerSelectionContext(3, viewerGeometry);
const setupMismatchToken = Object.freeze({ ...validContextToken, setupId: "setup-A" });
assert.equal(workspace.setViewerSelection(3, setupMismatchToken, viewerGeometry), false);
assert.deepEqual(workspace.selectedClicks(), [acceptedEntityId]);
const epochMismatchToken = Object.freeze({
  ...validContextToken,
  operationEpoch: validContextToken.operationEpoch - 1,
});
assert.equal(workspace.setViewerSelection(3, epochMismatchToken, viewerGeometry), false);
assert.equal(workspace.setViewerSelection(3, null, viewerGeometry), false);
assert.deepEqual(workspace.selectedClicks(), [acceptedEntityId]);

document.querySelector("#reject-material-proposal").click();
await waitFor(
  () => api.setups.get("setup-B").history.at(-1).intent.assumptions[0].status === "rejected"
    && document.querySelector("#material-decision-actions").hidden,
  "Reject material proposal control did not mutate once",
);

const materialName = document.querySelector("#material-name");
materialName.value = "exact-replay-in-origin";
document.querySelector("#material-e").value = "70";
document.querySelector("#material-e-unit").value = "GPa";
document.querySelector("#material-nu").value = "0.3";
dispatch(document.querySelector("#material-form"), "input");
api.failNetwork();
dispatch(document.querySelector("#material-form"), "submit");
await waitFor(() => !document.querySelector("#retry-mutation").hidden, "origin retry banner was not shown");
const failedExactBody = api.calls.at(-1).body;
document.querySelector("#retry-mutation-button").click();
await waitFor(
  () => document.querySelector("#material-name").value === "exact-replay-in-origin"
    && document.querySelector("#retry-mutation").hidden,
  "exact replay did not complete in its origin",
);
assert.deepEqual(api.calls.at(-1).body, failedExactBody);

// A failed exact request cannot replay after context replacement.
materialName.value = "draft-before-network-error";
dispatch(document.querySelector("#material-form"), "input");
api.failNetwork();
dispatch(document.querySelector("#material-form"), "submit");
await waitFor(() => !document.querySelector("#retry-mutation").hidden, "retry banner was not shown");
const mutationCount = api.calls.length;
const historyA = api.setups.get("setup-A").history.length;
const historyB = api.setups.get("setup-B").history.length;
setupSelect.value = "setup-A";
dispatch(setupSelect, "change");
await waitFor(() => loadDeferred.has("setup-A"), "setup A reopen was not requested");
loadDeferred.get("setup-A")();
await waitFor(
  () => document.querySelector("#source-version").textContent.includes("A.step"),
  "setup A did not open",
);
document.querySelector("#retry-mutation-button").click();
await tick();
assert.equal(api.calls.length, mutationCount);
assert.equal(api.setups.get("setup-A").history.length, historyA);
assert.equal(api.setups.get("setup-B").history.length, historyB);
document.querySelector("#accept-material-proposal").click();
await waitFor(
  () => api.setups.get("setup-A").history.at(-1).intent.assumptions[0].status === "accepted"
    && document.querySelector("#material-decision-actions").hidden,
  "Accept material proposal control did not mutate once",
);
const signedBaseline = api.setups.get("setup-A").history.length;

// Signed zero survives DOM input, cloning, mutation, reopen, and an unrelated mutation.
const bcType = document.querySelector("#bc-type");
bcType.value = "prescribed_displacement";
dispatch(bcType, "change");
document.querySelector("#bc-target").value = "existing|region-A";
const prescribedX = document.querySelector('input[name="prescribed_axis"][value="x"]');
prescribedX.checked = true;
const axisX = document.querySelector('[data-axis="x"]');
axisX.value = "-0";
dispatch(document.querySelector("#bc-form"), "input");
dispatch(document.querySelector("#bc-form"), "submit");
await waitFor(
  () => api.setups.get("setup-A").history.length === signedBaseline + 1
    && button(document.querySelector("#bc-list"), "edit", 0),
  `signed-zero BC was not saved: ${document.querySelector("#workspace-status").textContent}`,
);
let signedBody = api.calls.at(-1).body;
assert(Object.is(signedBody.intent.bcs.at(-1).components.x, -0));
assert(Object.is(signedBody.intent.bcs.at(-1).components_original.x.value, -0));
assert(Object.is(api.setups.get("setup-A").history.at(-1).intent.bcs.at(-1).components.x, -0));
button(document.querySelector("#bc-list"), "edit", 0).click();
assert.equal(axisX.value, "-0");
document.querySelector("#bc-cancel-edit").click();
materialName.value = "unrelated-material";
document.querySelector("#material-e").value = "70";
document.querySelector("#material-e-unit").value = "GPa";
document.querySelector("#material-nu").value = "0.3";
dispatch(document.querySelector("#material-form"), "input");
dispatch(document.querySelector("#material-form"), "submit");
await waitFor(() => api.setups.get("setup-A").history.length === signedBaseline + 2, "material mutation did not finish");
await waitFor(
  () => document.querySelector("#current-revision").textContent === `r${signedBaseline + 2}`,
  "material mutation did not render",
);
button(document.querySelector("#bc-list"), "edit", 0).click();
assert.equal(axisX.value, "-0");
assert(Object.is(api.setups.get("setup-A").history.at(-1).intent.bcs[0].components.x, -0));
document.querySelector("#bc-cancel-edit").click();

// The real API serializer emits a signed JSON number instead of collapsing it.
let serializedBody = null;
globalThis.fetch = async (_path, options) => {
  serializedBody = options.body;
  return { ok: true, status: 201, json: async () => ({}) };
};
await apiModule.durableApi.createRevision("setup-A", {
  expected_revision: 1,
  request_id: "signed-zero",
  intent: { value: -0, positive: 0 },
});
assert.match(serializedBody, /"value":-0\.0/);
assert.match(serializedBody, /"positive":0/);

// A checked blank component is accessible validation, not zero.
bcType.value = "prescribed_displacement";
dispatch(bcType, "change");
document.querySelector("#bc-target").value = "existing|region-A";
prescribedX.checked = true;
axisX.value = "   ";
const beforeBlank = api.calls.length;
dispatch(document.querySelector("#bc-form"), "submit");
await tick();
assert.equal(api.calls.length, beforeBlank);
assert.equal(axisX.getAttribute("aria-invalid"), "true");
assert.match(document.querySelector("#workspace-status").textContent, /required when its axis is selected/);
document.querySelector("#bc-cancel-edit").click();

// Targets are isolated between add/edit/clear and discriminator changes.
const fixedX = document.querySelector('input[name="fixed_axis"][value="x"]');
bcType.value = "fixed_displacement";
dispatch(bcType, "change");
fixedX.checked = true;
document.querySelector("#bc-target").value = "existing|region-A";
dispatch(document.querySelector("#bc-form"), "input");
assert.equal(fixedX.checked, true);
assert.equal(document.querySelector("#bc-edit-index").value, "");
assert.equal(bcType.value, "fixed_displacement");
dispatch(document.querySelector("#bc-form"), "submit");
await waitFor(
  () => document.querySelector("#bc-target").value === "",
  `fixed BC seeded the next BC: ${document.querySelector("#workspace-status").textContent}; `
    + `type=${bcType.value}; calls=${api.calls.length}; history=${api.setups.get("setup-A").history.length}`,
);
button(document.querySelector("#bc-list"), "edit", 1).click();
assert.equal(document.querySelector("#bc-target").value, "existing|region-A");
bcType.value = "prescribed_displacement";
dispatch(bcType, "change");
assert.equal(document.querySelector("#bc-target").value, "existing|region-A");
document.querySelector("#bc-cancel-edit").click();
assert.equal(document.querySelector("#bc-target").value, "");
bcType.value = "prescribed_displacement";
dispatch(bcType, "change");
assert.equal(document.querySelector("#bc-target").value, "");

const loadTypes = ["resultant_surface_force", "pressure", "surface_traction", "gravity"];
for (const [loadIndex, type] of loadTypes.entries()) {
  const loadType = document.querySelector("#load-type");
  loadType.value = type;
  dispatch(loadType, "change");
  assert.equal(
    document.querySelector("#load-target").value,
    type === "gravity" ? "whole-model" : "",
  );
  document.querySelector("#load-magnitude").value = type === "gravity" ? "9.81" : "2";
  document.querySelector("#load-direction-x").value = "1";
  document.querySelector("#load-direction-y").value = "0";
  document.querySelector("#load-direction-z").value = "0";
  if (type !== "gravity") document.querySelector("#load-target").value = "existing|region-A";
  dispatch(document.querySelector("#load-form"), "input");
  dispatch(document.querySelector("#load-form"), "submit");
  await waitFor(
    () => document.querySelector("#load-edit-index").value === ""
      && document.querySelector("#load-target").value === ""
      && button(document.querySelector("#load-list"), "edit", loadIndex),
    `${type} load did not clear`,
  );
  button(document.querySelector("#load-list"), "edit", loadIndex).click();
  assert.equal(
    document.querySelector("#load-target").value,
    type === "gravity" ? "whole-model" : "existing|region-A",
  );
  if (type === "pressure") {
    document.querySelector("#load-type").value = "surface_traction";
    dispatch(document.querySelector("#load-type"), "change");
    assert.equal(document.querySelector("#load-target").value, "existing|region-A");
  }
  if (type === "gravity") {
    document.querySelector("#load-type").value = "pressure";
    dispatch(document.querySelector("#load-type"), "change");
    assert.equal(document.querySelector("#load-target").value, "");
  }
  document.querySelector("#load-cancel-edit").click();
  assert.equal(document.querySelector("#load-target").value, "");
}
document.querySelector("#load-type").value = "gravity";
dispatch(document.querySelector("#load-type"), "change");
assert.equal(document.querySelector("#load-target").value, "whole-model");
document.querySelector("#load-type").value = "pressure";
dispatch(document.querySelector("#load-type"), "change");
assert.equal(document.querySelector("#load-target").value, "");

// Dirty unrelated editors survive authoritative revision refreshes.
materialName.value = "dirty-material";
dispatch(document.querySelector("#material-form"), "input");
actionButton(document.querySelector("#durable-region-list"), "confirm").click();
await waitFor(() => api.setups.get("setup-A").history.at(-1).intent.regions[0].status === "confirmed", "region decision failed");
assert.equal(materialName.value, "dirty-material");

bcType.value = "fixed_displacement";
dispatch(bcType, "change");
document.querySelector("#bc-target").value = "existing|region-A";
fixedX.checked = true;
document.querySelector("#mesh-size").value = "3";
dispatch(document.querySelector("#configuration-form"), "input");
dispatch(document.querySelector("#configuration-form"), "submit");
await tick();
assert.equal(document.querySelector("#bc-target").value, "existing|region-A");

document.querySelector("#load-type").value = "pressure";
dispatch(document.querySelector("#load-type"), "change");
document.querySelector("#load-target").value = "existing|region-A";
document.querySelector("#load-magnitude").value = "7";
materialName.value = "submitted-material";
document.querySelector("#material-e").value = "72";
document.querySelector("#material-nu").value = "0.31";
dispatch(document.querySelector("#material-form"), "input");
dispatch(document.querySelector("#material-form"), "submit");
await tick();
assert.equal(document.querySelector("#load-magnitude").value, "7");
assert.equal(materialName.value, "submitted-material");

// Proposal controls, stale conflicts, explicit reload, and listener de-duplication.
const assumptionActions = document.querySelector("#material-decision-actions");
if (!assumptionActions.hidden) {
  document.querySelector("#accept-material-proposal").click();
  await tick();
}
materialName.value = "preserved-on-conflict";
dispatch(document.querySelector("#material-form"), "input");
api.failConflict();
const beforeConflict = api.calls.length;
dispatch(document.querySelector("#material-form"), "submit");
await tick();
assert.equal(api.calls.length, beforeConflict + 1);
assert.equal(materialName.value, "preserved-on-conflict");
assert.equal(document.querySelector("#revision-conflict").hidden, false);
document.querySelector("#reload-current-revision").click();
await waitFor(() => loadDeferred.has("setup-A"), "reload did not request viewer");
loadDeferred.get("setup-A")();
await waitFor(() => materialName.value !== "preserved-on-conflict", "reload did not replace draft");

const callsBeforeSingleSubmit = api.calls.length;
materialName.value = "single-submit";
document.querySelector("#material-e").value = "73";
document.querySelector("#material-nu").value = "0.32";
dispatch(document.querySelector("#material-form"), "input");
dispatch(document.querySelector("#material-form"), "submit");
await tick();
assert.equal(api.calls.length, callsBeforeSingleSubmit + 1);

// Project replacement clears draft state and cannot leak its values.
materialName.value = "must-not-leak";
dispatch(document.querySelector("#material-form"), "input");
projectSelect.value = "other";
dispatch(projectSelect, "change");
await waitFor(() => document.querySelector("#setup-select").options.length === 2, "other project did not open");
setupSelect.value = "setup-C";
dispatch(setupSelect, "change");
await waitFor(() => loadDeferred.has("setup-C"), "setup C viewer load absent");
loadDeferred.get("setup-C")();
await waitFor(
  () => document.querySelector("#source-version").textContent.includes("C.step"),
  "setup C did not render",
);
assert.notEqual(materialName.value, "must-not-leak");

process.stdout.write(JSON.stringify({
  ok: true,
  mutationCalls: api.calls.length,
  finalSetup: document.querySelector("#setup-select").value,
  viewerVersion,
}));
