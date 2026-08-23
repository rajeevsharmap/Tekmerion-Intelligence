import { useMemo, useState } from "react";
import "../../styles/Reference.css";

const referenceCases = [
  {
    id: "CASE-2023-112B",
    outcome: "fraud",
    outcomeLabel: "Confirmed Fraud",
    closedDate: "Closed Oct 12, 2023",
    title: "Operation Silk Road Phantom",
    description:
      "Complex network of synthetic identities utilizing decentralized exchanges to obfuscate origin of funds across three jurisdictions.",
    typology: "smurfing",
    evidenceTypes: ["Network Graph", "Behavioral Timeline"],
  },
  {
    id: "CASE-2023-098A",
    outcome: "false_positive",
    outcomeLabel: "False Positive",
    closedDate: "Closed Nov 04, 2023",
    title: "Velocity Spike: Retail Acc",
    description:
      "Unusual transaction volume determined to be legitimate business expansion. Pattern flagged by legacy rule #402.",
    typology: "reverse_smurfing",
    evidenceTypes: ["Transaction Review", "KYC Document"],
  },
  {
    id: "CASE-2023-085C",
    outcome: "legitimate",
    outcomeLabel: "Confirmed Legitimate",
    closedDate: "Closed Sep 28, 2023",
    title: "Structuring Suspect",
    description:
      "Series of cash deposits just below the reporting threshold across multiple branches over a two-week period. Verified as routine business deposits.",
    typology: "smurfing",
    evidenceTypes: ["Location Mapping", "Merchant Audit"],
  },
  {
    id: "CASE-2023-071D",
    outcome: "fraud",
    outcomeLabel: "Confirmed Fraud",
    closedDate: "Closed Aug 19, 2023",
    title: "Rapid Account Takeover, Ring 4",
    description:
      "SIM-swap preceded a sudden 12x deviation in transaction value against the account's own baseline behavior.",
    typology: "account_swap",
    evidenceTypes: ["Behavioral Timeline"],
  },
];

function Reference() {
  const [search, setSearch] = useState("");
  const [typologyFilter, setTypologyFilter] = useState("all");
  const [outcomeFilter, setOutcomeFilter] = useState("all");

  const filteredCases = useMemo(() => {
    return referenceCases.filter((item) => {
      const searchValue = search.toLowerCase();

      const matchesSearch =
        item.id.toLowerCase().includes(searchValue) ||
        item.title.toLowerCase().includes(searchValue) ||
        item.description.toLowerCase().includes(searchValue);

      const matchesTypology =
        typologyFilter === "all" || item.typology === typologyFilter;

      const matchesOutcome =
        outcomeFilter === "all" || item.outcome === outcomeFilter;

      return matchesSearch && matchesTypology && matchesOutcome;
    });
  }, [search, typologyFilter, outcomeFilter]);

  return (
    <div className="dashboard-content">
      <header className="dashboard-header">
        <div>
          <span className="dashboard-eyebrow">
            FINANCIAL CRIME OPERATIONS
          </span>

          <h2>Reference Archive</h2>
        </div>

        <div className="header-status">
          <span className="status-dot" />
          Investigation System Online
        </div>
      </header>

      <section className="summary-grid">
        <div className="summary-card">
          <div className="summary-heading">
            <span className="material-symbols-outlined">library_books</span>
            <h3>Reference Cases</h3>
          </div>

          <strong className="summary-number">1,284</strong>
          <span className="summary-meta">Closed &amp; indexed</span>
        </div>

        <div className="summary-card">
          <div className="summary-heading">
            <span className="material-symbols-outlined">category</span>
            <h3>Typologies Covered</h3>
          </div>

          <strong className="summary-number">4/4</strong>
          <span className="summary-meta">All typologies</span>
        </div>

        <div className="summary-card">
          <div className="summary-heading">
            <span className="material-symbols-outlined">bar_chart</span>
            <h3>Most Referenced</h3>
          </div>

          <strong className="summary-number">Smurfing</strong>
          <span className="summary-meta">This quarter</span>
        </div>

        <div className="summary-card">
          <div className="summary-heading">
            <span className="material-symbols-outlined">check_circle</span>
            <h3>Closed This Month</h3>
          </div>

          <strong className="summary-number">42</strong>
          <span className="summary-meta">Cases resolved</span>
        </div>
      </section>

      <section className="cases-panel">
        <div className="cases-toolbar">
          <div>
            <h2>Reference Cases</h2>
            <p>Search closed investigations by pattern or outcome.</p>
          </div>

          <div className="case-controls">
            <div className="search-box">
              <span className="material-symbols-outlined">search</span>

              <input
                type="text"
                placeholder="Search archive..."
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>

            <select
              value={typologyFilter}
              onChange={(event) => setTypologyFilter(event.target.value)}
            >
              <option value="all">All Typologies</option>
              <option value="smurfing">Smurfing</option>
              <option value="reverse_smurfing">Reverse Smurfing</option>
              <option value="money_mule">Money Mule</option>
              <option value="account_swap">Account Swap</option>
            </select>

            <select
              value={outcomeFilter}
              onChange={(event) => setOutcomeFilter(event.target.value)}
            >
              <option value="all">All Outcomes</option>
              <option value="fraud">Fraud</option>
              <option value="false_positive">False Positive</option>
              <option value="legitimate">Legitimate</option>
            </select>
          </div>
        </div>

        <div className="case-list">
          {filteredCases.map((item) => (
            <article key={item.id} className="case-row reference-row">
              <div className="case-information">
                <div className="case-topline">
                  <span className="case-id">{item.id}</span>

                  <span className={`outcome-badge ${item.outcome}`}>
                    {item.outcomeLabel}
                  </span>

                  <span className="case-age">{item.closedDate}</span>
                </div>

                <h3>{item.title}</h3>

                <p className="case-description">{item.description}</p>

                <div className="case-tags">
                  {item.evidenceTypes.map((tag) => (
                    <span className="case-tag" key={tag}>
                      {tag}
                    </span>
                  ))}
                </div>
              </div>

              <div className="case-actions">
                <button type="button" className="view-case-button">
                  View Case
                </button>
              </div>
            </article>
          ))}

          {filteredCases.length === 0 && (
            <div className="empty-state">
              <span className="material-symbols-outlined">search_off</span>
              <h3>No matching reference cases</h3>
              <p>Try changing the search or filter criteria.</p>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

export default Reference;