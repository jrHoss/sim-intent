// Executable R4b.3 browser regressions against real DOM state.
//
// Each assertion below reproduces one measured breakage: a browser schema
// version the server rejects, a forbidden entity_ids projection on CAD
// creation paths, two distinct clicks aliasing onto one region, and a v3 CAD
// setup that could not render at all.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import path from "node:path";
import { buildDocument, dispatch, tick, waitFor } from "./fake-dom.mjs";

const root = process.env.SIM_INTENT_ROOT;
const VERSION_ID = "11111111-1111-1111-1111-111111111111";
const SUCCESSOR_ID = "22222222-2222-2222-2222-222222222222";
const ARTIFACT = "b".repeat(64);
// CollisionGroupIdentity is 64 lowercase hex digits with no prefix, so the
// mock must not teach a shape the real contract would reject.
const COLLISION_GROUP = "c".repeat(64);

// The exact identities the mock backend assigns. The browser must never
// produce these values itself; it only ever submits an unresolved claim.
const IDENTITIES = { 1: `gfi1:${"1".repeat(64)}`, 7: `gfi1:${"7".repeat(64)}` };

function blankIntent(schemaVersion) {
  return {
    schema_version: schemaVersion,
    analysis: {
      type: "static_structural",
      units: { length: "mm", force: "N", stress: "MPa" },
      dimensionality: "3d_solid",
      solver_target: "calculix",
      coordinate_system: "global_cartesian",
    },
    materials: [],
    regions: [],
    bcs: [],
    loads: [],
    assumptions: [],
    mesh_settings: null,
    solver_settings: null,
    validation_status: "unvalidated",
  };
}

// Mirrors the backend: resolve unresolved CAD claims against one exact
// artifact, and project truthful evidence for every CAD region.
function resolveIntent(intent, { stale = false } = {}) {
  for (const region of intent.regions) {
    if (region.entity_type !== "cad_face") continue;
    const target = region.cad_face_target;
    if (!target || target.resolution !== "unresolved") continue;
    const tags = target.source_face_tags;
    region.cad_face_target = {
      resolution: "resolved",
      model_version_id: VERSION_ID,
      artifact_sha256: ARTIFACT,
      stable_identities: tags.map((tag) => IDENTITIES[tag]),
      source_face_tags: [...tags],
    };
  }
  return evidenceFor(intent, stale);
}

function evidenceFor(intent, stale) {
  const evidence = {};
  for (const region of intent.regions) {
    if (region.entity_type !== "cad_face") continue;
    const target = region.cad_face_target;
    const resolution = target ? target.resolution : "target_missing";
    const bindingValid = !stale;
    let blocking = null;
    if (!target) blocking = "cad_region_stable_target_required";
    else if (resolution !== "resolved") blocking = "cad_region_unresolved";
    else if (!bindingValid) blocking = "setup_source_superseded";
    evidence[region.id] = {
      resolution,
      stable_identity_authoritative: resolution === "resolved",
      viewer_binding_valid: bindingValid,
      confirmable: blocking === null && region.status === "proposed",
      blocking_code: blocking,
      model_version_id: target?.model_version_id ?? null,
      artifact_sha256: target?.artifact_sha256 ?? null,
      stable_identities: target?.stable_identities ?? [],
      collision_group_ids: target?.collision_group_ids ?? [],
      source_face_tags: target?.source_face_tags ?? [],
      viewer_node_names: (target?.source_face_tags ?? []).map((tag) => `face_${tag}`),
    };
  }
  return evidence;
}

function revision(setupId, number, setupIntent, requestId, stale, resolve = true) {
  const intent = structuredClone(setupIntent);
  const evidence = resolve
    ? resolveIntent(intent, { stale })
    : evidenceFor(intent, stale);
  const selected = {};
  const highlights = {};
  for (const region of intent.regions) {
    const tags = region.entity_type === "cad_face"
      ? region.cad_face_target?.source_face_tags ?? []
      : region.entity_ids ?? [];
    if (region.status !== "rejected") selected[region.id] = [...tags];
    if (region.status === "proposed" || region.status === "confirmed") {
      highlights[region.id] = { entity_ids: [...tags], style: region.status };
    }
  }
  return {
    schema_version: 1,
    id: `${setupId}-revision-${number}`,
    setup_id: setupId,
    revision: number,
    parent_revision_id: number > 1 ? `${setupId}-revision-${number - 1}` : null,
    simulation_intent_schema_version: 3,
    intent_sha256: `${setupId}-${number}`,
    stored_simulation_intent_schema_version: 3,
    stored_intent_sha256: `${setupId}-${number}`,
    mutation_type: number === 1 ? "create" : "update",
    request_id: requestId,
    created_at: `2026-02-${String(number).padStart(2, "0")}T00:00:00Z`,
    intent,
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
    selected_entities: selected,
    highlight_state: highlights,
    cad_selection_evidence: evidence,
    engineering_ready: false,
    artifact_capability: { supported: false, blocking_issue_codes: [] },
    export_eligible: false,
  };
}

function makeApi() {
  const setup = {
    id: "setup-step",
    project_id: "project",
    model_id: "model-step",
    model_version_id: VERSION_ID,
    current_revision: 1,
    created_at: "2026-02-01T00:00:00Z",
    updated_at: "2026-02-01T00:00:00Z",
    model_version_is_current: true,
    is_stale: false,
    stale_reason: null,
    stale_at: null,
  };
  const state = {
    setup,
    history: [revision(setup.id, 1, blankIntent(3), "request-1", false)],
    calls: [],
  };
  const current = () => state.history.at(-1);
  const mutate = (body, transform = null) => {
    state.calls.push({ body: structuredClone(body) });
    const next = body.intent
      ? structuredClone(body.intent)
      : structuredClone(current().intent);
    transform?.(next);
    const created = revision(
      setup.id, state.history.length + 1, next, body.request_id, setup.is_stale,
    );
    state.history.push(created);
    setup.current_revision = created.revision;
    return structuredClone(created);
  };
  return {
    state,
    supersede() {
      setup.is_stale = true;
      setup.stale_reason = "source_replaced";
      state.history.push(revision(
        setup.id,
        state.history.length + 1,
        current().intent,
        "superseded",
        true,
      ));
      setup.current_revision = current().revision;
    },
    listProjects: async () => [{ id: "project", name: "Project" }],
    readProject: async () => ({ id: "project", name: "Project" }),
    listSetups: async () => [structuredClone(setup)],
    readSetup: async () => ({
      setup: structuredClone(setup),
      current: structuredClone(current()),
    }),
    readModelVersion: async (id) => ({
      id,
      model_id: "model-step",
      source_name: "bracket.step",
      model_kind: "step",
      version: id === SUCCESSOR_ID ? 2 : 1,
    }),
    listModelVersions: async () => [{
      id: VERSION_ID,
      model_id: "model-step",
      source_name: "bracket.step",
      model_kind: "step",
      version: 1,
      is_current: true,
    }],
    readInventory: async () => ({ regions: [] }),
    readGeometryIdentity: async () => ({
      artifact_sha256: ARTIFACT,
      model_version_id: VERSION_ID,
      faces: [
        { source_ref: 1, stable_identity: IDENTITIES[1], collision_group_id: null, ambiguous: false },
        { source_ref: 7, stable_identity: IDENTITIES[7], collision_group_id: null, ambiguous: false },
        { source_ref: 9, stable_identity: null, collision_group_id: COLLISION_GROUP, ambiguous: true },
      ],
      collision_groups: [{ collision_group_id: COLLISION_GROUP, source_refs: [9] }],
    }),
    listRevisions: async () => structuredClone(state.history),
    createRevision: async (_setupId, body) => mutate(body),
    decideRegion: async (_setupId, regionId, action, body) => mutate(body, (next) => {
      next.regions.find((region) => region.id === regionId).status =
        action === "confirm" ? "confirmed" : "rejected";
    }),
    decideAssumption: async (_setupId, assumptionId, action, body) => mutate(body, (next) => {
      next.assumptions.find((item) => item.id === assumptionId).status =
        action === "accept" ? "accepted" : "rejected";
    }),
    interpretVersion: async () => ({ grounding: { results: [] }, intent: blankIntent(3) }),
    createProject: async (name) => ({ id: "created", name }),
  };
}

globalThis.document = buildDocument();
globalThis.localStorage = {
  values: new Map(),
  getItem(key) { return this.values.get(key) ?? null; },
  setItem(key, value) { this.values.set(key, value); },
};

const engineering = await import(
  pathToFileURL(path.join(root, "app/static/engineering.js"))
);

// --------------------------------------------------------------------------
// F1 — the browser's schema version is bound to the server's single
// definition of it. This gate runs in the Node-only frontend job, where the
// Python constant cannot be imported, so it is read from its source of truth.
// --------------------------------------------------------------------------
const schemaVersionSource = readFileSync(
  path.join(root, "ir/schema_version.py"), "utf-8",
);
const declared = schemaVersionSource.match(
  /^SIMULATION_INTENT_SCHEMA_VERSION[^=]*=\s*(\d+)/m,
);
assert.ok(declared, "ir/schema_version.py no longer declares the constant");
assert.equal(
  engineering.SCHEMA_VERSION,
  Number(declared[1]),
  "browser SCHEMA_VERSION drifted from the server contract",
);

// The generated contract must carry the evidence projection to every client.
const openapi = JSON.parse(
  readFileSync(path.join(root, "schema/openapi.json"), "utf-8"),
);
assert.ok(openapi.components.schemas.CadSelectionEvidence);
assert.ok(
  openapi.components.schemas.SetupRevisionResponse.properties
    .cad_selection_evidence,
);

// Every established CAD and geometry-identity code renders a specific message.
const fallback = engineering.cadProblemMessage("not_a_real_code");
const REQUIRED_CODES = [
  "cad_region_entity_ids_forbidden",
  "cad_region_not_applicable",
  "cad_region_stable_target_required",
  "cad_region_unresolved",
  "cad_region_model_version_mismatch",
  "cad_region_artifact_mismatch",
  "cad_region_artifact_missing",
  "cad_region_artifact_integrity_failed",
  "cad_region_artifact_binding_mismatch",
  "cad_region_artifact_invalid",
  "cad_region_artifact_version_unsupported",
  "cad_region_evidence_unknown",
  "cad_region_identity_unknown",
  "cad_region_identity_evidence_inconsistent",
  "cad_region_collision_group_unknown",
  "cad_region_collision_evidence_inconsistent",
  "cad_region_legacy_client_forbidden",
  "setup_source_superseded",
  "geometry_identity_missing",
  "geometry_identity_integrity_failed",
  "geometry_identity_binding_mismatch",
  "geometry_identity_version_unsupported",
  "geometry_identity_schema_invalid",
  "geometry_identity_not_applicable",
];
for (const code of REQUIRED_CODES) {
  const message = engineering.cadProblemMessage(code);
  assert.notEqual(message, fallback, `${code} has no specific message`);
  assert.ok(message.length > 20, `${code} message is not human-readable`);
}

const api = makeApi();
const highlights = [];
let idCounter = 0;
let viewerSourceId = "geometry-1";
const workspace = engineering.createEngineeringWorkspace({
  api,
  makeRequestId: (prefix) => `${prefix}-${++idCounter}`,
  onLoadVersion: async () => {},
  onHighlight: (command) => highlights.push(structuredClone(command)),
  onStatus: () => {},
});

// --------------------------------------------------------------------------
// F3 — a v3 CAD setup renders, and it renders truthful authority.
// --------------------------------------------------------------------------
await workspace.initialize();
const projectSelect = document.querySelector("#project-select");
projectSelect.value = "project";
dispatch(projectSelect, "change");
await waitFor(
  () => document.querySelector("#setup-select").options.length === 2,
  "project did not open",
);
const setupSelect = document.querySelector("#setup-select");
setupSelect.value = "setup-step";
dispatch(setupSelect, "change");
await waitFor(
  () => workspace.currentSetup()?.id === "setup-step",
  "STEP setup did not open",
);

function select(entityId) {
  const token = workspace.captureViewerSelectionContext(entityId, viewerSourceId);
  assert.ok(token, "viewer selection context was refused");
  assert.equal(workspace.setViewerSelection(entityId, token, viewerSourceId), true);
}

// The workspace applies a mutation response after several awaits, so waiting
// on the mock's history alone would resume before the editor has re-rendered.
async function settleAt(revisionNumber, what) {
  await waitFor(
    () => document.querySelector("#current-revision").textContent === `r${revisionNumber}`,
    `${what} did not settle at r${revisionNumber}: `
      + document.querySelector("#workspace-status").textContent,
  );
}

async function addFixedBC(targetValue) {
  const bcType = document.querySelector("#bc-type");
  bcType.value = "fixed_displacement";
  dispatch(bcType, "change");
  document.querySelector("#bc-target").value = targetValue;
  document.querySelector('input[name="fixed_axis"][value="x"]').checked = true;
  dispatch(document.querySelector("#bc-form"), "input");
  const before = api.state.history.length;
  dispatch(document.querySelector("#bc-form"), "submit");
  await settleAt(before + 1, `BC on ${targetValue}`);
}

// F2 — a STEP click submits an unresolved cad_face_target and no entity_ids.
select(1);
await waitFor(
  () => document.querySelector("#bc-target").options.some((o) => o.value === "viewer"),
  "viewer target option is missing",
);
await addFixedBC("viewer");
const firstBody = api.state.calls.at(-1).body;
const firstRegion = firstBody.intent.regions.at(-1);
assert.equal(firstRegion.entity_type, "cad_face");
assert.equal(
  Object.hasOwn(firstRegion, "entity_ids"),
  false,
  "a CAD region carried the forbidden entity_ids key",
);
assert.deepEqual(firstRegion.cad_face_target, {
  resolution: "unresolved",
  model_version_id: VERSION_ID,
  source_face_tags: [1],
});
assert.equal(
  JSON.stringify(firstBody).includes("gfi1:"),
  false,
  "the browser minted a stable identity",
);

// F3 — a second, different click must not alias onto the first region.
select(7);
await addFixedBC("viewer");
const secondBody = api.state.calls.at(-1).body;
const cadRegions = secondBody.intent.regions.filter((r) => r.entity_type === "cad_face");
assert.equal(cadRegions.length, 2, "distinct clicked faces aliased onto one region");
assert.notEqual(cadRegions[0].id, cadRegions[1].id);
assert.deepEqual(
  cadRegions.map((region) => region.cad_face_target.source_face_tags),
  [[1], [7]],
);
assert.equal(
  secondBody.intent.bcs.at(-1).region_ref,
  cadRegions[1].id,
  "the second BC was attached to the wrong boundary",
);

// Re-clicking face 1 reuses its existing region rather than duplicating it.
select(1);
await addFixedBC("viewer");
const thirdBody = api.state.calls.at(-1).body;
assert.equal(
  thirdBody.intent.regions.filter((r) => r.entity_type === "cad_face").length,
  2,
);
assert.equal(thirdBody.intent.bcs.at(-1).region_ref, cadRegions[0].id);

// The region cards render backend authority, never a raw exception.
const cardText = document.querySelector("#durable-region-list").textContent;
assert.match(cardText, /Resolution/);
assert.match(cardText, /backend-authoritative/);
assert.match(cardText, /Source face tags \(non-authoritative\)/);
assert.match(cardText, /Stable identity/);
assert.match(cardText, /gfi1:/);
assert.equal(
  /undefined/.test(cardText),
  false,
  "a region card rendered an undefined value",
);

// Highlights address source face tags, not entity IDs.
assert.ok(
  highlights.some((command) => Array.isArray(command.entity_ids)
    && command.entity_ids.includes(7)),
  "no highlight addressed the second clicked face",
);

// Live selection evidence quotes backend identity, labelled non-authoritative.
await waitFor(
  () => document.querySelector("#bc-selection-evidence").textContent.includes("gfi1:"),
  "live selection evidence did not render backend identity",
);
for (const id of ["bc-selection-evidence", "load-selection-evidence"]) {
  const text = document.querySelector(`#${id}`).textContent;
  assert.match(text, /local evidence only/);
}
select(9);
await waitFor(
  () => document.querySelector("#bc-selection-evidence").textContent.includes("Ambiguous"),
  "an ambiguous face did not render as ambiguous",
);
assert.match(
  document.querySelector("#bc-selection-evidence").textContent,
  /cannot be confirmed/,
);

// --------------------------------------------------------------------------
// A confirmed CAD target round-trips byte-identically through a form edit.
// --------------------------------------------------------------------------
const confirmedRegionId = cadRegions[0].id;
const confirmBody = {
  expected_revision: api.state.history.at(-1).revision,
  request_id: "confirm-1",
};
api.state.history.push(revision(
  "setup-step",
  api.state.history.length + 1,
  (() => {
    const next = structuredClone(api.state.history.at(-1).intent);
    next.regions.find((region) => region.id === confirmedRegionId).status = "confirmed";
    return next;
  })(),
  confirmBody.request_id,
  false,
));
api.state.setup.current_revision = api.state.history.at(-1).revision;
await workspace.refreshCurrent();
const confirmedTarget = structuredClone(
  api.state.history.at(-1).intent.regions
    .find((region) => region.id === confirmedRegionId).cad_face_target,
);
assert.equal(confirmedTarget.resolution, "resolved");

document.querySelector("#material-name").value = "steel";
document.querySelector("#material-e").value = "210000";
document.querySelector("#material-e-unit").value = "MPa";
document.querySelector("#material-nu").value = "0.3";
dispatch(document.querySelector("#material-form"), "input");
const beforeEdit = api.state.history.length;
dispatch(document.querySelector("#material-form"), "submit");
await settleAt(beforeEdit + 1, "material edit");
const editedRegion = api.state.calls.at(-1).body.intent.regions
  .find((region) => region.id === confirmedRegionId);
assert.deepEqual(
  editedRegion.cad_face_target,
  confirmedTarget,
  "a confirmed CAD target was rebuilt instead of round-tripped",
);
assert.equal(Object.hasOwn(editedRegion, "entity_ids"), false);

// Replacing the target of an existing BC copies the durable target verbatim.
const bcList = document.querySelector("#bc-list");
const editButton = (() => {
  const visit = (node) => {
    if (node.tagName === "BUTTON" && node.dataset.action === "edit"
      && node.dataset.index === "0") return node;
    for (const child of node.children) {
      const found = visit(child);
      if (found) return found;
    }
    return null;
  };
  return visit(bcList);
})();
editButton.click();
const beforeReplace = api.state.history.length;
document.querySelector("#bc-target").value = `existing|${confirmedRegionId}`;
document.querySelector('input[name="fixed_axis"][value="y"]').checked = true;
dispatch(document.querySelector("#bc-form"), "input");
dispatch(document.querySelector("#bc-form"), "submit");
await settleAt(beforeReplace + 1, "BC target replacement");
const replacedRegion = api.state.calls.at(-1).body.intent.regions
  .find((region) => region.id === confirmedRegionId);
assert.deepEqual(replacedRegion.cad_face_target, confirmedTarget);
assert.equal(Object.hasOwn(replacedRegion, "entity_ids"), false);

// --------------------------------------------------------------------------
// Stale source replacement never renders as an unqualified confirmed green.
// --------------------------------------------------------------------------
api.supersede();
highlights.length = 0;
await workspace.refreshCurrent();
assert.equal(document.querySelector("#stale-source-banner").hidden, false);
assert.equal(
  highlights.some((command) => command.style === "confirmed"
    || command.style === "fixed_boundary_condition"),
  false,
  "a superseded setup drew a confirmed CAD highlight",
);
const staleText = document.querySelector("#durable-region-list").textContent;
assert.match(staleText, /invalid for the displayed source/);
assert.match(staleText, /never rebound/);
assert.equal(/undefined/.test(staleText), false);

// --------------------------------------------------------------------------
// The per-region viewer-binding guard is load-bearing on its own. The setup
// is live (is_stale === false), so the setup-wide early return cannot fire:
// only drawable()'s viewer_binding_valid check keeps a confirmed region whose
// backend binding is invalid off the viewer. It is defence in depth against
// an inconsistent or future additive projection state, and this case fails
// the moment that check is removed.
// --------------------------------------------------------------------------
api.state.setup.is_stale = false;
api.state.setup.stale_reason = null;
const guardRegion = (id, tag) => ({
  id,
  entity_type: "cad_face",
  cad_face_target: {
    resolution: "resolved",
    model_version_id: VERSION_ID,
    artifact_sha256: ARTIFACT,
    stable_identities: [IDENTITIES[tag]],
    source_face_tags: [tag],
  },
  selection_method: "user_click",
  confidence: 1,
  source_instruction: `Use selected viewer face_${tag}.`,
  status: "confirmed",
});
const guardIntent = blankIntent(3);
guardIntent.regions = [guardRegion("guard_unbound", 1), guardRegion("guard_bound", 7)];
guardIntent.bcs = [
  {
    id: "guard_bc_unbound",
    type: "fixed_displacement",
    components: ["x"],
    region_ref: "guard_unbound",
  },
  {
    id: "guard_bc_bound",
    type: "fixed_displacement",
    components: ["x"],
    region_ref: "guard_bound",
  },
];
const guardRevision = revision(
  "setup-step", api.state.history.length + 1, guardIntent, "guard", false, false,
);
// Exactly one region reports an invalid binding; its neighbour stays valid.
guardRevision.cad_selection_evidence.guard_unbound.viewer_binding_valid = false;
api.state.history.push(guardRevision);
api.state.setup.current_revision = guardRevision.revision;
highlights.length = 0;
await workspace.refreshCurrent();
assert.equal(
  document.querySelector("#stale-source-banner").hidden,
  true,
  "the guard case must run on a live, non-stale setup",
);
const guardDrawn = highlights.filter((command) => Array.isArray(command.entity_ids));
assert.equal(
  guardDrawn.some((command) => command.entity_ids.includes(1)),
  false,
  "a confirmed CAD region with an invalid viewer binding was highlighted",
);
assert.ok(
  guardDrawn.some((command) => command.entity_ids.includes(7)
    && command.style === "confirmed"),
  "the still-bound control region drew no confirmed highlight",
);
assert.ok(
  guardDrawn.some((command) => command.entity_ids.includes(7)
    && command.style === "fixed_boundary_condition"),
  "the still-bound control region drew no boundary-condition highlight",
);

// --------------------------------------------------------------------------
// Every remaining CAD resolution state renders without throwing.
// --------------------------------------------------------------------------
const states = [
  { resolution: "unresolved", model_version_id: VERSION_ID, source_face_tags: [1] },
  {
    resolution: "ambiguous",
    model_version_id: VERSION_ID,
    artifact_sha256: ARTIFACT,
    collision_group_ids: [COLLISION_GROUP],
    source_face_tags: [9],
  },
  { resolution: "legacy_local_only", legacy_status: "proposed", source_face_tags: [3] },
  {
    resolution: "invalid_legacy_evidence",
    legacy_status: "proposed",
    legacy_reason: "invalid_numeric_tags",
    source_face_tags: [0, -2],
  },
  null,
];
for (const [index, target] of states.entries()) {
  const intent = blankIntent(3);
  const region = {
    id: `state-region-${index}`,
    entity_type: "cad_face",
    selection_method: "user_click",
    confidence: 1,
    source_instruction: "state coverage",
    status: "proposed",
  };
  if (target) region.cad_face_target = structuredClone(target);
  intent.regions = [region];
  api.state.setup.is_stale = false;
  api.state.setup.stale_reason = null;
  // Pushed without backend resolution so each blocked state is rendered as
  // the client would actually receive it.
  api.state.history.push(revision(
    "setup-step", api.state.history.length + 1, intent, `state-${index}`, false, false,
  ));
  api.state.setup.current_revision = api.state.history.at(-1).revision;
  await workspace.refreshCurrent();
  const text = document.querySelector("#durable-region-list").textContent;
  assert.match(text, /Resolution/, `state ${index} did not render`);
  assert.equal(/undefined/.test(text), false, `state ${index} rendered undefined`);
  assert.match(text, /not authoritative/, `state ${index} claimed authority`);
  assert.match(text, /Blocked/, `state ${index} rendered no blocking reason`);
}

// --------------------------------------------------------------------------
// Native INP behaviour is unchanged: entity_ids remain the membership field.
// --------------------------------------------------------------------------
api.readModelVersion = async (id) => ({
  id,
  model_id: "model-step",
  source_name: "model.inp",
  model_kind: "inp",
  version: 1,
});
api.readInventory = async () => ({
  regions: [{ kind: "node_set", name: "FIXED" }],
});
const nativeIntent = blankIntent(3);
api.state.history.push(revision(
  "setup-step", api.state.history.length + 1, nativeIntent, "native", false,
));
api.state.setup.current_revision = api.state.history.at(-1).revision;
await workspace.refreshCurrent();
viewerSourceId = "geometry-inp";
select(4);
await waitFor(
  () => document.querySelector("#bc-target").options.some((o) => o.value === "viewer"),
  "native viewer target option is missing",
);
await addFixedBC("viewer");
const nativeRegion = api.state.calls.at(-1).body.intent.regions.at(-1);
assert.equal(nativeRegion.entity_type, "mesh_face");
assert.deepEqual(nativeRegion.entity_ids, [4]);
assert.equal(Object.hasOwn(nativeRegion, "cad_face_target"), false);

await addFixedBC("native|node_set|FIXED");
const nativeSetRegion = api.state.calls.at(-1).body.intent.regions.at(-1);
assert.equal(nativeSetRegion.entity_type, "node_set");
assert.deepEqual(nativeSetRegion.entity_ids, ["FIXED"]);

await tick();
process.stdout.write(JSON.stringify({
  ok: true,
  schemaVersion: engineering.SCHEMA_VERSION,
  cadRegions: cadRegions.length,
  mutations: api.state.calls.length,
}));
