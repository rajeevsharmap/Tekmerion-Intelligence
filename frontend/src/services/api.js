/**
 * api.js
 * ========
 * Centralized client for the Tekmerion Intelligence FastAPI backend
 * (backend/main.py). Every network call in the frontend goes through
 * this module — no component should call fetch() directly.
 *
 * Real, verified backend route inventory (backend/main.py):
 *   GET  /cases
 *   GET  /cases/{case_id}
 *   GET  /cases/{case_id}/evidence
 *   GET  /cases/{case_id}/network?role=junior|senior
 *   GET  /cases/{case_id}/regulatory
 *   GET  /cases/{case_id}/audit
 *   GET  /cases/{case_id}/actions
 *   GET  /cases/{case_id}/sar
 *   GET  /cases/{case_id}/timeline
 *   POST /cases/{case_id}/human-review   { reviewer_id, investigator_decision, decision_reason }
 *   POST /cases/{case_id}/action         { investigator_id, requested_action, reason, override_reason? }
 *
 * There is currently no backend authentication endpoint and no
 * /accounts endpoint — this module does not invent either. Investigator
 * identity/role is resolved backend-side from a fixed test directory
 * (investigator_action.py's INVESTIGATOR_DIRECTORY: INV-J001, INV-J002,
 * INV-S001, INV-S002) — the frontend never sends a caller-supplied role,
 * only an investigator_id, and displays whatever role the backend
 * actually authorizes/rejects the action against.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch {
    throw new ApiError(
      "Could not reach the Tekmerion backend. Confirm it is running and VITE_API_BASE_URL is correct.",
      0,
      null
    );
  }

  let body = null;
  const text = await response.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }

  if (!response.ok) {
    const detail = body?.detail || response.statusText || "Request failed";
    throw new ApiError(detail, response.status, body?.detail);
  }

  return body;
}

function get(path) {
  return request(path, { method: "GET" });
}

function post(path, payload) {
  return request(path, { method: "POST", body: JSON.stringify(payload) });
}

export const api = {
  listCases: () => get("/cases"),
  getCase: (caseId) => get(`/cases/${encodeURIComponent(caseId)}`),
  getEvidence: (caseId) => get(`/cases/${encodeURIComponent(caseId)}/evidence`),
  getNetwork: (caseId, role) =>
    get(`/cases/${encodeURIComponent(caseId)}/network?role=${encodeURIComponent(role)}`),
  getRegulatory: (caseId) => get(`/cases/${encodeURIComponent(caseId)}/regulatory`),
  getAudit: (caseId) => get(`/cases/${encodeURIComponent(caseId)}/audit`),
  getActions: (caseId) => get(`/cases/${encodeURIComponent(caseId)}/actions`),
  getSar: (caseId) => get(`/cases/${encodeURIComponent(caseId)}/sar`),
  getTimeline: (caseId) => get(`/cases/${encodeURIComponent(caseId)}/timeline`),

  submitHumanReview: (caseId, { reviewerId, investigatorDecision, decisionReason }) =>
    post(`/cases/${encodeURIComponent(caseId)}/human-review`, {
      reviewer_id: reviewerId,
      investigator_decision: investigatorDecision,
      decision_reason: decisionReason,
    }),

  submitAction: (caseId, { investigatorId, requestedAction, reason, overrideReason }) => {
    // NOTE: the backend's InvestigatorActionRequest model types
    // override_reason as a plain `str` (default None on the Python
    // side, not Optional[str]) — sending an explicit JSON `null` is
    // rejected by FastAPI/Pydantic with a 422. Confirmed by directly
    // exercising this endpoint. Omit the key entirely when there is no
    // override reason instead of sending null.
    const payload = {
      investigator_id: investigatorId,
      requested_action: requestedAction,
      reason,
    };
    if (overrideReason) {
      payload.override_reason = overrideReason;
    }
    return post(`/cases/${encodeURIComponent(caseId)}/action`, payload);
  },
};

export default api;