import "../../styles/Suspected.css";
import "../../styles/Shared.css";
import { useCases } from "../../hooks/useCases.js";
import { CaseListView } from "../../components/CaseListView.jsx";

// A case is in the active Suspected queue whenever it has not been
// escalated or closed. This mirrors case_state.py's own real states —
// nothing here is a frontend-invented status.
function isSuspected(item) {
  return item.case_state !== "ESCALATED" && item.case_state !== "CLOSED";
}

function Suspected() {
  const { cases, loading, error, reload } = useCases();

  return (
    <CaseListView
      eyebrow="FINANCIAL CRIME OPERATIONS"
      title="Suspected"
      description="Cases detected by the Detection Layer that are still active and have not been escalated or closed."
      cases={cases.filter(isSuspected)}
      loading={loading}
      error={error}
      onRetry={reload}
      basePath="/suspected"
      emptyTitle="No active cases"
      emptyBody="No cases are currently in the active investigation queue."
    />
  );
}

export default Suspected;