"""
case_completeness.py
=======================
CHECKPOINT 5 - Case Completeness Score.

    ... -> Investigation Auditor -> CASE COMPLETENESS SCORE -> [LOW -> re-gather
    | HIGH -> auditor routing] -> ...

Combines three already-computed, real signals into one explainable,
deterministic 0-100 score - never an arbitrary black-box number:

  1. Evidence completeness  - evidence_model.py's per-case weighted score,
     but recomputed over only the "reachable" evidence types (see
     `_reachable_score` below) so a dataset-wide structural gap (e.g.
     source_of_funds, permanently unavailable for every case - see
     evidence_model.py's own docstring) cannot by itself cap every case's
     score short of "complete". This is the direct fix for this
     checkpoint's explicit "structural evidence gaps must not automatically
     make every case incomplete" requirement, applied consistently with
     how authority_policy.py already excludes the same reasons from its
     own `critical_evidence_missing` trigger (Checkpoint 4).
  2. Regulatory rule resolution - the fraction of applicable regulatory
     rules (regulatory_rules.py) that were actually evaluable
     ("confirmed_concern" / "potentially_applicable" / "no_identified_breach"
     all count as resolved; "insufficient_evidence" does not).
  3. Auditor cleanliness - penalized per CRITICAL issue the Investigation
     Auditor (investigation_auditor.py) found; moderate issues do not
     block completeness by themselves.

Each component that has no applicable inputs for this case (e.g. a case
with zero applicable regulatory rules) is treated as fully satisfied
(100.0) rather than penalizing a case for a dimension that doesn't apply
to it, and is excluded from `reasons`/component weighting only insofar as
it contributes a neutral value - the weighting itself always uses the
same three fixed weights below so the score stays comparable across
cases.
"""

WEIGHTS = {
    "evidence": 0.60,
    "regulatory": 0.25,
    "auditor": 0.15,
}

DEFAULT_THRESHOLD = 75.0
AUDITOR_PENALTY_PER_CRITICAL_ISSUE = 25.0


def _reachable_score(evidence_items, structural_gap_reasons):
    """Evidence-completeness component, normalized only over evidence
    types that are actually obtainable for this dataset - see module
    docstring. Returns None if there is nothing reachable to score (e.g.
    an unclassified typology with no requirement table at all)."""
    reachable = [
        i for i in evidence_items
        if i["available"] or i.get("missing_reason", {}).get("reason") not in structural_gap_reasons
    ]
    if not reachable:
        return None
    total_weight = sum(i["weight"] for i in reachable) or 1.0
    available_weight = sum(i["weight"] for i in reachable if i["available"])
    return round(100.0 * available_weight / total_weight, 1)


def compute_case_completeness(case, evidence_items, completeness, regulatory_findings=None,
                               auditor_result=None, threshold=DEFAULT_THRESHOLD,
                               structural_gap_reasons=(), weights=WEIGHTS,
                               jurisdiction_context=None):
    """The one public entry point. Returns the exact output contract the
    checkpoint specifies (score/threshold/status/missing_evidence/
    critical_missing_evidence/satisfied_requirements/failed_requirements/
    reasons/next_step), plus additive `components` (each component's own
    sub-score, for auditability) and `case_id`.

    JURISDICTION (NEW this checkpoint): `jurisdiction_context` is optional
    and purely additive - it does not change `weights`/scoring, because
    jurisdiction uncertainty already reaches this score through the two
    channels that were built to carry it: an unresolved/mismatched
    jurisdiction turns the affected regulatory_rules.py result into
    "insufficient_evidence" (already discounted by the `regulatory`
    component below) and/or a critical investigation_auditor.py issue
    (already discounted by the `auditor` component below) - see those two
    modules' own docstrings. What this parameter adds is *transparency*:
    when supplied and the case's jurisdiction is not resolved with high
    confidence, that fact is surfaced explicitly in `reasons` so a human
    reviewer sees WHY the regulatory/auditor components are depressed,
    rather than only seeing the downstream numeric effect.
    """
    regulatory_findings = regulatory_findings or []
    auditor_result = auditor_result or {"issues": [], "critical_issue_count": 0}

    missing = completeness.get("missing", [])
    critical_missing_evidence = [
        m for m in missing
        if m.get("severity") == "critical" and m.get("reason") not in structural_gap_reasons
    ]
    structural_missing = [m for m in missing if m.get("reason") in structural_gap_reasons]

    evidence_component = _reachable_score(evidence_items, structural_gap_reasons)

    insufficient_rules = [r for r in regulatory_findings if r["status"] == "insufficient_evidence"]
    if regulatory_findings:
        regulatory_component = round(
            100.0 * (len(regulatory_findings) - len(insufficient_rules)) / len(regulatory_findings), 1
        )
    else:
        regulatory_component = 100.0  # no applicable rules for this typology - neutral, not penalized

    critical_issue_count = auditor_result.get("critical_issue_count", 0)
    auditor_component = max(0.0, 100.0 - AUDITOR_PENALTY_PER_CRITICAL_ISSUE * critical_issue_count)

    components = {"evidence": evidence_component, "regulatory": regulatory_component, "auditor": auditor_component}
    present = {k: v for k, v in components.items() if v is not None}
    if present:
        total_w = sum(weights[k] for k in present) or 1.0
        score = round(sum(weights[k] * v for k, v in present.items()) / total_w, 1)
    else:
        score = None

    satisfied_requirements = [i["evidence_type"] for i in evidence_items if i["available"]]
    failed_requirements = [i["evidence_type"] for i in evidence_items if not i["available"]]

    reasons = []
    if critical_missing_evidence:
        reasons.append("critical_case_specific_evidence_missing")
    if structural_missing:
        reasons.append("structural_dataset_wide_gap_excluded_from_score")
    if insufficient_rules:
        reasons.append("regulatory_rule_insufficient_evidence")
    if critical_issue_count:
        reasons.append("auditor_flagged_critical_issue")
    if jurisdiction_context and (
        jurisdiction_context.get("jurisdiction") == "unknown"
        or jurisdiction_context.get("confidence") == "low"
    ):
        reasons.append("case_jurisdiction_not_resolved_with_high_confidence")
    if not reasons:
        reasons.append("all_reachable_requirements_satisfied")

    is_complete = (
        score is not None
        and score >= threshold
        and not critical_missing_evidence
        and not critical_issue_count
    )
    status = "complete" if is_complete else "incomplete"
    next_step = "continue" if status == "complete" else "re_gather"

    return {
        "case_id": case["case_id"],
        "score": score,
        "threshold": threshold,
        "status": status,
        "missing_evidence": missing,
        "critical_missing_evidence": critical_missing_evidence,
        "satisfied_requirements": satisfied_requirements,
        "failed_requirements": failed_requirements,
        "reasons": reasons,
        "next_step": next_step,
        "components": components,
        "jurisdiction_context": jurisdiction_context,
    }