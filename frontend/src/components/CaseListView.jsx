import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CaseStateBadge, CompletenessBadge } from "./StatusBadge.jsx";

const TYPOLOGY_LABEL = {
  smurfing: "Smurfing",
  reverse_smurfing: "Reverse Smurfing",
  money_mule: "Money Mule",
  account_swap: "Account Swap",
};

function typologyLabel(typology) {
  return TYPOLOGY_LABEL[typology] || typology || "Unknown typology";
}

/**
 * Renders a real, backend-driven list of cases (from GET /cases,
 * already filtered by the caller per case_state — see main.py's own
 * documented intent: "Suspected/Audit-Ready/Escalated/Reference views
 * filter this client-side on case_state"). No hardcoded case arrays.
 */
export function CaseListView({
  title,
  eyebrow,
  description,
  cases,
  loading,
  error,
  onRetry,
  basePath,
  emptyIcon = "search_off",
  emptyTitle = "No cases here",
  emptyBody = "There are currently no cases in this queue.",
}) {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [typologyFilter, setTypologyFilter] = useState("all");

  const filtered = useMemo(() => {
    return (cases || []).filter((item) => {
      const query = search.trim().toLowerCase();
      const matchesSearch =
        !query ||
        item.case_id?.toLowerCase().includes(query) ||
        item.account_id?.toLowerCase().includes(query) ||
        typologyLabel(item.typology).toLowerCase().includes(query);

      const matchesTypology = typologyFilter === "all" || item.typology === typologyFilter;

      return matchesSearch && matchesTypology;
    });
  }, [cases, search, typologyFilter]);

  return (
    <div className="dashboard-content">
      <header className="dashboard-header">
        <div>
          <span className="dashboard-eyebrow">{eyebrow}</span>
          <h2>{title}</h2>
        </div>
      </header>

      {description && <p className="workspace-subline">{description}</p>}

      <section className="cases-panel">
        <div className="cases-toolbar">
          <div>
            <h2>{title}</h2>
            <p>{filtered.length} of {cases?.length || 0} cases shown.</p>
          </div>

          <div className="case-controls">
            <div className="search-box">
              <span className="material-symbols-outlined">search</span>
              <input
                type="text"
                placeholder="Search by case ID, account, typology..."
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>

            <select value={typologyFilter} onChange={(event) => setTypologyFilter(event.target.value)}>
              <option value="all">All Typologies</option>
              <option value="smurfing">Smurfing</option>
              <option value="reverse_smurfing">Reverse Smurfing</option>
              <option value="money_mule">Money Mule</option>
              <option value="account_swap">Account Swap</option>
            </select>
          </div>
        </div>

        {loading && (
          <div className="panel-status">
            <span className="material-symbols-outlined">progress_activity</span>
            <h3>Loading cases...</h3>
          </div>
        )}

        {!loading && error && (
          <div className="panel-status is-error">
            <span className="material-symbols-outlined">cloud_off</span>
            <h3>Could not load cases</h3>
            <p>{error.message}</p>
            {onRetry && (
              <button type="button" onClick={onRetry}>
                Retry
              </button>
            )}
          </div>
        )}

        {!loading && !error && (
          <div className="case-list">
            {filtered.map((item) => (
              <article key={item.case_id} className="case-row">
                <div className="case-information">
                  <div className="case-topline">
                    <span className="case-id">{item.case_id}</span>
                    <CaseStateBadge state={item.case_state} />
                    {item.case_completeness_status && (
                      <CompletenessBadge status={item.case_completeness_status} />
                    )}
                  </div>

                  <h3>{typologyLabel(item.typology)}</h3>

                  <div className="case-tags">
                    <span className="case-tag">
                      <span className="material-symbols-outlined">account_balance</span>
                      {item.account_id}
                    </span>

                    {item.recommended_action && (
                      <span className="case-tag">
                        <span className="material-symbols-outlined">recommend</span>
                        {item.recommended_action.replace(/_/g, " ")}
                      </span>
                    )}

                    {item.sar_status && (
                      <span className="case-tag">
                        <span className="material-symbols-outlined">description</span>
                        SAR: {item.sar_status.replace(/_/g, " ")}
                      </span>
                    )}
                  </div>
                </div>

                <div className="case-actions">
                  <button
                    type="button"
                    className="review-button"
                    onClick={() => navigate(`${basePath}/${item.case_id}`)}
                  >
                    Review
                  </button>
                </div>
              </article>
            ))}

            {filtered.length === 0 && (
              <div className="empty-state">
                <span className="material-symbols-outlined">{emptyIcon}</span>
                <h3>{emptyTitle}</h3>
                <p>{emptyBody}</p>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

export default CaseListView;