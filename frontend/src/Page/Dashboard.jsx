import { NavLink, Outlet, useNavigate } from "react-router-dom";
import "../styles/Dashboard.css";
import { useInvestigator } from "../context/useInvestigator.js";

const navItems = [
  { label: "Suspected", path: "/suspected", icon: "notifications_active" },
  { label: "Escalated", path: "/escalated", icon: "priority_high" },
  { label: "Reference", path: "/reference", icon: "library_books" },
  { label: "Audit-Ready", path: "/audit-ready", icon: "assignment_turned_in" },
];

const seniorOnlyNavItem = { label: "System Insights", path: "/system-insights", icon: "insights" };

function getRoleLabel(role) {
  return role === "senior" ? "Senior Investigator" : "Junior Investigator";
}

function Dashboard() {
  const navigate = useNavigate();
  const { investigator, role, logout } = useInvestigator();

  const fullName = investigator?.name || "Investigator";
  const investigatorId = investigator?.investigatorId || "";
  const roleLabel = getRoleLabel(role);

  const items = role === "senior" ? [...navItems, seniorOnlyNavItem] : navItems;

  const handleLogout = () => {
    logout();
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
          {items.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              className={({ isActive }) => `sidebar-link ${isActive ? "active" : ""}`}
            >
              <span className="material-symbols-outlined">{item.icon}</span>
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
              {investigatorId && <span className="analyst-id">{investigatorId}</span>}
            </div>
          </div>

          <button type="button" className="logout-button" onClick={handleLogout}>
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