import { Navigate, Route, Routes } from "react-router-dom";

import Login from "./Page/Login.jsx";
import Dashboard from "./Page/Dashboard.jsx";
import Suspected from "./Page/Dashboard/Suspected.jsx";
import AuditReady from "./Page/Dashboard/AuditReady.jsx";
import Reference from "./Page/Dashboard/Reference.jsx";
import Escalated from "./Page/Dashboard/Escalated.jsx";
import SystemInsights from "./Page/Dashboard/SystemInsights.jsx";
import CaseReview from "./Page/Dashboard/CaseReview.jsx";
import { useInvestigator } from "./context/useInvestigator.js";

function ProtectedDashboard() {
  const { isAuthenticated } = useInvestigator();
  if (!isAuthenticated) {
    return <Navigate to="/" replace />;
  }
  return <Dashboard />;
}

/*
 * RoleRoute guards a single tab that only makes sense for one
 * authorization level (System Insights is senior-only). If the
 * signed-in investigator's role does not match, they are redirected
 * to Suspected rather than seeing a blocked/empty page.
 */
function RoleRoute({ allowedRole, children }) {
  const { isAuthenticated, role } = useInvestigator();
  if (!isAuthenticated) {
    return <Navigate to="/" replace />;
  }
  if (role !== allowedRole) {
    return <Navigate to="/suspected" replace />;
  }
  return children;
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<Login />} />

      <Route path="/suspected" element={<ProtectedDashboard />}>
        <Route index element={<Suspected />} />
        <Route path=":caseId" element={<CaseReview />} />
      </Route>

      <Route path="/escalated" element={<ProtectedDashboard />}>
        <Route index element={<Escalated />} />
        <Route path=":caseId" element={<CaseReview />} />
      </Route>

      <Route path="/reference" element={<ProtectedDashboard />}>
        <Route index element={<Reference />} />
        <Route path=":caseId" element={<CaseReview />} />
      </Route>

      <Route path="/audit-ready" element={<ProtectedDashboard />}>
        <Route index element={<AuditReady />} />
        <Route path=":caseId" element={<CaseReview />} />
      </Route>

      <Route path="/system-insights" element={<ProtectedDashboard />}>
        <Route
          index
          element={
            <RoleRoute allowedRole="senior">
              <SystemInsights />
            </RoleRoute>
          }
        />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;