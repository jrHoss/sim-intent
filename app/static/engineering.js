import { ApiError, durableApi, requestId } from "/static/durable-api.js";

const SCHEMA_VERSION = 2;
const STORAGE_KEY = "sim-intent.durable-workspace.v1";
const AXES = ["x", "y", "z"];

const CAPABILITY_MESSAGES = {
  "artifact.target_not_selected": "Select the supported CalculiX target.",
  "artifact.step_meshing_required": "The STEP source must be meshed before a CalculiX artifact can be generated.",
  "artifact.mapping_not_verified": "A verified source-to-mesh boundary mapping is not available yet.",
  "artifact.native_region_missing": "A referenced native INP set or boundary region is missing.",
  "artifact.adapter_condition_unsupported": "The selected adapter cannot represent one or more conditions.",
  "artifact.calculix.surface_traction_unsupported": "The current CalculiX adapter cannot emit surface traction.",
  "artifact.calculix.pressure_mapping_required": "Pressure requires a verified element-face mapping.",
};

const FORCE_FACTORS = { N: 1, kN: 1e3, MN: 1e6 };
const STRESS_FACTORS = { Pa: 1e-6, kPa: 1e-3, MPa: 1, GPa: 1e3 };
const LENGTH_FACTORS = { mm: 1, m: 1e3 };
const DENSITY_FACTORS = {
  "kg/m^3": 1e-12,
  "kg/m3": 1e-12,
  "t/mm^3": 1,
  "tonne/mm^3": 1,
};
const ACCELERATION_FACTORS = { "mm/s^2": 1, "m/s^2": 1e3 };

const byId = (id) => document.querySelector(`#${id}`);

function clone(value) {
  if (typeof globalThis.structuredClone === "function") {
    return globalThis.structuredClone(value);
  }
  if (value === null || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map(clone);
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, clone(item)]));
}

function formatNumber(value) {
  return typeof value === "number" && Object.is(value, -0) ? "-0" : String(value);
}

function deepFreeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const item of Object.values(value)) deepFreeze(item);
  }
  return value;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function safeNumber(value, label) {
  const number = Number(value);
  if (!Number.isFinite(number)) throw new Error(`${label} must be a finite number.`);
  return number;
}

function requiredNumberInput(input, label) {
  const text = input.value.trim();
  if (!text) {
    input.setAttribute("aria-invalid", "true");
    throw new Error(`${label} is required when its axis is selected.`);
  }
  input.removeAttribute("aria-invalid");
  return safeNumber(text, label);
}

function normalizedDirection(values) {
  const vector = values.map((value, index) => safeNumber(value, `${"XYZ"[index]} direction`));
  const norm = Math.hypot(...vector);
  if (!(norm > 0)) throw new Error("Direction must be nonzero.");
  return vector.map((value) => value / norm);
}

function sameEntities(first, second) {
  return first.entity_type === second.entity_type
    && JSON.stringify(first.entity_ids) === JSON.stringify(second.entity_ids);
}

function baseIntent() {
  return {
    schema_version: SCHEMA_VERSION,
    analysis: {
      type: "static_structural",
      units: { length: "mm", force: "N", stress: "MPa" },
      dimensionality: null,
      solver_target: null,
      coordinate_system: null,
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

function persistedSelection() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

export async function runFaceSelectionOperation({
  entityId,
  contextToken,
  workspace,
  getViewerSourceId,
  requestSelection,
  responseError,
  onAccepted,
  onError,
}) {
  if (!contextToken) return false;
  const isCurrent = () => workspace.isViewerSelectionContextCurrent(
    contextToken,
    entityId,
    getViewerSourceId(),
  );
  try {
    const response = await requestSelection();
    if (!isCurrent()) return false;
    if (!response.ok) {
      const message = await responseError(response);
      if (!isCurrent()) return false;
      throw new Error(message);
    }
    const selection = await response.json();
    if (!isCurrent()) return false;
    if (!workspace.setViewerSelection(
      entityId,
      contextToken,
      getViewerSourceId(),
    )) return false;
    onAccepted(selection);
    return true;
  } catch (error) {
    if (isCurrent()) onError(error);
    return false;
  }
}

export function createEngineeringWorkspace({
  onLoadVersion,
  onHighlight,
  onStatus,
  api = durableApi,
  makeRequestId = requestId,
}) {
  const state = {
    projects: [],
    project: null,
    setups: [],
    setup: null,
    current: null,
    history: [],
    sourceVersion: null,
    latestVersion: null,
    inventory: null,
    viewerSelection: null,
    pendingReplay: null,
    proposal: null,
    operationEpoch: 0,
    requestedContext: null,
    dirtyEditors: new Set(),
    eventsBound: false,
    contextOpening: false,
  };

  const projectSelect = byId("project-select");
  const setupSelect = byId("setup-select");
  const workspaceStatus = byId("workspace-status");
  const conflictBanner = byId("revision-conflict");
  const retryBanner = byId("retry-mutation");
  const staleBanner = byId("stale-source-banner");

  function setWorkspaceStatus(message, error = false) {
    workspaceStatus.textContent = message;
    workspaceStatus.dataset.error = String(error);
    onStatus(message, error);
  }

  function storeSelection() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      project_id: state.project?.id || null,
      setup_id: state.setup?.id || null,
      model_id: state.latestVersion?.model_id || null,
      model_version_id: state.latestVersion?.id || null,
    }));
  }

  function errorMessage(error) {
    if (error instanceof ApiError) {
      const nestedCodes = (error.problem.errors || [])
        .map((item) => item.code)
        .filter(Boolean);
      const suffix = nestedCodes.length ? ` (${nestedCodes.join(", ")})` : "";
      return `${error.code}: ${error.message}${suffix}`;
    }
    return error.message || "The request could not be completed.";
  }

  function guardEditable() {
    if (!state.current || !state.setup) throw new Error("Open a durable setup first.");
    if (state.contextOpening) throw new Error("Wait for the selected durable context to finish opening.");
    if (state.setup.is_stale) throw new Error("The setup source is stale; mutations are blocked.");
  }

  function activeContext() {
    return {
      projectId: state.project?.id || null,
      setupId: state.setup?.id || null,
      modelId: state.sourceVersion?.model_id || state.latestVersion?.model_id || null,
      modelVersionId: state.sourceVersion?.id || state.latestVersion?.id || null,
    };
  }

  function replayContext() {
    return Object.freeze({
      ...activeContext(),
      expectedRevision: state.current?.revision ?? null,
      operationEpoch: state.operationEpoch,
    });
  }

  function sameContext(first, second) {
    return Boolean(first && second)
      && first.projectId === second.projectId
      && first.setupId === second.setupId
      && first.modelId === second.modelId
      && first.modelVersionId === second.modelVersionId;
  }

  function captureViewerSelectionContext(entityId, viewerSourceId) {
    const context = activeContext();
    if (!context.projectId || !context.modelVersionId || !viewerSourceId) return null;
    return Object.freeze({
      ...context,
      operationEpoch: state.operationEpoch,
      entityId,
      viewerSourceId,
    });
  }

  function isViewerSelectionContextCurrent(token, entityId, viewerSourceId) {
    return Boolean(token)
      && !state.contextOpening
      && sameContext(token, activeContext())
      && token.operationEpoch === state.operationEpoch
      && token.entityId === entityId
      && token.viewerSourceId === viewerSourceId;
  }

  function clearPendingReplay() {
    state.pendingReplay = null;
    retryBanner.hidden = true;
  }

  function clearDrafts() {
    state.dirtyEditors.clear();
    clearBCForm();
    clearLoadForm();
  }

  function beginContextChange({
    projectId = null,
    setupId = null,
    modelId = null,
    modelVersionId = null,
  } = {}) {
    const operation = {
      epoch: ++state.operationEpoch,
      projectId,
      setupId,
      modelId,
      modelVersionId,
    };
    state.requestedContext = { ...operation };
    state.contextOpening = true;
    state.viewerSelection = null;
    clearPendingReplay();
    clearDrafts();
    updateSelectionControls();
    conflictBanner.hidden = true;
    if (state.current) setEditorDisabled(true);
    return operation;
  }

  function finishContextChange(operation) {
    if (!isCurrentOperation(operation)) return false;
    state.contextOpening = false;
    return true;
  }

  function retargetOperation(operation, values) {
    if (!isCurrentOperation(operation)) return false;
    Object.assign(operation, values);
    Object.assign(state.requestedContext, values);
    return isCurrentOperation(operation);
  }

  function isCurrentOperation(operation) {
    const requested = state.requestedContext;
    return Boolean(requested)
      && operation.epoch === state.operationEpoch
      && operation.epoch === requested.epoch
      && operation.projectId === requested.projectId
      && operation.setupId === requested.setupId
      && operation.modelId === requested.modelId
      && operation.modelVersionId === requested.modelVersionId;
  }

  function versionLoadContext(operation) {
    return Object.freeze({
      epoch: operation.epoch,
      projectId: operation.projectId,
      setupId: operation.setupId,
      modelId: operation.modelId,
      modelVersionId: operation.modelVersionId,
      isCurrent: () => isCurrentOperation(operation),
    });
  }

  function updateSetupSelect() {
    const selected = state.setup?.id || "";
    setupSelect.replaceChildren();
    const placeholder = element("option", "", state.setups.length ? "Choose a setup" : "No durable setups");
    placeholder.value = "";
    setupSelect.append(placeholder);
    for (const setup of state.setups) {
      const option = element(
        "option",
        "",
        `Revision ${setup.current_revision}${setup.is_stale ? " · stale" : ""} · ${setup.id.slice(0, 8)}`,
      );
      option.value = setup.id;
      setupSelect.append(option);
    }
    setupSelect.disabled = !state.project;
    setupSelect.value = selected;
  }

  function updateProjectSelect() {
    projectSelect.replaceChildren();
    const placeholder = element("option", "", state.projects.length ? "Choose a project" : "No projects yet");
    placeholder.value = "";
    projectSelect.append(placeholder);
    for (const project of state.projects) {
      const option = element("option", "", project.name);
      option.value = project.id;
      projectSelect.append(option);
    }
    projectSelect.value = state.project?.id || "";
  }

  async function restoreLooseVersion(saved, operation, project) {
    if (!saved.model_version_id || !project || !isCurrentOperation(operation)) return false;
    try {
      const version = await api.readModelVersion(saved.model_version_id);
      if (!isCurrentOperation(operation) || version.model_id !== saved.model_id) return false;
      if (!retargetOperation(operation, {
        modelId: version.model_id,
        modelVersionId: version.id,
      })) return false;
      const inventory = await api.readInventory(version.id);
      if (!isCurrentOperation(operation)) return false;
      await onLoadVersion(version, versionLoadContext(operation));
      if (!isCurrentOperation(operation)) return false;
      state.project = project;
      state.latestVersion = version;
      state.sourceVersion = version;
      state.inventory = inventory;
      finishContextChange(operation);
      renderAll();
      return true;
    } catch {
      return false;
    }
  }

  async function refreshProjects(preferredProjectId = null) {
    const saved = persistedSelection();
    const projectId = preferredProjectId || state.project?.id || saved.project_id || null;
    const operation = beginContextChange({ projectId });
    const projects = await api.listProjects();
    if (!isCurrentOperation(operation)) return;
    state.projects = projects;
    const project = projects.find((item) => item.id === projectId) || null;
    if (project) {
      await openProject(project.id, saved.setup_id, saved);
      return;
    }
    state.project = null;
    state.setups = [];
    state.setup = null;
    state.current = null;
    state.history = [];
    state.sourceVersion = null;
    state.latestVersion = null;
    state.inventory = null;
    state.viewerSelection = null;
    finishContextChange(operation);
    updateProjectSelect();
    updateSetupSelect();
    renderAll();
  }

  async function openProject(projectId, preferredSetupId = null, saved = persistedSelection()) {
    const operation = beginContextChange({
      projectId,
      setupId: preferredSetupId,
    });
    const [project, setups] = await Promise.all([
      state.projects.find((item) => item.id === projectId) || api.readProject(projectId),
      api.listSetups(projectId),
    ]);
    if (!isCurrentOperation(operation)) return;
    const setup = setups.find((item) => item.id === preferredSetupId);
    if (setup) {
      await openSetup(setup.id, operation, { project, setups });
      return;
    }
    if (saved.project_id === projectId && saved.model_version_id) {
      const restored = await restoreLooseVersion(saved, operation, project);
      if (restored || !isCurrentOperation(operation)) return;
    }
    state.project = project;
    state.setups = setups;
    state.setup = null;
    state.current = null;
    state.history = [];
    state.sourceVersion = null;
    state.latestVersion = null;
    state.inventory = null;
    state.viewerSelection = null;
    finishContextChange(operation);
    updateProjectSelect();
    updateSetupSelect();
    renderAll();
    storeSelection();
  }

  async function openSetup(setupId, existingOperation = null, projectData = null) {
    const operation = existingOperation || beginContextChange({
      projectId: state.project?.id || null,
      setupId,
    });
    if (operation.setupId !== setupId) return;
    setWorkspaceStatus("Opening durable setup…");
    const view = await api.readSetup(setupId);
    if (!isCurrentOperation(operation)
      || view.setup.id !== setupId
      || (operation.projectId && view.setup.project_id !== operation.projectId)) return;
    if (!retargetOperation(operation, {
      projectId: view.setup.project_id,
      setupId: view.setup.id,
      modelId: view.setup.model_id,
      modelVersionId: view.setup.model_version_id,
    })) return;
    const [sourceVersion, versions, inventory, history] = await Promise.all([
      api.readModelVersion(view.setup.model_version_id),
      api.listModelVersions(view.setup.model_id),
      api.readInventory(view.setup.model_version_id),
      api.listRevisions(setupId),
    ]);
    if (!isCurrentOperation(operation)
      || sourceVersion.id !== operation.modelVersionId
      || sourceVersion.model_id !== operation.modelId
      || view.setup.project_id !== operation.projectId
      || view.setup.id !== operation.setupId) return;
    await onLoadVersion(sourceVersion, versionLoadContext(operation));
    if (!isCurrentOperation(operation)) return;
    if (projectData) {
      state.project = projectData.project;
      state.setups = projectData.setups;
    }
    state.setup = view.setup;
    state.current = view.current;
    state.sourceVersion = sourceVersion;
    state.latestVersion = versions.find((item) => item.is_current) || sourceVersion;
    state.inventory = inventory;
    state.history = history;
    state.viewerSelection = null;
    finishContextChange(operation);
    updateProjectSelect();
    updateSetupSelect();
    renderAll();
    storeSelection();
    setWorkspaceStatus(`Durable setup reopened at revision ${state.current.revision}.`);
  }

  async function refreshCurrent() {
    if (!state.setup) {
      await refreshProjects();
      return;
    }
    await openSetup(state.setup.id);
  }

  async function createProject(name) {
    beginContextChange();
    const project = await api.createProject(name);
    await refreshProjects(project.id);
    setWorkspaceStatus(`Project “${project.name}” created.`);
    return project;
  }

  async function uploadModel(file) {
    if (!state.project) throw new Error("Create or select a project before uploading a model.");
    const project = state.project;
    const operation = beginContextChange({ projectId: project.id });
    setWorkspaceStatus(`Uploading ${file.name} to ${state.project.name}…`);
    const uploaded = await api.uploadModel(project.id, file);
    if (!retargetOperation(operation, {
      modelId: uploaded.model_version.model_id,
      modelVersionId: uploaded.model_version.id,
    })) return null;
    const [inventory, setups] = await Promise.all([
      api.readInventory(uploaded.model_version.id),
      api.listSetups(project.id),
    ]);
    if (!isCurrentOperation(operation)) return null;
    await onLoadVersion(uploaded.model_version, versionLoadContext(operation));
    if (!isCurrentOperation(operation)) return null;
    state.setup = null;
    state.current = null;
    state.history = [];
    state.sourceVersion = uploaded.model_version;
    state.latestVersion = uploaded.model_version;
    state.viewerSelection = null;
    state.inventory = inventory;
    state.setups = setups;
    finishContextChange(operation);
    updateSetupSelect();
    renderAll();
    storeSelection();
    setWorkspaceStatus(`${file.name} stored as durable model version 1.`);
    return uploaded.model_version;
  }

  async function uploadNewVersion(file) {
    const modelId = state.setup?.model_id || state.latestVersion?.model_id;
    if (!state.project || !modelId) throw new Error("Open a durable model before uploading a new version.");
    const project = state.project;
    const setup = state.setup;
    const operation = beginContextChange({
      projectId: project.id,
      setupId: setup?.id || null,
      modelId,
      modelVersionId: setup?.model_version_id || null,
    });
    setWorkspaceStatus(`Uploading new source version ${file.name}…`);
    const uploaded = await api.uploadModelVersion(project.id, modelId, file);
    if (!isCurrentOperation(operation)) return null;
    if (setup) {
      const [view, history] = await Promise.all([
        api.readSetup(setup.id),
        api.listRevisions(setup.id),
      ]);
      if (!isCurrentOperation(operation)) return null;
      state.latestVersion = uploaded.model_version;
      state.setup = view.setup;
      state.current = view.current;
      state.history = history;
    } else {
      if (!retargetOperation(operation, {
        modelVersionId: uploaded.model_version.id,
      })) return null;
      const inventory = await api.readInventory(uploaded.model_version.id);
      if (!isCurrentOperation(operation)) return null;
      await onLoadVersion(uploaded.model_version, versionLoadContext(operation));
      if (!isCurrentOperation(operation)) return null;
      state.latestVersion = uploaded.model_version;
      state.sourceVersion = uploaded.model_version;
      state.inventory = inventory;
    }
    finishContextChange(operation);
    renderAll();
    storeSelection();
    setWorkspaceStatus(
      state.setup?.is_stale
        ? "New source version stored. The open setup is now stale and remains read-only."
        : "New source version stored.",
    );
    return uploaded.model_version;
  }

  async function createSetup(intent) {
    if (!state.project || !state.latestVersion) {
      throw new Error("Select a project and upload a model first.");
    }
    const project = state.project;
    const sourceVersion = state.latestVersion;
    const body = {
      model_id: sourceVersion.model_id,
      model_version_id: sourceVersion.id,
      request_id: makeRequestId("create-setup"),
      intent: clone(intent),
    };
    const operation = beginContextChange({
      projectId: project.id,
      modelId: sourceVersion.model_id,
      modelVersionId: sourceVersion.id,
    });
    const view = await api.createSetup(project.id, body);
    if (!retargetOperation(operation, { setupId: view.setup.id })) return null;
    const sourceChanged = state.sourceVersion?.id !== sourceVersion.id;
    const [history, setups, inventory] = await Promise.all([
      api.listRevisions(view.setup.id),
      api.listSetups(project.id),
      sourceChanged ? api.readInventory(sourceVersion.id) : Promise.resolve(state.inventory),
    ]);
    if (!isCurrentOperation(operation)) return null;
    if (sourceChanged) {
      await onLoadVersion(sourceVersion, versionLoadContext(operation));
      if (!isCurrentOperation(operation)) return null;
    }
    state.setup = view.setup;
    state.current = view.current;
    state.sourceVersion = sourceVersion;
    state.inventory = inventory;
    state.history = history;
    state.setups = setups;
    state.proposal = null;
    finishContextChange(operation);
    updateSetupSelect();
    renderAll();
    storeSelection();
    setWorkspaceStatus(`Setup created at immutable revision ${view.current.revision}.`);
    return view;
  }

  async function createBlankSetup() {
    return createSetup(baseIntent());
  }

  async function createSetupFromProposal(intent) {
    if (!intent) throw new Error("Interpret an instruction before creating a proposal setup.");
    return createSetup(intent);
  }

  async function executeEndpoint(endpoint, context, body) {
    if (endpoint.kind === "revision") {
      return api.createRevision(context.setupId, body);
    }
    if (endpoint.kind === "region") {
      return api.decideRegion(
        context.setupId,
        endpoint.regionId,
        endpoint.action,
        body,
      );
    }
    return api.decideAssumption(
      context.setupId,
      endpoint.assumptionId,
      endpoint.action,
      body,
    );
  }

  function replayMatchesActive(pending) {
    const current = activeContext();
    return sameContext(pending.context, current)
      && pending.context.operationEpoch === state.operationEpoch
      && pending.context.expectedRevision === state.current?.revision
      && pending.body.expected_revision === state.current?.revision;
  }

  function mutationContextMatches(context) {
    return sameContext(context, activeContext())
      && context.operationEpoch === state.operationEpoch;
  }

  async function sendRequest(endpoint, body, {
    context = replayContext(),
    submittedEditor = null,
  } = {}) {
    const frozenEndpoint = Object.freeze({ ...endpoint });
    const exactBody = deepFreeze(clone(body));
    const frozenContext = Object.freeze({ ...context });
    if (!mutationContextMatches(frozenContext)
      || frozenContext.expectedRevision !== state.current?.revision) {
      return null;
    }
    try {
      const response = await executeEndpoint(frozenEndpoint, frozenContext, exactBody);
      if (!mutationContextMatches(frozenContext)) return null;
      const history = await api.listRevisions(frozenContext.setupId);
      if (!mutationContextMatches(frozenContext)) return null;
      state.current = response;
      state.setup = {
        ...state.setup,
        current_revision: response.revision,
        updated_at: response.created_at,
      };
      state.history = history;
      clearPendingReplay();
      conflictBanner.hidden = true;
      if (submittedEditor) state.dirtyEditors.delete(submittedEditor);
      renderAll();
      storeSelection();
      setWorkspaceStatus(`Saved immutable revision ${response.revision}.`);
      return response;
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        if (error.code === "setup_revision_conflict") {
          conflictBanner.hidden = false;
          setWorkspaceStatus("A newer revision exists. Unsaved form input is preserved.", true);
        } else if (error.code === "setup_source_superseded") {
          const view = await api.readSetup(frozenContext.setupId);
          if (!mutationContextMatches(frozenContext)) return null;
          state.setup = view.setup;
          state.current = view.current;
          renderAll();
          setWorkspaceStatus(errorMessage(error), true);
        } else {
          setWorkspaceStatus(errorMessage(error), true);
        }
      } else if (error.name === "NetworkError") {
        if (mutationContextMatches(frozenContext)) {
          state.pendingReplay = Object.freeze({
            endpoint: frozenEndpoint,
            body: exactBody,
            context: frozenContext,
            submittedEditor,
          });
          retryBanner.hidden = false;
          setWorkspaceStatus(error.message, true);
        }
      } else {
        setWorkspaceStatus(errorMessage(error), true);
      }
      throw error;
    }
  }

  async function commitIntent(prefix, transform, submittedEditor = null) {
    guardEditable();
    const intent = clone(state.current.intent);
    const changed = transform(intent) || intent;
    const body = {
      expected_revision: state.current.revision,
      request_id: makeRequestId(prefix),
      intent: changed,
    };
    return sendRequest({ kind: "revision" }, body, { submittedEditor });
  }

  async function decideRegion(regionId, action) {
    guardEditable();
    const body = {
      expected_revision: state.current.revision,
      request_id: makeRequestId(`region-${action}`),
    };
    return sendRequest(
      { kind: "region", regionId, action },
      body,
    );
  }

  async function decideAssumption(assumptionId, action) {
    guardEditable();
    const body = {
      expected_revision: state.current.revision,
      request_id: makeRequestId(`assumption-${action}`),
    };
    return sendRequest(
      { kind: "assumption", assumptionId, action },
      body,
    );
  }

  function regionId() {
    const suffix = globalThis.crypto?.randomUUID?.().replaceAll("-", "").slice(0, 12)
      || `${Date.now()}${Math.random().toString(16).slice(2, 7)}`;
    return `region_${suffix}`;
  }

  function newRegion(candidate) {
    return {
      id: regionId(),
      entity_type: candidate.entity_type,
      entity_ids: candidate.entity_ids,
      selection_method: "user_click",
      confidence: 1,
      source_instruction: candidate.source_instruction,
      status: "proposed",
    };
  }

  function nativeCandidates() {
    if (state.sourceVersion?.model_kind !== "inp") return [];
    return (state.inventory?.regions || []).map((region) => ({
      key: `native|${region.kind}|${encodeURIComponent(region.name)}`,
      entity_type: region.kind,
      entity_ids: [region.name],
      label: `${region.kind}: ${region.name}`,
      source_instruction: `Use native ${region.kind} ${region.name}.`,
    }));
  }

  function viewerCandidate() {
    if (!state.viewerSelection || !state.sourceVersion) return null;
    return {
      key: "viewer",
      entity_type: state.sourceVersion.model_kind === "step" ? "cad_face" : "mesh_face",
      entity_ids: [state.viewerSelection],
      label: `Selected viewer face_${state.viewerSelection}`,
      source_instruction: `Use selected viewer face_${state.viewerSelection}.`,
    };
  }

  function permittedTypes(conditionType) {
    return {
      fixed_displacement: ["cad_face", "mesh_face", "node_set"],
      prescribed_displacement: ["cad_face", "mesh_face", "node_set"],
      resultant_surface_force: ["cad_face", "mesh_face", "node_set"],
      pressure: ["cad_face", "mesh_face"],
      surface_traction: ["cad_face", "mesh_face"],
      gravity: ["element_set"],
      concentrated_force: ["node_set"],
    }[conditionType] || [];
  }

  function fillTargetSelect(select, conditionType, preferred = null, preserve = false) {
    const previous = preferred ?? (preserve ? select.value : null);
    const allowed = permittedTypes(conditionType);
    select.replaceChildren();
    if (conditionType === "gravity") {
      const whole = element("option", "", "Whole model (default)");
      whole.value = "whole-model";
      select.append(whole);
    } else {
      const placeholder = element("option", "", "Choose or replace a target");
      placeholder.value = "";
      select.append(placeholder);
    }
    for (const region of state.current?.intent.regions || []) {
      if (!allowed.includes(region.entity_type)) continue;
      const option = element(
        "option",
        "",
        `${region.id} · ${region.entity_type} · ${region.status}`,
      );
      option.value = `existing|${region.id}`;
      select.append(option);
    }
    const viewer = viewerCandidate();
    if (viewer && allowed.includes(viewer.entity_type)) {
      const option = element("option", "", viewer.label);
      option.value = viewer.key;
      select.append(option);
    }
    for (const candidate of nativeCandidates()) {
      if (!allowed.includes(candidate.entity_type)) continue;
      const option = element("option", "", candidate.label);
      option.value = candidate.key;
      select.append(option);
    }
    if (previous !== null && [...select.options].some((option) => option.value === previous)) {
      select.value = previous;
    }
  }

  function resolveTarget(intent, value, conditionType, replacementRegionRef = null) {
    if (conditionType === "gravity" && value === "whole-model") return null;
    let candidate = null;
    if (value.startsWith("existing|")) {
      const selected = intent.regions.find(
        (region) => region.id === value.slice("existing|".length),
      );
      if (!selected) throw new Error("The selected engineering target no longer exists.");
      candidate = {
        entity_type: selected.entity_type,
        entity_ids: clone(selected.entity_ids),
        source_instruction: `Replace target with region ${selected.id}.`,
      };
    } else if (value === "viewer") {
      candidate = viewerCandidate();
    } else if (value.startsWith("native|")) {
      candidate = nativeCandidates().find((item) => item.key === value);
    }
    if (!candidate) throw new Error("Choose a supported engineering target.");
    const replacementIndex = intent.regions.findIndex(
      (region) => region.id === replacementRegionRef && region.status === "rejected",
    );
    if (replacementIndex >= 0) {
      const rejected = intent.regions[replacementIndex];
      intent.regions[replacementIndex] = {
        ...rejected,
        entity_type: candidate.entity_type,
        entity_ids: clone(candidate.entity_ids),
        selection_method: "user_click",
        confidence: 1,
        source_instruction: candidate.source_instruction,
        status: "proposed",
      };
      return rejected.id;
    }
    if (value.startsWith("existing|")) {
      return value.slice("existing|".length);
    }
    const existing = intent.regions.find(
      (region) => region.status !== "rejected" && sameEntities(region, candidate),
    );
    if (existing) return existing.id;
    const region = newRegion(candidate);
    intent.regions.push(region);
    return region.id;
  }

  function removeUnusedProposedRegion(intent, regionRef) {
    if (!regionRef) return;
    const stillUsed = [...intent.bcs, ...intent.loads].some((item) => item.region_ref === regionRef);
    const region = intent.regions.find((item) => item.id === regionRef);
    if (!stillUsed && region?.status === "proposed") {
      intent.regions = intent.regions.filter((item) => item.id !== regionRef);
    }
  }

  function renderConfiguration() {
    if (!state.current) return;
    const { analysis, mesh_settings: mesh, solver_settings: solver } = state.current.intent;
    byId("analysis-type").value = analysis.type;
    byId("analysis-dimensionality").value = analysis.dimensionality || "3d_solid";
    byId("analysis-coordinate-system").value = analysis.coordinate_system || "global_cartesian";
    byId("analysis-solver-target").value = analysis.solver_target || "calculix";
    const original = mesh?.target_size_original;
    const size = original?.value ?? mesh?.global_element_size_mm ?? "";
    byId("mesh-size").value = size === "" ? "" : formatNumber(size);
    byId("mesh-size-unit").value = original?.unit || "mm";
    for (const input of document.querySelectorAll('input[name="result_field"]')) {
      input.checked = Boolean(solver?.requested_results?.includes(input.value));
    }
  }

  function linkedMaterialDecision(material) {
    return state.current?.intent.assumptions.find(
      (item) => item.id === material?.proposal_assumption_ref,
    ) || null;
  }

  function renderMaterial(preserveDraft = false) {
    if (!state.current) return;
    const material = state.current.intent.materials[0];
    const authority = byId("material-authority");
    const actions = byId("material-decision-actions");
    if (!material) {
      authority.textContent = "No material assigned.";
      actions.hidden = true;
      if (preserveDraft) return;
      byId("material-name").value = "";
      byId("material-e").value = "";
      byId("material-nu").value = "";
      byId("material-density").value = "";
      return;
    }
    const decision = linkedMaterialDecision(material);
    const status = material.authority === "engineer_entered"
      ? "engineer-entered"
      : `${decision?.status || "pending"} system proposal`;
    authority.textContent = `${material.name} · ${status} · E=${material.E_MPa} MPa · ν=${material.nu}`
      + (material.density_tonne_per_mm3 ? ` · density=${material.density_tonne_per_mm3} tonne/mm³` : "");
    actions.hidden = !(material.authority === "system_proposed" && decision?.status === "pending");
    actions.dataset.assumptionId = decision?.id || "";
    if (preserveDraft) return;
    byId("material-name").value = material.name;
    byId("material-e").value = formatNumber(material.youngs_modulus_original?.value ?? material.E_MPa);
    byId("material-e-unit").value = material.youngs_modulus_original?.unit || "MPa";
    byId("material-nu").value = formatNumber(material.nu);
    const density = material.density_original?.value ?? "";
    byId("material-density").value = density === "" ? "" : formatNumber(density);
    byId("material-density-unit").value = material.density_original?.unit || "kg/m^3";
  }

  function describeBC(bc) {
    if (bc.type === "fixed_displacement") return `Fixed ${bc.components.join(", ").toUpperCase()}`;
    return `Prescribed ${Object.entries(bc.components).map(([axis, value]) => `${axis.toUpperCase()}=${formatNumber(value)} mm`).join(", ")}`;
  }

  function renderConditionList(container, items, kind) {
    container.replaceChildren();
    if (!items.length) {
      container.append(element("p", "audit-empty-inline", `No ${kind === "bc" ? "boundary conditions" : "loads"} defined.`));
      return;
    }
    items.forEach((item, index) => {
      const card = element("article", "engineering-item");
      const header = element("header");
      header.append(
        element("strong", "", kind === "bc" ? describeBC(item) : item.type.replaceAll("_", " ")),
        element("span", "audit-status", item.region_ref || "whole model"),
      );
      const details = kind === "load" && item.vector
        ? `vector [${item.vector.join(", ")}]`
        : `target ${item.region_ref || "whole model"}`;
      card.append(header, element("p", "", details));
      const actions = element("div", "form-actions");
      const edit = element("button", "", "Edit / replace target");
      edit.type = "button";
      edit.dataset.action = "edit";
      edit.dataset.index = String(index);
      const remove = element("button", "", "Remove");
      remove.type = "button";
      remove.dataset.action = "remove";
      remove.dataset.index = String(index);
      actions.append(edit, remove);
      card.append(actions);
      container.append(card);
    });
  }

  function renderBCs() {
    if (!state.current) return;
    renderConditionList(byId("bc-list"), state.current.intent.bcs, "bc");
    if (!state.dirtyEditors.has("bc")) {
      fillTargetSelect(byId("bc-target"), byId("bc-type").value);
    }
  }

  function renderLoads() {
    if (!state.current) return;
    renderConditionList(byId("load-list"), state.current.intent.loads, "load");
    const hasNodeSet = nativeCandidates().some((item) => item.entity_type === "node_set");
    byId("concentrated-force-option").disabled = !hasNodeSet;
    if (!state.dirtyEditors.has("load")) updateLoadFields();
  }

  function statusCard(kind, item) {
    const card = element("article", `audit-card ${kind}-card ${item.status}`);
    const heading = element("div", "audit-card-heading");
    heading.append(
      element("strong", "audit-card-title", item.id),
      element("span", `audit-status ${item.status}`, item.status),
    );
    card.append(heading);
    if (kind === "region") {
      const facts = element("dl", "audit-facts");
      for (const [label, value] of [
        ["Entities", `${item.entity_type}: ${item.entity_ids.join(", ")}`],
        ["Selection", item.selection_method],
        ["Evidence", item.source_instruction],
      ]) {
        facts.append(element("dt", "", label), element("dd", "", value));
      }
      card.append(facts);
      card.addEventListener("click", () => onHighlight({
        entity_ids: item.entity_ids.filter((value) => Number.isInteger(value)),
        style: item.status,
      }));
    } else {
      card.append(element("p", "assumption-text", item.text));
    }
    if (item.status === (kind === "region" ? "proposed" : "pending")) {
      const actions = element("div", "audit-actions");
      for (const action of kind === "region" ? ["confirm", "reject"] : ["accept", "reject"]) {
        const button = element("button", `audit-action ${action}`, action);
        button.type = "button";
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          button.disabled = true;
          try {
            if (kind === "region") await decideRegion(item.id, action);
            else await decideAssumption(item.id, action);
          } catch {
            button.disabled = false;
          }
        });
        actions.append(button);
      }
      card.append(actions);
    }
    return card;
  }

  function renderReview() {
    if (!state.current) return;
    const revision = state.current;
    const report = revision.validation;
    const engineering = byId("engineering-ready-value");
    engineering.textContent = revision.engineering_ready ? "Ready" : report.readiness_status.replaceAll("_", " ");
    engineering.dataset.ready = String(revision.engineering_ready);
    const artifact = byId("artifact-capability-value");
    artifact.textContent = revision.artifact_capability.supported ? "Capable" : "Blocked";
    artifact.dataset.ready = String(revision.artifact_capability.supported);
    const compatibility = byId("compatibility-export-value");
    compatibility.textContent = revision.export_eligible ? "Eligible" : "Blocked";
    compatibility.dataset.ready = String(revision.export_eligible);

    const issues = byId("engineering-issue-list");
    issues.replaceChildren();
    for (const issue of report.issues) {
      const item = element("li", issue.severity);
      const code = element("code", "", issue.code);
      item.append(code, document.createTextNode(` — ${issue.message}`));
      issues.append(item);
    }
    for (const codeValue of revision.artifact_capability.blocking_issue_codes) {
      const item = element("li", "error");
      item.append(
        element("code", "", codeValue),
        document.createTextNode(` — ${CAPABILITY_MESSAGES[codeValue] || "The selected artifact target is not currently capable."}`),
      );
      issues.append(item);
    }
    if (!issues.children.length) issues.append(element("li", "clear", "No backend blockers."));

    const summary = report.load_summary;
    const summaryNode = byId("load-summary");
    summaryNode.replaceChildren();
    for (const [label, value] of [
      ["Explicit force total", `[${summary.explicit_force_vector_sum_N.join(", ")}] N`],
      ["Distributed loads", String(summary.distributed_load_count)],
      ["Gravity density", summary.gravity_density_required ? (summary.gravity_density_available ? "available" : "required") : "not required"],
      ["Unresolved resultants", String(summary.unresolved_resultants.length)],
    ]) {
      summaryNode.append(element("dt", "", label), element("dd", "", value));
    }
    byId("durable-region-list").replaceChildren(
      ...revision.intent.regions.map((item) => statusCard("region", item)),
    );
    if (!revision.intent.regions.length) {
      byId("durable-region-list").append(element("p", "audit-empty-inline", "No regions defined."));
    }
    byId("durable-assumption-list").replaceChildren(
      ...revision.intent.assumptions.map((item) => statusCard("assumption", item)),
    );
    if (!revision.intent.assumptions.length) {
      byId("durable-assumption-list").append(element("p", "audit-empty-inline", "No assumptions recorded."));
    }
  }

  function renderHistory() {
    const list = byId("revision-history-list");
    list.replaceChildren();
    if (!state.history.length) {
      list.append(element("li", "", "No setup selected."));
      return;
    }
    for (const revision of [...state.history].reverse()) {
      list.append(element(
        "li",
        "",
        `r${revision.revision} · ${revision.mutation_type} · ${revision.request_id} · ${new Date(revision.created_at).toLocaleString()}`,
      ));
    }
  }

  function setEditorDisabled(disabled) {
    for (const control of document.querySelectorAll(
      "#engineering-content form input, #engineering-content form select, #engineering-content form button",
    )) {
      control.disabled = disabled;
    }
    byId("concentrated-force-option").disabled = disabled
      || !nativeCandidates().some((item) => item.entity_type === "node_set");
  }

  function renderAll() {
    updateProjectSelect();
    updateSetupSelect();
    const hasVersion = Boolean(state.latestVersion);
    byId("create-blank-setup").disabled = !hasVersion
      || Boolean(state.current && !state.setup?.is_stale && state.setup.model_version_id === state.latestVersion.id);
    byId("upload-version-button").disabled = !hasVersion;
    byId("current-revision").textContent = state.current ? `r${state.current.revision}` : "—";
    byId("source-version").textContent = state.sourceVersion
      ? `${state.sourceVersion.source_name} · v${state.sourceVersion.version}`
      : "—";
    staleBanner.hidden = !state.setup?.is_stale;
    byId("engineering-empty").hidden = Boolean(state.current);
    byId("engineering-content").hidden = !state.current;
    const badge = byId("engineering-readiness-badge");
    badge.textContent = state.current
      ? state.current.validation.readiness_status.replaceAll("_", " ")
      : "No setup";
    badge.dataset.status = state.current?.engineering_ready ? "valid" : "invalid";
    if (!state.current) {
      renderHistory();
      onHighlight({ reset: true });
      return;
    }
    if (!state.dirtyEditors.has("configuration")) renderConfiguration();
    renderMaterial(state.dirtyEditors.has("material"));
    renderBCs();
    renderLoads();
    renderReview();
    renderHistory();
    setEditorDisabled(Boolean(state.setup.is_stale));
    onHighlight({ reset: true });
    if (state.setup.is_stale) {
      return;
    } else {
      for (const highlight of Object.values(state.current.highlight_state)) {
        onHighlight(highlight);
      }
      const regions = new Map(
        state.current.intent.regions.map((region) => [region.id, region]),
      );
      for (const bc of state.current.intent.bcs) {
        const region = regions.get(bc.region_ref);
        if (region?.status === "confirmed") {
          onHighlight({
            entity_ids: region.entity_ids.filter((value) => Number.isInteger(value)),
            style: "fixed_boundary_condition",
          });
        }
      }
      for (const load of state.current.intent.loads) {
        const region = regions.get(load.region_ref);
        if (region?.status === "confirmed") {
          onHighlight({
            entity_ids: region.entity_ids.filter((value) => Number.isInteger(value)),
            style: "load_direction",
            vector: load.vector || load.direction,
          });
        }
      }
    }
  }

  function updateSelectionControls() {
    const label = state.viewerSelection ? `face_${state.viewerSelection}` : "None";
    byId("bc-viewer-selection").textContent = label;
    byId("load-viewer-selection").textContent = label;
    fillTargetSelect(
      byId("bc-target"),
      byId("bc-type").value,
      null,
      byId("bc-edit-index").value !== "",
    );
    fillTargetSelect(
      byId("load-target"),
      byId("load-type").value,
      null,
      byId("load-edit-index").value !== "",
    );
  }

  function setViewerSelection(entityId, contextToken, viewerSourceId) {
    if (!isViewerSelectionContextCurrent(contextToken, entityId, viewerSourceId)) return false;
    state.viewerSelection = entityId;
    updateSelectionControls();
    return true;
  }

  function clearBCForm() {
    state.dirtyEditors.delete("bc");
    byId("bc-edit-index").value = "";
    byId("bc-type").value = "fixed_displacement";
    for (const input of document.querySelectorAll('input[name="fixed_axis"], input[name="prescribed_axis"]')) {
      input.checked = false;
    }
    for (const input of document.querySelectorAll("[data-axis]")) input.value = "";
    updateBCFields();
  }

  function updateBCFields(preserveTarget = false) {
    const prescribed = byId("bc-type").value === "prescribed_displacement";
    byId("bc-fixed-fields").hidden = prescribed;
    byId("bc-prescribed-fields").hidden = !prescribed;
    fillTargetSelect(byId("bc-target"), byId("bc-type").value, null, preserveTarget);
    updateBCPreview();
  }

  function updateBCPreview() {
    const values = [];
    for (const axis of AXES) {
      const enabled = document.querySelector(`input[name="prescribed_axis"][value="${axis}"]`)?.checked;
      if (!enabled) continue;
      const input = document.querySelector(`[data-axis="${axis}"]`);
      const unit = document.querySelector(`[data-axis-unit="${axis}"]`).value;
      if (!input.value.trim()) continue;
      const value = Number(input.value);
      if (Number.isFinite(value)) {
        values.push(`${axis.toUpperCase()}=${formatNumber(value * LENGTH_FACTORS[unit])} mm`);
      }
    }
    byId("bc-normalized-preview").textContent = `Normalized preview: ${values.join(", ") || "—"}`;
  }

  function editBC(index) {
    const bc = state.current.intent.bcs[index];
    clearBCForm();
    byId("bc-edit-index").value = String(index);
    byId("bc-type").value = bc.type;
    updateBCFields();
    fillTargetSelect(byId("bc-target"), bc.type, `existing|${bc.region_ref}`);
    if (bc.type === "fixed_displacement") {
      for (const axis of bc.components) {
        document.querySelector(`input[name="fixed_axis"][value="${axis}"]`).checked = true;
      }
    } else {
      for (const [axis, value] of Object.entries(bc.components)) {
        document.querySelector(`input[name="prescribed_axis"][value="${axis}"]`).checked = true;
        const original = bc.components_original?.[axis];
        document.querySelector(`[data-axis="${axis}"]`).value = formatNumber(original?.value ?? value);
        document.querySelector(`[data-axis-unit="${axis}"]`).value = original?.unit || "mm";
      }
      updateBCPreview();
    }
    state.dirtyEditors.add("bc");
    byId("bc-form").scrollIntoView({ block: "nearest" });
  }

  async function saveBC() {
    const indexText = byId("bc-edit-index").value;
    const type = byId("bc-type").value;
    const targetValue = byId("bc-target").value;
    await commitIntent(indexText ? "edit-bc" : "add-bc", (intent) => {
      const old = indexText ? intent.bcs[Number(indexText)] : null;
      const regionRef = resolveTarget(intent, targetValue, type, old?.region_ref);
      let bc;
      if (type === "fixed_displacement") {
        const components = [...document.querySelectorAll('input[name="fixed_axis"]:checked')]
          .map((input) => input.value);
        if (!components.length) throw new Error("Choose at least one fixed translational component.");
        bc = { type, region_ref: regionRef, components };
      } else {
        const components = {};
        const componentsOriginal = {};
        for (const input of document.querySelectorAll('input[name="prescribed_axis"]:checked')) {
          const axis = input.value;
          const value = requiredNumberInput(
            document.querySelector(`[data-axis="${axis}"]`),
            `${axis.toUpperCase()} displacement`,
          );
          const unit = document.querySelector(`[data-axis-unit="${axis}"]`).value;
          componentsOriginal[axis] = { value, unit };
          components[axis] = value * LENGTH_FACTORS[unit];
        }
        if (!Object.keys(components).length) throw new Error("Choose at least one prescribed translational component.");
        bc = { type, region_ref: regionRef, components, components_original: componentsOriginal };
      }
      if (old) intent.bcs[Number(indexText)] = bc;
      else intent.bcs.push(bc);
      if (old?.region_ref !== regionRef) removeUnusedProposedRegion(intent, old?.region_ref);
      return intent;
    }, "bc");
    clearBCForm();
  }

  function loadUnitOptions(type) {
    const select = byId("load-unit");
    const current = select.value;
    const units = type === "pressure" || type === "surface_traction"
      ? Object.keys(STRESS_FACTORS)
      : type === "gravity"
        ? Object.keys(ACCELERATION_FACTORS)
        : Object.keys(FORCE_FACTORS);
    select.replaceChildren(...units.map((unit) => {
      const option = element("option", "", unit);
      option.value = unit;
      return option;
    }));
    if (units.includes(current)) select.value = current;
  }

  function updateLoadFields(preserveTarget = false) {
    const type = byId("load-type").value;
    const pressure = type === "pressure";
    const gravity = type === "gravity";
    byId("load-direction-fields").hidden = pressure;
    byId("pressure-convention").hidden = !pressure;
    byId("gravity-dependency").hidden = !gravity;
    byId("load-target-label").hidden = gravity && !nativeCandidates().some((item) => item.entity_type === "element_set");
    loadUnitOptions(type);
    fillTargetSelect(byId("load-target"), type, null, preserveTarget);
    updateLoadPreview();
  }

  function loadFactor(type, unit) {
    if (type === "pressure" || type === "surface_traction") return STRESS_FACTORS[unit];
    if (type === "gravity") return ACCELERATION_FACTORS[unit];
    return FORCE_FACTORS[unit];
  }

  function updateLoadPreview() {
    const type = byId("load-type").value;
    const magnitude = Number(byId("load-magnitude").value);
    const unit = byId("load-unit").value;
    if (!Number.isFinite(magnitude) || !unit) {
      byId("load-normalized-preview").textContent = "Normalized preview: —";
      return;
    }
    const canonical = magnitude * loadFactor(type, unit);
    if (type === "pressure") {
      byId("load-normalized-preview").textContent = `Normalized preview: ${canonical} MPa inward-normal`;
      return;
    }
    try {
      const direction = normalizedDirection([
        byId("load-direction-x").value,
        byId("load-direction-y").value,
        byId("load-direction-z").value,
      ]);
      const vector = direction.map((value) => value * canonical);
      const canonicalUnit = type === "gravity" ? "mm/s²" : type === "surface_traction" ? "MPa" : "N";
      byId("load-normalized-preview").textContent = `Normalized preview: direction [${direction.map((v) => v.toPrecision(4)).join(", ")}], vector [${vector.map((v) => v.toPrecision(4)).join(", ")}] ${canonicalUnit}`;
    } catch {
      byId("load-normalized-preview").textContent = `Normalized magnitude: ${canonical}`;
    }
  }

  function clearLoadForm() {
    state.dirtyEditors.delete("load");
    byId("load-edit-index").value = "";
    byId("load-type").value = "resultant_surface_force";
    byId("load-magnitude").value = "";
    byId("load-direction-x").value = "0";
    byId("load-direction-y").value = "-1";
    byId("load-direction-z").value = "0";
    updateLoadFields();
  }

  function editLoad(index) {
    const load = state.current.intent.loads[index];
    clearLoadForm();
    byId("load-edit-index").value = String(index);
    byId("load-type").value = load.type;
    updateLoadFields();
    fillTargetSelect(
      byId("load-target"),
      load.type,
      load.region_ref ? `existing|${load.region_ref}` : "whole-model",
    );
    const original = load.original_force || load.original_traction || load.original_pressure || load.original_acceleration;
    const magnitude = original?.value
      ?? load.magnitude_N
      ?? load.magnitude_MPa
      ?? load.magnitude
      ?? load.magnitude_mm_per_s2
      ?? "";
    byId("load-magnitude").value = magnitude === "" ? "" : formatNumber(magnitude);
    if (original?.unit) byId("load-unit").value = original.unit;
    const direction = load.direction || (load.vector
      ? normalizedDirection(load.vector)
      : [0, -1, 0]);
    [byId("load-direction-x").value, byId("load-direction-y").value, byId("load-direction-z").value] = direction;
    updateLoadPreview();
    state.dirtyEditors.add("load");
    byId("load-form").scrollIntoView({ block: "nearest" });
  }

  function buildLoad(intent, type, targetValue, replacementRegionRef = null) {
    const originalMagnitude = safeNumber(byId("load-magnitude").value, "Load magnitude");
    const unit = byId("load-unit").value;
    if (type === "pressure") {
      if (originalMagnitude < 0) throw new Error("Pressure must be nonnegative.");
      return {
        type,
        region_ref: resolveTarget(intent, targetValue, type, replacementRegionRef),
        magnitude: originalMagnitude * STRESS_FACTORS[unit],
        original_pressure: { value: originalMagnitude, unit },
        distribution: "uniform",
      };
    }
    if (!(originalMagnitude > 0)) throw new Error("Load magnitude must be greater than zero.");
    const direction = normalizedDirection([
      byId("load-direction-x").value,
      byId("load-direction-y").value,
      byId("load-direction-z").value,
    ]);
    const magnitude = originalMagnitude * loadFactor(type, unit);
    const vector = direction.map((value) => value * magnitude);
    const common = {
      type,
      region_ref: type === "gravity" && targetValue === "whole-model"
        ? null
        : resolveTarget(intent, targetValue, type, replacementRegionRef),
      vector,
      direction,
      distribution: "uniform",
    };
    if (type === "surface_traction") {
      return {
        ...common,
        original_traction: { value: originalMagnitude, unit },
        magnitude_MPa: magnitude,
      };
    }
    if (type === "gravity") {
      return {
        ...common,
        original_acceleration: { value: originalMagnitude, unit },
        magnitude_mm_per_s2: magnitude,
      };
    }
    return {
      ...common,
      original_force: { value: originalMagnitude, unit },
      magnitude_N: magnitude,
    };
  }

  async function saveLoad() {
    const indexText = byId("load-edit-index").value;
    const type = byId("load-type").value;
    const targetValue = byId("load-target").value;
    await commitIntent(indexText ? "edit-load" : "add-load", (intent) => {
      const old = indexText ? intent.loads[Number(indexText)] : null;
      const load = buildLoad(intent, type, targetValue, old?.region_ref);
      if (old) intent.loads[Number(indexText)] = load;
      else intent.loads.push(load);
      if (old?.region_ref !== load.region_ref) removeUnusedProposedRegion(intent, old?.region_ref);
      return intent;
    }, "load");
    clearLoadForm();
  }

  async function initialize() {
    bindEvents();
    try {
      await refreshProjects();
      setWorkspaceStatus(state.project ? `Project “${state.project.name}” open.` : "Create a project to begin.");
    } catch (error) {
      setWorkspaceStatus(errorMessage(error), true);
    }
  }

  function bindEvents() {
    if (state.eventsBound) return;
    state.eventsBound = true;
    byId("project-create-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await createProject(byId("project-name").value.trim());
        byId("project-name").value = "";
      } catch (error) {
        setWorkspaceStatus(errorMessage(error), true);
      }
    });
    projectSelect.addEventListener("change", async () => {
      try {
        if (projectSelect.value) await openProject(projectSelect.value);
      } catch (error) {
        setWorkspaceStatus(errorMessage(error), true);
      }
    });
    setupSelect.addEventListener("change", async () => {
      try {
        if (setupSelect.value) await openSetup(setupSelect.value);
      } catch (error) {
        setWorkspaceStatus(errorMessage(error), true);
      }
    });
    byId("workspace-refresh").addEventListener("click", () => refreshCurrent().catch(
      (error) => setWorkspaceStatus(errorMessage(error), true),
    ));
    byId("create-blank-setup").addEventListener("click", () => createBlankSetup().catch(
      (error) => setWorkspaceStatus(errorMessage(error), true),
    ));
    byId("reload-current-revision").addEventListener("click", () => refreshCurrent().catch(
      (error) => setWorkspaceStatus(errorMessage(error), true),
    ));
    byId("dismiss-revision-conflict").addEventListener("click", () => {
      conflictBanner.hidden = true;
    });
    byId("retry-mutation-button").addEventListener("click", async () => {
      if (!state.pendingReplay) return;
      const pending = state.pendingReplay;
      if (!replayMatchesActive(pending)) {
        clearPendingReplay();
        setWorkspaceStatus("Retry discarded because the active project, setup, or model version changed.", true);
        return;
      }
      try {
        await sendRequest(pending.endpoint, pending.body, {
          context: pending.context,
          submittedEditor: pending.submittedEditor,
        });
      } catch {
        // The banners and safe message are managed by sendRequest.
      }
    });
    byId("configuration-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const original = safeNumber(byId("mesh-size").value, "Element size");
        const unit = byId("mesh-size-unit").value;
        if (!(original > 0)) throw new Error("Element size must be greater than zero.");
        const requested = [...document.querySelectorAll('input[name="result_field"]:checked')]
          .map((input) => input.value);
        if (!requested.length) throw new Error("Choose at least one requested result field.");
        await commitIntent("configuration", (intent) => {
          intent.analysis = {
            type: "static_structural",
            units: { length: "mm", force: "N", stress: "MPa" },
            dimensionality: "3d_solid",
            solver_target: "calculix",
            coordinate_system: "global_cartesian",
          };
          intent.mesh_settings = {
            global_element_size_mm: original * LENGTH_FACTORS[unit],
            element_type: "tetrahedral",
            element_order: "first_order",
            mesher: "gmsh",
            mesher_preset: "gmsh_tet_v1",
            target_size_original: { value: original, unit },
          };
          intent.solver_settings = {
            target: "calculix",
            analysis_profile: "linear_static_v1",
            requested_results: requested,
          };
          return intent;
        }, "configuration");
      } catch (error) {
        setWorkspaceStatus(errorMessage(error), true);
      }
    });
    byId("material-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const eValue = safeNumber(byId("material-e").value, "Young’s modulus");
        const eUnit = byId("material-e-unit").value;
        const nu = safeNumber(byId("material-nu").value, "Poisson’s ratio");
        const densityText = byId("material-density").value.trim();
        const densityUnit = byId("material-density-unit").value;
        const material = {
          name: byId("material-name").value.trim(),
          model: "linear_elastic_isotropic",
          authority: "engineer_entered",
          proposal_assumption_ref: null,
          E_MPa: eValue * STRESS_FACTORS[eUnit],
          nu,
          density_tonne_per_mm3: null,
          youngs_modulus_original: { value: eValue, unit: eUnit },
          density_original: null,
        };
        if (!material.name) throw new Error("Material name is required.");
        if (densityText) {
          const density = safeNumber(densityText, "Density");
          material.density_tonne_per_mm3 = density * DENSITY_FACTORS[densityUnit];
          material.density_original = { value: density, unit: densityUnit };
        }
        await commitIntent("material", (intent) => {
          intent.materials = [material];
          return intent;
        }, "material");
      } catch (error) {
        setWorkspaceStatus(errorMessage(error), true);
      }
    });
    byId("accept-material-proposal").addEventListener("click", () => {
      const assumptionId = byId("material-decision-actions").dataset.assumptionId;
      decideAssumption(assumptionId, "accept").catch(() => {});
    });
    byId("reject-material-proposal").addEventListener("click", () => {
      const assumptionId = byId("material-decision-actions").dataset.assumptionId;
      decideAssumption(assumptionId, "reject").catch(() => {});
    });
    byId("configuration-form").addEventListener("input", () => state.dirtyEditors.add("configuration"));
    byId("material-form").addEventListener("input", () => state.dirtyEditors.add("material"));
    byId("bc-type").addEventListener("change", () => {
      state.dirtyEditors.add("bc");
      updateBCFields(byId("bc-edit-index").value !== "");
    });
    byId("bc-form").addEventListener("input", () => {
      state.dirtyEditors.add("bc");
      updateBCPreview();
    });
    byId("bc-form").addEventListener("submit", (event) => {
      event.preventDefault();
      saveBC().catch((error) => setWorkspaceStatus(errorMessage(error), true));
    });
    byId("bc-cancel-edit").addEventListener("click", clearBCForm);
    byId("bc-list").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const index = Number(button.dataset.index);
      if (button.dataset.action === "edit") editBC(index);
      else commitIntent("remove-bc", (intent) => {
        const [removed] = intent.bcs.splice(index, 1);
        removeUnusedProposedRegion(intent, removed.region_ref);
        return intent;
      }).catch((error) => setWorkspaceStatus(errorMessage(error), true));
    });
    byId("load-type").addEventListener("change", () => {
      state.dirtyEditors.add("load");
      updateLoadFields(byId("load-edit-index").value !== "");
    });
    byId("load-form").addEventListener("input", () => {
      state.dirtyEditors.add("load");
      updateLoadPreview();
    });
    byId("load-form").addEventListener("submit", (event) => {
      event.preventDefault();
      saveLoad().catch((error) => setWorkspaceStatus(errorMessage(error), true));
    });
    byId("load-cancel-edit").addEventListener("click", clearLoadForm);
    byId("load-list").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const index = Number(button.dataset.index);
      if (button.dataset.action === "edit") editLoad(index);
      else commitIntent("remove-load", (intent) => {
        const [removed] = intent.loads.splice(index, 1);
        removeUnusedProposedRegion(intent, removed.region_ref);
        return intent;
      }).catch((error) => setWorkspaceStatus(errorMessage(error), true));
    });
  }

  return {
    initialize,
    refreshCurrent,
    uploadModel,
    uploadNewVersion,
    createSetupFromProposal,
    captureViewerSelectionContext,
    isViewerSelectionContextCurrent,
    setViewerSelection,
    setProposal(proposal) {
      state.proposal = proposal;
    },
    currentVersion() {
      return state.sourceVersion || state.latestVersion;
    },
    currentSetup() {
      return state.setup;
    },
    selectedClicks() {
      return state.viewerSelection ? [state.viewerSelection] : [];
    },
    async interpret(instruction, clickedEntityIds) {
      if (!state.latestVersion) throw new Error("Upload a durable model first.");
      const context = activeContext();
      const epoch = state.operationEpoch;
      const versionId = state.latestVersion.id;
      const result = await api.interpretVersion(
        versionId,
        instruction,
        clone(clickedEntityIds),
      );
      if (epoch !== state.operationEpoch
        || versionId !== state.latestVersion?.id
        || !sameContext(context, activeContext())) {
        throw new Error("The interpretation was discarded because the active durable context changed.");
      }
      return result;
    },
  };
}
