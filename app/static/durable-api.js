// Browser API client for the generated /api/v1 contract. Request and response
// property names intentionally mirror schema/generated/typescript/api-types.ts.
/** @typedef {import("../../schema/generated/typescript/api-types").components["schemas"]["SetupRevisionResponse"]} SetupRevisionResponse */
/** @typedef {import("../../schema/generated/typescript/api-types").components["schemas"]["SimulationIntent"]} SimulationIntent */

export class ApiError extends Error {
  constructor(response, problem) {
    super(problem?.detail || `Request failed (${response.status})`);
    this.name = "ApiError";
    this.status = response.status;
    this.code = problem?.code || "api_request_failed";
    this.problem = problem || {};
  }
}

async function apiRequest(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    const unavailable = new Error("The server could not be reached. Your form input has been preserved.");
    unavailable.name = "NetworkError";
    unavailable.cause = error;
    throw unavailable;
  }
  if (!response.ok) {
    let problem = {};
    try {
      problem = await response.json();
    } catch {
      problem = { detail: `Request failed (${response.status})` };
    }
    throw new ApiError(response, problem);
  }
  if (response.status === 204) return null;
  return response.json();
}

function jsonOptions(method, body) {
  // JSON.stringify normally collapses IEEE-754 -0 to 0. The R3 displacement
  // contract deliberately preserves signed zero, so encode that one numeric
  // value as the valid JSON number -0.0 without changing any request shape.
  const serialized = JSON.stringify(body, (_key, value) => (
    typeof value === "number" && Object.is(value, -0)
      ? { __sim_intent_signed_zero__: true }
      : value
  )).replaceAll('{"__sim_intent_signed_zero__":true}', "-0.0");
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: serialized,
  };
}

export const durableApi = {
  listProjects: () => apiRequest("/api/v1/projects"),
  createProject: (name) => apiRequest(
    "/api/v1/projects",
    jsonOptions("POST", { name }),
  ),
  readProject: (projectId) => apiRequest(`/api/v1/projects/${projectId}`),
  uploadModel: (projectId, file) => {
    const body = new FormData();
    body.append("file", file, file.name);
    return apiRequest(`/api/v1/projects/${projectId}/models`, { method: "POST", body });
  },
  uploadModelVersion: (projectId, modelId, file) => {
    const body = new FormData();
    body.append("file", file, file.name);
    return apiRequest(
      `/api/v1/projects/${projectId}/models/${modelId}/versions`,
      { method: "POST", body },
    );
  },
  readModelVersion: (versionId) => apiRequest(`/api/v1/model-versions/${versionId}`),
  listModelVersions: (modelId) => apiRequest(`/api/v1/models/${modelId}/versions`),
  readInventory: (versionId) => apiRequest(`/api/v1/model-versions/${versionId}/inventory`),
  listSetups: (projectId) => apiRequest(`/api/v1/projects/${projectId}/setups`),
  createSetup: (projectId, body) => apiRequest(
    `/api/v1/projects/${projectId}/setups`,
    jsonOptions("POST", body),
  ),
  readSetup: (setupId) => apiRequest(`/api/v1/setups/${setupId}`),
  listRevisions: (setupId) => apiRequest(`/api/v1/setups/${setupId}/revisions`),
  createRevision: (setupId, body) => apiRequest(
    `/api/v1/setups/${setupId}/revisions`,
    jsonOptions("POST", body),
  ),
  decideRegion: (setupId, regionId, action, body) => apiRequest(
    `/api/v1/setups/${setupId}/regions/${encodeURIComponent(regionId)}/${action}`,
    jsonOptions("POST", body),
  ),
  decideAssumption: (setupId, assumptionId, action, body) => apiRequest(
    `/api/v1/setups/${setupId}/assumptions/${encodeURIComponent(assumptionId)}/${action}`,
    jsonOptions("POST", body),
  ),
  interpretVersion: (versionId, instruction, clickedEntityIds) => apiRequest(
    `/api/v1/model-versions/${versionId}/interpret`,
    jsonOptions("POST", {
      instruction,
      clicked_entity_ids: clickedEntityIds,
    }),
  ),
};

export function requestId(prefix) {
  const value = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `browser-${prefix}-${value}`;
}
