import { useEffect, useState } from "react";
import api, { ApiError } from "../services/api.js";

function QualityPill({ quality }) {
  if (!quality) return <span className="pill">unknown quality</span>;
  return <span className="pill">{quality} quality</span>;
}

export function CaseEvidence({ caseId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    // See hooks/useCases.js for why this fetch-on-mount pattern is
    // suppressed rather than restructured.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setError(null);
    api
      .getEvidence(caseId)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err : new ApiError(String(err), 0, null));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [caseId]);

  if (loading) {
    return (
      <div className="panel-status">
        <span className="material-symbols-outlined">progress_activity</span>
        <h3>Loading evidence...</h3>
      </div>
    );
  }

  if (error) {
    return (
      <div className="panel-status is-error">
        <span className="material-symbols-outlined">cloud_off</span>
        <h3>Could not load evidence</h3>
        <p>{error.message}</p>
      </div>
    );
  }

  const items = data?.evidence_items || [];
  const available = items.filter((i) => i.available);
  const missing = items.filter((i) => !i.available);

  return (
    <div>
      <p className="workspace-subline" style={{ marginBottom: 14 }}>
        Note: the backend's evidence endpoint currently returns the same evidence set for
        junior and senior roles — role-based scoping is applied on the Graph tab's network
        endpoint, not here. This view reflects the actual backend response as-is.
      </p>

      <div className="section-block">
        <h3>Observed Evidence ({available.length})</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>Evidence Type</th>
              <th>Source</th>
              <th>Source Record IDs</th>
              <th>Quality</th>
              <th>Supports</th>
            </tr>
          </thead>
          <tbody>
            {available.map((item) => (
              <tr key={item.evidence_id}>
                <td>{item.evidence_type.replace(/_/g, " ")}</td>
                <td>{item.source}</td>
                <td>{item.source_record_ids?.length ? item.source_record_ids.join(", ") : "—"}</td>
                <td><QualityPill quality={item.quality} /></td>
                <td>{item.supports?.join(", ") || "—"}</td>
              </tr>
            ))}
            {available.length === 0 && (
              <tr><td colSpan={5}>No observed evidence available.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="section-block">
        <h3>Missing / Unavailable Evidence ({missing.length})</h3>
        <table className="data-table">
          <thead>
            <tr>
              <th>Evidence Type</th>
              <th>Reason</th>
              <th>Severity</th>
            </tr>
          </thead>
          <tbody>
            {missing.map((item) => (
              <tr key={item.evidence_id} className="evidence-missing-row">
                <td>{item.evidence_type.replace(/_/g, " ")}</td>
                <td>{item.missing_reason?.reason?.replace(/_/g, " ") || "Not documented"}</td>
                <td>{item.missing_reason?.severity || "—"}</td>
              </tr>
            ))}
            {missing.length === 0 && (
              <tr><td colSpan={3}>No missing evidence recorded.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {data?.source_transactions?.length > 0 && (
        <div className="section-block">
          <h3>Source Transactions</h3>
          <div className="pill-list">
            {data.source_transactions.map((txnId) => (
              <span key={txnId} className="pill">{txnId}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default CaseEvidence;