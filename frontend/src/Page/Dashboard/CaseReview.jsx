import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import "../../styles/CaseReview.css";
import CaseGraph from "./CaseGraph.jsx";
const SUB_TABS = [
  { key: "overview", label: "Overview" },
  { key: "graph", label: "Graph" },
  { key: "evidence", label: "Evidence" },
  { key: "sar", label: "SAR/Audit" },
];

// Placeholder mock data — replace with a real fetch to the backend
// once a `/cases/:id` endpoint exists (main.py currently only
// exposes GET /). Field names mirror the shapes already produced
// by pipeline_output/suspected_alerts.json and
// pipeline_output/evidence/*.json.
const mockCase = {
  alertCount: 5,
  createdAt: "24 Feb 2025, 09:26",
  alerts: [
    {
      alertId: "ALERT-MONE-5D8DE1E0",
      typology: "Money Mule",
      severity: "high",
      riskScore: 92,
      rules: ["MM-001", "MM-003"],
      signals: [{ label: "unique_counterparties", value: "5 > 4" }],
      explanation:
        "This case follows a classic money mule pattern: a high volume of small inbound transfers followed by a single large outbound transfer within minutes.",
    },
  ],
  entity: {
    primaryAccount: "ACC000001",
    status: "Active",
  },
};

const mockNode = {
  transactionId: "TXN001576",
  timestamp: "2025-02-24 08:24",
  direction: "in",
  amount: "₹1,06,970.90",
  channel: "IMPS",
  deviceId: "DEV000333",
  geoId: "GEO000748",
};

const mockEvidenceSummary = {
  totalInbound: "₹2,66,539",
  outboundRatio: "0.90",
  medianGap: "45m",
};

const mockEvidenceRows = [
  { amount: "+₹61,502.69", channel: "net_banking", deviceId: "DEV000198", geoId: "GEO000443" },
  { amount: "+₹1,06,970.90", channel: "IMPS", deviceId: "DEV000333", geoId: "GEO000748" },
  { amount: "-₹2,39,885.18", channel: "net_banking", deviceId: "DEV000002", geoId: "GEO000003" },
];

const mockHypotheses = {
  fraudulent: {
    confidence: 84,
    points: [
      "High pass-through velocity (12m gap).",
      "Single device ID across multiple counterparties.",
      "Outbound ratio approaches 1.0 (0.90).",
    ],
  },
  legitimate: {
    confidence: 16,
    points: [
      "Same geographic region as usual profile.",
      "KYC verification passed recently.",
    ],
  },
};

const mockResolution = {
  favored: "Fraudulent Hypothesis Favored (84% Confidence)",
  decidingFactor: "Rapid pass-through velocity exceeding 0.90 ratio.",
};

const mockAuditTrail = [
  { time: "09:26", label: "Account Trace Completed", done: true },
  { time: "09:27", label: "Typology Assigned (Money Mule)", done: true },
  { time: "09:30", label: "Pending Analyst Review", done: false },
];

function CaseReview() {
  const { caseId } = useParams();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState("overview");

  return (
    <div className="case-review">
      <button type="button" className="back-link" onClick={() => navigate(-1)}>
        <span className="material-symbols-outlined">arrow_back</span>
        Back to list
      </button>

      <header className="case-review-header">
        <div>
          <div className="case-review-titlerow">
            <span className="case-review-id">{caseId}</span>
            <span className="case-review-badge">
              {mockCase.alertCount} Bundled Alerts
            </span>
          </div>
          <p className="case-review-created">
            Case created: {mockCase.createdAt}
          </p>
        </div>

        <button type="button" className="start-investigation-button">
          <span className="material-symbols-outlined">play_arrow</span>
          Start Investigation
        </button>
      </header>

      <nav className="case-review-tabs" aria-label="Case review sections">
        {SUB_TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={`case-tab ${activeTab === tab.key ? "active" : ""}`}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {activeTab === "overview" && (
        <section className="case-review-body overview-grid">
          <div className="overview-alerts">
            {mockCase.alerts.map((alert) => (
              <div key={alert.alertId} className="alert-card">
                <div className="alert-card-top">
                  <h3>{alert.alertId}</h3>
                  <span className={`severity-badge ${alert.severity}`}>
                    <span className="material-symbols-outlined">warning</span>
                    {alert.severity === "high"
                      ? "High"
                      : alert.severity === "medium"
                        ? "Medium"
                        : "Low"}{" "}
                    Severity
                  </span>
                </div>

                <div className="alert-card-fields">
                  <div>
                    <p className="field-label">Typology</p>
                    <p className="field-value">{alert.typology}</p>
                  </div>
                  <div>
                    <p className="field-label">Risk Score</p>
                    <p className="field-value risk">{alert.riskScore}/100</p>
                  </div>
                  <div>
                    <p className="field-label">Rules</p>
                    <p className="field-value mono">{alert.rules.join(", ")}</p>
                  </div>
                  <div>
                    <p className="field-label">Signals</p>
                    <p className="field-value mono">
                      {alert.signals
                        .map((s) => `${s.label}: ${s.value}`)
                        .join(", ")}
                    </p>
                  </div>
                </div>

                <div className="alert-explanation">
                  <span className="material-symbols-outlined">info</span>
                  <p>{alert.explanation}</p>
                </div>
              </div>
            ))}
          </div>

          <aside className="entity-summary">
            <h4>Entity Summary</h4>
            <ul>
              <li>
                <span>Primary Account</span>
                <span className="mono">{mockCase.entity.primaryAccount}</span>
              </li>
              <li>
                <span>Status</span>
                <span className="status-active">{mockCase.entity.status}</span>
              </li>
            </ul>
          </aside>
        </section>
      )}

      {activeTab === "graph" && <CaseGraph caseId={caseId} />}

      {activeTab === "evidence" && (
        <section className="case-review-body">
          <div className="evidence-summary">
            <div>
              <p className="field-label">Total Inbound</p>
              <p className="summary-figure">{mockEvidenceSummary.totalInbound}</p>
            </div>
            <div>
              <p className="field-label">Outbound Ratio</p>
              <p className="summary-figure">{mockEvidenceSummary.outboundRatio}</p>
            </div>
            <div>
              <p className="field-label">Median Gap</p>
              <p className="summary-figure">{mockEvidenceSummary.medianGap}</p>
            </div>
          </div>

          <div className="evidence-table-wrap">
            <table className="evidence-table">
              <thead>
                <tr>
                  <th>Transaction Info</th>
                  <th>Device Data</th>
                  <th>Geolocation</th>
                </tr>
              </thead>
              <tbody>
                {mockEvidenceRows.map((row, i) => (
                  <tr key={i}>
                    <td>
                      <div className="evidence-row-txn">
                        <span
                          className={
                            row.amount.startsWith("+") ? "amount-in" : "amount-out"
                          }
                        >
                          {row.amount}
                        </span>
                        <span className="mono muted">{row.channel}</span>
                      </div>
                    </td>
                    <td className="mono secondary">{row.deviceId}</td>
                    <td className="mono">{row.geoId}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {activeTab === "sar" && (
        <section className="case-review-body sar-audit">
          <div className="hypothesis-grid">
            <div className="hypothesis-card fraudulent">
              <span className="confidence-pill">
                {mockHypotheses.fraudulent.confidence}% Confidence
              </span>
              <h4>
                <span className="material-symbols-outlined">gavel</span>
                Fraudulent / Compromised
              </h4>
              <ul>
                {mockHypotheses.fraudulent.points.map((point, i) => (
                  <li key={i}>
                    <span className="material-symbols-outlined">check_circle</span>
                    {point}
                  </li>
                ))}
              </ul>
            </div>

            <div className="hypothesis-card legitimate">
              <span className="confidence-pill muted">
                {mockHypotheses.legitimate.confidence}% Confidence
              </span>
              <h4>
                <span className="material-symbols-outlined">verified_user</span>
                Legitimate Activity
              </h4>
              <ul>
                {mockHypotheses.legitimate.points.map((point, i) => (
                  <li key={i}>
                    <span className="material-symbols-outlined">check_circle</span>
                    {point}
                  </li>
                ))}
              </ul>
            </div>
          </div>

          <div className="resolution-banner">
            <h4>Resolution</h4>
            <p>
              {mockResolution.favored}. Deciding Factor:{" "}
              {mockResolution.decidingFactor}
            </p>
          </div>

          <div className="audit-trail-card">
            <h4>Audit Trail</h4>
            <div className="audit-trail-list">
              {mockAuditTrail.map((step, i) => (
                <div key={i} className="audit-trail-step">
                  <span className={`trail-dot ${step.done ? "done" : "pending"}`} />
                  <p className="trail-time">{step.time}</p>
                  <p className={`trail-label ${step.done ? "" : "pending"}`}>
                    {step.label}
                  </p>
                </div>
              ))}
            </div>
          </div>

          <button type="button" className="generate-sar-button">
            <span className="material-symbols-outlined">description</span>
            Generate SAR Document
          </button>
        </section>
      )}
    </div>
  );
}

export default CaseReview;