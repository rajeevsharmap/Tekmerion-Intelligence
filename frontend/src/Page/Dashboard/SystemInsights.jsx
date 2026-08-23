import { useState } from "react";
import "../../styles/SystemInsights.css";

const pipelineStages = [
  {
    id: "datastore",
    name: "DataStore",
    metric: "220 accounts indexed",
    detail:
      "Loads and indexes the 5 bank source CSVs (accounts, transactions, devices, geo events, beneficiaries) into in-memory lookups used by every downstream stage.",
  },
  {
    id: "detection",
    name: "Detection Layer",
    metric: "31 alerts, 4 typologies",
    detail:
      "Runs the 4 rule-based typology detectors (smurfing, reverse smurfing, money mule, account swap) over every account and returns triggered alerts with explainable, scored rulebooks.",
  },
  {
    id: "bundling",
    name: "Case Bundling",
    metric: "21 cases from 31 alerts",
    detail:
      "Groups same-account alerts within a 24-hour window into a case. No investigator tier or escalation is assigned here — a case only carries status: open at creation.",
  },
  {
    id: "evidence",
    name: "Evidence Layer",
    metric: "21 case-scoped evidence records",
    detail:
      "Generates one Evidence Store record per case — a network graph for structuring typologies, a transaction timeline for money mule, a behavioral transaction-vs-time view for account swap.",
  },
  {
    id: "agents",
    name: "Hypothesis & Contradiction",
    metric: "2 verdicts reconciled per case",
    detail:
      "Two LLM agents argue fraud vs. legitimate from the same evidence. A Contradiction Agent resolves the disagreement into a single confidence-scored verdict.",
  },
];

const insightFeed = [
  {
    id: 1,
    initials: "RS",
    typology: "Account Swap",
    excerpt:
      "The deviation ratio alone wasn't conclusive — what confirmed it was a SIM change 40 minutes before the first high-value transfer.",
    time: "2 days ago",
  },
  {
    id: 2,
    initials: "PK",
    typology: "Smurfing",
    excerpt:
      "Watch for beneficiaries reused across otherwise unrelated accounts — that's a stronger signal than transaction count alone.",
    time: "5 days ago",
  },
  {
    id: 3,
    initials: "RS",
    typology: "Money Mule",
    excerpt:
      "Legitimate-looking payroll timing was the disguise here. Cross-checked against the beneficiary's account age to confirm.",
    time: "1 week ago",
  },
];

const typologyOptions = [
  "Smurfing",
  "Reverse Smurfing",
  "Money Mule",
  "Account Swap",
];

function SystemInsights() {
  const [activeSubTab, setActiveSubTab] = useState("trace");
  const [activeStageId, setActiveStageId] = useState(pipelineStages[0].id);
  const [selectedTypology, setSelectedTypology] = useState(
    typologyOptions[0]
  );
  const [insightText, setInsightText] = useState("");
  const [tellText, setTellText] = useState("");

  const activeStage = pipelineStages.find(
    (stage) => stage.id === activeStageId
  );

  const handleSubmitInsight = (event) => {
    event.preventDefault();
    // Frontend-only for now — wiring to a Case Memory endpoint is a
    // later step once the human-in-the-loop stage exists in the backend.
    setInsightText("");
    setTellText("");
  };

  return (
    <div className="dashboard-content">
      <header className="dashboard-header">
        <div>
          <span className="dashboard-eyebrow">
            FINANCIAL CRIME OPERATIONS
          </span>

          <h2>System Insights</h2>
        </div>

        <div className="header-status">
          <span className="status-dot" />
          Investigation System Online
        </div>
      </header>

      <section className="summary-grid">
        <div className="summary-card">
          <div className="summary-heading">
            <span className="material-symbols-outlined">
              account_tree
            </span>
            <h3>Cases Processed</h3>
          </div>

          <strong className="summary-number">21</strong>
          <span className="summary-meta">Live pipeline runs</span>
        </div>

        <div className="summary-card">
          <div className="summary-heading">
            <span className="material-symbols-outlined">psychology</span>
            <h3>Insights Submitted</h3>
          </div>

          <strong className="summary-number">
            {insightFeed.length}
          </strong>
          <span className="summary-meta">By senior investigators</span>
        </div>

        <div className="summary-card">
          <div className="summary-heading">
            <span className="material-symbols-outlined">category</span>
            <h3>Typologies Covered</h3>
          </div>

          <strong className="summary-number">4/4</strong>
          <span className="summary-meta">Smurfing to account swap</span>
        </div>

        <div className="summary-card">
          <div className="summary-heading">
            <span className="material-symbols-outlined">bolt</span>
            <h3>Avg. Pipeline Trace Time</h3>
          </div>

          <strong className="summary-number">1.2s</strong>
          <span className="summary-meta">Detection to evidence</span>
        </div>
      </section>

      <section className="insights-panel">
        <div className="insight-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={activeSubTab === "trace"}
            className={`insight-tab ${activeSubTab === "trace" ? "active" : ""
              }`}
            onClick={() => setActiveSubTab("trace")}
          >
            Pipeline Trace
          </button>

          <button
            type="button"
            role="tab"
            aria-selected={activeSubTab === "insights"}
            className={`insight-tab ${activeSubTab === "insights" ? "active" : ""
              }`}
            onClick={() => setActiveSubTab("insights")}
          >
            Investigator Insights
          </button>
        </div>

        {activeSubTab === "trace" && (
          <div className="insight-panel-body">
            <p className="insight-panel-intro">
              A live trace of how a case moves through the pipeline —
              select a stage to see what it actually produced.
            </p>

            <div className="pipeline-trace">
              {pipelineStages.map((stage, index) => (
                <div className="pipeline-stage-wrapper" key={stage.id}>
                  <button
                    type="button"
                    className={`pipeline-stage ${activeStageId === stage.id ? "active" : ""
                      }`}
                    onClick={() => setActiveStageId(stage.id)}
                  >
                    <span className="pipeline-stage-name">
                      {stage.name}
                    </span>
                    <span className="pipeline-stage-metric">
                      {stage.metric}
                    </span>
                  </button>

                  {index < pipelineStages.length - 1 && (
                    <span className="pipeline-connector" />
                  )}
                </div>
              ))}
            </div>

            <div className="stage-detail-panel">
              <div className="stage-detail-heading">
                <span className="material-symbols-outlined">
                  network_intelligence
                </span>
                <h3>{activeStage.name}</h3>
              </div>

              <p>{activeStage.detail}</p>

              <div className="stage-trail">
                <span className="material-symbols-outlined">link</span>
                ACC000001 → ALERT-MONE-8EEE00D4 → CASE-5BA32240 →
                EVID-081D9EAF
              </div>
            </div>
          </div>
        )}

        {activeSubTab === "insights" && (
          <div className="insight-panel-body">
            <form className="insight-form" onSubmit={handleSubmitInsight}>
              <div className="insight-form-row">
                <span className="insight-case-chip">
                  CASE-5BA32240
                </span>

                <div className="typology-chip-selector">
                  {typologyOptions.map((option) => (
                    <button
                      type="button"
                      key={option}
                      className={`typology-chip ${selectedTypology === option ? "active" : ""
                        }`}
                      onClick={() => setSelectedTypology(option)}
                    >
                      {option}
                    </button>
                  ))}
                </div>
              </div>

              <textarea
                className="insight-textarea"
                placeholder="What actually gave this case away? How did you confirm the real culprit?"
                value={insightText}
                onChange={(event) => setInsightText(event.target.value)}
                rows={3}
              />

              <textarea
                className="insight-textarea"
                placeholder="What would you tell the detection rules?"
                value={tellText}
                onChange={(event) => setTellText(event.target.value)}
                rows={2}
              />

              <div className="insight-form-footer">
                <span className="advisory-badge">
                  Advisory insight — not automated retraining.
                </span>

                <button type="submit" className="submit-insight-button">
                  Submit Insight
                </button>
              </div>
            </form>

            <div className="insight-feed">
              <h3 className="insight-feed-heading">
                Institutional Memory
              </h3>

              {insightFeed.map((item) => (
                <article className="insight-card" key={item.id}>
                  <div className="insight-avatar">{item.initials}</div>

                  <div className="insight-card-body">
                    <div className="insight-card-topline">
                      <span className="insight-typology-tag">
                        {item.typology}
                      </span>

                      <span className="insight-time">{item.time}</span>
                    </div>

                    <p>{item.excerpt}</p>
                  </div>
                </article>
              ))}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

export default SystemInsights;