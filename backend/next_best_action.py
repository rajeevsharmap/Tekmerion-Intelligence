"""
next_best_action.py
======================
CHECKPOINT 6 - Next-Best-Action Engine.

    ... -> Case Completeness Score -> [re-gather loop] -> NEXT-BEST-ACTION
    -> Audit Trail -> Human Review -> Investigator Action -> Case Memory

Deterministic, config-driven mapping from a case's already-computed
upstream signals to exactly ONE recommended action, with machine-readable
reason codes and full evidence/regulatory provenance. Same style as
authority_policy.py / case_completeness.py / regulatory_rules.py: no
random, no uuid4, no LLM call, no ground truth. This module does NOT
gather evidence and does NOT decide investigator authority - it only
reads:

  - detection result / primary typology      -> case["primary_trigger"]
  - network evidence                          -> `net`
  - hypotheses / contradiction result         -> `contradiction_state`
    (optional - not yet wired into run_pipeline.py's live loop, same
    documented limitation as authority_policy.py; defaults to
    "not_evaluated", which is policy-neutral, never silently guessed)
  - regulatory findings                       -> `regulatory_findings`
    (regulatory_rules.py output, Checkpoint 5)
  - completeness result                       -> `case_completeness`
    (case_completeness.py output, Checkpoint 5)
  - auditor findings                          -> `auditor_result`
    (investigation_auditor.py output, Checkpoint 5)
  - authority decision                        -> `authority_decision`
    (authority_policy.py output, Checkpoint 4 - CONSUMED, never
    re-derived; this module does not duplicate authority-policy logic)
  - risk level / case severity                -> derived from the above,
    never re-queried from raw CSVs

### Action vocabulary (Step 2) ###
CLEAR, MONITOR, REQUEST_MORE_INFORMATION, ESCALATE_TO_SENIOR,
RESTRICT_ACCOUNT, BLOCK_TRANSACTION, FILE_SAR, CLOSE_CASE.

Not every action is appropriate for every case - this module picks ONE via
a documented, ordered decision cascade (POLICY ASSUMPTIONS, same
disclosure style as authority_policy.py's config comments):

  1. Case not yet investigation-ready (Checkpoint 5's `case_completeness
     .status != "complete"`) -> REQUEST_MORE_INFORMATION. This is checked
     first because no downstream recommendation can be trusted against an
     incomplete evidentiary record.
  2. A regulatory rule already reached "confirmed_concern" -> a protective
     or reporting action, picked by typology (POLICY ASSUMPTION,
     documented in FUNDS_IN_MOTION_TYPOLOGIES / NETWORK_TYPOLOGIES below):
     BLOCK_TRANSACTION (funds actively moving - money_mule/account_swap),
     RESTRICT_ACCOUNT (network/topology typologies - smurfing/reverse_
     smurfing, funds still traceable mid-chain), or FILE_SAR (fallback).
     If the Investigation Auditor separately flagged a CRITICAL issue on
     top of a confirmed concern, that overrides straight-to-filing with
     ESCALATE_TO_SENIOR instead - a critical audit issue means a human
     needs to look at *why* before the system recommends acting.
  3. No confirmed concern, but the auditor flagged a CRITICAL issue ->
     ESCALATE_TO_SENIOR.
  4. A regulatory rule reached "potentially_applicable" (one real signal,
     not yet corroborated) -> MONITOR if the case is already
     junior-authorized (Checkpoint 4), otherwise ESCALATE_TO_SENIOR.
  5. No regulatory signal at all, but Checkpoint 4's authority decision
     itself says `can_resolve: False` (e.g. high-risk typology, complex
     network, high-value transaction) -> ESCALATE_TO_SENIOR, citing
     Checkpoint 4's own reasons directly (never re-derived).
  6. Clean case (complete, no regulatory concern, junior-authorized) ->
     CLEAR when the typology itself is low/moderate risk and no risk
     factors were raised; otherwise CLOSE_CASE (investigated and found
     nothing actionable, but the case's own risk profile warrants a
     recorded closure rather than an unqualified "clear").

`requires_human_review` is unconditionally `True` - this engine never
executes an action itself; that is the whole point of Checkpoint 6's
"AUTOMATED RECOMMENDATION" vs "HUMAN DECISION" vs "AUTHORIZED ACTION"
distinction (see investigator_action.py).

`required_authority` is the greater of (a) Checkpoint 4's own case-level
authority_tier and (b) this recommended action's own minimum authority
(ACTION_MINIMUM_AUTHORITY below - e.g. BLOCK_TRANSACTION/FILE_SAR/
RESTRICT_ACCOUNT always need senior authority regardless of how the case
itself was routed, because those actions have irreversible real-world
effects). POLICY ASSUMPTION, documented here rather than silently assumed.
"""
import hashlib
import json

# ----------------------------------------------------------------------
# 1. Action vocabulary + policy configuration
# ----------------------------------------------------------------------
ACTIONS = (
    "CLEAR",
    "MONITOR",
    "REQUEST_MORE_INFORMATION",
    "ESCALATE_TO_SENIOR",
    "RESTRICT_ACCOUNT",
    "BLOCK_TRANSACTION",
    "FILE_SAR",
    "CLOSE_CASE",
)

# POLICY ASSUMPTION: minimum authority tier required to actually EXECUTE
# each action, independent of the case's own Checkpoint-4 authority tier.
# Actions with irreversible/high-impact real-world effects (freezing funds,
# restricting an account, filing a regulatory report) always require
# senior authority; investigative/administrative actions may be junior.
ACTION_MINIMUM_AUTHORITY = {
    "CLEAR": "junior",
    "MONITOR": "junior",
    "REQUEST_MORE_INFORMATION": "junior",
    "CLOSE_CASE": "junior",
    "ESCALATE_TO_SENIOR": "junior",   # any investigator may escalate upward
    "RESTRICT_ACCOUNT": "senior",
    "BLOCK_TRANSACTION": "senior",
    "FILE_SAR": "senior",
}

# POLICY ASSUMPTION (mirrors authority_policy.py's Section 6B typology-risk
# split): money_mule / account_swap represent funds that have already
# moved to a mule/attacker-controlled destination - a confirmed concern on
# one of these typologies should recommend stopping further movement
# (BLOCK_TRANSACTION). smurfing / reverse_smurfing are traversal-graph
# typologies where funds are still traceable mid-chain - a confirmed
# concern there recommends restricting the account under investigation
# (RESTRICT_ACCOUNT) rather than blocking a specific transaction.
FUNDS_IN_MOTION_TYPOLOGIES = ("money_mule", "account_swap")
NETWORK_TYPOLOGIES = ("smurfing", "reverse_smurfing")

# Typology risk tiers a CLEAR (vs. CLOSE_CASE) verdict is available for -
# reuses authority_policy.AUTHORITY_POLICY["typology_risk"]'s own
# "low"/"moderate" allowed-for-junior tiers so the two modules never
# silently disagree about which typologies are low-risk enough to clear
# outright. Imported lazily (see _typology_risk below) to avoid a hard
# import-order dependency for callers that only want the action table.
_TIER_RANK = {"junior": 0, "senior": 1}


def _typology_risk(typology):
    try:
        from authority_policy import AUTHORITY_POLICY
        return AUTHORITY_POLICY["typology_risk"].get(typology, "moderate")
    except Exception:
        return "moderate"


def _content_hash_id(prefix, *parts):
    """Deterministic content-hash ID - same style as case_id/alert_id in
    detection_layer.py (Checkpoint 3 fix): identical inputs always produce
    an identical ID, never uuid4/random."""
    raw = json.dumps(parts, sort_keys=True, default=str)
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:8].upper()}"


def _max_authority(a, b):
    return a if _TIER_RANK.get(a, 0) >= _TIER_RANK.get(b, 0) else b


# ----------------------------------------------------------------------
# 2. Public API
# ----------------------------------------------------------------------
def recommend_next_best_action(case, evidence_items, completeness, case_completeness,
                                regulatory_findings, auditor_result, authority_decision,
                                net=None, case_alerts=None, jurisdiction_context=None,
                                contradiction_state=None):
    """The one Next-Best-Action decision function. Deterministic: identical
    inputs always produce an identical return value.

    Returns:
        {
            "recommendation_id": deterministic content-hash ID,
            "case_id": ...,
            "recommended_action": one of ACTIONS,
            "reason_codes": [...],
            "supporting_evidence_ids": [...],
            "regulatory_basis": [...],
            "confidence": 0.0-1.0,
            "required_authority": "junior"|"senior",
            "requires_human_review": True,
            "typology": ...,
            "policy_version": "v1",
        }
    """
    typology = case.get("primary_trigger")
    regulatory_findings = regulatory_findings or []
    auditor_result = auditor_result or {"issues": [], "critical_issue_count": 0}
    authority_decision = authority_decision or {}

    confirmed = [r for r in regulatory_findings if r.get("status") == "confirmed_concern"]
    potential = [r for r in regulatory_findings if r.get("status") == "potentially_applicable"]
    critical_issue_count = auditor_result.get("critical_issue_count", 0)
    can_resolve = authority_decision.get("can_resolve", False)
    case_authority_tier = authority_decision.get("authority_tier", "senior")

    reason_codes = []
    regulatory_basis = sorted({r["rule_id"] for r in (confirmed + potential)})

    # ---- Step 1: case not investigation-ready --------------------------
    if (case_completeness or {}).get("status") != "complete":
        action = "REQUEST_MORE_INFORMATION"
        reason_codes = ["case_completeness_below_threshold"] + list(
            (case_completeness or {}).get("reasons", [])
        )

    # ---- Step 2: confirmed regulatory concern ---------------------------
    elif confirmed:
        reason_codes = ["confirmed_regulatory_concern"] + sorted({r["rule_id"] for r in confirmed})
        if typology in FUNDS_IN_MOTION_TYPOLOGIES:
            action = "BLOCK_TRANSACTION"
            reason_codes.append("funds_in_motion_typology")
        elif typology in NETWORK_TYPOLOGIES:
            action = "RESTRICT_ACCOUNT"
            reason_codes.append("network_topology_typology")
        else:
            action = "FILE_SAR"
            reason_codes.append("typology_not_time_critical")
        if critical_issue_count:
            action = "ESCALATE_TO_SENIOR"
            reason_codes.append("auditor_critical_issue_overrides_automatic_filing")

    # ---- Step 3: auditor critical issue, no confirmed concern -----------
    elif critical_issue_count:
        action = "ESCALATE_TO_SENIOR"
        reason_codes = ["auditor_flagged_critical_issue"]

    # ---- Step 4: potentially-applicable regulatory concern ---------------
    elif potential:
        reason_codes = ["potentially_applicable_regulatory_concern"] + sorted({r["rule_id"] for r in potential})
        if can_resolve:
            action = "MONITOR"
        else:
            action = "ESCALATE_TO_SENIOR"
            reason_codes.append("case_requires_senior_authority")

    # ---- Step 5: no regulatory signal, but authority says senior --------
    elif not can_resolve:
        action = "ESCALATE_TO_SENIOR"
        reason_codes = list(authority_decision.get("reasons", ["senior_review_required"]))

    # ---- Step 6: clean case -----------------------------------------------
    else:
        risk_factors = authority_decision.get("risk_factors", [])
        if _typology_risk(typology) in ("low", "moderate") and not risk_factors:
            action = "CLEAR"
            reason_codes = ["no_identified_regulatory_breach", "sufficient_evidence_reviewed"]
        else:
            action = "CLOSE_CASE"
            reason_codes = ["no_identified_regulatory_breach", "investigated_no_actionable_finding"]
        reason_codes += list(authority_decision.get("reasons", []))

    # ---- Confidence: deterministic combination of two real, already-
    # computed signals (case completeness score, authority confidence) -
    # never random, never a placeholder constant. Explicitly labeled an
    # APPROXIMATION (same disclosure style as authority_policy.py's
    # _derive_confidence_from_evidence).
    completeness_score = (case_completeness or {}).get("score")
    authority_confidence = authority_decision.get("confidence")
    parts = []
    if completeness_score is not None:
        parts.append(completeness_score / 100.0)
    if authority_confidence is not None:
        parts.append(authority_confidence)
    confidence = round(sum(parts) / len(parts), 3) if parts else 0.0

    # ---- Supporting evidence: real evidence_ids already available/gathered
    supporting_evidence_ids = [i["evidence_id"] for i in (evidence_items or []) if i.get("available")]

    # ---- required_authority: greater of this action's own minimum and the
    # case's own Checkpoint-4 authority tier (see module docstring).
    action_min_authority = ACTION_MINIMUM_AUTHORITY.get(action, "senior")
    required_authority = _max_authority(action_min_authority, case_authority_tier)

    recommendation_id = _content_hash_id(
        "NBA", case["case_id"], action, tuple(reason_codes), completeness_score, authority_confidence
    )

    return {
        "recommendation_id": recommendation_id,
        "case_id": case["case_id"],
        "typology": typology,
        "recommended_action": action,
        "reason_codes": reason_codes,
        "supporting_evidence_ids": supporting_evidence_ids,
        "regulatory_basis": regulatory_basis,
        "confidence": confidence,
        "required_authority": required_authority,
        "requires_human_review": True,
        "policy_version": "v1",
    }