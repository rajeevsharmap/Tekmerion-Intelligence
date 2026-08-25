import "../../styles/Suspected.css";
import "../../styles/Shared.css";
import { useCases } from "../../hooks/useCases.js";
import { CaseListView } from "../../components/CaseListView.jsx";

// Audit-ready means the backend has recorded a SAR outcome and/or the
// case has reached ACTION_EXECUTED — a completed investigator action.
function isAuditReady(item) {
  return Boolean(item.sar_status) || item.case_state === "ACTION_EXECUTED";
}

function AuditReady() {
  const { cases, loading, error, reload } = useCases();

  return (
    <CaseListView
      eyebrow="FINANCIAL CRIME OPERATIONS"
      title="Audit-Ready"
      description="Cases with a generated SAR outcome and/or a completed investigator action."
      cases={cases.filter(isAuditReady)}
      loading={loading}
      error={error}
      onRetry={reload}
      basePath="/audit-ready"
      emptyIcon="assignment_turned_in"
      emptyTitle="No audit-ready cases yet"
      emptyBody="Cases will appear here once a SAR outcome or a completed action is recorded."
    />
  );
}

export default AuditReady;