import { Navigate, Route, Routes } from "react-router-dom";

import Login from "./Page/Login.jsx";
import Dashboard from "./Page/Dashboard.jsx";
import Suspected from "./Page/Dashboard/Suspected.jsx";
import AuditReady from "./Page/Dashboard/AuditReady.jsx";
import Reference from "./Page/Dashboard/Reference.jsx";
import Escalated from "./Page/Dashboard/Escalated.jsx";

function isAuthenticated() {
  return Boolean(
    sessionStorage.getItem("tekmerion_auth") ||
    localStorage.getItem("tekmerion_auth")
  );
}

function ProtectedDashboard() {
  if (!isAuthenticated()) {
    return <Navigate to="/" replace />;
  }

  return <Dashboard />;
}

function App() {
  return (
    <Routes>
      {/* Public route */}
      <Route path="/" element={<Login />} />

      {/* Protected dashboard routes */}
      <Route
        path="/suspected"
        element={<ProtectedDashboard />}
      >
        <Route index element={<Suspected />} />
      </Route>

      <Route
        path="/audit-ready"
        element={<ProtectedDashboard />}
      >
        <Route index element={<AuditReady />} />
      </Route>

      <Route
        path="/reference"
        element={<ProtectedDashboard />}
      >
        <Route index element={<Reference />} />
      </Route>

      <Route
        path="/escalated"
        element={<ProtectedDashboard />}
      >
        <Route index element={<Escalated />} />
      </Route>

      {/* Fallback */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;