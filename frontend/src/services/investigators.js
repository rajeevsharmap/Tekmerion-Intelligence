/**
 * investigators.js
 * ==================
 * There is no backend authentication endpoint. Investigator identity
 * and role are resolved backend-side, per request, from a fixed test
 * directory (backend/investigator_action.py's INVESTIGATOR_DIRECTORY) —
 * the backend NEVER trusts a caller-supplied role. This list mirrors
 * that same real directory so the login screen can offer real,
 * backend-recognized investigator IDs instead of inventing arbitrary
 * ones. The role shown here is a UX label only: every mutating request
 * (human-review, action) still sends investigator_id alone, and the
 * backend independently authorizes it — see services/api.js.
 */
export const KNOWN_INVESTIGATORS = [
  { investigatorId: "INV-J001", name: "Junior Investigator 1", role: "junior" },
  { investigatorId: "INV-J002", name: "Junior Investigator 2", role: "junior" },
  { investigatorId: "INV-S001", name: "Senior Investigator 1", role: "senior" },
  { investigatorId: "INV-S002", name: "Senior Investigator 2", role: "senior" },
];

export function findKnownInvestigator(investigatorId) {
  return KNOWN_INVESTIGATORS.find((i) => i.investigatorId === investigatorId) || null;
}