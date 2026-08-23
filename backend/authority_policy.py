"""
authority_policy.py
====================
CHECKPOINT 4 - Investigator Authority / Escalation Policy Engine.

Implements docs/ARCHITECTURE.md's "Investigator authority model" and
"Missing-evidence-driven escalation" sections: the stage that sits between

    EVIDENCE COMPLETENESS -> INVESTIGATOR AUTHORITY -> ACTION / ESCALATION

This module owns exactly ONE decision: given a case's already-computed
evidence/completeness (Checkpoint 2's evidence_model.py output) plus a
small set of additional real, already-produced signals (typology risk,
transaction/value risk, network complexity, contradiction state,
investigation confidence), decide whether the case may be resolved by a
junior investigator or requires senior review - and record WHY as
structured, machine-readable reason codes, never free text.

This module does NOT:
  - gather its own evidence (it only reads the `net`/`evidence_items`/
    `completeness` dicts already produced upstream by network_layer.py /
    evidence_model.py and passed in by the caller)
  - read ground truth (no ground_truth_*, fraud_networks, expected_signals,
    or expected_evidence reference anywhere in this file's actual code -
    see tests/test_ground_truth_isolation.py, which this checkpoint adds
    authority_policy.py to)
  - use randomness anywhere (no random-module usage, no `random.*` call - see
    test_authority_policy.py::test_never_uses_random_module, same static
    guard style as evidence_model.py)
  - decide detection, case bundling, or contradiction-agent behavior -
    those stages remain exactly as Checkpoint 3 left them

Policy inputs, mapped to docs/ARCHITECTURE.md Section 5:
  A. Evidence completeness      -> `completeness` (evidence_model.py output, unchanged contract)
  B. Typology risk               -> `TYPOLOGY_RISK` config (data, not if/elif)
  C. Investigation confidence    -> `confidence` param, OR (if not supplied)
                                     a deterministic fallback derived from
                                     the real evidence_items' `quality`
                                     field - see "Confidence" section below.
  D. Contradiction state         -> `contradiction_state` param (optional -
                                     see "Contradiction state" section below)
  E. Transaction/value risk      -> derived from `net`'s already-gathered
                                     transaction/edge/event amounts, or (for
                                     account_swap) network_layer.py's own
                                     already-computed `high_value_transaction`
                                     pattern - never re-queried from raw CSVs
  F. Network complexity          -> derived from `net`'s already-gathered
                                     nodes/edges/depth, smurfing/reverse_
                                     smurfing only (Section 5F: must not be
                                     forced onto money_mule/account_swap)

### Confidence - documented policy assumption ###
docs/ARCHITECTURE.md's investigation-confidence signal is, today, only
produced by the LLM contradiction/hypothesis agents (agents/*.py), which
are NOT wired into run_pipeline.py's live flow (they're invoked separately,
by eval_pipeline.py's rebuild - see backend_implementation_status.md
section 11). So run_pipeline.py has no real per-case confidence number to
pass in yet. Per this checkpoint's Section 5C instruction ("If the current
pipeline does not expose a required confidence field, inspect the existing
implementation and design the smallest additive interface necessary. Do
not create fake/random confidence."):

  - `assess_authority(..., confidence=None)` accepts an explicit confidence
    (0.0-1.0) from any caller that HAS a real one (e.g. a future checkpoint
    that wires the contradiction agent into run_pipeline.py, or a
    evaluation harness comparing against the LLM agents' own confidence).
  - When confidence is not supplied (today's run_pipeline.py case), a
    deterministic fallback is computed from the real, already-gathered
    `evidence_items`' `quality` field (`_derive_confidence_from_evidence`)
    - real data, zero randomness, but an explicitly-labeled APPROXIMATION,
    not a substitute for a real investigation-confidence signal. This is
    flagged in the returned record via `confidence_source`.

### Contradiction state - documented policy assumption ###
Same situation: agents/contradiction_agent.py exists and is contract-
correct (docs/ARCHITECTURE.md's "Contradiction agent constraints" - already
verified [VERIFIED] in a prior checkpoint), but is not called anywhere in
run_pipeline.py's live per-case loop today - it is only invoked by the
separate (currently-being-rebuilt) eval_pipeline.py path. So this module
accepts an optional `contradiction_state` and, when the caller has none to
give (today's run_pipeline.py), defaults to `"not_evaluated"` - which is
POLICY-NEUTRAL (does not by itself force senior review), because treating
"we never ran the contradiction check" identically to "we ran it and found
a real conflict" would be dishonest and would force every single case to
senior regardless of evidence quality. Only `"unresolved"` and
`"material_conflict"` (real contradiction-agent outputs, once wired in)
trigger the `unresolved_contradiction` senior reason. This is a real,
explicitly-documented limitation - see docs/backend_implementation_status.md
"Known limitations" for Checkpoint 4.
"""

# ----------------------------------------------------------------------
# 1. Policy configuration - all thresholds live here, not scattered
#    through if/elif branches. Every threshold below that isn't directly
#    established by docs/ARCHITECTURE.md is an explicit POLICY ASSUMPTION,
#    documented inline, made for policy correctness (a defensible,
#    internally-consistent routing scheme) - NOT tuned to hit any
#    particular junior/senior split on the checked-in mock dataset.
# ----------------------------------------------------------------------
AUTHORITY_POLICY = {
    "version": "v1",

    "junior": {
        # POLICY ASSUMPTION: a case needs at least 70% of its weighted
        # required evidence available before a junior investigator may
        # resolve it, mirroring evidence_model.py's own 0/100 weighted
        # scale (nothing in ARCHITECTURE.md pins an exact number - it only
        # says "evidence sufficient").
        "minimum_completeness": 70.0,
        # POLICY ASSUMPTION: same idea for the (approximated, see module
        # docstring) confidence signal, on its 0.0-1.0 scale.
        "minimum_confidence": 0.6,
        # Typology risk tiers a junior investigator is permitted to close
        # without escalation - "high" risk typologies always need senior
        # review regardless of how clean the evidence looks (Section 7).
        "allowed_risk_levels": ["low", "moderate"],
    },

    "senior_triggers": {
        "critical_missing_evidence": True,
        "high_risk_typology": True,
        "high_value_transaction": True,
        "complex_network": True,
        "unresolved_contradiction": True,
    },

    # POLICY ASSUMPTION: missing-evidence reason codes that describe a
    # dataset-wide, permanent limitation (evidence_model.py's own honest
    # "not modeled in this dataset" gaps) rather than something specific to
    # one case's investigation quality. `critical_missing_evidence` above is
    # meant to flag cases with a genuine, case-specific critical gap, so
    # these reasons are excluded from that trigger even when the item's
    # severity is "critical" - they still appear in `missing_evidence`
    # untouched. Today this is exactly evidence_model.py's source_of_funds
    # checker (`documentation_not_available`, returned unconditionally for
    # every case/typology); add further reason codes here only if a future
    # evidence type is similarly never-modeled dataset-wide.
    "structural_gap_reasons": ["documentation_not_available"],

    # POLICY ASSUMPTION (Section 6B): money_mule and account_swap are rated
    # "high" risk because both typologies represent funds already having
    # moved to a mule/attacker-controlled destination (immediate, often
    # irreversible loss) by the time a case exists; smurfing/reverse_
    # smurfing are network-topology typologies where funds are still
    # traceable/interceptable mid-chain, rated "moderate". No fifth "low"
    # tier exists among the 4 known typologies today - documented, not
    # hidden.
    "typology_risk": {
        "smurfing": "moderate",
        "reverse_smurfing": "moderate",
        "money_mule": "high",
        "account_swap": "high",
    },

    # POLICY ASSUMPTION (Section 5E): explicit monetary threshold, per this
    # checkpoint's instruction ("If a monetary threshold is required, make
    # it explicit configuration" / "never invent thresholds from ground
    # truth" - this number was chosen independently of, and never
    # cross-checked against, the evaluation-only reference dataset). Two independent triggers:
    # an absolute amount, OR a multiple of the account's own
    # avg_monthly_txn_amount baseline (same "3x baseline" concept
    # detection_layer.py/network_layer.py already use elsewhere in this
    # codebase for profile-deviation rules, reused here at a slightly
    # higher multiplier since this is an authority decision, not an alert
    # trigger).
    "high_value_transaction": {
        "absolute_threshold": 10000.0,
        "baseline_multiplier": 5.0,
    },

    # POLICY ASSUMPTION (Section 5F): "complex" for a smurfing/reverse_
    # smurfing traversal graph. depth_threshold intentionally equals
    # network_layer.py's own MAX_DEPTH (3) - i.e. a case that used the
    # full available traversal depth is treated as complex. node/edge
    # thresholds are independent, deliberately-documented round numbers.
    # Section 5F: NEVER applied to money_mule/account_swap (those are
    # timelines, not graphs) - enforced in _is_complex_network below.
    "network_complexity": {
        "node_threshold": 8,
        "edge_threshold": 10,
        "depth_threshold": 3,
    },

    # Not in ARCHITECTURE.md's explicit senior_triggers list, but directly
    # implements Section 6's "action falls within junior authority" /
    # Section 7's "junior authority exceeded" principle: reuses
    # detection_layer.py's own already-computed alert `severity` field
    # (never re-derived here) for the alert(s) that produced this case.
    "junior_action_limit": {
        "blocking_severities": ["high", "critical"],
    },
}

# contradiction_state values that actually force senior review. "not_
# evaluated" (the default when no contradiction agent output is available
# - see module docstring) and "no_contradiction"/"resolved" (a contradiction
# agent ran and found nothing/settled it) do NOT trigger this.
_CONTRADICTION_SENIOR_STATES = {"unresolved", "material_conflict"}

# Positive/explanatory reason codes used when a case is junior-authorized -
# see docs/ARCHITECTURE.md Section 10's own example list, which names these
# three explicitly. Negative/escalation reason codes are appended inline as
# each policy dimension is evaluated below.
_POSITIVE_REASONS = ["no_critical_gap", "sufficient_evidence", "sufficient_confidence"]


# ----------------------------------------------------------------------
# 2. Signal extraction - each function reads ONLY data already produced
#    upstream (the `net` dict returned by network_layer.generate_network_
#    evidence(), the `account` row already looked up by the caller, the
#    case's own already-computed alerts) - never queries raw CSVs, never
#    calls network_layer.py/detection_layer.py itself.
# ----------------------------------------------------------------------
def _max_amount_touched(net, typology):
    """The largest single transaction amount present in whatever evidence
    was already gathered for this case, per typology's evidence shape
    (Section 5E: "Use real transaction data already available to the
    case/evidence pipeline"). Returns None if nothing is available to
    check - never guessed."""
    evidence = (net or {}).get("evidence") or {}
    if typology in ("smurfing", "reverse_smurfing"):
        amounts = [e["data"]["amount"] for e in evidence.get("edges", [])
                   if "data" in e and "amount" in e["data"]]
    elif typology == "money_mule":
        amounts = [t["amount"] for t in evidence.get("transactions", []) if "amount" in t]
    elif typology == "account_swap":
        amounts = [e["amount"] for e in evidence.get("events", [])
                   if e.get("event_type") == "transaction" and "amount" in e]
    else:
        amounts = []
    return max(amounts) if amounts else None


def _is_high_value_transaction(net, account, typology, config):
    if typology == "account_swap":
        # network_layer.py's build_account_swap_timeline() already computes
        # this exact signal (transaction_amount > 3x avg_monthly_txn_amount,
        # AS-007's own threshold) as the "high_value_transaction" pattern -
        # reuse that already-produced result rather than recomputing a
        # second, possibly-inconsistent version of the same check.
        return "high_value_transaction" in ((net or {}).get("patterns") or [])

    max_amount = _max_amount_touched(net, typology)
    if max_amount is None:
        return False
    if max_amount >= config["absolute_threshold"]:
        return True
    baseline = (account or {}).get("avg_monthly_txn_amount")
    if baseline and baseline > 0 and max_amount >= config["baseline_multiplier"] * baseline:
        return True
    return False


def _is_complex_network(net, typology, config):
    """Section 5F: network complexity is a NETWORK GRAPH concept
    (smurfing/reverse_smurfing only) - never forced onto money_mule's
    transaction timeline or account_swap's behavioral timeline, which have
    no nodes/edges/depth to measure in the first place."""
    if typology not in ("smurfing", "reverse_smurfing"):
        return False
    evidence = (net or {}).get("evidence") or {}
    nodes = evidence.get("nodes", [])
    edges = evidence.get("edges", [])
    depths = [e["data"].get("depth", 0) for e in edges if "data" in e]
    max_depth_reached = max(depths) if depths else 0
    return (
        len(nodes) >= config["node_threshold"]
        or len(edges) >= config["edge_threshold"]
        or max_depth_reached >= config["depth_threshold"]
    )


def _junior_action_limit_exceeded(case_alerts, config):
    """True if any of THIS case's own already-computed alerts (Detection
    Layer's own `severity` field, never re-derived here) is rated high
    enough that Detection itself already recommended escalation-level
    handling. `case_alerts` is optional - callers that don't have the
    alert list on hand (e.g. network_layer.py's standalone __main__, which
    only has cases.json, not the alerts that produced them) simply don't
    get this signal; documented as a known limitation, never silently
    guessed as True or False."""
    if not case_alerts:
        return False
    blocking = set(config.get("blocking_severities", ()))
    return any(a.get("severity") in blocking for a in case_alerts)


def _derive_confidence_from_evidence(evidence_items):
    """Deterministic APPROXIMATION of investigation confidence from the
    real evidence_items already produced by evidence_model.py, for use
    only when no real confidence value is supplied - see module docstring
    "Confidence" section. Never random, never a placeholder constant:
    scales with (a) how much of the required evidence is actually
    available and (b) how much of what's available is `quality: "high"`
    rather than `"low"`. Returns 0.0 for a case with no evidence items at
    all (e.g. an unclassified typology - Checkpoint 2's own
    no_requirement_table_for_typology case)."""
    if not evidence_items:
        return 0.0
    available = [i for i in evidence_items if i["available"]]
    if not available:
        return 0.0
    completeness_ratio = len(available) / len(evidence_items)
    high_quality_ratio = sum(1 for i in available if i.get("quality") == "high") / len(available)
    return round(0.5 * completeness_ratio + 0.5 * high_quality_ratio, 3)


# ----------------------------------------------------------------------
# 3. Public API
# ----------------------------------------------------------------------
def assess_authority(case, evidence_items, completeness, net=None, account=None,
                      contradiction_state=None, confidence=None, case_alerts=None,
                      policy=AUTHORITY_POLICY):
    """The one authority-policy decision function. Deterministic: identical
    (case, evidence_items, completeness, net, account, contradiction_state,
    confidence, case_alerts, policy) always produces an identical return
    value - no random, uuid, or wall-clock timestamps anywhere in this module. Never
    mutates any of its arguments.

    Returns the structured schema from docs/ARCHITECTURE.md Section 9
    (case_id, authority_tier, can_resolve, decision, reasons, risk_factors,
    missing_evidence, policy_version, confidence), plus two additive,
    inspectable fields (`confidence_source`, `policy_inputs`) that do not
    change the meaning of the core schema - included so the decision is
    auditable without re-deriving typology risk / high-value / complexity
    by hand from `net` again.
    """
    typology = case.get("primary_trigger")
    weighted_score = completeness.get("weighted_score")
    missing = completeness.get("missing", [])
    # `critical_missing_evidence` must mean "this case is missing critical
    # evidence it could plausibly have had" - NOT "this evidence type is
    # unobtainable for every case in the entire dataset" (evidence_model.py's
    # own documented, honest, permanent gap - see its `_check_source_of_funds`
    # checker, which returns not-available unconditionally for every single
    # case/typology). Treating that dataset-wide structural gap the same as a
    # genuine per-case gap would make this trigger fire on 100% of cases
    # regardless of how complete or clean the rest of the evidence is,
    # silently defeating the junior/senior distinction entirely (the
    # `minimum_completeness` threshold below is deliberately set at 70%,
    # below evidence_model.py's own 85% dataset-wide ceiling, specifically so
    # this permanent gap alone does not block junior authorization on the
    # completeness axis - the critical-missing trigger must honor that same
    # design intent rather than re-introducing the block through a second,
    # inconsistent path). `policy["structural_gap_reasons"]` names the
    # missing-evidence reason codes that are dataset-wide/permanent rather
    # than case-specific; those items still surface in `missing_evidence` for
    # transparency, they just don't independently force senior review.
    structural_gap_reasons = policy.get("structural_gap_reasons", ())
    critical_missing = [
        m for m in missing
        if m.get("severity") == "critical" and m.get("reason") not in structural_gap_reasons
    ]

    confidence_source = "supplied"
    if confidence is None:
        confidence = _derive_confidence_from_evidence(evidence_items)
        confidence_source = "derived_from_evidence_quality"

    typology_risk = policy["typology_risk"].get(typology, "moderate")
    high_value = _is_high_value_transaction(net, account, typology, policy["high_value_transaction"])
    complex_network = _is_complex_network(net, typology, policy["network_complexity"])
    state = contradiction_state or "not_evaluated"
    action_limit_exceeded = _junior_action_limit_exceeded(case_alerts, policy["junior_action_limit"])

    reasons = []
    risk_factors = []

    if policy["senior_triggers"]["critical_missing_evidence"] and critical_missing:
        reasons.append("critical_evidence_missing")
        risk_factors.append("critical_evidence_gap")

    if weighted_score is None or weighted_score < policy["junior"]["minimum_completeness"]:
        reasons.append("evidence_below_required_threshold")
        risk_factors.append("below_completeness_threshold")

    if confidence < policy["junior"]["minimum_confidence"]:
        reasons.append("confidence_below_required_threshold")
        risk_factors.append("low_confidence")

    if policy["senior_triggers"]["high_risk_typology"] and typology_risk not in policy["junior"]["allowed_risk_levels"]:
        reasons.append("high_risk_typology")
        risk_factors.append("typology_high_risk")

    if policy["senior_triggers"]["high_value_transaction"] and high_value:
        reasons.append("high_value_transaction")
        risk_factors.append("high_value_transaction")

    if policy["senior_triggers"]["complex_network"] and complex_network:
        reasons.append("complex_network")
        risk_factors.append("complex_network")

    if policy["senior_triggers"]["unresolved_contradiction"] and state in _CONTRADICTION_SENIOR_STATES:
        reasons.append("unresolved_contradiction")
        risk_factors.append("unresolved_contradiction")

    if action_limit_exceeded:
        reasons.append("junior_action_limit_exceeded")
        risk_factors.append("high_severity_alert")

    can_resolve = len(reasons) == 0
    if can_resolve:
        reasons = list(_POSITIVE_REASONS)
        authority_tier = "junior"
        decision = "junior_authorized"
    else:
        authority_tier = "senior"
        decision = "senior_review_required"

    return {
        "case_id": case["case_id"],
        "authority_tier": authority_tier,
        "can_resolve": can_resolve,
        "decision": decision,
        "reasons": reasons,
        "risk_factors": sorted(set(risk_factors)),
        "missing_evidence": missing,
        "policy_version": policy["version"],
        "confidence": confidence,
        "confidence_source": confidence_source,
        "policy_inputs": {
            "typology": typology,
            "typology_risk": typology_risk,
            "weighted_completeness": weighted_score,
            "high_value_transaction": high_value,
            "complex_network": complex_network,
            "contradiction_state": state,
            "junior_action_limit_exceeded": action_limit_exceeded,
        },
    }