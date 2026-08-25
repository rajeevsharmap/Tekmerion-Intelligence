import { CompletenessBadge } from "./StatusBadge.jsx";

function fmtPercent(value) {
  return typeof value === "number" ? `${value.toFixed(1)}%` : "Not available";
}

function fmtConfidence(value) {
  if (typeof value === "number") return `${(value * 100).toFixed(0)}%`;
  if (typeof value === "string") return value;
  return "Not available";
}

export function CaseOverview({ caseSummary }) {
  const completeness = caseSummary.completeness;
  const caseCompleteness = caseSummary.case_completeness;
  const authority = caseSummary.authority;
  const nba = caseSummary.next_best_action;
  const jurisdiction = caseSummary.jurisdiction;

  return (
    <div>
      <div className="info-grid">
        <div className="info-card">
          <h4>Detection Basis</h4>
          <div className="info-value">{caseSummary.alert_trigger || "Not available"}</div>
          <div className="info-sub">Confidence: {fmtConfidence(caseSummary.confidence)}</div>
        </div>

        <div className="info-card">
          <h4>Evidence Completeness</h4>
          <div className="info-value">{fmtPercent(completeness?.weighted_score)}</div>
          <div className="info-sub">
            {completeness?.available_count ?? "—"} of {completeness?.required_count ?? "—"} required items available
          </div>
        </div>

        <div className="info-card">
          <h4>Case Completeness</h4>
          <div className="info-value">
            <CompletenessBadge status={caseCompleteness?.status} />
          </div>
          <div className="info-sub">Score: {fmtPercent(caseCompleteness?.score)}</div>
        </div>

        <div className="info-card">
          <h4>Required Authority</h4>
          <div className="info-value">{nba?.required_authority || authority?.authority_tier || "Not available"}</div>
          <div className="info-sub">
            {nba?.requires_human_review ? "Requires human review" : "No mandatory human review flagged"}
          </div>
        </div>

        <div className="info-card">
          <h4>Jurisdiction</h4>
          <div className="info-value">{jurisdiction?.jurisdiction || "Not resolved"}</div>
          <div className="info-sub">
            Base: {jurisdiction?.base_jurisdiction || "—"} · Confidence: {jurisdiction?.confidence || "—"}
          </div>
        </div>

        <div className="info-card">
          <h4>Recommended Action</h4>
          <div className="info-value">
            {nba?.recommended_action ? nba.recommended_action.replace(/_/g, " ") : "Not available"}
          </div>
          <div className="info-sub">
            {typeof nba?.confidence === "number" ? `Confidence ${(nba.confidence * 100).toFixed(0)}%` : ""}
          </div>
        </div>
      </div>

      {caseCompleteness?.missing_evidence?.length > 0 && (
        <div className="section-block">
          <h3>Missing Evidence</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Evidence Type</th>
                <th>Reason</th>
                <th>Severity</th>
              </tr>
            </thead>
            <tbody>
              {caseCompleteness.missing_evidence.map((m) => (
                <tr key={m.evidence_type} className="evidence-missing-row">
                  <td>{m.evidence_type.replace(/_/g, " ")}</td>
                  <td>{m.reason.replace(/_/g, " ")}</td>
                  <td>{m.severity}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {nba?.reason_codes?.length > 0 && (
        <div className="section-block">
          <h3>Reason Codes</h3>
          <div className="pill-list">
            {nba.reason_codes.map((code) => (
              <span key={code} className="pill">{code}</span>
            ))}
          </div>
        </div>
      )}

      {caseSummary.sar_status && (
        <div className="section-block">
          <h3>SAR Status</h3>
          <p className="workspace-subline">{caseSummary.sar_status.replace(/_/g, " ")}</p>
        </div>
      )}
    </div>
  );
}

export default CaseOverview;