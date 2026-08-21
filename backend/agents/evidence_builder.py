"""
agents/evidence_builder.py
============================
Replaces the old per-table Supabase agents (beneficiary_agent.py,
transaction_agent.py, device_geo_agent.py) and generalizes
utils/derived_signals.py beyond account_swap.

gather_evidence(store, case) assembles exactly what the Hypothesis Agents
reason over: the account profile, the REAL alerts our own detect_all()
produces for that account (not the mock generator's illustrative labels),
the Network Evidence Layer's output for the case's typology, and the raw
beneficiary/device/geo/transaction records. This is the same DataStore and
the same network_layer.generate_network_evidence() the graph-visualization
demo uses - one evidence path, no drift between what triggered a case and
what the LLM agents get to see.

compute_derived_signals(evidence, as_of) turns that into the compact,
typology-aware numbers the prompts reference (structuring ratios, pass-
through ratios, beneficiary recency, device/geo flags, amount anomalies,
balance drawdown) - one function, branching internally per typology,
instead of one hardcoded account-swap-only version.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone

from data_store import DataStore
from detection_layer import detect_all
from network_layer import generate_network_evidence

MAX_TXNS_IN_EVIDENCE = 15  # keep the prompt bounded regardless of account history length


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


# ----------------------------------------------------------------------
# Evidence assembly
# ----------------------------------------------------------------------
def gather_evidence(store: DataStore, case: dict) -> dict:
    """`case` just needs case_id, account_id, primary_trigger - i.e. one row
    of mock_data/cases.csv. Everything else is pulled live from the store,
    exactly like a real Case Intake handing a case to the investigation
    pipeline would."""
    account_id = case["account_id"]
    account = store.accounts_by_id.get(account_id, {})
    typology = case.get("primary_trigger")

    alerts = detect_all(store, account_id)
    alerts_for_typology = [a for a in alerts if a["typology"] == typology] or alerts

    network_evidence = None
    if typology in ("smurfing", "reverse_smurfing", "money_mule", "account_swap"):
        try:
            network_evidence = generate_network_evidence(
                store, {"case_id": case["case_id"], "account_id": account_id, "primary_trigger": typology}
            )
        except Exception as e:
            network_evidence = {"error": str(e)}

    beneficiaries = store.bene_by_account.get(account_id, [])
    devices = store.devices_by_account.get(account_id, [])
    geo_events = store.geo_by_account.get(account_id, [])
    inbound = store.inbound_by_account.get(account_id, [])
    outbound = store.outbound_by_account.get(account_id, [])
    txns = sorted(inbound + outbound, key=lambda t: t["timestamp"], reverse=True)[:MAX_TXNS_IN_EVIDENCE]

    def _clean_txn(t):
        return {"transaction_id": t["transaction_id"], "sender_account_id": t["sender_account_id"],
                "receiver_account_id": t["receiver_account_id"], "timestamp": _iso(t["timestamp"]),
                "amount": t["amount"], "currency": t["currency"], "channel": t["channel"],
                "beneficiary_id": t.get("beneficiary_id") or None, "device_id": t.get("device_id") or None,
                "geo_event_id": t.get("geo_event_id") or None, "is_international": t["is_international"],
                "balance_after": t["balance_after"]}

    def _clean_dev(d):
        return {"device_id": d["device_id"], "device_type": d["device_type"],
                "is_trusted_device": d["is_trusted_device"], "sim_change_detected": d["sim_change_detected"],
                "jailbroken_rooted": d["jailbroken_rooted"], "first_seen_date": _iso(d["first_seen_date"]),
                "last_seen_date": _iso(d["last_seen_date"])}

    def _clean_geo(g):
        return {"geo_event_id": g["geo_event_id"], "timestamp": _iso(g["timestamp"]), "city": g["city"],
                "country": g["country"], "is_vpn_or_proxy": g["is_vpn_or_proxy"],
                "distance_from_last_location_km": g["distance_from_last_location_km"],
                "registered_country_match": g["registered_country_match"]}

    def _clean_bene(b):
        return {"beneficiary_id": b["beneficiary_id"], "beneficiary_name": b["beneficiary_name"],
                "relationship_to_account_holder": b["relationship_to_account_holder"],
                "date_added": b["date_added"], "is_first_time_beneficiary": b["is_first_time_beneficiary"],
                "is_verified": b["is_verified"], "beneficiary_risk_flag": b["beneficiary_risk_flag"],
                "total_transfers_to_date": b["total_transfers_to_date"]}

    return {
        "case_id": case["case_id"],
        "account_id": account_id,
        "typology": typology,
        "account_profile": {
            "risk_rating": account.get("risk_rating"), "kyc_status": account.get("kyc_status"),
            "account_type": account.get("account_type"), "account_open_date": account.get("account_open_date"),
            "avg_monthly_txn_count": account.get("avg_monthly_txn_count"),
            "avg_monthly_txn_amount": account.get("avg_monthly_txn_amount"),
        },
        "detection_alerts": alerts_for_typology,
        "network_evidence": network_evidence,
        "beneficiaries": [_clean_bene(b) for b in beneficiaries],
        "devices": [_clean_dev(d) for d in devices],
        "geo_events": [_clean_geo(g) for g in geo_events],
        "recent_transactions": [_clean_txn(t) for t in txns],
    }


# ----------------------------------------------------------------------
# Derived signals - one function, typology-aware branching
# ----------------------------------------------------------------------
def compute_derived_signals(evidence: dict, as_of: datetime = None) -> dict:
    as_of = as_of or datetime.now(timezone.utc)
    typology = evidence.get("typology")
    account = evidence.get("account_profile", {})
    baseline = account.get("avg_monthly_txn_amount") or 0
    account_id = evidence.get("account_id")
    txns = evidence.get("recent_transactions", [])
    net = evidence.get("network_evidence") or {}

    signals = {"typology": typology}

    # -- universal signals (relevant regardless of typology) ------------
    amount_anomalies = []
    for t in txns:
        if baseline and t["amount"] > 3 * baseline:
            amount_anomalies.append({"transaction_id": t["transaction_id"], "amount": t["amount"],
                                      "ratio_to_account_baseline": round(t["amount"] / baseline, 2)})
    signals["amount_anomalies"] = amount_anomalies

    drawdowns = []
    for t in txns:
        bal_after = t.get("balance_after")
        if bal_after is not None and t["sender_account_id"] == account_id:
            bal_before = bal_after + t["amount"]
            if bal_before > 0:
                drawdowns.append(t["amount"] / bal_before * 100)
    signals["max_balance_drawdown_pct"] = round(max(drawdowns), 1) if drawdowns else 0.0

    bene_signals = []
    for b in evidence.get("beneficiaries", []):
        hours = None
        try:
            added = datetime.strptime(b["date_added"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            hours = round((as_of - added).total_seconds() / 3600, 1)
        except (ValueError, TypeError):
            pass
        bene_signals.append({"beneficiary_id": b["beneficiary_id"], "hours_since_added": hours,
                              "is_verified": b["is_verified"], "is_first_time_beneficiary": b["is_first_time_beneficiary"],
                              "relationship_to_account_holder": b["relationship_to_account_holder"]})
    signals["beneficiary_signals"] = bene_signals

    signals["device_signals"] = [
        {"device_id": d["device_id"], "is_trusted_device": d["is_trusted_device"],
         "sim_change_detected": d["sim_change_detected"], "jailbroken_rooted": d["jailbroken_rooted"]}
        for d in evidence.get("devices", [])
    ]
    signals["geo_signals"] = [
        {"geo_event_id": g["geo_event_id"], "is_vpn_or_proxy": g["is_vpn_or_proxy"],
         "registered_country_match": g["registered_country_match"],
         "distance_from_last_location_km": g["distance_from_last_location_km"]}
        for g in evidence.get("geo_events", [])
    ]

    # -- typology-specific signals, sourced from the Network Evidence Layer's
    #    own output so the numbers here match exactly what the graph/timeline
    #    visualizer would show -----------------------------------------------
    if typology in ("smurfing", "reverse_smurfing") and net and "evidence" in net:
        nodes = net["evidence"].get("nodes", [])
        edges = net["evidence"].get("edges", [])
        root = net["evidence"].get("root_account")
        patterns = net.get("patterns", [])

        if typology == "smurfing":
            inbound_edges = [e["data"] for e in edges if e["data"]["target"] == root]
            outbound_edges = [e["data"] for e in edges if e["data"]["source"] == root]
            aggregate_in = sum(e["amount"] for e in inbound_edges)
            signals["structuring"] = {
                "unique_inbound_senders": len({e["source"] for e in inbound_edges}),
                "aggregate_inbound_amount": round(aggregate_in, 2),
                "ratio_to_account_baseline": round(aggregate_in / baseline, 2) if baseline else None,
                "unique_outbound_receivers": len({e["target"] for e in outbound_edges}),
                "rapid_onward_transfer": any(p["type"] == "rapid_onward_transfer" for p in patterns),
                "multi_hop_depth": next((p["depth"] for p in patterns if p["type"] == "multi_hop_flow"), 0),
            }
        else:
            outbound_edges = [e["data"] for e in edges if e["data"]["source"] == root]
            aggregate_out = sum(e["amount"] for e in outbound_edges)
            signals["structuring"] = {
                "unique_outbound_receivers": len({e["target"] for e in outbound_edges}),
                "aggregate_outbound_amount": round(aggregate_out, 2),
                "ratio_to_account_baseline": round(aggregate_out / baseline, 2) if baseline else None,
                "one_to_many_fan_out": any(p["type"] == "one_to_many" for p in patterns),
                "multi_hop_depth": next((p["depth"] for p in patterns if p["type"] == "multi_hop_flow"), 0),
            }

    elif typology == "money_mule" and net and "evidence" in net:
        summary = dict(net["evidence"].get("summary", {}))
        summary["patterns"] = net.get("patterns", [])
        signals["mule_pattern"] = summary

    elif typology == "account_swap" and net and "evidence" in net:
        signals["takeover_pattern"] = {"patterns": net.get("patterns", [])}

    return signals