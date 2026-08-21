"""
network_layer.py
==================
Network Evidence Layer for the Autonomous Financial Crime Investigation
hackathon MVP.

Answers "what happened AROUND this account?" - never "is this suspicious?"
(that's detection_layer.py's job). This layer:

    SMURFING / REVERSE_SMURFING -> NetworkX DiGraph, max depth 3,
                                    traversed against the GLOBAL transaction
                                    database (not just the case's own
                                    alerted transactions) -> Cytoscape.js JSON

    MONEY_MULE                 -> inflow/outflow transaction timeline

    ACCOUNT_SWAP                -> security + transaction timeline
                                    (SIM / device / geo / beneficiary / txn)

Per spec section 19: discovering X -> Y -> Z -> A during traversal does
NOT retroactively add TXN(Y,Z) / TXN(Z,A) to the originating case's alert
list. Those become *contextual network evidence* only, wrapped separately
via wrap_as_evidence(). If Z independently trips a rule, that's the
Detection Agent's job to raise as its own alert/case - this layer never
creates alerts or cases.
"""

import statistics
import uuid
from datetime import datetime, timedelta

import networkx as nx

from data_store import DataStore

MAX_DEPTH = 3
CASE_BUNDLE_WINDOW_HOURS = 24


# ----------------------------------------------------------------------
# 1. Smurfing network (many-to-one aggregation, then multi-hop onward flow)
# ----------------------------------------------------------------------
def _filter_by_window(txns, anchor_time, window_hours):
    if anchor_time is None or window_hours is None:
        return txns
    lo = anchor_time - timedelta(hours=window_hours)
    hi = anchor_time + timedelta(hours=window_hours)
    return [t for t in txns if lo <= t["timestamp"] <= hi]


def build_smurf_network(store, root_account, max_depth=MAX_DEPTH, direction="both",
                         anchor_time=None, window_hours=72):
    """BFS over the GLOBAL transaction set starting at root_account, up to
    max_depth hops, in `direction` ('both' | 'inbound' | 'outbound').

    Traversal is time-windowed around `anchor_time` (+/- window_hours, default
    72h). Without this, 3-hop BFS on a densely-connected bank-wide graph
    touches most of the customer base within a few hops (small-world effect)
    and returns an unusable "everyone is connected to everyone" graph instead
    of the actual fraud ring. A real investigator traces flows near the
    suspicious event, not an account's entire transaction history."""
    graph = nx.DiGraph()
    visited = set()
    queue = [(root_account, 0)]
    source_transactions = []

    while queue:
        acc, depth = queue.pop(0)
        if acc in visited:
            continue
        visited.add(acc)
        if depth >= max_depth:
            continue

        related = []
        if direction in ("both", "inbound"):
            related += store.inbound_by_account.get(acc, [])
        if direction in ("both", "outbound"):
            related += store.outbound_by_account.get(acc, [])
        related = _filter_by_window(related, anchor_time, window_hours)

        for t in related:
            u, v = t["sender_account_id"], t["receiver_account_id"]
            graph.add_edge(u, v, transaction_id=t["transaction_id"], amount=t["amount"],
                            currency=t["currency"], timestamp=t["timestamp"], depth=depth + 1)
            source_transactions.append(t["transaction_id"])
            nxt = v if u == acc else u
            if nxt not in visited and nxt in store.accounts_by_id:
                queue.append((nxt, depth + 1))

    nodes = _nodes_to_cytoscape(graph, root_account)
    edges = _edges_to_cytoscape(graph)
    patterns = _smurf_patterns(graph, root_account)
    return {
        "root_account": root_account,
        "max_depth": max_depth,
        "nodes": nodes,
        "edges": edges,
        "patterns": patterns,
        "source_transactions": sorted(set(source_transactions)),
    }


def _smurf_patterns(graph, root):
    patterns = []
    if root not in graph:
        return patterns

    inbound_to_root = list(graph.predecessors(root))
    if len(inbound_to_root) >= 3:
        patterns.append({
            "type": "many_to_one",
            "account": root,
            "supporting_transactions": [graph[u][root]["transaction_id"] for u in inbound_to_root],
        })

    for s in graph.successors(root):
        edge = graph[root][s]
        # find fastest matching inbound edge to estimate onward-transfer speed
        gaps = [(edge["timestamp"] - graph[p][root]["timestamp"]).total_seconds() / 60
                for p in graph.predecessors(root)
                if graph[p][root]["timestamp"] <= edge["timestamp"]]
        if gaps and min(gaps) <= 360:
            patterns.append({
                "type": "rapid_onward_transfer",
                "from": root, "to": s,
                "time_difference_minutes": round(min(gaps), 1),
            })

    path = _longest_path_from(graph, root)
    if len(path) > 2:
        patterns.append({"type": "multi_hop_flow", "path": path, "depth": len(path) - 1})
    return patterns


# ----------------------------------------------------------------------
# 2. Reverse smurfing network (one-to-many distribution, downstream depth)
# ----------------------------------------------------------------------
def build_reverse_smurf_network(store, root_account, max_depth=MAX_DEPTH, direction="outbound",
                                 anchor_time=None, window_hours=72):
    graph = nx.DiGraph()
    visited = set()
    queue = [(root_account, 0)]
    source_transactions = []

    while queue:
        acc, depth = queue.pop(0)
        if acc in visited:
            continue
        visited.add(acc)
        if depth >= max_depth:
            continue
        outbound = _filter_by_window(store.outbound_by_account.get(acc, []), anchor_time, window_hours)
        for t in outbound:
            u, v = t["sender_account_id"], t["receiver_account_id"]
            graph.add_edge(u, v, transaction_id=t["transaction_id"], amount=t["amount"],
                            currency=t["currency"], timestamp=t["timestamp"], depth=depth + 1)
            source_transactions.append(t["transaction_id"])
            if v not in visited and v in store.accounts_by_id:
                queue.append((v, depth + 1))

    nodes = _nodes_to_cytoscape(graph, root_account, root_role="root", other_role="distribution_target")
    edges = _edges_to_cytoscape(graph)
    patterns = []
    if root_account in graph:
        successors = list(graph.successors(root_account))
        if len(successors) >= 3:
            patterns.append({"type": "one_to_many", "source": root_account, "target_count": len(successors)})
        out_edges_from_root = graph.out_degree(root_account)
        if out_edges_from_root >= 3:
            patterns.append({"type": "amount_fragmentation", "source": root_account, "transaction_count": out_edges_from_root})
        path = _longest_path_from(graph, root_account)
        if len(path) > 2:
            patterns.append({"type": "multi_hop_flow", "path": path, "depth": len(path) - 1})

    return {
        "root_account": root_account,
        "max_depth": max_depth,
        "nodes": nodes,
        "edges": edges,
        "patterns": patterns,
        "source_transactions": sorted(set(source_transactions)),
    }


# ----------------------------------------------------------------------
# 3. Money mule timeline (inflow/outflow, NOT a graph)
# ----------------------------------------------------------------------
def build_money_mule_timeline(store, account_id, time_window=None):
    inbound = store.inbound_by_account.get(account_id, [])
    outbound = store.outbound_by_account.get(account_id, [])
    txns = sorted(inbound + outbound, key=lambda t: t["timestamp"])

    if time_window:
        start, end = time_window["start"], time_window["end"]
        txns = [t for t in txns if start <= t["timestamp"] <= end]
        inbound = [t for t in inbound if start <= t["timestamp"] <= end]
        outbound = [t for t in outbound if start <= t["timestamp"] <= end]

    events = []
    for t in txns:
        is_in = t["receiver_account_id"] == account_id
        events.append({
            "transaction_id": t["transaction_id"],
            "timestamp": t["timestamp"].isoformat(),
            "direction": "in" if is_in else "out",
            "counterparty": t["sender_account_id"] if is_in else t["receiver_account_id"],
            "amount": t["amount"],
            "currency": t["currency"],
            "channel": t.get("channel"),
            "beneficiary_id": t.get("beneficiary_id") or None,
            "device_id": t.get("device_id") or None,
            "geo_event_id": t.get("geo_event_id") or None,
        })

    total_in = sum(t["amount"] for t in inbound)
    total_out = sum(t["amount"] for t in outbound)
    ratio = round(total_out / total_in, 3) if total_in else 0.0

    gaps = []
    for o in outbound:
        preceding = [i for i in inbound if i["timestamp"] <= o["timestamp"]]
        if preceding:
            gaps.append((o["timestamp"] - max(i["timestamp"] for i in preceding)).total_seconds() / 60)
    median_gap = round(statistics.median(gaps), 1) if gaps else None

    patterns = []
    if ratio >= 0.70:
        patterns.append("rapid_fund_pass_through")
    if ratio >= 0.80:
        patterns.append("high_outbound_inbound_ratio")
    counterparties = {t["sender_account_id"] for t in inbound} | {t["receiver_account_id"] for t in outbound}
    if len(counterparties) >= 4:
        patterns.append("multiple_counterparties")

    return {
        "account_id": account_id,
        "transactions": events,
        "summary": {
            "total_inbound": round(total_in, 2),
            "total_outbound": round(total_out, 2),
            "outbound_to_inbound_ratio": ratio,
            "median_inbound_to_outbound_minutes": median_gap,
        },
        "patterns": patterns,
        "source_transactions": [t["transaction_id"] for t in txns],
    }


# ----------------------------------------------------------------------
# 4. Account swap security + transaction timeline (NOT a graph)
# ----------------------------------------------------------------------
def build_account_swap_timeline(store, account_id, time_window=None):
    events = []

    for g in store.geo_by_account.get(account_id, []):
        events.append({
            "event_id": g["geo_event_id"],
            "timestamp": g["timestamp"],
            "event_type": "geo",
            "city": g["city"],
            "country": g["country"],
            "distance_from_last_location_km": g["distance_from_last_location_km"],
            "registered_country_match": g["registered_country_match"],
            "is_vpn_or_proxy": g["is_vpn_or_proxy"],
        })

    for d in store.devices_by_account.get(account_id, []):
        events.append({
            "event_id": d["device_id"],
            "timestamp": d["first_seen_date"],
            "event_type": "device_change",
            "device_id": d["device_id"],
            "is_trusted_device": d["is_trusted_device"],
        })
        if d["sim_change_detected"]:
            events.append({
                "event_id": f"{d['device_id']}-SIM",
                "timestamp": d["first_seen_date"],
                "event_type": "sim_change",
                "sim_change_detected": True,
            })

    for t in store.outbound_by_account.get(account_id, []):
        bene = store.bene_by_id.get(t.get("beneficiary_id"))
        events.append({
            "event_id": t["transaction_id"],
            "timestamp": t["timestamp"],
            "event_type": "transaction",
            "direction": "out",
            "amount": t["amount"],
            "currency": t["currency"],
            "beneficiary_id": t.get("beneficiary_id") or None,
            "is_first_time_beneficiary": bool(bene and bene["is_first_time_beneficiary"]),
        })

    if time_window:
        start, end = time_window["start"], time_window["end"]
        events = [e for e in events if start <= e["timestamp"] <= end]

    events.sort(key=lambda e: e["timestamp"])

    patterns = []
    sim_events = [e for e in events if e["event_type"] == "sim_change"]
    device_events = [e for e in events if e["event_type"] == "device_change" and not e["is_trusted_device"]]
    geo_jumps = [e for e in events if e["event_type"] == "geo" and e["distance_from_last_location_km"] > 500]
    txn_events = [e for e in events if e["event_type"] == "transaction"]

    for txn in txn_events:
        if any(s["timestamp"] <= txn["timestamp"] and (txn["timestamp"] - s["timestamp"]) <= timedelta(hours=24) for s in sim_events):
            patterns.append("sim_change_before_transaction")
        if any(d["timestamp"] <= txn["timestamp"] and (txn["timestamp"] - d["timestamp"]) <= timedelta(hours=24) for d in device_events):
            patterns.append("new_device_before_transaction")
        if any(g["timestamp"] <= txn["timestamp"] and (txn["timestamp"] - g["timestamp"]) <= timedelta(hours=4) for g in geo_jumps):
            patterns.append("rapid_geographic_change")
        if txn.get("is_first_time_beneficiary"):
            patterns.append("new_beneficiary")
        account = store.accounts_by_id.get(account_id)
        if account and txn["amount"] > 3 * account["avg_monthly_txn_amount"]:
            patterns.append("high_value_transaction")
    patterns = sorted(set(patterns))

    for e in events:
        e["timestamp"] = e["timestamp"].isoformat()

    return {
        "account_id": account_id,
        "events": events,
        "patterns": patterns,
        "source_transactions": [e["event_id"] for e in txn_events],
    }


# ----------------------------------------------------------------------
# 5. Cytoscape.js conversion helpers
# ----------------------------------------------------------------------
def _nodes_to_cytoscape(graph, root, root_role="root", other_role=None):
    nodes = []
    for n in graph.nodes():
        if n == root:
            role = root_role
        elif other_role:
            role = other_role
        elif graph.has_edge(n, root) and not graph.has_edge(root, n):
            role = "source"
        elif graph.has_edge(root, n):
            role = "downstream"
        else:
            role = "related"
        nodes.append({"data": {"id": n, "label": n, "role": role, "risk": "high" if n == root else "unknown"}})
    return nodes


def _edges_to_cytoscape(graph):
    edges = []
    for u, v, d in graph.edges(data=True):
        edges.append({"data": {
            "id": d["transaction_id"],
            "source": u,
            "target": v,
            "amount": d["amount"],
            "currency": d.get("currency", "INR"),
            "timestamp": d["timestamp"].isoformat() if hasattr(d["timestamp"], "isoformat") else str(d["timestamp"]),
            "depth": d["depth"],
        }})
    return edges


def _longest_path_from(graph, root, max_len=MAX_DEPTH + 1):
    if root not in graph:
        return []
    best = [root]
    stack = [(root, [root])]
    while stack:
        node, path = stack.pop()
        if len(path) > len(best):
            best = path
        if len(path) >= max_len:
            continue
        for nxt in graph.successors(node):
            if nxt not in path:
                stack.append((nxt, path + [nxt]))
    return best


# ----------------------------------------------------------------------
# 6. Dispatcher + common response contract + Evidence Store wrapping
# ----------------------------------------------------------------------
def generate_network_evidence(store, case, time_window=None):
    """POST /api/cases/{case_id}/network-evidence - picks the typology
    strategy internally and returns the common response contract
    (spec section 16)."""
    typology = case.get("primary_trigger") or (case.get("typologies") or [None])[0]
    account_id = case["account_id"]
    case_id = case["case_id"]
    anchor_time = datetime.fromisoformat(case["created_at"]) if case.get("created_at") else None

    if typology == "smurfing":
        result = build_smurf_network(store, account_id, max_depth=MAX_DEPTH, direction="both",
                                      anchor_time=anchor_time)
        visualization_type, network_type = "network", "smurfing"
        evidence = {k: result[k] for k in ("root_account", "max_depth", "nodes", "edges")}
    elif typology == "reverse_smurfing":
        result = build_reverse_smurf_network(store, account_id, max_depth=MAX_DEPTH, anchor_time=anchor_time)
        visualization_type, network_type = "network", "reverse_smurfing"
        evidence = {k: result[k] for k in ("root_account", "max_depth", "nodes", "edges")}
    else:
        # money_mule / account_swap / fallback are per-account timelines, not graph
        # traversals, so they can't explode combinatorially the way BFS can - but a
        # full year of history still dilutes the signal, so default to a window
        # around the anchor event unless the caller passed an explicit one.
        default_window = None
        if time_window is None and anchor_time is not None:
            # money_mule pass-through happens within hours (MM-002: <=6h) - a 7-day
            # window would dilute the median-gap signal with unrelated later activity.
            span_days = 2 if typology == "money_mule" else 7
            default_window = {"start": anchor_time - timedelta(days=span_days), "end": anchor_time + timedelta(days=span_days)}
        effective_window = time_window or default_window

        if typology == "money_mule" or typology not in ("smurfing", "reverse_smurfing", "account_swap"):
            result = build_money_mule_timeline(store, account_id, time_window=effective_window)
            visualization_type, network_type = "transaction_timeline", typology or "money_mule"
            evidence = {"transactions": result["transactions"], "summary": result["summary"]}
        else:  # account_swap
            result = build_account_swap_timeline(store, account_id, time_window=effective_window)
            visualization_type, network_type = "security_transaction_timeline", "account_swap"
            evidence = {"events": result["events"]}

    return {
        "case_id": case_id,
        "account_id": account_id,
        "typology": typology,
        "network_type": network_type,
        "visualization_type": visualization_type,
        "evidence": evidence,
        "patterns": result["patterns"],
        "source_transactions": result["source_transactions"],
        "generated_at": datetime.now().isoformat(),
        "network_scope": {"max_depth": MAX_DEPTH, "time_window_hours": CASE_BUNDLE_WINDOW_HOURS},
    }


def wrap_as_evidence(network_response, confidence="high"):
    """Wrap a generate_network_evidence() response as an Evidence Store
    object (spec section 18) ready for the Evidence Store to persist."""
    return {
        "evidence_id": f"EVID-{uuid.uuid4().hex[:8].upper()}",
        "case_id": network_response["case_id"],
        "evidence_type": "network_analysis",
        "typology": network_response["typology"],
        "source": "network_evidence_layer",
        "confidence": confidence,
        "data": {
            "visualization_type": network_response["visualization_type"],
            **network_response["evidence"],
            "patterns": network_response["patterns"],
        },
        "generated_at": network_response["generated_at"],
    }


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Generate network evidence for detected cases.")
    parser.add_argument("--data_dir", default="mock_data")
    parser.add_argument("--cases_file", default="mock_data/detected_cases.json")
    parser.add_argument("--out_dir", default="mock_data")
    parser.add_argument("--limit", type=int, default=8, help="max cases to render (keeps demo output small)")
    args = parser.parse_args()

    store = DataStore(args.data_dir)
    with open(args.cases_file) as f:
        cases = json.load(f)

    evidence_objects = []
    for case in cases[: args.limit]:
        net = generate_network_evidence(store, case)
        evidence_objects.append(wrap_as_evidence(net))
        print(f"{case['case_id']} [{case['primary_trigger']}] -> "
              f"{net['visualization_type']}, {len(net['patterns'])} pattern(s), "
              f"{len(net['source_transactions'])} source txn(s)")

    with open(f"{args.out_dir}/network_evidence.json", "w") as f:
        json.dump(evidence_objects, f, indent=2, default=str)