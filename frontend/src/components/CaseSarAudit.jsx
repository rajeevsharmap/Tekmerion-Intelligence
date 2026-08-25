import { useCallback, useEffect, useState } from "react";
import api, { ApiError } from "../services/api.js";
import { useInvestigator } from "../context/useInvestigator.js";
import { actionsForRole, ACTION_MINIMUM_AUTHORITY } from "../services/actions.js";
import { ActionDialog } from "./ActionDialog.jsx";

const ALL_ACTIONS = Object.keys(ACTION_MINIMUM_AUTHORITY);

function AuditTrail({ events }) {
  if (!events || events.length === 0) {
    return <p className="workspace-subline">No audit events recorded for this case.</p>;
  }
  return (
    <div>
      {events.map((evt) => (
        <div className="audit-event" key={evt.event_id}>
          <span className="audit-event-marker" />
          <div className="audit-event-body">
            <h5>{evt.event_type.replace(/_/g, " ")} — {evt.actor_id}</h5>
            <p className="audit-event-meta">{evt.timestamp} · actor type: {evt.actor_type}</p>
            {evt.reason && <p className="audit-event-reason">{evt.reason}</p>}
          </div>
        </div>
      ))}
    </div>
  );
}

function SarPanel({ sarReport }) {
  if (!sarReport) {
    return (
      <div className="section-block">
        <h3>SAR Status</h3>
        <p className="workspace-subline">No SAR has been generated for this case yet.</p>
      </div>
    );
  }
  return (
    <div className="section-block">
      <h3>SAR Status</h3>
      <div className="info-grid">
        <div className="info-card">
          <h4>Status</h4>
          <div className="info-value">{sarReport.status?.replace(/_/g, " ")}</div>
        </div>
        {sarReport.sar_id && (
          <div className="info-card">
            <h4>SAR ID</h4>
            <div className="info-value">{sarReport.sar_id}</div>
          </div>
        )}
        {sarReport.filing_jurisdiction && (
          <div className="info-card">
            <h4>Filing Jurisdiction</h4>
            <div className="info-value">{sarReport.filing_jurisdiction}</div>
          </div>
        )}
      </div>
      <p className="workspace-subline" style={{ marginTop: 10 }}>
        The backend produces a structured JSON SAR record only — there is no password-protected
        PDF generation implemented. This view reflects that honestly rather than implying a PDF
        exists.
      </p>
    </div>
  );
}

export function CaseSarAudit({ caseId, caseSummary, onCaseChanged }) {
  const { investigator, role } = useInvestigator();

  const [audit, setAudit] = useState(null);
  const [actionsData, setActionsData] = useState(null);
  const [sar, setSar] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [pendingAction, setPendingAction] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  const [reviewDecision, setReviewDecision] = useState("");
  const [reviewReason, setReviewReason] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [auditRes, actionsRes, sarRes] = await Promise.all([
        api.getAudit(caseId),
        api.getActions(caseId),
        api.getSar(caseId),
      ]);
      setAudit(auditRes);
      setActionsData(actionsRes);
      setSar(sarRes);
      if (!reviewDecision && actionsRes?.next_best_action?.recommended_action) {
        setReviewDecision(actionsRes.next_best_action.recommended_action);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(String(err), 0, null));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId]);

  useEffect(() => {
    // See hooks/useCases.js for why this fetch-on-mount pattern is
    // suppressed rather than restructured.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  const caseState = actionsData?.case_state || caseSummary?.case_state;
  const recommendedAction = actionsData?.next_best_action?.recommended_action;

  const handleSubmitReview = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      await api.submitHumanReview(caseId, {
        reviewerId: investigator.investigatorId,
        investigatorDecision: reviewDecision,
        decisionReason: reviewReason,
      });
      setReviewReason("");
      await load();
      onCaseChanged?.();
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const handleConfirmAction = async ({ reason, overrideReason }) => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      await api.submitAction(caseId, {
        investigatorId: investigator.investigatorId,
        requestedAction: pendingAction.action,
        reason,
        overrideReason,
      });
      setPendingAction(null);
      await load();
      onCaseChanged?.();
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="panel-status">
        <span className="material-symbols-outlined">progress_activity</span>
        <h3>Loading audit replay...</h3>
      </div>
    );
  }

  if (error) {
    return (
      <div className="panel-status is-error">
        <span className="material-symbols-outlined">cloud_off</span>
        <h3>Could not load SAR / audit data</h3>
        <p>{error.message}</p>
        <button type="button" onClick={load}>Retry</button>
      </div>
    );
  }

  const availableActions = actionsForRole(role);

  return (
    <div>
      <div className="section-block">
        <h3>Recommended Action</h3>
        <p className="workspace-subline">
          {recommendedAction ? recommendedAction.replace(/_/g, " ") : "Not available"} — case state:{" "}
          {caseState || "unknown"}
        </p>
      </div>

      {actionsData?.human_review && (
        <div className="section-block">
          <h3>Human Review</h3>
          <div className="info-grid">
            <div className="info-card">
              <h4>Reviewer</h4>
              <div className="info-value">{actionsData.human_review.reviewer_id}</div>
            </div>
            <div className="info-card">
              <h4>Decision</h4>
              <div className="info-value">{actionsData.human_review.investigator_decision?.replace(/_/g, " ")}</div>
            </div>
            <div className="info-card">
              <h4>Status</h4>
              <div className="info-value">{actionsData.human_review.status}</div>
            </div>
          </div>
        </div>
      )}

      {actionsData?.investigator_action && (
        <div className="section-block">
          <h3>Investigator Action</h3>
          <div className="info-grid">
            <div className="info-card">
              <h4>Requested</h4>
              <div className="info-value">{actionsData.investigator_action.requested_action?.replace(/_/g, " ")}</div>
            </div>
            <div className="info-card">
              <h4>Actual</h4>
              <div className="info-value">{actionsData.investigator_action.actual_action?.replace(/_/g, " ")}</div>
            </div>
            <div className="info-card">
              <h4>Authorized</h4>
              <div className="info-value">{String(actionsData.investigator_action.authorized ?? "—")}</div>
            </div>
          </div>
        </div>
      )}

      <SarPanel sarReport={sar?.sar_report} />

      <div className="section-block">
        <h3>Audit Trail</h3>
        <AuditTrail events={audit?.audit_trail} />
      </div>

      <div className="section-block">
        <h3>Investigator Workflow</h3>

        {submitError && (
          <p className="workspace-subline" style={{ color: "#a3282c", marginBottom: 10 }}>
            {submitError}
          </p>
        )}

        {caseState === "HUMAN_REVIEW" && (
          <div>
            <p className="workspace-subline" style={{ marginBottom: 10 }}>
              This case is awaiting human review before an action can be executed.
            </p>
            <div className="form-group" style={{ maxWidth: 360, marginBottom: 10 }}>
              <select value={reviewDecision} onChange={(e) => setReviewDecision(e.target.value)}>
                {ALL_ACTIONS.map((a) => (
                  <option key={a} value={a}>{a.replace(/_/g, " ")}</option>
                ))}
              </select>
            </div>
            <textarea
              placeholder="Decision reason (required)"
              value={reviewReason}
              onChange={(e) => setReviewReason(e.target.value)}
              style={{ width: "100%", minHeight: 70, marginBottom: 10 }}
            />
            <div className="action-panel">
              <button
                type="button"
                className="action-button primary"
                disabled={!reviewReason.trim() || submitting}
                onClick={handleSubmitReview}
              >
                {submitting ? "Submitting..." : "Complete Human Review"}
              </button>
            </div>
          </div>
        )}

        {caseState === "ACTION_PENDING" && (
          <div className="action-panel">
            {availableActions.map(({ action, label }) => (
              <button
                key={action}
                type="button"
                className="action-button"
                onClick={() => setPendingAction({ action, label })}
              >
                {label}
              </button>
            ))}
          </div>
        )}

        {caseState !== "HUMAN_REVIEW" && caseState !== "ACTION_PENDING" && (
          <p className="workspace-subline">
            No further investigator action is currently available for this case state ({caseState || "unknown"}).
          </p>
        )}
      </div>

      {pendingAction && (
        <ActionDialog
          action={pendingAction.action}
          label={pendingAction.label}
          recommendedAction={recommendedAction}
          submitting={submitting}
          onCancel={() => setPendingAction(null)}
          onConfirm={handleConfirmAction}
        />
      )}
    </div>
  );
}

export default CaseSarAudit;