import "../../styles/Escalated.css";
import { useNavigate } from "react-router-dom";
const escalatedCases = [
  {
    id: "CASE-992-A",
    status: "pending",
    age: "14 mins ago",
    escalatedBy: "Agent Sarah Jenkins",
    seniorReviewRequired: true,
    title: "High-Value Account Takeover Cluster",
    typology: "Money Mule",
    verdict: {
      label: "Fraud (82%)",
      agreement: "contested",
    },
  },
  {
    id: "CASE-884-B",
    status: "pending",
    age: "1 hour ago",
    escalatedBy: "Agent Marcus Chen",
    seniorReviewRequired: false,
    title: "Unusual Cross-Border Velocity Anomaly",
    typology: "Smurfing",
    verdict: {
      label: "Suspicious (94%)",
      agreement: "agreed",
    },
  },
  {
    id: "CASE-771-C",
    status: "actioned",
    age: "3 hours ago",
    escalatedBy: "Agent Elena Rodriguez",
    seniorReviewRequired: false,
    title: "Synthetic Identity Application",
    typology: "ID Fraud",
    verdict: {
      label: "Clear (12%)",
      agreement: "agreed",
    },
  },
];

function Escalated() {
  const navigate = useNavigate();
  return (
    <div className="dashboard-content">
      <header className="dashboard-header">
        <div>
          <span className="dashboard-eyebrow">
            FINANCIAL CRIME OPERATIONS
          </span>

          <h2>Escalated Cases</h2>
        </div>

        <div className="header-status">
          <span className="status-dot" />
          Investigation System Online
        </div>
      </header>

      <section className="summary-grid">
        <div className="summary-card high-risk">
          <div className="summary-heading">
            <span className="material-symbols-outlined">priority_high</span>
            <h3>Awaiting Decision</h3>
          </div>

          <strong className="summary-number">14</strong>
          <span className="summary-meta">Critical priority</span>
        </div>

        <div className="summary-card">
          <div className="summary-heading">
            <span className="material-symbols-outlined">trending_up</span>
            <h3>Escalated Today</h3>
          </div>

          <strong className="summary-number">28</strong>
          <span className="summary-meta increase">
            <span className="material-symbols-outlined">arrow_upward</span>
            +12% from yesterday
          </span>
        </div>

        <div className="summary-card">
          <div className="summary-heading">
            <span className="material-symbols-outlined">schedule</span>
            <h3>Avg. Resolution</h3>
          </div>

          <strong className="summary-number">4.2h</strong>
          <span className="summary-meta">Target &lt; 6h</span>
        </div>

        <div className="summary-card warning">
          <div className="summary-heading">
            <span className="material-symbols-outlined">rule</span>
            <h3>Overridden</h3>
          </div>

          <strong className="summary-number">3</strong>
          <span className="summary-meta">Contradiction Agent conflict</span>
        </div>
      </section>

      <section className="cases-panel">
        <div className="case-list">
          {escalatedCases.map((item) => {
            const isActioned = item.status === "actioned";

            return (
              <article
                key={item.id}
                className={`case-row escalated-row ${isActioned ? "actioned" : ""
                  }`}
              >
                <div className="case-information">
                  <div className="case-topline">
                    <span className="case-id">{item.id}</span>

                    <span
                      className={`escalation-badge ${isActioned ? "actioned" : "pending"
                        }`}
                    >
                      {isActioned ? "Actioned" : "Pending Decision"}
                    </span>

                    <span className="case-age">
                      <span className="material-symbols-outlined">
                        schedule
                      </span>
                      {item.age}
                    </span>
                  </div>

                  <p className="case-provenance">
                    Escalated by {item.escalatedBy}
                    {item.seniorReviewRequired &&
                      " · Senior Review Required"}
                  </p>

                  <h3>{item.title}</h3>

                  <div className="case-tags">
                    <span className="case-tag">
                      <span className="material-symbols-outlined">
                        category
                      </span>
                      Typology: {item.typology}
                    </span>

                    <span className="verdict-chip">
                      <span
                        className={`verdict-dot ${item.verdict.agreement}`}
                      />
                      Agent verdict: {item.verdict.label}
                    </span>
                  </div>
                </div>

                <div className="case-actions">
                  <button
                    type="button"
                    className="review-button"
                    disabled={isActioned}
                    onClick={() => navigate(`/escalated/${item.id}`)}
                  >
                    {isActioned ? "View Record" : "Review & Decide"}
                  </button>

                  {!isActioned && (
                    <button
                      type="button"
                      className="more-button"
                      aria-label={`More options for ${item.id}`}
                    >
                      <span className="material-symbols-outlined">
                        more_vert
                      </span>
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}

export default Escalated;