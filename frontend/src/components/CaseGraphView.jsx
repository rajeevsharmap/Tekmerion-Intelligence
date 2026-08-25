import { useCallback, useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import api, { ApiError } from "../services/api.js";

const FIELD_LABELS = {
  id: "ID",
  role: "Role",
  risk: "Risk",
  source: "Source Account",
  target: "Target Account",
  amount: "Amount",
  currency: "Currency",
  timestamp: "Timestamp",
  depth: "Network Depth",
};

function GraphDetailPanel({ selection }) {
  if (!selection) {
    return (
      <aside className="detail-panel">
        <h4>Selection Details</h4>
        <p className="workspace-subline">Select a node or relationship to see its details.</p>
      </aside>
    );
  }

  const fields = Object.entries(selection.data).filter(([key]) => key !== "label");

  return (
    <aside className="detail-panel">
      <h4>{selection.kind === "node" ? "Account" : "Relationship"} Details</h4>
      {fields.map(([key, value]) => (
        <div className="detail-field" key={key}>
          <span>{FIELD_LABELS[key] || key}</span>
          <span>{value === null || value === undefined || value === "" ? "—" : String(value)}</span>
        </div>
      ))}
    </aside>
  );
}

function NetworkGraph({ graph }) {
  const containerRef = useRef(null);
  const cyRef = useRef(null);
  const [selection, setSelection] = useState(null);

  useEffect(() => {
    if (!containerRef.current || !graph) return undefined;

    const rootId = graph.metadata?.root_account;
    const elements = [
      ...(graph.nodes || []).map((n) => ({
        data: { ...n.data, kind: n.data.id === rootId ? "root" : n.data.role || "account" },
      })),
      ...(graph.edges || []).map((e) => ({ data: { ...e.data } })),
    ];

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "font-size": 9,
            color: "#33404c",
            "text-valign": "bottom",
            "text-margin-y": 6,
            "background-color": "#8ea3b8",
            width: 26,
            height: 26,
            "border-width": 2,
            "border-color": "#fff",
          },
        },
        {
          selector: "node[kind = 'root']",
          style: { "background-color": "#172231", width: 34, height: 34 },
        },
        {
          selector: "node[risk = 'high']",
          style: { "background-color": "#c0433f" },
        },
        {
          selector: "edge",
          style: {
            width: 1.6,
            "line-color": "#c7d0d8",
            "target-arrow-color": "#c7d0d8",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
          },
        },
        {
          selector: ":selected",
          style: { "border-color": "#172231", "line-color": "#172231", "target-arrow-color": "#172231" },
        },
      ],
      layout: { name: "cose", animate: false, padding: 30 },
      wheelSensitivity: 0.25,
    });

    cy.on("tap", "node", (evt) => setSelection({ kind: "node", data: evt.target.data() }));
    cy.on("tap", "edge", (evt) => setSelection({ kind: "edge", data: evt.target.data() }));
    cy.on("tap", (evt) => {
      if (evt.target === cy) setSelection(null);
    });

    cyRef.current = cy;
    return () => cy.destroy();
  }, [graph]);

  const riskCount = (graph?.nodes || []).filter((n) => n.data.risk === "high").length;

  return (
    <div className="graph-layout">
      <div className="graph-canvas-frame">
        <div className="graph-toolbar">
          <button type="button" title="Zoom in" onClick={() => cyRef.current?.zoom(cyRef.current.zoom() * 1.2)}>
            <span className="material-symbols-outlined">add</span>
          </button>
          <button type="button" title="Zoom out" onClick={() => cyRef.current?.zoom(cyRef.current.zoom() * 0.8)}>
            <span className="material-symbols-outlined">remove</span>
          </button>
          <button type="button" title="Fit to screen" onClick={() => cyRef.current?.fit(undefined, 30)}>
            <span className="material-symbols-outlined">fit_screen</span>
          </button>
        </div>

        <div className="graph-legend">
          <div className="graph-legend-item">
            <span className="graph-legend-dot" style={{ background: "#172231" }} /> Root account
          </div>
          <div className="graph-legend-item">
            <span className="graph-legend-dot" style={{ background: "#c0433f" }} /> High-risk account
          </div>
          <div className="graph-legend-item">
            <span className="graph-legend-dot" style={{ background: "#8ea3b8" }} /> Counterparty account
          </div>
        </div>

        <div ref={containerRef} className="graph-canvas-inner" />
      </div>

      <GraphDetailPanel selection={selection} />

      <p className="workspace-subline" style={{ gridColumn: "1 / -1" }}>
        {graph?.nodes?.length || 0} accounts, {graph?.edges?.length || 0} transactions shown ·
        role scope: {graph?.metadata?.role_scope} ·
        {riskCount > 0 ? ` ${riskCount} flagged high-risk` : " no high-risk accounts flagged"}
      </p>
    </div>
  );
}

function TransactionTimeline({ transactions, summary }) {
  return (
    <div>
      {summary && (
        <div className="info-grid">
          <div className="info-card">
            <h4>Total Inbound</h4>
            <div className="info-value">₹{summary.total_inbound?.toLocaleString?.() ?? "—"}</div>
          </div>
          <div className="info-card">
            <h4>Total Outbound</h4>
            <div className="info-value">₹{summary.total_outbound?.toLocaleString?.() ?? "—"}</div>
          </div>
          <div className="info-card">
            <h4>Outbound / Inbound Ratio</h4>
            <div className="info-value">{summary.outbound_to_inbound_ratio ?? "—"}</div>
          </div>
          <div className="info-card">
            <h4>Median Pass-Through Time</h4>
            <div className="info-value">{summary.median_inbound_to_outbound_minutes ?? "—"} min</div>
          </div>
        </div>
      )}

      <div className="timeline-list">
        <div className="timeline-row header">
          <span>Timestamp</span>
          <span>Direction</span>
          <span>Counterparty</span>
          <span>Amount</span>
        </div>
        {(transactions || []).map((txn) => (
          <div className="timeline-row" key={txn.transaction_id}>
            <span>{txn.timestamp}</span>
            <span>
              <span className={`direction-chip ${txn.direction}`}>{txn.direction}</span>
            </span>
            <span>{txn.counterparty} · {txn.channel}</span>
            <span>{txn.currency} {txn.amount?.toLocaleString?.()}</span>
          </div>
        ))}
        {(!transactions || transactions.length === 0) && (
          <div className="timeline-row"><span>No transaction timeline data available.</span></div>
        )}
      </div>
    </div>
  );
}

function BehavioralTimeline({ events, summary }) {
  return (
    <div>
      {summary && (
        <div className="info-grid">
          <div className="info-card">
            <h4>Baseline Avg Amount</h4>
            <div className="info-value">{summary.baseline_avg_amount?.toLocaleString?.() ?? "—"}</div>
          </div>
          <div className="info-card">
            <h4>Recent Avg Amount</h4>
            <div className="info-value">{summary.recent_avg_amount?.toLocaleString?.() ?? "—"}</div>
          </div>
          <div className="info-card">
            <h4>Amount Deviation</h4>
            <div className="info-value">{summary.amount_deviation_ratio ?? "—"}x</div>
          </div>
          <div className="info-card">
            <h4>Frequency Deviation</h4>
            <div className="info-value">{summary.frequency_deviation_ratio ?? "—"}x</div>
          </div>
        </div>
      )}

      <div className="timeline-list">
        <div className="timeline-row header">
          <span>Timestamp</span>
          <span>Event</span>
          <span>Details</span>
          <span> </span>
        </div>
        {(events || []).map((evt) => (
          <div className="timeline-row" key={evt.event_id}>
            <span>{evt.timestamp}</span>
            <span>{evt.event_type?.replace(/_/g, " ")}</span>
            <span>
              {evt.event_type === "transaction" &&
                `${evt.direction} · ${evt.currency} ${evt.amount?.toLocaleString?.()}${evt.is_first_time_beneficiary ? " · new beneficiary" : ""}`}
              {evt.event_type === "device_change" && `device ${evt.device_id}${evt.is_trusted_device === false ? " · untrusted" : ""}`}
              {evt.event_type === "sim_change" && "SIM change detected"}
              {evt.event_type === "geo" &&
                `${evt.city}, ${evt.country}${evt.registered_country_match === false ? " · country mismatch" : ""}${evt.is_vpn_or_proxy ? " · VPN/proxy" : ""}`}
            </span>
            <span />
          </div>
        ))}
        {(!events || events.length === 0) && (
          <div className="timeline-row"><span>No behavioral timeline data available.</span></div>
        )}
      </div>
    </div>
  );
}

export function CaseGraphView({ caseId, caseSummary, role }) {
  const [network, setNetwork] = useState(null);
  const [timeline, setTimeline] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const isGraphTypology = caseSummary.typology === "smurfing" || caseSummary.typology === "reverse_smurfing";

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (isGraphTypology) {
        const data = await api.getNetwork(caseId, role);
        setNetwork(data);
      } else {
        const data = await api.getTimeline(caseId);
        setTimeline(data);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(String(err), 0, null));
    } finally {
      setLoading(false);
    }
  }, [caseId, role, isGraphTypology]);

  useEffect(() => {
    // See hooks/useCases.js for why this fetch-on-mount pattern is
    // suppressed rather than restructured.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  if (loading) {
    return (
      <div className="panel-status">
        <span className="material-symbols-outlined">progress_activity</span>
        <h3>Loading graph...</h3>
      </div>
    );
  }

  if (error) {
    return (
      <div className="panel-status is-error">
        <span className="material-symbols-outlined">cloud_off</span>
        <h3>Could not load graph data</h3>
        <p>{error.message}</p>
        <button type="button" onClick={load}>Retry</button>
      </div>
    );
  }

  if (isGraphTypology) {
    if (!network?.graph) {
      return (
        <div className="panel-status">
          <span className="material-symbols-outlined">hub</span>
          <h3>No network graph available</h3>
          <p>{network?.reason || "This case has no persisted network graph."}</p>
        </div>
      );
    }
    return <NetworkGraph graph={network.graph} />;
  }

  if (caseSummary.typology === "money_mule") {
    return <TransactionTimeline transactions={timeline?.transaction_timeline} summary={null} />;
  }

  if (caseSummary.typology === "account_swap") {
    return (
      <>
        <p className="workspace-subline" style={{ marginBottom: 14 }}>
          Note: this timeline reflects the full case-scoped data as returned by the backend — the
          backend's timeline endpoint does not currently apply junior/senior scoping the way the
          network graph endpoint does.
        </p>
        <BehavioralTimeline events={timeline?.behavioral_events} summary={timeline?.behavioral_summary} />
      </>
    );
  }

  return (
    <div className="panel-status">
      <span className="material-symbols-outlined">help</span>
      <h3>No visualization available for this typology</h3>
    </div>
  );
}

export default CaseGraphView;