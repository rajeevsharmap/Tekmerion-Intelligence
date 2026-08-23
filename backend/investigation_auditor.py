"""
investigation_auditor.py
===========================
CHECKPOINT 5 - Investigation Auditor.

    ... -> Regulatory RAG -> INVESTIGATION AUDITOR -> Case Completeness Score -> ...

Evaluates whether a case's investigation is sufficiently supported by
inspecting the already-computed outputs of every upstream stage (evidence
items/completeness, network evidence/patterns, regulatory rule-engine
results, the authority-policy decision, and - if supplied - the
contradiction-agent's state) and flags structural problems as their own
typed issue objects.

Per the checkpoint's explicit instruction ("Do not let the auditor simply
repeat the conclusion produced by another agent"), every check here is an
INDEPENDENT structural inspection - cross-checking one stage's output
against another's, or against a stated invariant (e.g. "a confirmed
regulatory concern must cite real supporting evidence") - never a
pass-through of `authority["decision"]` or a regulatory rule's own
`status` field re-labeled as an auditor finding.

Each issue is a structured object (never a free-text paragraph):
{
    "issue_type": one of the fixed types below,
    "severity": "critical" | "moderate",
    "detail": {...case-specific structured fields...},
}

JURISDICTION MISMATCH (NEW this checkpoint - see `jurisdiction.py`): this
auditor independently re-verifies that every regulatory citation a rule
result carries is actually within the case's own determined jurisdiction
- it does NOT simply trust that `regulatory_rules.py`/`regulatory_rag.py`
applied the jurisdiction gate correctly upstream (per this module's own
"never repeat another stage's conclusion" principle above). If
`jurisdiction_context` is not supplied by the caller, this check is
honestly skipped (no issue raised either way) rather than guessed -
mirroring the existing `contradiction_state` "not evaluated" pattern.
"""

# Evidence types whose checkers (evidence_model.py) are designed to return
# real source_record_ids whenever `available` is True - used only to catch
# a genuine provenance regression (available=True with no record backing
# it), not to second-guess evidence types that are legitimately
# ids-optional by their own checker's design (e.g. pass_through_timing,
# amount_retention_ratio, behavioral_baseline - those never carry
# per-record ids even when available, by evidence_model.py's own design;
# temporal_pattern's "ids" are actually pattern-type labels, a pre-existing
# quirk this auditor does not attempt to relitigate).
_EXPECTS_RECORD_IDS_WHEN_AVAILABLE = {
    "transaction_chain", "counterparty_relationship", "beneficiary_information",
    "device_information", "geo_information", "inbound_transaction_chain",
    "outbound_transaction_chain", "sim_change_evidence",
}

_CONTRADICTION_UNRESOLVED_STATES = {"unresolved", "material_conflict"}


def _check_missing_critical_evidence(completeness, structural_gap_reasons):
    issues = []
    for m in completeness.get("missing", []):
        if m.get("severity") == "critical" and m.get("reason") not in structural_gap_reasons:
            issues.append({
                "issue_type": "missing_critical_evidence",
                "severity": "critical",
                "detail": {"evidence_type": m.get("evidence_type"), "reason": m.get("reason")},
            })
    return issues


def _check_contradictory_evidence(contradiction_state):
    if contradiction_state in _CONTRADICTION_UNRESOLVED_STATES:
        return [{
            "issue_type": "contradictory_evidence",
            "severity": "critical",
            "detail": {"contradiction_state": contradiction_state},
        }]
    return []


def _check_unsupported_regulatory_claims(regulatory_findings):
    """A rule result claiming "confirmed_concern" must cite at least one
    real supporting_evidence entry - if it doesn't, that is a genuine
    regulatory-rule-engine defect this auditor is specifically supposed to
    catch (checkpoint requirement: "Auditor detects unsupported
    conclusion" / "Unsupported regulatory claim is rejected/flagged")."""
    issues = []
    for r in regulatory_findings:
        if r["status"] == "confirmed_concern" and not r.get("supporting_evidence"):
            issues.append({
                "issue_type": "unsupported_regulatory_claim",
                "severity": "critical",
                "detail": {"rule_id": r["rule_id"], "rule_name": r["rule_name"]},
            })
    return issues


def _check_weak_hypothesis_support(completeness, regulatory_findings, weak_threshold=50.0):
    """A "confirmed_concern" regulatory finding resting on a case whose
    OVERALL evidence completeness is itself weak is a weak-basis flag -
    independent of whatever the rule engine's own per-rule confidence
    says, since a rule can be internally well-evaluated yet still rest on
    a thin overall evidentiary base."""
    weighted_score = completeness.get("weighted_score")
    if weighted_score is None or weighted_score >= weak_threshold:
        return []
    confirmed = [r for r in regulatory_findings if r["status"] == "confirmed_concern"]
    if not confirmed:
        return []
    return [{
        "issue_type": "weak_hypothesis_support",
        "severity": "moderate",
        "detail": {
            "weighted_completeness": weighted_score,
            "confirmed_rules": [r["rule_id"] for r in confirmed],
        },
    }]


def _check_provenance_problems(evidence_items):
    issues = []
    for item in evidence_items:
        if (item["available"] and item["evidence_type"] in _EXPECTS_RECORD_IDS_WHEN_AVAILABLE
                and not item.get("source_record_ids")):
            issues.append({
                "issue_type": "evidence_provenance_problem",
                "severity": "moderate",
                "detail": {"evidence_id": item["evidence_id"], "evidence_type": item["evidence_type"]},
            })
    return issues


def _check_incomplete_investigation_requirements(completeness):
    if completeness.get("method") == "no_requirement_table_for_typology":
        return [{
            "issue_type": "incomplete_investigation_requirement",
            "severity": "critical",
            "detail": {"reason": "no_typed_evidence_requirement_table_for_this_typology"},
        }]
    return []


def _check_unsupported_authority_conclusion(authority_decision, regulatory_findings):
    """Independent cross-check: if the authority-policy engine authorized
    junior resolution (`can_resolve: True`) while a regulatory rule
    reached "confirmed_concern" for THIS case, that is a real cross-stage
    inconsistency worth surfacing - authority_policy.py has no visibility
    into regulatory findings (Checkpoint 4 predates this checkpoint), so
    this is a genuine, newly-possible check, not a restatement of either
    stage's own conclusion."""
    if not authority_decision or not authority_decision.get("can_resolve"):
        return []
    confirmed = [r for r in regulatory_findings if r["status"] == "confirmed_concern"]
    if not confirmed:
        return []
    return [{
        "issue_type": "unsupported_conclusion",
        "severity": "critical",
        "detail": {
            "authority_decision": authority_decision.get("decision"),
            "conflicting_regulatory_rules": [r["rule_id"] for r in confirmed],
        },
    }]


def _check_jurisdiction_mismatch(regulatory_findings, jurisdiction_context):
    """Independent structural re-check (NEW this checkpoint): for every
    regulatory citation a rule result carries, its own `jurisdiction` must
    be one of this case's `applicable_jurisdictions` - e.g. a US citation
    must never appear on an India-jurisdiction case's finding merely
    because a keyword happened to overlap. Skipped entirely (no issue
    either way) if `jurisdiction_context` was not supplied - see module
    docstring."""
    if not jurisdiction_context:
        return []
    applicable = set(jurisdiction_context.get("applicable_jurisdictions") or [])
    issues = []
    for r in regulatory_findings:
        for entry in r.get("regulatory_context", []):
            if entry.get("jurisdiction") not in applicable:
                issues.append({
                    "issue_type": "jurisdiction_mismatch",
                    "severity": "critical",
                    "detail": {
                        "rule_id": r["rule_id"],
                        "source_id": entry.get("source_id"),
                        "entry_jurisdiction": entry.get("jurisdiction"),
                        "case_applicable_jurisdictions": sorted(applicable),
                    },
                })
    return issues


def _check_unresolved_jurisdiction(jurisdiction_context, regulatory_findings):
    """Independent structural re-check (NEW this checkpoint): a
    "confirmed_concern" regulatory conclusion resting on a case whose own
    jurisdiction could not be determined at all ("unknown") is a
    structural problem worth surfacing on its own - distinct from
    `_check_jurisdiction_mismatch` above, which only fires when a citation
    exists but is the WRONG one; this fires when jurisdiction is unresolved
    entirely, so any citation present is unverifiable applicability, not
    merely a wrong one. Skipped if `jurisdiction_context` was not
    supplied."""
    if not jurisdiction_context:
        return []
    if jurisdiction_context.get("jurisdiction") != "unknown":
        return []
    confirmed = [r for r in regulatory_findings if r["status"] == "confirmed_concern"]
    if not confirmed:
        return []
    return [{
        "issue_type": "regulatory_conclusion_without_resolved_jurisdiction",
        "severity": "critical",
        "detail": {
            "registered_country": jurisdiction_context.get("registered_country"),
            "confirmed_rules": [r["rule_id"] for r in confirmed],
        },
    }]


def audit_investigation(case, evidence_items, completeness, net=None, account=None,
                         contradiction_state=None, regulatory_findings=None,
                         authority_decision=None, structural_gap_reasons=(),
                         jurisdiction_context=None):
    """The one public entry point. Every `_check_*` helper above is an
    independent structural inspection over already-computed upstream
    output - nothing here re-gathers evidence or calls an LLM.

    Returns {"case_id", "issues": [...], "issue_count", "critical_issue_count"}.
    """
    regulatory_findings = regulatory_findings or []
    issues = []
    issues += _check_missing_critical_evidence(completeness, structural_gap_reasons)
    issues += _check_contradictory_evidence(contradiction_state)
    issues += _check_unsupported_regulatory_claims(regulatory_findings)
    issues += _check_weak_hypothesis_support(completeness, regulatory_findings)
    issues += _check_provenance_problems(evidence_items)
    issues += _check_incomplete_investigation_requirements(completeness)
    issues += _check_unsupported_authority_conclusion(authority_decision, regulatory_findings)
    issues += _check_jurisdiction_mismatch(regulatory_findings, jurisdiction_context)
    issues += _check_unresolved_jurisdiction(jurisdiction_context, regulatory_findings)

    critical_count = sum(1 for i in issues if i["severity"] == "critical")
    return {
        "case_id": case["case_id"],
        "issues": issues,
        "issue_count": len(issues),
        "critical_issue_count": critical_count,
    }