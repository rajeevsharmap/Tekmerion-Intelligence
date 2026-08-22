import { NavLink, Outlet, useNavigate } from "react-router-dom";
import "../styles/Dashboard.css";

const navItems = [
  {
    label: "Suspected",
    path: "/suspected",
    icon: "notifications_active",
  },
  {
    label: "Audit-Ready",
    path: "/audit-ready",
    icon: "assignment_turned_in",
  },
  {
    label: "Reference",
    path: "/reference",
    icon: "library_books",
  },
  {
    label: "Escalated",
    path: "/escalated",
    icon: "priority_high",
  },
];

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
    // Remove corrupted authentication data.
    sessionStorage.removeItem("tekmerion_auth");
    localStorage.removeItem("tekmerion_auth");
    return null;
  }
}

function getRoleLabel(role) {
  const roles = {
    junior: "Junior Investigator",
    senior: "Senior Investigator",
  };

  return roles[role] || "Investigator";
}

function Dashboard() {
  const navigate = useNavigate();
  const authData = getAuthData();

  const fullName =
    authData?.name ||
    authData?.fullName ||
    "Investigator";

  const agentId = authData?.agentId || "";
  const roleLabel = getRoleLabel(authData?.role);

  const handleLogout = () => {
    // Clear both storage locations so old login data cannot interfere.
    sessionStorage.removeItem("tekmerion_auth");
    localStorage.removeItem("tekmerion_auth");

    navigate("/", { replace: true });
  };

  return (
    <div className="dashboard">
      <aside className="dashboard-sidebar">
        <div className="sidebar-brand">
          <h1>Tekmerion Intelligence</h1>
          <p>Financial Crime Unit</p>
        </div>

        <nav className="sidebar-navigation" aria-label="Dashboard navigation">
          {navItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) =>
                `sidebar-link ${isActive ? "active" : ""}`
              }
            >
              <span className="material-symbols-outlined">
                {item.icon}
              </span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="analyst-profile">
            <div className="analyst-avatar">
              <span className="material-symbols-outlined">person</span>
            </div>

            <div className="analyst-details">
              <p className="analyst-name">{fullName}</p>
              <span className="analyst-role">{roleLabel}</span>

              {agentId && (
                <span className="analyst-id">{agentId}</span>
              )}
            </div>
          </div>

          <button
            type="button"
            className="logout-button"
            onClick={handleLogout}
          >
            <span className="material-symbols-outlined">logout</span>
            Sign Out
          </button>
        </div>
      </aside>

      <main className="dashboard-main">
        <Outlet />
      </main>
    </div>
  );
}

export default Dashboard;