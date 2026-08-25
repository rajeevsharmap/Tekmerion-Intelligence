import "../../styles/Suspected.css";
import "../../styles/Shared.css";
import { useCases } from "../../hooks/useCases.js";
import { CaseListView } from "../../components/CaseListView.jsx";
import { useInvestigator } from "../../context/useInvestigator.js";

function isEscalated(item) {
  return item.case_state === "ESCALATED";
}

function Escalated() {
  const { cases, loading, error, reload } = useCases();
  const { role } = useInvestigator();

  return (
    <CaseListView
      eyebrow="FINANCIAL CRIME OPERATIONS"
      title="Escalated"
      description={
        role === "senior"
          ? "Cases escalated by a junior investigator and awaiting senior review."
          : "Cases you escalated to a senior investigator."
      }
      cases={cases.filter(isEscalated)}
      loading={loading}
      error={error}
      onRetry={reload}
      basePath="/escalated"
      emptyIcon="priority_high"
      emptyTitle="No escalated cases"
      emptyBody="No cases are currently escalated."
    />
  );
}

export default Escalated;