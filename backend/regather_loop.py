"""
regather_loop.py
===================
CHECKPOINT 5 - Low-completeness re-gather loop.

    Case Completeness Score -> LOW -> RE-GATHER EVIDENCE -> (back to)
    Case Completeness Score -> ... -> HIGH -> Auditor Routing

When a case's completeness is below the configured threshold, this module
converts the SPECIFIC missing evidence (evidence_model.py's own
`completeness["missing"]` - never "redo the whole investigation") into
targeted re-gather requests and routes each one to the existing evidence
source that can plausibly satisfy it: network_layer.py's own typology
builders, re-run with a WIDER time window/traversal - the same real
DataStore, no fabricated evidence, no new data invented.

Structural, dataset-wide gaps (evidence_model.py's own permanently-
unavailable evidence types, e.g. source_of_funds - "not_modeled_in_dataset")
are never turned into a re-gather request: no widening of the time window
can produce evidence that was never modeled in mock_data/ in the first
place, so requesting it would be dishonest busy-work, not a real retry.

Bounded: `max_iterations` (default 2) hard-caps the loop; the loop also
stops itself early, before hitting that cap, the moment there is nothing
case-specific left to request (never "keep looping just because the
counter allows it").

JURISDICTION (NEW this checkpoint): this loop is intentionally
jurisdiction-BLIND by design, not by omission - `jurisdiction.py`
determines a case's jurisdiction from the account's `registered_country`
and its transactions/geo events, none of which a wider network/timeline
traversal window can change. There is therefore no "jurisdiction-specific
evidence" for this loop to request: an unresolved jurisdiction is a
structural fact about the account, not a missing evidence item, and is
never converted into a fabricated re-gather request here. `run_pipeline.py`
re-evaluates regulatory findings/the auditor against the SAME, already-
determined `jurisdiction_context` after this loop runs (it is not
recomputed), which is what keeps the re-gather output honest.
"""
from datetime import timedelta

from network_layer import (
    build_smurf_network, build_reverse_smurf_network,
    build_money_mule_timeline, build_account_swap_timeline,
    MAX_DEPTH,
)
from evidence_model import build_evidence_items, compute_completeness

DEFAULT_MAX_ITERATIONS = 2

# POLICY ASSUMPTION: widen the same traversal/time window network_layer.py
# already uses per typology, by a fixed multiplier per iteration - a
# targeted widening of the EXISTING evidence source, not a new one.
_BASE_WINDOW_HOURS_GRAPH = 72     # smurfing / reverse_smurfing (build_smurf_network's own default)
_BASE_WINDOW_DAYS_TIMELINE = {"money_mule": 2, "account_swap": 7}


def _widened_network_evidence(store, case, iteration):
    """Re-derive the SAME shape generate_network_evidence() returns
    (network_layer.py), but with a wider window for iteration N - the
    smallest additive re-gather mechanism, reusing the existing typology
    builders rather than inventing a parallel evidence path."""
    typology = case.get("primary_trigger")
    account_id = case["account_id"]
    case_id = case["case_id"]
    anchor_time = None
    if case.get("created_at"):
        from datetime import datetime
        anchor_time = datetime.fromisoformat(case["created_at"])

    multiplier = 1 + iteration  # iteration 1 -> 2x, iteration 2 -> 3x, ...

    if typology == "smurfing":
        result = build_smurf_network(store, account_id, max_depth=MAX_DEPTH, direction="both",
                                      anchor_time=anchor_time, window_hours=_BASE_WINDOW_HOURS_GRAPH * multiplier)
        visualization_type, network_type = "network", "smurfing"
        evidence = {k: result[k] for k in ("root_account", "max_depth", "nodes", "edges")}
    elif typology == "reverse_smurfing":
        result = build_reverse_smurf_network(store, account_id, max_depth=MAX_DEPTH,
                                              anchor_time=anchor_time, window_hours=_BASE_WINDOW_HOURS_GRAPH * multiplier)
        visualization_type, network_type = "network", "reverse_smurfing"
        evidence = {k: result[k] for k in ("root_account", "max_depth", "nodes", "edges")}
    elif typology == "money_mule":
        days = _BASE_WINDOW_DAYS_TIMELINE["money_mule"] * multiplier
        window = {"start": anchor_time - timedelta(days=days), "end": anchor_time + timedelta(days=days)} if anchor_time else None
        result = build_money_mule_timeline(store, account_id, time_window=window)
        visualization_type, network_type = "transaction_timeline", "money_mule"
        evidence = {"transactions": result["transactions"], "summary": result["summary"]}
    elif typology == "account_swap":
        days = _BASE_WINDOW_DAYS_TIMELINE["account_swap"] * multiplier
        window = {"start": anchor_time - timedelta(days=days), "end": anchor_time + timedelta(days=days)} if anchor_time else None
        result = build_account_swap_timeline(store, account_id, time_window=window, anchor_time=anchor_time)
        visualization_type, network_type = "behavioral_transaction_timeline", "account_swap"
        evidence = {"events": result["events"], "behavioral_summary": result["behavioral_summary"]}
    else:
        return None  # unclassified typology has no typed builder to widen - nothing to re-gather from

    from datetime import datetime as _dt
    return {
        "case_id": case_id,
        "account_id": account_id,
        "typology": typology,
        "network_type": network_type,
        "visualization_type": visualization_type,
        "evidence": evidence,
        "patterns": result["patterns"],
        "source_transactions": result["source_transactions"],
        "generated_at": _dt.now().isoformat(),
        "network_scope": {"max_depth": MAX_DEPTH, "widened_multiplier": multiplier},
    }


def targeted_regather_requests(completeness, structural_gap_reasons):
    """Convert `completeness["missing"]` into targeted requests, excluding
    structural/dataset-wide gaps (see module docstring) - those can never
    be satisfied by re-gathering and are not requested."""
    return [
        {"evidence_type": m["evidence_type"], "reason": m.get("reason"), "severity": m.get("severity")}
        for m in completeness.get("missing", [])
        if m.get("reason") not in structural_gap_reasons
    ]


def run_regather_loop(store, case, evidence_items, completeness,
                       max_iterations=DEFAULT_MAX_ITERATIONS, structural_gap_reasons=()):
    """Runs the bounded targeted re-gather loop for one case.

    Returns:
    {
        "case_id", "max_iterations", "iterations": [...],
        "final_net", "final_evidence_items", "final_completeness",
        "final_disposition": "no_regather_needed" | "resolved" |
                              "unresolved_after_max_iterations" |
                              "unresolved_no_further_evidence_available",
    }
    `final_net` is None unless at least one iteration actually ran (the
    caller should keep using its own already-computed `net` when no
    re-gather occurred).
    """
    iterations = []
    current_items = evidence_items
    current_completeness = completeness
    final_net = None

    initial_requests = targeted_regather_requests(current_completeness, structural_gap_reasons)
    if not initial_requests:
        return {
            "case_id": case["case_id"],
            "max_iterations": max_iterations,
            "iterations": [],
            "final_net": None,
            "final_evidence_items": current_items,
            "final_completeness": current_completeness,
            "final_disposition": "no_regather_needed",
        }

    for i in range(1, max_iterations + 1):
        requests = targeted_regather_requests(current_completeness, structural_gap_reasons)
        if not requests:
            break

        score_before = current_completeness.get("weighted_score")
        widened_net = _widened_network_evidence(store, case, i)
        if widened_net is None:
            # No typed builder exists for this typology (unclassified) -
            # there is no existing evidence source to route this request
            # to; record honestly and stop rather than fabricate.
            iterations.append({
                "iteration": i,
                "requested_evidence": requests,
                "evidence_returned": [],
                "completeness_before": score_before,
                "completeness_after": score_before,
                "note": "no_typed_evidence_source_available_for_this_typology",
            })
            break

        new_items = build_evidence_items(store, case, widened_net)
        new_completeness = compute_completeness(new_items)
        score_after = new_completeness.get("weighted_score")

        requested_types = {r["evidence_type"] for r in requests}
        evidence_returned = [
            it["evidence_type"] for it in new_items
            if it["available"] and it["evidence_type"] in requested_types
        ]

        iterations.append({
            "iteration": i,
            "requested_evidence": requests,
            "evidence_returned": evidence_returned,
            "completeness_before": score_before,
            "completeness_after": score_after,
        })

        final_net = widened_net
        current_items = new_items
        current_completeness = new_completeness

        # Nothing new was actually recovered by widening - further
        # iterations would just repeat the same result, so stop early
        # rather than loop pointlessly up to max_iterations.
        if not evidence_returned:
            break

    remaining = targeted_regather_requests(current_completeness, structural_gap_reasons)
    if not remaining:
        disposition = "resolved"
    elif len(iterations) >= max_iterations:
        disposition = "unresolved_after_max_iterations"
    else:
        disposition = "unresolved_no_further_evidence_available"

    return {
        "case_id": case["case_id"],
        "max_iterations": max_iterations,
        "iterations": iterations,
        "final_net": final_net,
        "final_evidence_items": current_items,
        "final_completeness": current_completeness,
        "final_disposition": disposition,
    }