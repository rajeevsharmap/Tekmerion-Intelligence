function Escalated() {
  return (
    <div className="dashboard-content">
      <header className="dashboard-header">
        <div>
          <span className="dashboard-eyebrow">
            FINANCIAL CRIME OPERATIONS
          </span>

          <h2>Escalated</h2>
        </div>

        <div className="header-status">
          <span className="status-dot" />
          Investigation System Online
        </div>
      </header>

      <section className="dashboard-placeholder">
        <span className="material-symbols-outlined">
          priority_high
        </span>

        <h3>Escalated Cases</h3>

        <p>
          Escalated financial crime cases requiring further action will
          be rendered here.
        </p>
      </section>
    </div>
  );
}

export default Escalated;