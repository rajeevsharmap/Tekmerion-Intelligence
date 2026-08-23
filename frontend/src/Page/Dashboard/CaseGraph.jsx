// src/Page/Dashboard/CaseGraph.jsx
import { useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import "../../styles/CaseReview.css";

const API_BASE = "http://localhost:8000";

// Field labels for the side panel — only fields actually present in
// the selected node/edge are rendered, since the shape differs by
// typology (network graph vs transaction timeline).
const FIELD_LABELS = {
  id: "Account",
  role: "Role",
  risk: "Risk",
  transaction_id: "Transaction ID",
  timestamp: "Timestamp",
  direction: "Direction",
  amount: "Amount",
  channel: "Channel",
  beneficiary_id: "Beneficiary ID",
  device_id: "Device ID",
  geo_event_id: "Geo Event ID",
  depth: "Network Depth",
};

function buildElements(evidence) {
  const vizType = evidence.data?.visualization_type;

  // Smurfing / reverse-smurfing / account-swap network cases: the
  // backend already ships cytoscape-ready nodes[] and edges[].
  if (vizType === "network") {
    const rootId = evidence.data.root_account;

    const nodes = (evidence.data.nodes || []).map((n) => ({
      data: {
        ...n.data,
        kind: n.data.id === rootId ? "center" : "counterparty",
      },
    }));

    const edges = (evidence.data.edges || []).map((e) => ({
      data: { ...e.data },
    }));

    return [...nodes, ...edges];
  }

  // Money-mule / pass-through cases: a flat transaction list, so we
  // build the star topology (counterparties around the account).
  const centerId = evidence.account_id;
  const transactions = evidence.data?.transactions || [];

  const nodeIds = new Set([centerId]);
  const elements = [{ data: { id: centerId, label: centerId, kind: "center" } }];

  transactions.forEach((txn) => {
    if (!nodeIds.has(txn.counterparty)) {
      nodeIds.add(txn.counterparty);
      elements.push({
        data: { id: txn.counterparty, label: txn.counterparty, kind: "counterparty" },
      });
    }

    const source = txn.direction === "in" ? txn.counterparty : centerId;
    const target = txn.direction === "in" ? centerId : txn.counterparty;

    elements.push({
      data: {
        id: txn.transaction_id,
        source,
        target,
        transaction_id: txn.transaction_id,
        timestamp: txn.timestamp,
        direction: txn.direction,
        amount: txn.amount,
        currency: txn.currency,
        channel: txn.channel,
        beneficiary_id: txn.beneficiary_id,
        device_id: txn.device_id,
        geo_event_id: txn.geo_event_id,
      },
    });
  });

  return elements;
}

const STYLE = [
  {
    selector: "node",
    style: {
      "background-color": "#00668a",
      label: "data(label)",
      color: "#191c1e",
      "font-size": 11,
      "font-family": "JetBrains Mono, monospace",
      "text-valign": "bottom",
      "text-margin-y": 6,
      "text-outline-width": 2,            // NEW — keeps labels legible over crossing edges
      "text-outline-color": "#f7f9fb",    // NEW — matches page background
      width: 20,
      height: 20,
    },
  },
  {
    selector: "node[kind = 'center']",
    style: {
      "background-color": "#000000",
      width: 20,
      height: 20,
      "border-width": 3,
      "border-color": "#20b2eb",
    },
  },
  // Optional: distinguish role in network-shaped evidence.
  {
    selector: "node[role = 'downstream']",
    style: {
      "background-color": "#8a4f00",
      shape: "triangle",
      width: 20,
      height: 20,
    },
  },
  {
    selector: "node[role = 'related']",
    style: { "background-color": "#5e91ad", shape: "round-rectangle" },
  },
  {
    selector: "edge",
    style: {
      width: 2,
      "curve-style": "bezier",
      "target-arrow-shape": "triangle",
      "arrow-scale": 0.9,
      "line-color": "#ba1a1a",
      "target-arrow-color": "#ba1a1a",

      // CHANGED — was label: "data(amount)", which sat at the edge
      // midpoint and crowded together near the hub. source-label
      // anchors it near the source-end node and text-rotation
      // follows the edge's own angle.
      "source-label": "data(amount)",
      "source-text-offset": 45,
      "text-rotation": "autorotate",

      "font-size": 9,
      "font-family": "JetBrains Mono, monospace",
      "text-background-color": "#f7f9fb",
      "text-background-opacity": 0.9,
      "text-background-padding": 2,
    },
  },
  {
    selector: "edge[direction = 'out']",
    style: {
      "line-color": "#00668a",
      "target-arrow-color": "#00668a",
    },
  },
  {
    selector: "node:selected, edge:selected",
    style: {
      "border-width": 4,
      "border-color": "#20b2eb",
      "line-color": "#20b2eb",
      "target-arrow-color": "#20b2eb",
    },
  },
];

function CaseGraph({ caseId }) {
  const containerRef = useRef(null);
  const cyRef = useRef(null);

  const [evidence, setEvidence] = useState(null);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setEvidence(null);
    setSelected(null);

    fetch(`${API_BASE}/api/cases/${caseId}/evidence`)
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
      })
      .then((data) => {
        if (!cancelled) setEvidence(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [caseId]);

  useEffect(() => {
    if (!evidence || !containerRef.current) return;

    const cy = cytoscape({
      container: containerRef.current,
      elements: buildElements(evidence),
      style: STYLE,
      layout: {
        name: "cose",
        animate: false,
        padding: 40,
        idealEdgeLength: 220,     // was 150 — longer spokes, more room for labels
        nodeRepulsion: 30000,     // was 12000 — pushes nodes further apart
        edgeElasticity: 250,
        gravity: 1,               // was 2 — less pull back toward center
        componentSpacing: 50,    // NEW — spacing between disconnected clusters
      },
    });

    cy.on("tap", "node, edge", (event) => {
      setSelected({ isEdge: event.target.isEdge(), ...event.target.data() });
    });

    cy.on("tap", (event) => {
      if (event.target === cy) setSelected(null);
    });

    cyRef.current = cy;

    return () => cy.destroy();
  }, [evidence]);

  if (loading) {
    return (
      <section className="case-review-body graph-layout">
        <div className="graph-canvas">
          <p>Loading network evidence…</p>
        </div>
      </section>
    );
  }

  if (error) {
    return (
      <section className="case-review-body graph-layout">
        <div className="graph-canvas">
          <span className="material-symbols-outlined">error</span>
          <p>Couldn't load evidence: {error}</p>
          <p style={{ fontSize: 13 }}>
            Is the backend running? <code>uvicorn main:app --reload --port 8000</code>
          </p>
        </div>
      </section>
    );
  }

  // Render only whichever fields actually exist on the selected
  // node/edge — the field set differs between the "network" shape
  // (id, role, risk / id, amount, timestamp, depth) and the
  // "transaction_timeline" shape (device_id, geo_event_id, etc.).
  const detailFields = selected
    ? Object.keys(FIELD_LABELS).filter(
      (key) => selected[key] !== undefined && key !== "id"
    )
    : [];

  return (
    <section className="case-review-body graph-layout">
      <div className="graph-canvas" ref={containerRef} style={{ minHeight: 0 }} />

      <aside className="node-details">
        <h4>
          <span className="material-symbols-outlined">info</span>
          {selected?.isEdge ? "Transaction Details" : "Node Details"}
        </h4>

        {!selected && <p className="muted">Click a node or edge to inspect it.</p>}

        {selected && !selected.isEdge && (
          <div className="node-detail-field">
            <p className="field-label">Account</p>
            <p className="field-value mono chip">{selected.id}</p>
          </div>
        )}

        {selected &&
          detailFields.map((key) => (
            <div className="node-detail-field" key={key}>
              <p className="field-label">{FIELD_LABELS[key]}</p>
              <p
                className={`field-value ${key === "amount" ? "bold" : "mono"} ${key === "direction" ? `direction-${selected[key]}` : ""
                  }`}
              >
                {key === "amount"
                  ? `${selected.currency || ""} ${Number(selected[key]).toLocaleString()}`
                  : String(selected[key])}
              </p>
            </div>
          ))}
      </aside>
    </section>
  );
}

export default CaseGraph;