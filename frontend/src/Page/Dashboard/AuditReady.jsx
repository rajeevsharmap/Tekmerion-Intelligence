import "../../styles/AuditReady.css";

const auditReadyCases = [
  {
    id: "CASE-2023-8902A",
    completeness: 98,
    title: "Nexus Capital Transfers",
    chain: [
      "ACC000001",
      "ALERT-MONE-8EEE00D4",
      "CASE-5BA32240",
      "EVID-081D9EAF",
    ],
  },
  {
    id: "CASE-2023-8914B",
    completeness: 100,
    title: "Meridian Trade Finance",
    chain: [
      "CORP-89211",
      "ALERT-SANC-1A4C",
      "CASE-9F12BBA",
      "EVID-77C1A2",
    ],
  },
  {
    id: "CASE-2023-8927C",
    completeness: 91,
    title: "Coastal Freight Beneficiary Cluster",
    chain: [
      "ACC000184",
      "ALERT-SMUR-2C9A",
      "CASE-7712FE1",
      "EVID-4B3A90",
    ],
  },
];

function AuditReady() {
  return (
    <div className="dashboard-content">
      <header className="dashboard-header">
        <div>
          <span className="dashboard-eyebrow">
            FINANCIAL CRIME OPERATIONS
          </span>

          <h2>Audit-Ready Cases</h2>
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
              assignment_turned_in
            </span>
            <h3>Audit-Ready Cases</h3>
          </div>

          <strong className="summary-number">14</strong>
          <span className="summary-meta">Verified this month</span>
        </div>

        <div className="summary-card">
          <div className="summary-heading">
            <span className="material-symbols-outlined">fact_check</span>
            <h3>Avg. Evidence Completeness</h3>
          </div>

          <strong className="summary-number">94%</strong>
          <span className="summary-meta">Across active cases</span>
        </div>

        <div className="summary-card">
          <div className="summary-heading">
            <span className="material-symbols-outlined">link</span>
            <h3>Full Trail Verified</h3>
          </div>

          <strong className="summary-number">11</strong>
          <span className="summary-meta">Unbroken chain</span>
        </div>

        <div className="summary-card warning">
          <div className="summary-heading">
            <span className="material-symbols-outlined">
              report_problem
            </span>
            <h3>Evidence Gaps Flagged</h3>
          </div>

          <strong className="summary-number">3</strong>
          <span className="summary-meta">Needs more evidence</span>
        </div>
      </section>

      <section className="cases-panel">
        <div className="case-list">
          {auditReadyCases.map((item) => (
            <article key={item.id} className="case-row audit-row">
              <div className="case-information audit-case-information">
                <div className="case-topline">
                  <span className="case-id">{item.id}</span>

                  <span className="audit-badge">
                    <span className="material-symbols-outlined">
                      check_circle
                    </span>
                    Audit-Ready
                  </span>

                  <span className="completeness-chip">
                    {item.completeness}% Complete
                  </span>
                </div>

                <h3>{item.title}</h3>

                <div className="evidence-chain-strip">
                  {item.chain.map((node, index) => (
                    <span key={node} className="chain-node-group">
                      <span className="chain-node">{node}</span>

                      {index < item.chain.length - 1 && (
                        <span className="chain-arrow">→</span>
                      )}
                    </span>
                  ))}
                </div>

                <div className="audit-row-footer">
                  <div className="completeness-bar-track">
                    <div
                      className="completeness-bar-fill"
                      style={{ width: `${item.completeness}%` }}
                    />
                  </div>

                  <button type="button" className="view-trail-button">
                    View Audit Trail
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}

export default AuditReady;