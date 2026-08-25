import { useState } from "react";

const REASON_REQUIRED_ACTIONS = new Set([
  "ESCALATE_TO_SENIOR",
  "RESTRICT_ACCOUNT",
  "BLOCK_TRANSACTION",
  "FILE_SAR",
  "CLOSE_CASE",
]);

export function ActionDialog({ action, label, recommendedAction, onCancel, onConfirm, submitting }) {
  const [reason, setReason] = useState("");
  const [overrideReason, setOverrideReason] = useState("");

  const differsFromRecommendation = recommendedAction && action !== recommendedAction;
  const reasonRequired = REASON_REQUIRED_ACTIONS.has(action);

  const canSubmit =
    (!reasonRequired || reason.trim().length > 0) &&
    (!differsFromRecommendation || overrideReason.trim().length > 0);

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <h3>{label}</h3>
        <p>
          {action === "ESCALATE_TO_SENIOR"
            ? "This case will move to the Escalated queue for senior review."
            : "This action will be submitted to the backend action pipeline and cannot be undone from this screen."}
        </p>

        <textarea
          placeholder={reasonRequired ? "Reason (required)" : "Reason (optional)"}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />

        {differsFromRecommendation && (
          <textarea
            placeholder="Override reason (required — this differs from the system's recommended action)"
            value={overrideReason}
            onChange={(e) => setOverrideReason(e.target.value)}
          />
        )}

        <div className="modal-actions">
          <button type="button" className="action-button" onClick={onCancel} disabled={submitting}>
            Cancel
          </button>
          <button
            type="button"
            className="action-button primary"
            disabled={!canSubmit || submitting}
            onClick={() => onConfirm({ reason: reason.trim(), overrideReason: overrideReason.trim() || undefined })}
          >
            {submitting ? "Submitting..." : "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default ActionDialog;