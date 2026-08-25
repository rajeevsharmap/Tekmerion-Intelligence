/**
 * Mirrors backend/next_best_action.py's real ACTION_MINIMUM_AUTHORITY
 * table. This is a UX convenience only — every submitted action is
 * independently re-resolved and authorized by the backend against the
 * investigator_id sent (see investigator_action.py's resolve_investigator
 * + authorize_action). Presenting an action here does not grant it.
 */
export const ACTION_MINIMUM_AUTHORITY = {
  CLEAR: "junior",
  MONITOR: "junior",
  REQUEST_MORE_INFORMATION: "junior",
  CLOSE_CASE: "junior",
  ESCALATE_TO_SENIOR: "junior",
  RESTRICT_ACCOUNT: "senior",
  BLOCK_TRANSACTION: "senior",
  FILE_SAR: "senior",
};

const ACTION_LABEL = {
  CLEAR: "Clear",
  MONITOR: "Monitor",
  REQUEST_MORE_INFORMATION: "Request More Information",
  CLOSE_CASE: "Close Case",
  ESCALATE_TO_SENIOR: "Escalate to Senior",
  RESTRICT_ACCOUNT: "Restrict Account",
  BLOCK_TRANSACTION: "Block Transaction",
  FILE_SAR: "File SAR",
};

export function actionsForRole(role) {
  return Object.entries(ACTION_MINIMUM_AUTHORITY)
    .filter(([, minRole]) => role === "senior" || minRole === "junior")
    .map(([action]) => ({ action, label: ACTION_LABEL[action] }));
}