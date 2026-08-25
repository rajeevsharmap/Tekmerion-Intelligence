import "../../styles/Suspected.css";
import "../../styles/Shared.css";
import { useCases } from "../../hooks/useCases.js";

const TYPOLOGY_LABEL = {
  smurfing: "Smurfing",
  reverse_smurfing: "Reverse Smurfing",
  money_mule: "Money Mule",
  account_swap: "Account Swap",
};

function tally(items, keyFn) {
  const counts = {};
  for (const item of items) {
    const key = keyFn(item) ?? "unknown";
    counts[key] = (counts[key] || 0) + 1;
  }
  return Object.entries(counts).sort((a, b) => b[1] - a[1]);
}

function BreakdownTable({ title, rows, total, formatLabel }) {
  return (
    <div className="section-block">
      <h3>{title}</h3>
      <table className="data-table">
        <thead>
          <tr>
            <th>Category</th>
            <th>Cases</th>
            <th>Share</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([key, count]) => (
            <tr key={key}>
              <td>{formatLabel ? formatLabel(key) : key.replace(/_/g, " ")}</td>
              <td>{count}</td>
              <td>{total ? `${((count / total) * 100).toFixed(1)}%` : "—"}</td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan={3}>No data available.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function SystemInsights() {
  const { cases, loading, error, reload } = useCases();

  const total = cases.length;
  const byTypology = tally(cases, (c) => c.typology);
  const byState = tally(cases, (c) => c.case_state);
  const byCompleteness = tally(cases, (c) => c.case_completeness_status);
  const byRecommendedAction = tally(cases, (c) => c.recommended_action);
  const sarGenerated = cases.filter((c) => c.sar_status).length;
  const escalatedCount = cases.filter((c) => c.case_state === "ESCALATED").length;
  const closedCount = cases.filter((c) => c.case_state === "CLOSED").length;
  const humanReviewCount = cases.filter((c) => c.case_state === "HUMAN_REVIEW").length;

  return (
    <div className="dashboard-content">
      <header className="dashboard-header">
        <div>
          <span className="dashboard-eyebrow">FINANCIAL CRIME OPERATIONS</span>
          <h2>System Insights</h2>
        </div>
      </header>

      <p className="workspace-subline" style={{ marginBottom: 18 }}>
        Aggregate statistics derived from the current case set returned by the backend
        (GET /cases). This is a live snapshot of what the backend has detected and
        recorded — not a modeled forecast or an externally sourced metric.
      </p>

      {loading && (
        <div className="panel-status">
          <span className="material-symbols-outlined">progress_activity</span>
          <h3>Loading system insights...</h3>
        </div>
      )}

      {!loading && error && (
        <div className="panel-status is-error">
          <span className="material-symbols-outlined">cloud_off</span>
          <h3>Could not load case data</h3>
          <p>{error.message}</p>
          <button type="button" onClick={reload}>Retry</button>
        </div>
      )}

      {!loading && !error && (
        <>
          <div className="info-grid">
            <div className="info-card">
              <h4>Total Cases</h4>
              <div className="info-value">{total}</div>
            </div>
            <div className="info-card">
              <h4>Awaiting Human Review</h4>
              <div className="info-value">{humanReviewCount}</div>
            </div>
            <div className="info-card">
              <h4>Escalated</h4>
              <div className="info-value">{escalatedCount}</div>
            </div>
            <div className="info-card">
              <h4>Closed</h4>
              <div className="info-value">{closedCount}</div>
            </div>
            <div className="info-card">
              <h4>SAR Outcomes Recorded</h4>
              <div className="info-value">{sarGenerated}</div>
              <div className="info-sub">of {total} total cases</div>
            </div>
          </div>

          <BreakdownTable
            title="Cases by Typology"
            rows={byTypology}
            total={total}
            formatLabel={(k) => TYPOLOGY_LABEL[k] || k}
          />

          <BreakdownTable
            title="Cases by Lifecycle State"
            rows={byState}
            total={total}
          />

          <BreakdownTable
            title="Cases by Completeness Status"
            rows={byCompleteness}
            total={total}
          />

          <BreakdownTable
            title="Cases by Recommended Action"
            rows={byRecommendedAction}
            total={total}
          />
        </>
      )}
    </div>
  );
}

export default SystemInsights;