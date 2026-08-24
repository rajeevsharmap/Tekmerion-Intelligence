"""
evidence_model.py
====================
CHECKPOINT 2 - Evidence Architecture.

Defines the canonical EvidenceItem representation (docs/ARCHITECTURE.md ->
"Evidence object model") and a deterministic, per-typology-configurable
evidence-completeness model (-> "Evidence completeness model"), computed
from whatever generate_network_evidence()/the DataStore actually returned
for a case - NEVER a random draw. `random.gauss(...)`-style completeness
stays confined to generate_mock_data.py's ground truth (unchanged by this
module); this is the live equivalent, and this module is the only place
that computes the `evidence_items` / `completeness` fields persisted to
pipeline_output/evidence/{case_id}.json.

This module does not replace wrap_as_evidence() or generate_network_evidence()
in network_layer.py - it consumes their output. `run_pipeline.py` calls
build_evidence_items(store, case, network_response) and
compute_completeness(items) as an additive step after wrap_as_evidence(),
so the existing `data` blob (typology visualization payload) is unchanged
and nothing that currently reads it breaks.

Each typology's REQUIRED evidence set + weights is declared once, in
TYPOLOGY_EVIDENCE_REQUIREMENTS below - not implicit in code, not scattered
across if/elif branches - so weights/evidence types can be retuned per
typology without touching the checker functions. Weights within a typology
are documented to sum to 1.0 (see test_evidence_model.py:
test_weights_sum_to_one), but compute_completeness() normalizes by the
actual total weight regardless, so a future edit that doesn't perfectly
sum to 1.0 still produces a sane 0-100 score rather than a silently wrong
one.

An unrecognized/"unclassified" typology (network_layer.py's own explicit
fallback for anything outside the 4 known typologies) has no typed
requirement table - completeness is reported as None with an honest
`method`, never guessed or defaulted to a made-up table.
"""
import hashlib
import json
import uuid


def _content_hash_id(prefix, *parts):
    """Deterministic content-hash ID, same pattern used by audit_trail.py
    and next_best_action.py: identical inputs always yield the same ID,
    across repeated invocations and across process runs - unlike
    uuid.uuid4(), which is random per call."""
    raw = json.dumps(parts, sort_keys=True, default=str)
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:8].upper()}"

# ----------------------------------------------------------------------
# 1. Typology-specific required evidence.
#
# Smurfing/reverse_smurfing weights are the Phase 7 example weighting from
# docs/ARCHITECTURE.md's typology table, applied as-is (the doc notes this
# generic scheme is meant to be reused, not smurfing-exclusive). Money_mule
# and account_swap tables are new: built directly from ARCHITECTURE.md's
# "Required evidence" bullet lists for those typologies, grouped into the
# evidence_type granularity this codebase can actually check availability
# for today (e.g. "inbound senders" + "inbound timestamps" both live under
# money_mule's inbound_transaction_chain, since both are read off the same
# generate_network_evidence() transaction list).
# ----------------------------------------------------------------------
TYPOLOGY_EVIDENCE_REQUIREMENTS = {
    "smurfing": [
        ("transaction_chain", 0.20),
        ("temporal_pattern", 0.15),
        ("counterparty_relationship", 0.15),
        ("beneficiary_information", 0.10),
        ("device_information", 0.15),
        ("geo_information", 0.10),
        ("source_of_funds", 0.15),
    ],
    "reverse_smurfing": [
        ("transaction_chain", 0.20),
        ("temporal_pattern", 0.15),
        ("counterparty_relationship", 0.15),
        ("beneficiary_information", 0.10),
        ("device_information", 0.15),
        ("geo_information", 0.10),
        ("source_of_funds", 0.15),
    ],
    "money_mule": [
        ("inbound_transaction_chain", 0.20),
        ("outbound_transaction_chain", 0.15),
        ("pass_through_timing", 0.15),
        ("amount_retention_ratio", 0.15),
        ("counterparty_relationship", 0.10),
        ("beneficiary_information", 0.10),
        ("device_information", 0.075),
        ("geo_information", 0.075),
    ],
    "account_swap": [
        ("device_information", 0.15),
        ("sim_change_evidence", 0.15),
        ("geo_information", 0.15),
        ("impossible_travel", 0.10),
        ("high_value_transaction", 0.15),
        ("beneficiary_information", 0.15),
        ("behavioral_baseline", 0.15),
    ],
}

# Where each evidence_type is actually sourced from - mirrors
# ARCHITECTURE.md's evidence-item `source` field (e.g. "transactions.csv").
_EVIDENCE_SOURCE = {
    "transaction_chain": "network_evidence_layer",
    "temporal_pattern": "network_evidence_layer",
    "counterparty_relationship": "network_evidence_layer",
    "beneficiary_information": "beneficiaries.csv",
    "device_information": "devices.csv",
    "geo_information": "geo_events.csv",
    "source_of_funds": "not_modeled_in_dataset",
    "inbound_transaction_chain": "network_evidence_layer",
    "outbound_transaction_chain": "network_evidence_layer",
    "pass_through_timing": "network_evidence_layer",
    "amount_retention_ratio": "network_evidence_layer",
    "sim_change_evidence": "network_evidence_layer",
    "impossible_travel": "network_evidence_layer",
    "high_value_transaction": "network_evidence_layer",
    "behavioral_baseline": "network_evidence_layer",
}


# ----------------------------------------------------------------------
# 2. Checkers - one per evidence_type. Each takes (store, case, net,
#    account) and returns (available: bool, source_record_ids: list,
#    quality: "high"|"low"|None, missing_reason: str). `net` is the raw
#    dict returned by network_layer.generate_network_evidence() (i.e.
#    network_response, BEFORE wrap_as_evidence() wraps it) - `net["evidence"]`
#    is the typology-specific payload (nodes/edges, transactions/summary,
#    or events/behavioral_summary depending on typology).
# ----------------------------------------------------------------------
def _account_id_of(case, net):
    return (net or {}).get("account_id") or case["account_id"]


def _check_transaction_chain(store, case, net, account):
    edges = ((net or {}).get("evidence") or {}).get("edges", [])
    ids = sorted({e["data"]["id"] for e in edges})
    return bool(ids), ids, ("high" if len(ids) >= 3 else "low") if ids else None, "no_transaction_chain_discovered"


def _check_temporal_pattern(store, case, net, account):
    patterns = (net or {}).get("patterns", [])
    types = [p["type"] for p in patterns if isinstance(p, dict) and "type" in p]
    relevant = [t for t in types if t in ("rapid_onward_transfer", "multi_hop_flow",
                                           "one_to_many", "amount_fragmentation")]
    return bool(relevant), relevant, ("high" if relevant else None), "no_temporal_pattern_detected"


def _check_counterparty_relationship(store, case, net, account):
    nodes = ((net or {}).get("evidence") or {}).get("nodes", [])
    ids = [n["data"]["id"] for n in nodes]
    available = len(ids) > 1  # root account alone isn't a "relationship"
    return available, ids, ("high" if len(ids) >= 4 else "low") if available else None, "insufficient_counterparty_data"


def _check_beneficiary_information(store, case, net, account):
    benes = store.bene_by_account.get(_account_id_of(case, net), [])
    ids = [b["beneficiary_id"] for b in benes]
    quality = "high" if any(b.get("is_verified") for b in benes) else ("low" if ids else None)
    return bool(ids), ids, quality, "no_beneficiary_records_for_account"


def _check_device_information(store, case, net, account):
    devs = store.devices_by_account.get(_account_id_of(case, net), [])
    ids = [d["device_id"] for d in devs]
    quality = "high" if any(d.get("is_trusted_device") for d in devs) else ("low" if ids else None)
    return bool(ids), ids, quality, "no_device_records_for_account"


def _check_geo_information(store, case, net, account):
    geos = store.geo_by_account.get(_account_id_of(case, net), [])
    ids = [g["geo_event_id"] for g in geos]
    return bool(ids), ids, ("high" if len(ids) >= 2 else "low") if ids else None, "no_geo_records_for_account"


def _check_source_of_funds(store, case, net, account):
    # Not modeled anywhere in this dataset or pipeline - reported as missing,
    # honestly, rather than silently defaulted to available. See
    # ARCHITECTURE.md's "API/LLM safety" / evidence-model notes: missing
    # evidence must be a first-class state, not a guess.
    return False, [], None, "documentation_not_available"


def _check_inbound_transaction_chain(store, case, net, account):
    txns = ((net or {}).get("evidence") or {}).get("transactions", [])
    ids = [t["transaction_id"] for t in txns if t.get("direction") == "in"]
    return bool(ids), ids, ("high" if len(ids) >= 3 else "low") if ids else None, "no_inbound_transactions_in_window"


def _check_outbound_transaction_chain(store, case, net, account):
    txns = ((net or {}).get("evidence") or {}).get("transactions", [])
    ids = [t["transaction_id"] for t in txns if t.get("direction") == "out"]
    return bool(ids), ids, ("high" if ids else None), "no_outbound_transactions_in_window"


def _check_pass_through_timing(store, case, net, account):
    summary = ((net or {}).get("evidence") or {}).get("summary", {})
    gap = summary.get("median_inbound_to_outbound_minutes")
    return gap is not None, [], ("high" if gap is not None else None), "no_paired_inbound_outbound_transactions"


def _check_amount_retention_ratio(store, case, net, account):
    summary = ((net or {}).get("evidence") or {}).get("summary", {})
    total_in = summary.get("total_inbound") or 0
    total_out = summary.get("total_outbound") or 0
    available = total_in > 0 and total_out > 0
    return available, [], ("high" if available else None), "insufficient_inbound_or_outbound_volume"


def _check_sim_change_evidence(store, case, net, account):
    events = ((net or {}).get("evidence") or {}).get("events", [])
    ids = [e["event_id"] for e in events if e.get("event_type") == "sim_change"]
    return bool(ids), ids, ("high" if ids else None), "no_sim_change_event_recorded"


def _check_impossible_travel(store, case, net, account):
    hit = "rapid_geographic_change" in ((net or {}).get("patterns", []))
    events = ((net or {}).get("evidence") or {}).get("events", [])
    ids = [e["event_id"] for e in events
           if e.get("event_type") == "geo" and (e.get("distance_from_last_location_km") or 0) > 500]
    return hit, ids, ("high" if hit else None), "no_impossible_travel_pattern_detected"


def _check_high_value_transaction(store, case, net, account):
    hit = "high_value_transaction" in ((net or {}).get("patterns", []))
    events = ((net or {}).get("evidence") or {}).get("events", [])
    ids = [e["event_id"] for e in events if e.get("event_type") == "transaction" and e.get("direction") == "out"]
    return hit, ids, ("high" if hit else None), "no_high_value_transaction_detected"


def _check_behavioral_baseline(store, case, net, account):
    summary = ((net or {}).get("evidence") or {}).get("behavioral_summary", {})
    ratio = summary.get("amount_deviation_ratio")
    return ratio is not None, [], ("high" if ratio is not None else None), "insufficient_transaction_history_for_baseline"


_CHECKERS = {
    "transaction_chain": _check_transaction_chain,
    "temporal_pattern": _check_temporal_pattern,
    "counterparty_relationship": _check_counterparty_relationship,
    "beneficiary_information": _check_beneficiary_information,
    "device_information": _check_device_information,
    "geo_information": _check_geo_information,
    "source_of_funds": _check_source_of_funds,
    "inbound_transaction_chain": _check_inbound_transaction_chain,
    "outbound_transaction_chain": _check_outbound_transaction_chain,
    "pass_through_timing": _check_pass_through_timing,
    "amount_retention_ratio": _check_amount_retention_ratio,
    "sim_change_evidence": _check_sim_change_evidence,
    "impossible_travel": _check_impossible_travel,
    "high_value_transaction": _check_high_value_transaction,
    "behavioral_baseline": _check_behavioral_baseline,
}


# ----------------------------------------------------------------------
# 3. Public API
# ----------------------------------------------------------------------
def build_evidence_items(store, case, net):
    """Build the canonical list of typed EvidenceItem dicts for one case,
    per docs/ARCHITECTURE.md's "Evidence object model" section.

    `net` is the raw dict returned by
    network_layer.generate_network_evidence(store, case) - NOT the
    wrap_as_evidence()-wrapped record. Every item is derived from data
    actually present in `store`/`net`; nothing here is randomly assigned
    or hardcoded to "available". Returns [] for typologies with no
    requirement table (i.e. the "unclassified" fallback) - never a guess.
    """
    typology = (net or {}).get("typology") or case.get("primary_trigger")
    requirements = TYPOLOGY_EVIDENCE_REQUIREMENTS.get(typology)
    if not requirements:
        return []

    account_id = _account_id_of(case, net)
    account = store.accounts_by_id.get(account_id, {}) if store else {}

    items = []
    for evidence_type, weight in requirements:
        checker = _CHECKERS[evidence_type]
        available, source_record_ids, quality, missing_reason = checker(store, case, net, account)
        item = {
            # Content-hash, not uuid.uuid4(): the same case run twice must
            # produce the same evidence_id for the same evidence_type, or
            # downstream determinism checks (and stable case-memory/audit
            # references to a given piece of evidence) break.
            "evidence_id": _content_hash_id(
                "EVD", case["case_id"], evidence_type, source_record_ids
            ),
            "case_id": case["case_id"],
            "evidence_type": evidence_type,
            "source": _EVIDENCE_SOURCE.get(evidence_type, "network_evidence_layer"),
            "source_record_ids": source_record_ids,
            "required": True,
            "weight": weight,
            "available": available,
            "quality": quality,
            "supports": [typology] if available else [],
            "contradicts": [],
        }
        if not available:
            # Missing evidence is a first-class structured object (never a
            # free-text string) - see ARCHITECTURE.md's evidence object
            # model + "Missing-evidence-driven escalation" sections.
            # severity is a deterministic function of the item's configured
            # weight, not randomly or manually assigned per case.
            item["missing_reason"] = {
                "reason": missing_reason,
                "severity": "critical" if weight >= 0.15 else "moderate",
            }
        items.append(item)
    return items


def compute_completeness(evidence_items):
    """Deterministic evidence completeness, computed only from the
    EvidenceItems actually produced by build_evidence_items() for THIS
    case. No random draw anywhere in this function - see module docstring.

    Returns both a weighted score (weighted by each evidence_type's
    configured importance - ARCHITECTURE.md's "preferred version") and a
    simple available/required ratio (the "simple version"), so both are
    inspectable on the persisted record rather than only one being kept.
    """
    if not evidence_items:
        return {
            "weighted_score": None,
            "simple_score": None,
            "required_count": 0,
            "available_count": 0,
            "missing": [],
            "method": "no_requirement_table_for_typology",
        }

    total_weight = sum(i["weight"] for i in evidence_items) or 1.0
    available_weight = sum(i["weight"] for i in evidence_items if i["available"])
    required_count = len(evidence_items)
    available_count = sum(1 for i in evidence_items if i["available"])
    missing = [
        {"evidence_type": i["evidence_type"], **i["missing_reason"]}
        for i in evidence_items if not i["available"]
    ]

    return {
        "weighted_score": round(100.0 * available_weight / total_weight, 1),
        "simple_score": round(100.0 * available_count / required_count, 1),
        "required_count": required_count,
        "available_count": available_count,
        "missing": missing,
        "method": "deterministic_weighted_availability",
    }