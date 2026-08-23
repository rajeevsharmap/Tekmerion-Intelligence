"""
network_layer.py
==================
Network Evidence Layer for the Autonomous Financial Crime Investigation
hackathon MVP.

Answers "what happened AROUND this account, for THIS case?" - never "is
this suspicious?" (that's detection_layer.py's job). This layer:

    SMURFING / REVERSE_SMURFING -> NetworkX DiGraph, max depth 3,
                                    traversed against the GLOBAL transaction
                                    database (not just the case's own
                                    alerted transactions) -> Cytoscape.js JSON

    MONEY_MULE                 -> inflow/outflow transaction timeline
                                   (NOT a graph)

    ACCOUNT_SWAP                -> behavioral transaction-vs-time timeline:
                                   SIM/device/geo events plotted alongside
                                   transaction amount/frequency, with a
                                   baseline-vs-recent behavioral_summary
                                   (NOT a graph)

Every call to generate_network_evidence(store, case) is scoped to exactly
ONE case - it returns one case-specific evidence response, never a batch.
Persisting many cases' evidence (see __main__ below) means calling it once
per case and writing one file per case_id, not building one shared object
for all cases.

Per spec section 19: discovering X -> Y -> Z -> A during traversal does
NOT retroactively add TXN(Y,Z) / TXN(Z,A) to the originating case's alert
list. Those become *contextual network evidence* only, wrapped separately
via wrap_as_evidence(). If Z independently trips a rule, that's the
Detection Agent's job to raise as its own alert/case - this layer never
creates alerts or cases, and never mutates the `case` object it's given.
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
# 4. Account swap: behavioral transaction-vs-time timeline (NOT a graph)
# ----------------------------------------------------------------------
def build_account_swap_timeline(store, account_id, time_window=None, anchor_time=None):
    """Security + transaction events over time, PLUS a behavioral_summary
    comparing the account's normal baseline activity (value & frequency)
    against the period right around the suspicious event - this is what
    lets a "sudden increase in transaction value/frequency" actually be
    observed rather than just listed as raw events. Bar/timeline chart
    ready: each transaction event carries amount + timestamp + direction,
    and behavioral_summary gives the before/after comparison to annotate it
    with."""
    events = []
    all_txns = sorted(store.inbound_by_account.get(account_id, []) + store.outbound_by_account.get(account_id, []),
                       key=lambda t: t["timestamp"])

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

    for t in all_txns:
        is_out = t["sender_account_id"] == account_id
        bene = store.bene_by_id.get(t.get("beneficiary_id")) if is_out else None
        events.append({
            "event_id": t["transaction_id"],
            "timestamp": t["timestamp"],
            "event_type": "transaction",
            "direction": "out" if is_out else "in",
            "amount": t["amount"],
            "currency": t["currency"],
            "beneficiary_id": (t.get("beneficiary_id") or None) if is_out else None,
            "is_first_time_beneficiary": bool(bene and bene["is_first_time_beneficiary"]),
        })

    if time_window:
        start, end = time_window["start"], time_window["end"]
        events = [e for e in events if start <= e["timestamp"] <= end]
        all_txns = [t for t in all_txns if start <= t["timestamp"] <= end]

    events.sort(key=lambda e: e["timestamp"])

    patterns = []
    sim_events = [e for e in events if e["event_type"] == "sim_change"]
    device_events = [e for e in events if e["event_type"] == "device_change" and not e["is_trusted_device"]]
    geo_jumps = [e for e in events if e["event_type"] == "geo" and e["distance_from_last_location_km"] > 500]
    txn_events = [e for e in events if e["event_type"] == "transaction"]
    out_txn_events = [e for e in txn_events if e["direction"] == "out"]

    account = store.accounts_by_id.get(account_id)
    for txn in out_txn_events:
        if any(s["timestamp"] <= txn["timestamp"] and (txn["timestamp"] - s["timestamp"]) <= timedelta(hours=24) for s in sim_events):
            patterns.append("sim_change_before_transaction")
        if any(d["timestamp"] <= txn["timestamp"] and (txn["timestamp"] - d["timestamp"]) <= timedelta(hours=24) for d in device_events):
            patterns.append("new_device_before_transaction")
        if any(g["timestamp"] <= txn["timestamp"] and (txn["timestamp"] - g["timestamp"]) <= timedelta(hours=4) for g in geo_jumps):
            patterns.append("rapid_geographic_change")
        if txn.get("is_first_time_beneficiary"):
            patterns.append("new_beneficiary")
        if account and txn["amount"] > 3 * account["avg_monthly_txn_amount"]:
            patterns.append("high_value_transaction")

    # Behavioral baseline vs. the window right around the anchor event - this is
    # what makes "sudden increase in value/frequency" an observable number, not
    # just a qualitative pattern label.
    behavioral_summary = _compute_behavioral_summary(all_txns, anchor_time)
    if behavioral_summary.get("amount_deviation_ratio") and behavioral_summary["amount_deviation_ratio"] >= 3:
        patterns.append("sudden_value_increase")
    if behavioral_summary.get("frequency_deviation_ratio") and behavioral_summary["frequency_deviation_ratio"] >= 3:
        patterns.append("sudden_frequency_increase")
    patterns = sorted(set(patterns))

    for e in events:
        e["timestamp"] = e["timestamp"].isoformat()

    return {
        "account_id": account_id,
        "events": events,
        "behavioral_summary": behavioral_summary,
        "patterns": patterns,
        "source_transactions": [e["event_id"] for e in txn_events],
    }


def _compute_behavioral_summary(all_txns, anchor_time, recent_window_hours=48):
    """Splits an account's transactions into "baseline" (everything more than
    `recent_window_hours` from the anchor) and "recent" (within that window)
    and compares average amount and daily frequency between the two - the
    numeric backbone for a "normal activity vs sudden change" bar/timeline
    chart."""
    if not all_txns or anchor_time is None:
        return {"baseline_avg_amount": None, "baseline_avg_daily_count": None,
                "recent_avg_amount": None, "recent_daily_count": None,
                "amount_deviation_ratio": None, "frequency_deviation_ratio": None,
                "anchor_time": anchor_time.isoformat() if anchor_time else None}

    lo = anchor_time - timedelta(hours=recent_window_hours)
    hi = anchor_time + timedelta(hours=recent_window_hours)
    baseline = [t for t in all_txns if not (lo <= t["timestamp"] <= hi)]
    recent = [t for t in all_txns if lo <= t["timestamp"] <= hi]

    baseline_span_days = max(1.0, (all_txns[-1]["timestamp"] - all_txns[0]["timestamp"]).total_seconds() / 86400
                              - (2 * recent_window_hours / 24))
    baseline_avg_amount = round(statistics.mean(t["amount"] for t in baseline), 2) if baseline else None
    baseline_avg_daily_count = round(len(baseline) / baseline_span_days, 3) if baseline else None
    recent_span_days = max(1.0, (2 * recent_window_hours) / 24)
    recent_avg_amount = round(statistics.mean(t["amount"] for t in recent), 2) if recent else None
    recent_daily_count = round(len(recent) / recent_span_days, 3) if recent else None

    amount_ratio = round(recent_avg_amount / baseline_avg_amount, 2) if (recent_avg_amount and baseline_avg_amount) else None
    freq_ratio = round(recent_daily_count / baseline_avg_daily_count, 2) if (recent_daily_count and baseline_avg_daily_count) else None

    return {
        "baseline_avg_amount": baseline_avg_amount,
        "baseline_avg_daily_count": baseline_avg_daily_count,
        "recent_avg_amount": recent_avg_amount,
        "recent_daily_count": recent_daily_count,
        "amount_deviation_ratio": amount_ratio,
        "frequency_deviation_ratio": freq_ratio,
        "anchor_time": anchor_time.isoformat(),
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
    """Case-scoped Network Evidence Layer entry point - the internal
    equivalent of `GET /api/cases/{case_id}/network-evidence`.

    ONE CASE IN -> ONE CASE-SCOPED EVIDENCE RESPONSE OUT. `case` must carry
    at least case_id, account_id, primary_trigger, and created_at (the real
    case object from bundle_alerts_into_cases, or a mock_data ground-truth
    row - both shapes work since evidence_builder.gather_evidence() passes
    the case straight through rather than reconstructing a subset of it).

    Typology dispatch is intentionally a flat, explicit if/elif chain - one
    branch per known typology, each calling exactly one builder function, no
    shared "everything else" branch and no double-negative boolean logic.
    An unrecognized typology gets its own explicit, clearly-labeled fallback
    (network_type="unclassified") rather than silently being treated as
    money_mule or anything else."""
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

    elif typology == "money_mule":
        # rapid pass-through happens within hours (MM-002: <=6h) - a 7-day window
        # would dilute the median-gap signal with unrelated later activity.
        effective_window = time_window or _default_window(anchor_time, days=2)
        result = build_money_mule_timeline(store, account_id, time_window=effective_window)
        visualization_type, network_type = "transaction_timeline", "money_mule"
        evidence = {"transactions": result["transactions"], "summary": result["summary"]}

    elif typology == "account_swap":
        effective_window = time_window or _default_window(anchor_time, days=7)
        result = build_account_swap_timeline(store, account_id, time_window=effective_window, anchor_time=anchor_time)
        visualization_type, network_type = "behavioral_transaction_timeline", "account_swap"
        evidence = {"events": result["events"], "behavioral_summary": result["behavioral_summary"]}

    else:
        # explicit, honest fallback for anything that isn't one of the 4 known
        # typologies (e.g. the mock dataset's "behavioral_deviation" ground-truth
        # label) - a plain transaction timeline, clearly marked as unclassified
        # rather than silently mislabeled as money_mule.
        effective_window = time_window or _default_window(anchor_time, days=7)
        result = build_money_mule_timeline(store, account_id, time_window=effective_window)
        visualization_type, network_type = "transaction_timeline", "unclassified"
        evidence = {"transactions": result["transactions"], "summary": result["summary"]}

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


def _default_window(anchor_time, days):
    if anchor_time is None:
        return None
    return {"start": anchor_time - timedelta(days=days), "end": anchor_time + timedelta(days=days)}


def wrap_as_evidence(network_response, confidence="high"):
    """Wrap a generate_network_evidence() response as an Evidence Store
    object (spec section 18) ready for the Evidence Store to persist.

    source_transactions and network_scope are kept as their own top-level
    fields (not folded into `data`) specifically so this record can prove,
    on inspection, which transactions were CONTEXTUAL global-traversal
    discoveries for this case versus the originating alert's own
    transaction_id(s) recorded on the case/alert objects - the two must
    stay visibly distinct, never merged."""
    return {
        "evidence_id": f"EVID-{uuid.uuid4().hex[:8].upper()}",
        "case_id": network_response["case_id"],
        "account_id": network_response["account_id"],
        "evidence_type": "network_analysis",
        "typology": network_response["typology"],
        "source": "network_evidence_layer",
        "confidence": confidence,
        "data": {
            "visualization_type": network_response["visualization_type"],
            **network_response["evidence"],
            "patterns": network_response["patterns"],
        },
        # contextual only - discovered via traversal of the GLOBAL transaction
        # database, not limited to the case's own originating alert(s). Never
        # merged into the case/alert's own transaction_id/alert_ids.
        "source_transactions": network_response["source_transactions"],
        "network_scope": network_response["network_scope"],
        "generated_at": network_response["generated_at"],
    }


if __name__ == "__main__":
    import argparse
    import json
    import os

    parser = argparse.ArgumentParser(description="Generate CASE-SCOPED network evidence for detection_layer.py's "
                                                   "own cases.csv output. Writes ONE evidence file per case_id - "
                                                   "for the full Detection -> Case -> Evidence chain in one process, "
                                                   "use run_pipeline.py instead.")
    parser.add_argument("--data_dir", default="mock_data")
    parser.add_argument("--cases_file", default="pipeline_output/cases.json")
    parser.add_argument("--out_dir", default="pipeline_output/evidence")
    parser.add_argument("--limit", type=int, default=None, help="max cases to render (omit for all)")
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    store = DataStore(args.data_dir)
    with open(args.cases_file) as f:
        cases = json.load(f)
    if args.limit:
        cases = cases[: args.limit]

    for case in cases:
        # ONE case in -> ONE case-scoped evidence response out. Each case is
        # requested and persisted independently - never batched into one
        # shared object.
        net = generate_network_evidence(store, case)
        evidence = wrap_as_evidence(net)
        with open(f"{args.out_dir}/{case['case_id']}.json", "w") as f:
            json.dump(evidence, f, indent=2, default=str)
        print(f"{case['case_id']} [{case['primary_trigger']}] -> "
              f"{net['visualization_type']} ({net['network_type']}), {len(net['patterns'])} pattern(s), "
              f"{len(net['source_transactions'])} source txn(s) -> {args.out_dir}/{case['case_id']}.json")

    print(f"\nWrote {len(cases)} case-scoped evidence file(s) to {args.out_dir}/")
    