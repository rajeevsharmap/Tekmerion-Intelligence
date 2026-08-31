import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import "../../styles/Suspected.css";

// main.py (backend) serves plain, unprefixed routes - GET /cases, not
// /api/cases - so that's what this points at.
const API_BASE = "http://localhost:8000";

// GET /cases exposes each case's real `recommended_action`
// (next_best_action.py's own output) but not a plain risk label - this
// maps that action to the badge levels the UI already has, rather
// than inventing an unrelated risk score.
const RISK_BY_RECOMMENDED_ACTION = {
  BLOCK_TRANSACTION: "High",
  RESTRICT_ACCOUNT: "Medium",
  REQUEST_MORE_INFORMATION: "Low",
};

function typologyLabel(typology) {
  if (!typology) return "Unknown Typology";
  return typology
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function formatAge(isoTimestamp) {
  if (!isoTimestamp) return "Unknown";

  const created = new Date(isoTimestamp);
  if (Number.isNaN(created.getTime())) return "Unknown";

  const diffMs = Date.now() - created.getTime();
  const minutes = Math.floor(diffMs / 60000);

  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} min${minutes === 1 ? "" : "s"} ago`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;

  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;

  const months = Math.floor(days / 30);
  if (months < 12) return `${months} month${months === 1 ? "" : "s"} ago`;

  const years = Math.floor(months / 12);
  return `${years} year${years === 1 ? "" : "s"} ago`;
}

// Maps a case record from GET /cases into the shape this page renders.
// Every field is either read straight off the API response or a
// deterministic, documented transform of one (see helpers above) -
// nothing here is invented per-case data.
function toDisplayCase(apiCase) {
  return {
    id: apiCase.case_id,
    risk: RISK_BY_RECOMMENDED_ACTION[apiCase.recommended_action] || "Medium",
    age: formatAge(apiCase.created_at),
    title: `${typologyLabel(apiCase.typology)} Case`,
    typology: typologyLabel(apiCase.typology),
    typologyKey: apiCase.typology,
    alerts: apiCase.alert_count ?? 0,
  };
}

function Suspected() {
  const navigate = useNavigate();

  const [cases, setCases] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [search, setSearch] = useState("");
  const [riskFilter, setRiskFilter] = useState("all");
  const [typologyFilter, setTypologyFilter] = useState("all");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetch(`${API_BASE}/cases`)
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
      })
      .then((data) => {
        if (cancelled) return;
        // Show every bundled case, all typologies and lifecycle
        // states - no client-side state filtering.
        setCases((data.cases || []).map(toDisplayCase));
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

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

          <strong className="summary-number">{cases.length}</strong>

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
          {loading && (
            <div className="empty-state">
              <h3>Loading cases…</h3>
            </div>
          )}

          {!loading && error && (
            <div className="empty-state">
              <span className="material-symbols-outlined">error</span>
              <h3>Couldn't load cases</h3>
              <p>
                {error} — is the backend running?{" "}
                <code>uvicorn main:app --reload --port 8000</code>
              </p>
            </div>
          )}

          {!loading && !error && filteredCases.map((item) => (
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

          {!loading && !error && filteredCases.length === 0 && (
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
            Showing {filteredCases.length} of {cases.length} cases
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