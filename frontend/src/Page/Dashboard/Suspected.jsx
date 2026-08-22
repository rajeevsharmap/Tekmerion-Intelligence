import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import "../../styles/Suspected.css";

const cases = [
  {
    id: "CASE-8992-A",
    risk: "High",
    age: "12 mins ago",
    title: "Unusual Cross-Border Transfer",
    typology: "Money Mule",
    alerts: 5,
  },
  {
    id: "CASE-8991-B",
    risk: "Medium",
    age: "1 hour ago",
    title: "Account Takeover Suspected",
    typology: "Account Swap",
    alerts: 2,
  },
  {
    id: "CASE-8990-C",
    risk: "High",
    age: "3 hours ago",
    title: "Structuring / Smurfing",
    typology: "Smurfing",
    alerts: 18,
  },
  {
    id: "CASE-8989-D",
    risk: "Low",
    age: "5 hours ago",
    title: "Velocity Anomaly",
    typology: "Reverse Smurfing",
    alerts: 3,
  },
];

function Suspected() {
  const navigate = useNavigate();

  const [search, setSearch] = useState("");
  const [riskFilter, setRiskFilter] = useState("all");
  const [typologyFilter, setTypologyFilter] = useState("all");

  const filteredCases = useMemo(() => {
    return cases.filter((item) => {
      const searchValue = search.toLowerCase();

      const matchesSearch =
        item.id.toLowerCase().includes(searchValue) ||
        item.title.toLowerCase().includes(searchValue) ||
        item.typology.toLowerCase().includes(searchValue);

      const matchesRisk =
        riskFilter === "all" ||
        item.risk.toLowerCase() === riskFilter;

      const matchesTypology =
        typologyFilter === "all" ||
        item.typology.toLowerCase().replaceAll(" ", "_") ===
        typologyFilter;

      return matchesSearch && matchesRisk && matchesTypology;
    });
  }, [search, riskFilter, typologyFilter]);

  return (
    <div className="dashboard-content">
      <header className="dashboard-header">
        <div>
          <span className="dashboard-eyebrow">
            FINANCIAL CRIME OPERATIONS
          </span>

          <h2>Dashboard Overview</h2>
        </div>

        <div className="header-status">
          <span className="status-dot" />
          Investigation System Online
        </div>
      </header>

      <section className="summary-grid">
        <div className="summary-card high-risk">
          <div className="summary-heading">
            <span className="material-symbols-outlined">warning</span>
            <h3>High Risk Alerts</h3>
          </div>

          <p>Immediate attention required.</p>

          <div className="summary-value-row">
            <strong>12</strong>
            <button type="button">View</button>
          </div>
        </div>

        <div className="summary-card">
          <div className="summary-heading">
            <span className="material-symbols-outlined">
              notifications_active
            </span>
            <h3>Suspected Alerts</h3>
          </div>

          <strong className="summary-number">148</strong>

          <span className="summary-meta increase">
            <span className="material-symbols-outlined">
              arrow_upward
            </span>
            +12%
          </span>
        </div>

        <div className="summary-card">
          <div className="summary-heading">
            <span className="material-symbols-outlined">
              account_tree
            </span>
            <h3>Bundled Cases</h3>
          </div>

          <strong className="summary-number">34</strong>

          <span className="summary-meta">Active</span>
        </div>

        <div className="summary-card">
          <div className="summary-heading">
            <span className="material-symbols-outlined">
              check_circle
            </span>
            <h3>Solved Cases</h3>
          </div>

          <strong className="summary-number">89</strong>

          <span className="summary-meta">This Month</span>
        </div>
      </section>

      <section className="cases-panel">
        <div className="cases-toolbar">
          <div>
            <h2>Active Cases</h2>

            <p>
              Review and manage bundled alerts.
            </p>
          </div>

          <div className="case-controls">
            <div className="search-box">
              <span className="material-symbols-outlined">
                search
              </span>

              <input
                type="text"
                placeholder="Search cases..."
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>

            <select
              value={riskFilter}
              onChange={(event) => setRiskFilter(event.target.value)}
            >
              <option value="all">All Risk Levels</option>
              <option value="high">High Risk</option>
              <option value="medium">Medium Risk</option>
              <option value="low">Low Risk</option>
            </select>

            <select
              value={typologyFilter}
              onChange={(event) =>
                setTypologyFilter(event.target.value)
              }
            >
              <option value="all">All Typologies</option>
              <option value="reverse_smurfing">Reverse Smurfing</option>
              <option value="smurfing">Smurfing</option>
              <option value="money_mule">Money Mule</option>
              <option value="account_swap">Account Swap</option>
            </select>
          </div>
        </div>

        <div className="case-list">
          {filteredCases.map((item) => (
            <article key={item.id} className="case-row">
              <div className="case-information">
                <div className="case-topline">
                  <span className="case-id">{item.id}</span>

                  <span
                    className={`risk-badge ${item.risk.toLowerCase()}`}
                  >
                    <span />
                    {item.risk} Risk
                  </span>

                  <span className="case-age">
                    <span className="material-symbols-outlined">
                      schedule
                    </span>

                    {item.age}
                  </span>
                </div>

                <h3>{item.title}</h3>

                <div className="case-tags">
                  <span className="case-tag">
                    <span className="material-symbols-outlined">
                      category
                    </span>

                    {item.typology}
                  </span>

                  <span className="case-tag">
                    <span className="material-symbols-outlined">
                      layers
                    </span>

                    {item.alerts} Bundled Alerts
                  </span>
                </div>
              </div>

              <div className="case-actions">
                <button
                  type="button"
                  className="review-button"
                  onClick={() =>
                    navigate(`/suspected/${item.id}`)
                  }
                >
                  Review
                </button>

                <button
                  type="button"
                  className="more-button"
                  aria-label={`More options for ${item.id}`}
                >
                  <span className="material-symbols-outlined">
                    more_vert
                  </span>
                </button>
              </div>
            </article>
          ))}

          {filteredCases.length === 0 && (
            <div className="empty-state">
              <span className="material-symbols-outlined">
                search_off
              </span>

              <h3>No cases found</h3>

              <p>
                Try changing the search or filter criteria.
              </p>
            </div>
          )}
        </div>

        <div className="pagination">
          <span>
            Showing {filteredCases.length} of 34 cases
          </span>

          <div>
            <button type="button" disabled>
              Previous
            </button>

            <button type="button">Next</button>
          </div>
        </div>
      </section>
    </div>
  );
}

export default Suspected;