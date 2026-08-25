import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import "../../styles/CaseReview.css";
import "../../styles/Shared.css";
import api, { ApiError } from "../../services/api.js";
import { useInvestigator } from "../../context/useInvestigator.js";
import { CaseStateBadge } from "../../components/StatusBadge.jsx";
import CaseOverview from "../../components/CaseOverview.jsx";
import CaseGraphView from "../../components/CaseGraphView.jsx";
import CaseEvidence from "../../components/CaseEvidence.jsx";
import CaseSarAudit from "../../components/CaseSarAudit.jsx";

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "graph", label: "Graph" },
  { key: "evidence", label: "Evidence" },
  { key: "sar", label: "SAR / Audit Replay" },
];

const TYPOLOGY_LABEL = {
  smurfing: "Smurfing",
  reverse_smurfing: "Reverse Smurfing",
  money_mule: "Money Mule",
  account_swap: "Account Swap",
};

function CaseReview() {
  const { caseId } = useParams();
  const navigate = useNavigate();
  const { role } = useInvestigator();

  const [activeTab, setActiveTab] = useState("overview");
  const [caseSummary, setCaseSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const summary = await api.getCase(caseId);
      setCaseSummary(summary);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(String(err), 0, null));
    } finally {
      setLoading(false);
    }
  }, [caseId]);

  useEffect(() => {
    // Resetting the active tab and fetching the newly-selected case on
    // caseId change is the standard "sync with a prop change" effect
    // pattern; see the note in hooks/useCases.js for why this is
    // suppressed rather than restructured.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setActiveTab("overview");
    load();
  }, [load]);

  return (
    <div className="dashboard-content case-workspace">
      <div className="workspace-header">
        <div>
          <button type="button" className="back-link" onClick={() => navigate(-1)}>
            <span className="material-symbols-outlined">arrow_back</span>
            Back
          </button>

          <div className="workspace-title-row">
            <h2>{caseId}</h2>
            {caseSummary?.case_state && <CaseStateBadge state={caseSummary.case_state} />}
            {caseSummary?.typology && (
              <span className="pill">{TYPOLOGY_LABEL[caseSummary.typology] || caseSummary.typology}</span>
            )}
          </div>

          <p className="workspace-subline">
            Account {caseSummary?.account_id || "—"} · viewing as {role === "senior" ? "Senior" : "Junior"} Investigator
          </p>
        </div>
      </div>

      {loading && (
        <div className="panel-status">
          <span className="material-symbols-outlined">progress_activity</span>
          <h3>Loading case...</h3>
        </div>
      )}

      {!loading && error && (
        <div className="panel-status is-error">
          <span className="material-symbols-outlined">cloud_off</span>
          <h3>Could not load this case</h3>
          <p>{error.message}</p>
          <button type="button" onClick={load}>Retry</button>
        </div>
      )}

      {!loading && !error && caseSummary && (
        <>
          <nav className="workspace-tabs">
            {TABS.map((tab) => (
              <button
                key={tab.key}
                type="button"
                className={`workspace-tab ${activeTab === tab.key ? "active" : ""}`}
                onClick={() => setActiveTab(tab.key)}
              >
                {tab.label}
              </button>
            ))}
          </nav>

          {activeTab === "overview" && <CaseOverview caseSummary={caseSummary} />}
          {activeTab === "graph" && <CaseGraphView caseId={caseId} caseSummary={caseSummary} role={role} />}
          {activeTab === "evidence" && <CaseEvidence caseId={caseId} />}
          {activeTab === "sar" && (
            <CaseSarAudit caseId={caseId} caseSummary={caseSummary} onCaseChanged={load} />
          )}
        </>
      )}
    </div>
  );
}

export default CaseReview;