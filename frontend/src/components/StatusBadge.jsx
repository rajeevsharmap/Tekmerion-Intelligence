// Maps the backend's real case_state values (case_state.py) to a
// display tone. No state is invented here — an unrecognized value
// still renders, just without special styling.
const STATE_TONE = {
  SUSPECTED: "neutral",
  INVESTIGATING: "neutral",
  AUDIT_READY: "positive",
  HUMAN_REVIEW: "attention",
  ACTION_PENDING: "attention",
  ACTION_EXECUTED: "positive",
  ESCALATED: "critical",
  CLOSED: "muted",
};

const STATE_LABEL = {
  SUSPECTED: "Suspected",
  INVESTIGATING: "Investigating",
  AUDIT_READY: "Audit Ready",
  HUMAN_REVIEW: "Human Review",
  ACTION_PENDING: "Action Pending",
  ACTION_EXECUTED: "Action Executed",
  ESCALATED: "Escalated",
  CLOSED: "Closed",
};

export function CaseStateBadge({ state }) {
  if (!state) {
    return <span className="state-badge tone-muted">Unknown</span>;
  }
  const tone = STATE_TONE[state] || "neutral";
  const label = STATE_LABEL[state] || state;
  return <span className={`state-badge tone-${tone}`}>{label}</span>;
}

export function CompletenessBadge({ status }) {
  if (!status) {
    return <span className="state-badge tone-muted">Not assessed</span>;
  }
  const tone = status === "complete" ? "positive" : "attention";
  const label = status === "complete" ? "Complete" : status.replace(/_/g, " ");
  return <span className={`state-badge tone-${tone}`}>{label}</span>;
}

export default CaseStateBadge;