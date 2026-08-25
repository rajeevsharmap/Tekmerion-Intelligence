import "../../styles/Suspected.css";
import "../../styles/Shared.css";
import { useCases } from "../../hooks/useCases.js";
import { CaseListView } from "../../components/CaseListView.jsx";

// Reference holds cases that have reached the terminal CLOSED state —
// the point at which case_memory.py's record becomes purely historical.
function isReference(item) {
  return item.case_state === "CLOSED";
}

function Reference() {
  const { cases, loading, error, reload } = useCases();

  return (
    <CaseListView
      eyebrow="FINANCIAL CRIME OPERATIONS"
      title="Reference"
      description="Closed cases retained in case memory for historical reference."
      cases={cases.filter(isReference)}
      loading={loading}
      error={error}
      onRetry={reload}
      basePath="/reference"
      emptyIcon="library_books"
      emptyTitle="No reference cases yet"
      emptyBody="Closed cases will appear here once an investigator closes them."
    />
  );
}

export default Reference;