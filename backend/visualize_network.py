"""
visualize_network.py
======================
Quick NetworkX + matplotlib visualization of a case's fraud network -
purely to eyeball what the graph looks like before wiring up a real
frontend (Cytoscape.js etc). Reuses the exact same BFS traversal as
network_layer.py's build_smurf_network / build_reverse_smurf_network,
so what you see here is exactly what the Network Evidence Layer would
hand to the frontend, just rendered with matplotlib instead of JSON.

Usage (all arguments have sensible defaults - just run it):

    python3 visualize_network.py

    # or from another script:
    from visualize_network import visualize_case_network
    visualize_case_network()                          # first smurfing/reverse_smurfing case found
    visualize_case_network(typology="money_mule")      # first money-mule case, drawn as a timeline
    visualize_case_network(case_id="CASE-9672AB3B")     # a specific case
"""

import json
import os

import matplotlib
matplotlib.use("Agg")  # headless-safe; still writes a PNG you can open
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import networkx as nx

from data_store import DataStore
from network_layer import (
    build_smurf_network,
    build_reverse_smurf_network,
    build_money_mule_timeline,
    build_account_swap_timeline,
    MAX_DEPTH,
)
from datetime import datetime, timedelta

ROLE_COLORS = {
    "root": "#d62728",               # red
    "source": "#1f77b4",             # blue  (feeds into the root - smurfing senders)
    "downstream": "#2ca02c",         # green (root sends onward - smurfing chain)
    "distribution_target": "#2ca02c",  # green (reverse-smurfing fan-out targets)
    "related": "#7f7f7f",            # grey  (anything else picked up in traversal)
}


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _pick_default_case(cases, typology=None, case_id=None):
    if case_id:
        for c in cases:
            if c["case_id"] == case_id:
                return c
        raise ValueError(f"case_id {case_id!r} not found in the loaded cases file.")

    graph_typologies = ("smurfing", "reverse_smurfing")
    pool = [c for c in cases if (c.get("primary_trigger") == typology if typology else
                                  c.get("primary_trigger") in graph_typologies)]
    if not pool:
        available = sorted({c.get("primary_trigger") for c in cases})
        raise ValueError(f"No case found for typology={typology!r}. Typologies present: {available}")
    return pool[0]


def _layered_positions(graph, root):
    """Arrange nodes left-to-right by hop distance from root: senders feeding
    the root sit to the left (negative depth), funds moving onward from the
    root sit to the right (positive depth) - so fund flow reads left-to-right
    like the spec's ASCII diagrams."""
    depth = {root: 0}

    frontier, visited, d = [root], {root}, 0
    while True:
        d -= 1
        nxt = []
        for n in frontier:
            for p in graph.predecessors(n):
                if p not in visited:
                    depth[p] = d
                    visited.add(p)
                    nxt.append(p)
        if not nxt:
            break
        frontier = nxt

    frontier, visited, d = [root], {root}, 0
    while True:
        d += 1
        nxt = []
        for n in frontier:
            for s in graph.successors(n):
                if s not in visited:
                    depth[s] = d
                    visited.add(s)
                    nxt.append(s)
        if not nxt:
            break
        frontier = nxt

    for n in graph.nodes():
        depth.setdefault(n, 0)

    levels = {}
    for n, dd in depth.items():
        levels.setdefault(dd, []).append(n)

    pos = {}
    for dd, nodes_at_level in levels.items():
        nodes_at_level = sorted(nodes_at_level)
        n = len(nodes_at_level)
        for i, node in enumerate(nodes_at_level):
            pos[node] = (dd, i - (n - 1) / 2)
    return pos


def _fmt_amount(amount):
    if amount >= 100000:
        return f"₹{amount/100000:.1f}L"
    if amount >= 1000:
        return f"₹{amount/1000:.0f}k"
    return f"₹{amount:.0f}"


# ----------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------
def visualize_case_network(
    data_dir="../mock_data",
    cases_file="../mock_data/detected_cases.json",
    case_id=None,
    typology=None,
    max_depth=MAX_DEPTH,
    window_hours=72,
    save_path=None,
    show_edge_labels=True,
    figsize=(12, 7),
):
    """Build and draw the network graph for one case, purely with NetworkX +
    matplotlib. Defaults to the first smurfing/reverse_smurfing case in
    `cases_file` if no case_id/typology is given, so it runs out of the box
    against the mock data already generated for this project.

    Returns a dict with the case metadata, the underlying nx.DiGraph, and
    the path the PNG was saved to.
    """
    store = DataStore(data_dir)
    with open(cases_file) as f:
        cases = json.load(f)

    case = _pick_default_case(cases, typology=typology, case_id=case_id)
    root = case["account_id"]
    ctyp = case.get("primary_trigger")
    anchor_time = datetime.fromisoformat(case["created_at"]) if case.get("created_at") else None

    if ctyp == "smurfing":
        result = build_smurf_network(store, root, max_depth=max_depth, direction="both",
                                      anchor_time=anchor_time, window_hours=window_hours)
    elif ctyp == "reverse_smurfing":
        result = build_reverse_smurf_network(store, root, max_depth=max_depth,
                                              anchor_time=anchor_time, window_hours=window_hours)
    else:
        raise ValueError(f"visualize_case_network draws a GRAPH - case {case['case_id']} is "
                          f"typology={ctyp!r}, which network_layer renders as a timeline, not a "
                          f"graph. Use visualize_case_timeline() instead.")

    graph = nx.DiGraph()
    for e in result["edges"]:
        d = e["data"]
        graph.add_edge(d["source"], d["target"], amount=d["amount"], transaction_id=d["id"],
                        timestamp=d["timestamp"], depth=d["depth"])
    roles = {n["data"]["id"]: n["data"]["role"] for n in result["nodes"]}

    pos = _layered_positions(graph, root)
    node_colors = [ROLE_COLORS.get(roles.get(n, "related"), "#7f7f7f") for n in graph.nodes()]
    node_sizes = [1400 if n == root else 800 for n in graph.nodes()]

    amounts = [d["amount"] for _, _, d in graph.edges(data=True)] or [1]
    lo, hi = min(amounts), max(amounts)
    def _edge_width(a):
        if hi == lo:
            return 2.5
        return 1.2 + 4.5 * (a - lo) / (hi - lo)
    edge_widths = [_edge_width(d["amount"]) for _, _, d in graph.edges(data=True)]

    fig, ax = plt.subplots(figsize=figsize)
    nx.draw_networkx_nodes(graph, pos, node_color=node_colors, node_size=node_sizes,
                            edgecolors="black", linewidths=1.2, ax=ax)
    nx.draw_networkx_labels(graph, pos, font_size=8, font_weight="bold", ax=ax)
    nx.draw_networkx_edges(graph, pos, width=edge_widths, edge_color="#555555",
                            arrows=True, arrowsize=16, arrowstyle="-|>",
                            connectionstyle="arc3,rad=0.08", ax=ax)

    if show_edge_labels and graph.number_of_edges() <= 20:
        edge_labels = {(u, v): _fmt_amount(d["amount"]) for u, v, d in graph.edges(data=True)}
        nx.draw_networkx_edge_labels(graph, pos, edge_labels=edge_labels, font_size=7,
                                      label_pos=0.5, ax=ax)

    legend_handles = [mpatches.Patch(color=c, label=r.replace("_", " ").title())
                       for r, c in {"root": ROLE_COLORS["root"], "source": ROLE_COLORS["source"],
                                     "downstream / distribution target": ROLE_COLORS["downstream"],
                                     "related": ROLE_COLORS["related"]}.items()]
    ax.legend(handles=legend_handles, loc="upper center", bbox_to_anchor=(0.5, -0.03), ncol=4, frameon=False)

    pattern_summary = ", ".join(p["type"] for p in result["patterns"]) or "no named patterns detected"
    ax.set_title(f"{ctyp.replace('_', ' ').title()} network — {case['case_id']}  (root: {root})\n"
                 f"patterns: {pattern_summary}", fontsize=11)
    ax.axis("off")
    plt.tight_layout()

    if save_path is None:
        save_dir = os.path.join(data_dir, "graphs")
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, f"{case['case_id']}_{ctyp}.png")
    plt.savefig(save_path, dpi=150)
    plt.close(fig)

    return {
        "case_id": case["case_id"],
        "typology": ctyp,
        "root_account": root,
        "num_nodes": graph.number_of_nodes(),
        "num_edges": graph.number_of_edges(),
        "patterns": result["patterns"],
        "graph": graph,
        "image_path": save_path,
    }


def visualize_case_timeline(
    data_dir="../mock_data",
    cases_file="../mock_data/detected_cases.json",
    case_id=None,
    typology="money_mule",
    save_path=None,
    figsize=(12, 5),
):
    """Companion function for the two non-graph typologies (money_mule,
    account_swap) so the whole demo runs end to end without needing the
    frontend - draws a simple matplotlib timeline instead of a NetworkX
    graph, since that's what network_layer.py itself returns for these."""
    store = DataStore(data_dir)
    with open(cases_file) as f:
        cases = json.load(f)
    case = _pick_default_case(cases, typology=typology, case_id=case_id)
    root = case["account_id"]
    ctyp = case.get("primary_trigger")
    anchor_time = datetime.fromisoformat(case["created_at"]) if case.get("created_at") else None
    window = {"start": anchor_time - timedelta(days=7), "end": anchor_time + timedelta(days=7)} if anchor_time else None

    fig, ax = plt.subplots(figsize=figsize)
    if ctyp == "money_mule":
        result = build_money_mule_timeline(store, root, time_window=window)
        for t in result["transactions"]:
            ts = datetime.fromisoformat(t["timestamp"])
            color = "#2ca02c" if t["direction"] == "in" else "#d62728"
            ax.scatter(ts, t["amount"], color=color, s=60, zorder=3)
        ax.scatter([], [], color="#2ca02c", label="inbound")
        ax.scatter([], [], color="#d62728", label="outbound")
        ax.set_ylabel("Amount (INR)")
        ax.set_title(f"Money mule timeline — {case['case_id']} (account: {root})\n"
                     f"ratio out/in: {result['summary']['outbound_to_inbound_ratio']}, "
                     f"patterns: {', '.join(result['patterns']) or 'none'}")
    elif ctyp == "account_swap":
        result = build_account_swap_timeline(store, root, time_window=window)
        type_y = {"geo": 0, "device_change": 1, "sim_change": 2, "transaction": 3}
        type_color = {"geo": "#1f77b4", "device_change": "#9467bd", "sim_change": "#d62728", "transaction": "#2ca02c"}
        for e in result["events"]:
            ts = datetime.fromisoformat(e["timestamp"])
            ax.scatter(ts, type_y[e["event_type"]], color=type_color[e["event_type"]], s=80, zorder=3)
        ax.set_yticks(list(type_y.values()))
        ax.set_yticklabels(list(type_y.keys()))
        ax.set_title(f"Account-swap security timeline — {case['case_id']} (account: {root})\n"
                     f"patterns: {', '.join(result['patterns']) or 'none'}")
    else:
        raise ValueError(f"typology={ctyp!r} is graph-based - use visualize_case_network() instead.")

    ax.legend(loc="upper right", frameon=False)
    fig.autofmt_xdate()
    plt.tight_layout()

    if save_path is None:
        save_dir = os.path.join(data_dir, "graphs")
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, f"{case['case_id']}_{ctyp}.png")
    plt.savefig(save_path, dpi=150)
    plt.close(fig)

    return {"case_id": case["case_id"], "typology": ctyp, "image_path": save_path, "patterns": result["patterns"]}


if __name__ == "__main__":
    for typ in ("smurfing", "reverse_smurfing"):
        info = visualize_case_network(typology=typ)
        print(f"{typ}: {info['case_id']} -> {info['num_nodes']} nodes, {info['num_edges']} edges -> {info['image_path']}")
    for typ in ("money_mule", "account_swap"):
        info = visualize_case_timeline(typology=typ)
        print(f"{typ}: {info['case_id']} -> {info['image_path']}")