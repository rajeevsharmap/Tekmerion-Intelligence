import { Navigate, Route, Routes } from "react-router-dom";

import Login from "./Page/Login.jsx";
import Dashboard from "./Page/Dashboard.jsx";
import Suspected from "./Page/Dashboard/Suspected.jsx";
import AuditReady from "./Page/Dashboard/AuditReady.jsx";
import Reference from "./Page/Dashboard/Reference.jsx";
import Escalated from "./Page/Dashboard/Escalated.jsx";
import SystemInsights from "./Page/Dashboard/SystemInsights.jsx";
import CaseReview from "./Page/Dashboard/CaseReview.jsx";

function getAuthData() {
  const raw =
    sessionStorage.getItem("tekmerion_auth") ||
    localStorage.getItem("tekmerion_auth");

  if (!raw) {
    return null;
  }

  try {
    return JSON.parse(raw);
  } catch {
    // Corrupted auth payload — treat as unauthenticated.
    sessionStorage.removeItem("tekmerion_auth");
    localStorage.removeItem("tekmerion_auth");
    return null;
  }
}

function isAuthenticated() {
  return Boolean(getAuthData());
}

function getRole() {
  const authData = getAuthData();
  return authData?.role === "senior" ? "senior" : "junior";
}

function ProtectedDashboard() {
  if (!isAuthenticated()) {
    return <Navigate to="/" replace />;
  }

  return <Dashboard />;
}

/*
 * RoleRoute guards a single tab that only makes sense for one
 * authorization level (e.g. Escalated for juniors, System Insights
 * for seniors). If the signed-in investigator's role does not match,
 * they are redirected to that role's equivalent tab instead of
 * seeing a blocked/empty page.
 */
function RoleRoute({ allowedRole, redirectTo, children }) {
  if (!isAuthenticated()) {
    return <Navigate to="/" replace />;
  }

  const role = getRole();

  if (role !== allowedRole) {
    return <Navigate to={redirectTo} replace />;
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

      <Route path="/audit-ready" element={<ProtectedDashboard />}>
        <Route index element={<AuditReady />} />
        <Route path=":caseId" element={<CaseReview />} />
      </Route>

      <Route path="/reference" element={<ProtectedDashboard />}>
        <Route index element={<Reference />} />
        <Route path=":caseId" element={<CaseReview />} />
      </Route>

      <Route path="/escalated" element={<ProtectedDashboard />}>
        <Route
          index
          element={
            <RoleRoute allowedRole="junior" redirectTo="/system-insights">
              <Escalated />
            </RoleRoute>
          }
        />
        <Route
          path=":caseId"
          element={
            <RoleRoute allowedRole="junior" redirectTo="/system-insights">
              <CaseReview />
            </RoleRoute>
          }
        />
      </Route>

      <Route path="/system-insights" element={<ProtectedDashboard />}>
        <Route
          index
          element={
            <RoleRoute allowedRole="senior" redirectTo="/escalated">
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