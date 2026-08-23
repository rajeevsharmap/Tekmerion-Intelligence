"""
tests/test_checkpoint6.py
============================
CHECKPOINT 6 test suite: Next-Best-Action, Audit Trail, Human Review,
Investigator Action, Action Authorization Enforcement, Case State Machine,
Case Memory (next_best_action.py, audit_trail.py, investigator_action.py,
case_state.py, case_memory.py, action_pipeline.py).

Two kinds of tests, same split as test_evidence_model.py /
test_authority_policy.py:
  1. Unit tests against small hand-built case/evidence/authority/
     regulatory/auditor fixtures - exercise each decision branch directly.
  2. An integration test against the REAL, checked-in
     `pipeline_output/evidence/*.json` (produced by a real run of
     run_pipeline.py against mock_data/) - confirms the whole Checkpoint 6
     layer runs cleanly on genuine upstream output, not just fixtures.
     (`pipeline_output/` is checked-in/regenerable via
     `python3 run_pipeline.py`; this test skips gracefully if it is
     absent rather than failing the whole file.)

Known, documented dataset property (same as Checkpoint 4's own test
suite): every real case in the checked-in mock dataset routes to
`authority_tier: "senior"` - there is no real junior-authorized case on
disk. Junior-path tests below therefore use hand-built fixtures, exactly
as test_authority_policy.py already does for the same reason.
"""
import glob
import json
import os

import pytest

import case_state as cs
from audit_trail import AuditTrail, is_append_only_extension
from next_best_action import recommend_next_best_action, ACTION_MINIMUM_AUTHORITY
from investigator_action import (
    authorize_action,
    create_human_review,
    record_investigator_action,
    resolve_investigator,
    OverrideReasonRequiredError,
    INVESTIGATOR_DIRECTORY,
)
from case_memory import build_case_memory, update_case_memory
from action_pipeline import CaseActionLayer, InvalidActionLayerStateError

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE_DIR = os.path.join(BACKEND_DIR, "pipeline_output", "evidence")


# ----------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------
def _case(case_id="CASE-T1", typology="smurfing", account_id="ACC1", status="open"):
    return {"case_id": case_id, "account_id": account_id, "primary_trigger": typology, "status": status}


def _evidence_items(n=3, available=True):
    return [
        {"evidence_id": f"EVD-{i}", "evidence_type": f"type_{i}", "weight": 0.2,
         "available": available, "quality": "high"}
        for i in range(n)
    ]


def _completeness(score=90.0, missing=None):
    return {"weighted_score": score, "simple_score": score, "required_count": 3,
            "available_count": 3, "missing": missing or [], "method": "deterministic_weighted_availability"}


def _case_completeness(status="complete", score=90.0, reasons=None):
    return {"case_id": "CASE-T1", "score": score, "threshold": 75.0, "status": status,
            "missing_evidence": [], "critical_missing_evidence": [],
            "satisfied_requirements": [], "failed_requirements": [],
            "reasons": reasons or ["all_reachable_requirements_satisfied"],
            "next_step": "continue" if status == "complete" else "re_gather",
            "components": {"evidence": score, "regulatory": 100.0, "auditor": 100.0},
            "jurisdiction_context": None}


def _regulatory_findings(statuses):
    return [
        {"rule_id": f"RULE-{i}", "rule_name": f"Rule {i}", "status": status, "confidence": 1.0,
         "supporting_evidence": [], "rationale": "test fixture"}
        for i, status in enumerate(statuses)
    ]


def _auditor(critical=0, issues=None):
    return {"case_id": "CASE-T1", "issues": issues or [], "issue_count": critical,
            "critical_issue_count": critical}


def _authority(tier="junior", can_resolve=True, confidence=0.9, reasons=None, risk_factors=None):
    return {"case_id": "CASE-T1", "authority_tier": tier, "can_resolve": can_resolve,
            "decision": "junior_authorized" if can_resolve else "senior_review_required",
            "reasons": reasons or (["no_critical_gap"] if can_resolve else ["high_risk_typology"]),
            "risk_factors": risk_factors or [], "missing_evidence": [], "policy_version": "v1",
            "confidence": confidence, "confidence_source": "supplied", "policy_inputs": {}}


def _recommendation(action="MONITOR", required_authority="junior", case_id="CASE-T1",
                     supporting_evidence_ids=None):
    return {
        "recommendation_id": "NBA-TEST", "case_id": case_id, "typology": "smurfing",
        "recommended_action": action, "reason_codes": ["test_reason"],
        "supporting_evidence_ids": supporting_evidence_ids or ["EVD-0"], "regulatory_basis": [],
        "confidence": 0.8, "required_authority": required_authority, "requires_human_review": True,
        "policy_version": "v1",
    }


def _evidence_dict(typology="smurfing", cc_status="complete", authority_tier="senior",
                    can_resolve=False, reg_statuses=("no_identified_breach",), critical_issues=0,
                    regather=None):
    return {
        "evidence_items": _evidence_items(),
        "completeness": _completeness(),
        "authority": _authority(tier=authority_tier, can_resolve=can_resolve),
        "jurisdiction": {"jurisdiction": "IN", "base_jurisdiction": "IN", "confidence": "high"},
        "regulatory_findings": _regulatory_findings(reg_statuses),
        "auditor": _auditor(critical=critical_issues),
        "case_completeness": _case_completeness(status=cc_status),
        "regather": regather,
    }


# ----------------------------------------------------------------------
# 1-3. Next-Best-Action engine
# ----------------------------------------------------------------------
def test_next_best_action_generated():
    case = _case()
    rec = recommend_next_best_action(case, _evidence_items(), _completeness(), _case_completeness(),
                                      [], _auditor(), _authority())
    assert rec["recommended_action"] in (
        "CLEAR", "MONITOR", "REQUEST_MORE_INFORMATION", "ESCALATE_TO_SENIOR",
        "RESTRICT_ACCOUNT", "BLOCK_TRANSACTION", "FILE_SAR", "CLOSE_CASE",
    )
    assert rec["case_id"] == case["case_id"]
    assert rec["requires_human_review"] is True


def test_recommendation_has_reason_codes():
    case = _case()
    rec = recommend_next_best_action(case, _evidence_items(), _completeness(), _case_completeness(),
                                      [], _auditor(), _authority())
    assert isinstance(rec["reason_codes"], list) and len(rec["reason_codes"]) > 0


def test_required_authority_derived_correctly():
    case = _case(typology="money_mule")
    # confirmed_concern on a funds-in-motion typology -> BLOCK_TRANSACTION,
    # which always requires senior (ACTION_MINIMUM_AUTHORITY), regardless
    # of a junior case-level authority tier.
    rec = recommend_next_best_action(
        case, _evidence_items(), _completeness(), _case_completeness(),
        _regulatory_findings(["confirmed_concern"]), _auditor(), _authority(tier="junior", can_resolve=True),
    )
    assert rec["recommended_action"] == "BLOCK_TRANSACTION"
    assert rec["required_authority"] == "senior"
    assert ACTION_MINIMUM_AUTHORITY["BLOCK_TRANSACTION"] == "senior"


def test_incomplete_case_recommends_request_more_information():
    case = _case()
    rec = recommend_next_best_action(case, _evidence_items(), _completeness(), _case_completeness(status="incomplete"),
                                      [], _auditor(), _authority())
    assert rec["recommended_action"] == "REQUEST_MORE_INFORMATION"


def test_clean_junior_case_recommends_clear():
    case = _case(typology="smurfing")
    rec = recommend_next_best_action(
        case, _evidence_items(), _completeness(), _case_completeness(),
        _regulatory_findings(["no_identified_breach"]), _auditor(),
        _authority(tier="junior", can_resolve=True, risk_factors=[]),
    )
    assert rec["recommended_action"] == "CLEAR"
    assert rec["required_authority"] == "junior"


def test_confirmed_concern_smurfing_recommends_restrict_account():
    case = _case(typology="smurfing")
    rec = recommend_next_best_action(
        case, _evidence_items(), _completeness(), _case_completeness(),
        _regulatory_findings(["confirmed_concern"]), _auditor(), _authority(tier="senior", can_resolve=False),
    )
    assert rec["recommended_action"] == "RESTRICT_ACCOUNT"


def test_confirmed_concern_with_critical_auditor_issue_escalates_instead_of_filing():
    case = _case(typology="money_mule")
    rec = recommend_next_best_action(
        case, _evidence_items(), _completeness(), _case_completeness(),
        _regulatory_findings(["confirmed_concern"]), _auditor(critical=1), _authority(tier="senior", can_resolve=False),
    )
    assert rec["recommended_action"] == "ESCALATE_TO_SENIOR"
    assert "auditor_critical_issue_overrides_automatic_filing" in rec["reason_codes"]


# ----------------------------------------------------------------------
# 4-7. Action Authorization Enforcement
# ----------------------------------------------------------------------
def test_junior_can_execute_junior_authorized_action():
    result = authorize_action("MONITOR", "junior", "INV-J001")
    assert result["authorized"] is True
    assert result["reason"] == "AUTHORIZED"


def test_junior_cannot_execute_senior_only_action():
    result = authorize_action("BLOCK_TRANSACTION", "senior", "INV-J001")
    assert result["authorized"] is False
    assert result["reason"] == "ACTION_REQUIRES_SENIOR_AUTHORITY"


def test_senior_can_execute_senior_authorized_action():
    result = authorize_action("BLOCK_TRANSACTION", "senior", "INV-S001")
    assert result["authorized"] is True


def test_unauthorized_attempt_is_recorded_not_silently_rejected():
    """record_investigator_action must still return a full record (not
    raise/None) for an unauthorized attempt, so the caller can append it
    to the audit trail."""
    case = _case()
    rec = _recommendation(action="BLOCK_TRANSACTION", required_authority="senior")
    action_record = record_investigator_action(case, rec, "INV-J001", "BLOCK_TRANSACTION",
                                                 reason="attempting to follow recommendation")
    assert action_record["authorized"] is False
    assert action_record["actual_action"] == "REJECTED_UNAUTHORIZED"
    assert action_record["action_id"]


# ----------------------------------------------------------------------
# 8-11. Human Review + Investigator Action + Override
# ----------------------------------------------------------------------
def test_human_review_is_recorded():
    case = _case()
    rec = _recommendation(action="MONITOR")
    review = create_human_review(case, rec, "INV-S001", "MONITOR", "agrees with recommendation")
    assert review["case_id"] == case["case_id"]
    assert review["reviewer_role"] == "senior"
    assert review["system_recommendation"] == "MONITOR"
    assert review["status"] == "approved"


def test_investigator_can_follow_recommendation():
    case = _case()
    rec = _recommendation(action="MONITOR", required_authority="junior")
    action_record = record_investigator_action(case, rec, "INV-J001", "MONITOR", reason="agree")
    assert action_record["recommendation_followed"] is True
    assert action_record["override_reason"] is None
    assert action_record["authorized"] is True


def test_investigator_can_override_recommendation():
    case = _case()
    rec = _recommendation(action="MONITOR", required_authority="junior")
    action_record = record_investigator_action(
        case, rec, "INV-S001", "ESCALATE_TO_SENIOR", reason="unusual pattern",
        override_reason="additional context outside the automated evidence suggests higher risk",
    )
    assert action_record["recommendation_followed"] is False
    assert action_record["override_reason"]
    assert action_record["actual_action"] == "ESCALATE_TO_SENIOR"


def test_override_requires_reason():
    case = _case()
    rec = _recommendation(action="MONITOR", required_authority="junior")
    with pytest.raises(OverrideReasonRequiredError):
        record_investigator_action(case, rec, "INV-S001", "ESCALATE_TO_SENIOR", reason="disagree")


# ----------------------------------------------------------------------
# 12-15. Audit Trail
# ----------------------------------------------------------------------
def test_audit_trail_records_recommendation():
    trail = AuditTrail("CASE-T1")
    trail.append("next_best_action_generated", "system", "next_best_action", after_state={"a": 1})
    assert len(trail.events) == 1
    assert trail.events[0]["event_type"] == "next_best_action_generated"


def test_audit_trail_records_review():
    trail = AuditTrail("CASE-T1")
    trail.append("human_review_completed", "investigator", "INV-S001", after_state={"status": "approved"})
    assert trail.events[-1]["actor_type"] == "investigator"


def test_audit_trail_records_action():
    trail = AuditTrail("CASE-T1")
    trail.append("action_executed", "system", "investigator_action", after_state={"actual_action": "MONITOR"})
    assert trail.events[-1]["event_type"] == "action_executed"


def test_audit_trail_is_append_only():
    trail = AuditTrail("CASE-T1")
    trail.append("case_created", "system", "detection_layer")
    snapshot_1 = trail.events
    trail.append("human_review_started", "system", "action_pipeline")
    snapshot_2 = trail.events
    assert is_append_only_extension(snapshot_1, snapshot_2)
    # mutating a returned snapshot must never affect internal state
    snapshot_2.pop()
    assert len(trail.events) == 2
    # no public API to remove/overwrite an event
    assert not hasattr(trail, "remove")
    assert not hasattr(trail, "clear")
    assert not hasattr(trail, "pop")


# ----------------------------------------------------------------------
# 16-17. Case State Machine
# ----------------------------------------------------------------------
def test_invalid_case_transitions_are_rejected():
    with pytest.raises(cs.InvalidTransitionError):
        cs.transition(cs.CLOSED, cs.INVESTIGATING)
    with pytest.raises(cs.InvalidTransitionError):
        cs.transition(cs.SUSPECTED, cs.ACTION_EXECUTED)


def test_valid_case_transitions_succeed():
    state = cs.SUSPECTED
    state = cs.transition(state, cs.INVESTIGATING)
    state = cs.transition(state, cs.AUDIT_READY)
    state = cs.transition(state, cs.HUMAN_REVIEW)
    state = cs.transition(state, cs.ACTION_PENDING)
    state = cs.transition(state, cs.ACTION_EXECUTED)
    state = cs.transition(state, cs.CLOSED)
    assert state == cs.CLOSED


# ----------------------------------------------------------------------
# 18-19. Case Memory
# ----------------------------------------------------------------------
def test_case_memory_retains_prior_actions():
    case = _case()
    memory = build_case_memory(case, None, _evidence_items(), _completeness(), _authority(),
                                [], _auditor(), _case_completeness(), _recommendation(), cs.HUMAN_REVIEW)
    review1 = create_human_review(case, _recommendation(), "INV-S001", "MONITOR", "first pass")
    memory = update_case_memory(memory, human_review=review1, lifecycle_state=cs.ACTION_PENDING)
    action1 = record_investigator_action(case, _recommendation(action="MONITOR", required_authority="junior"),
                                          "INV-J001", "MONITOR", reason="following recommendation")
    memory = update_case_memory(memory, investigator_action=action1, lifecycle_state=cs.ACTION_EXECUTED)
    assert memory["human_review"] == review1
    assert memory["investigator_action"] == action1
    assert memory["human_review_history"] == [review1]
    assert memory["investigator_action_history"] == [action1]


def test_case_memory_does_not_erase_historical_events():
    case = _case()
    memory = build_case_memory(case, None, _evidence_items(), _completeness(), _authority(),
                                [], _auditor(), _case_completeness(), _recommendation(), cs.HUMAN_REVIEW)
    review1 = create_human_review(case, _recommendation(), "INV-S001", "MONITOR", "first pass")
    memory = update_case_memory(memory, human_review=review1)
    review2 = create_human_review(case, _recommendation(), "INV-S002", "ESCALATE_TO_SENIOR", "second look")
    memory = update_case_memory(memory, human_review=review2)
    assert review1 in memory["human_review_history"]
    assert review2 in memory["human_review_history"]
    assert len(memory["human_review_history"]) == 2
    assert memory["lifecycle_history"] == [cs.HUMAN_REVIEW]  # unchanged - not touched this update


# ----------------------------------------------------------------------
# 20-24. Cross-checkpoint data preservation
# ----------------------------------------------------------------------
def test_authority_decision_from_checkpoint4_is_preserved():
    case = _case()
    evidence = _evidence_dict(authority_tier="senior", can_resolve=False)
    layer = CaseActionLayer(case, evidence, case_alerts=[])
    assert layer.memory["authority_decision"] == evidence["authority"]
    assert layer.memory["authority_decision"]["authority_tier"] == "senior"


def test_regulatory_findings_from_checkpoint5_remain_available():
    case = _case(typology="money_mule")
    evidence = _evidence_dict(typology="money_mule", reg_statuses=["confirmed_concern", "no_identified_breach"])
    layer = CaseActionLayer(case, evidence, case_alerts=[])
    assert layer.memory["regulatory_findings"] == evidence["regulatory_findings"]
    assert any(r["status"] == "confirmed_concern" for r in layer.memory["regulatory_findings"])


def test_jurisdiction_context_remains_available():
    case = _case()
    evidence = _evidence_dict()
    layer = CaseActionLayer(case, evidence, case_alerts=[])
    assert layer.memory["jurisdiction"] == evidence["jurisdiction"]


def test_completeness_history_remains_available():
    case = _case()
    evidence = _evidence_dict(regather={"iterations": [{"iteration": 1}]})
    layer = CaseActionLayer(case, evidence, case_alerts=[])
    assert layer.memory["case_completeness_history"] == [evidence["case_completeness"]]
    assert any(e["event_type"] == "evidence_regathered" for e in layer.trail.events)


def test_sar_required_fields_are_available():
    case = _case(typology="money_mule")
    evidence = _evidence_dict(typology="money_mule", reg_statuses=["confirmed_concern"])
    layer = CaseActionLayer(case, evidence, case_alerts=[])
    memory = layer.memory
    required = {
        "case_id", "account_id", "typology", "jurisdiction", "evidence_references",
        "regulatory_findings", "auditor_findings", "authority_decision",
        "recommended_action", "audit_trail_ref",
    }
    assert required <= set(memory.keys())


# ----------------------------------------------------------------------
# 25. Backend does not trust client-supplied investigator role
# ----------------------------------------------------------------------
def test_backend_does_not_trust_client_supplied_role():
    """`resolve_investigator`/`authorize_action` only ever accept an
    investigator_id - there is no parameter anywhere in this module's
    public API that lets a caller assert a role directly. A junior
    investigator's own id, even if a hypothetical caller also passed
    role="senior" alongside it (impossible to express through this API -
    which is itself the point), still resolves to "junior" server-side."""
    assert resolve_investigator("INV-J001")["role"] == "junior"
    result = authorize_action("BLOCK_TRANSACTION", "senior", "INV-J001")
    assert result["investigator_authority"] == "junior"
    assert result["authorized"] is False
    # an unrecognized identity is never treated as authorized/permissive
    unknown = authorize_action("MONITOR", "junior", "INV-DOES-NOT-EXIST")
    assert unknown["authorized"] is False
    assert unknown["investigator_authority"] is None


# ----------------------------------------------------------------------
# Additional required scenarios: junior/senior escalation, override,
# rejected, closed, incomplete, re-gathered, regulatory concern, cleared.
# ----------------------------------------------------------------------
def test_scenario_junior_senior_escalation():
    case = _case(typology="smurfing")
    evidence = _evidence_dict(typology="smurfing", authority_tier="senior", can_resolve=False,
                               reg_statuses=["potentially_applicable"])
    layer = CaseActionLayer(case, evidence, case_alerts=[])
    assert layer.state == cs.HUMAN_REVIEW
    assert layer.recommendation["recommended_action"] == "ESCALATE_TO_SENIOR"
    layer.complete_human_review("INV-S001", "ESCALATE_TO_SENIOR", "agrees, escalating")
    action = layer.submit_action("INV-S001", "ESCALATE_TO_SENIOR", "escalating per review")
    assert action["authorized"] is True
    assert layer.state == cs.ESCALATED


def test_scenario_action_override():
    case = _case(typology="smurfing")
    evidence = _evidence_dict(typology="smurfing", authority_tier="junior", can_resolve=True,
                               reg_statuses=["no_identified_breach"])
    layer = CaseActionLayer(case, evidence, case_alerts=[])
    assert layer.recommendation["recommended_action"] == "CLEAR"
    layer.complete_human_review("INV-S001", "MONITOR", "senior disagrees with auto-clear",
                                 evidence_reviewed=["EVD-0"])
    assert layer.human_review["status"] == "overridden"
    action = layer.submit_action("INV-S001", "MONITOR", "monitoring instead of clearing",
                                  override_reason="senior investigator judgment: pattern warrants monitoring")
    assert action["recommendation_followed"] is False
    assert any(e["event_type"] == "recommendation_overridden" for e in layer.trail.events)


def test_scenario_rejected_unauthorized_action():
    case = _case(typology="money_mule")
    evidence = _evidence_dict(typology="money_mule", authority_tier="senior", can_resolve=False,
                               reg_statuses=["confirmed_concern"])
    layer = CaseActionLayer(case, evidence, case_alerts=[])
    assert layer.recommendation["recommended_action"] == "BLOCK_TRANSACTION"
    layer.complete_human_review("INV-J001", "BLOCK_TRANSACTION", "attempting to follow recommendation")
    action = layer.submit_action("INV-J001", "BLOCK_TRANSACTION", "trying to block")
    assert action["authorized"] is False
    assert action["actual_action"] == "REJECTED_UNAUTHORIZED"
    assert layer.state == cs.HUMAN_REVIEW  # returned for a different investigator
    assert any(e["event_type"] == "action_rejected" for e in layer.trail.events)


def test_scenario_closed_case():
    case = _case(typology="smurfing")
    evidence = _evidence_dict(typology="smurfing", authority_tier="junior", can_resolve=True,
                               reg_statuses=["no_identified_breach"])
    layer = CaseActionLayer(case, evidence, case_alerts=[])
    layer.complete_human_review("INV-J001", "CLEAR", "confirmed clean")
    action = layer.submit_action("INV-J001", "CLEAR", "clearing per review")
    assert action["authorized"] is True
    assert layer.state == cs.CLOSED
    assert any(e["event_type"] == "case_closed" for e in layer.trail.events)


def test_scenario_incomplete_case_stays_in_investigating():
    case = _case(typology="smurfing")
    evidence = _evidence_dict(cc_status="incomplete")
    layer = CaseActionLayer(case, evidence, case_alerts=[])
    assert layer.recommendation["recommended_action"] == "REQUEST_MORE_INFORMATION"
    assert layer.state == cs.INVESTIGATING
    with pytest.raises(InvalidActionLayerStateError):
        layer.complete_human_review("INV-S001", "REQUEST_MORE_INFORMATION", "not ready")


def test_scenario_regathered_case_logs_evidence_regathered_event():
    case = _case(typology="account_swap")
    evidence = _evidence_dict(typology="account_swap", cc_status="complete",
                               regather={"iterations": [{"iteration": 1}, {"iteration": 2}]})
    layer = CaseActionLayer(case, evidence, case_alerts=[])
    types = [e["event_type"] for e in layer.trail.events]
    assert "evidence_regathered" in types


def test_scenario_regulatory_concern_case_recommends_protective_action():
    case = _case(typology="account_swap")
    evidence = _evidence_dict(typology="account_swap", reg_statuses=["confirmed_concern"])
    layer = CaseActionLayer(case, evidence, case_alerts=[])
    assert layer.recommendation["recommended_action"] == "BLOCK_TRANSACTION"


def test_scenario_legitimate_cleared_case():
    case = _case(typology="reverse_smurfing")
    evidence = _evidence_dict(typology="reverse_smurfing", authority_tier="junior", can_resolve=True,
                               reg_statuses=["no_identified_breach"])
    layer = CaseActionLayer(case, evidence, case_alerts=[])
    assert layer.recommendation["recommended_action"] == "CLEAR"
    assert layer.recommendation["required_authority"] == "junior"


# ----------------------------------------------------------------------
# Integration: real, checked-in pipeline output (no networkx/DataStore
# needed - reads already-persisted JSON from a real run_pipeline.py run).
# ----------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isdir(EVIDENCE_DIR), reason="pipeline_output/evidence not present - run run_pipeline.py first")
def test_real_pipeline_output_produces_well_formed_checkpoint6_layer():
    files = sorted(glob.glob(os.path.join(EVIDENCE_DIR, "*.json")))
    assert files, "expected at least one persisted evidence file"
    checked = 0
    for path in files:
        with open(path) as f:
            evidence = json.load(f)
        case = {"case_id": evidence["case_id"], "account_id": evidence["account_id"],
                "primary_trigger": evidence["typology"], "status": "open"}
        layer = CaseActionLayer(case, evidence, case_alerts=[])
        assert layer.recommendation["recommended_action"] in (
            "CLEAR", "MONITOR", "REQUEST_MORE_INFORMATION", "ESCALATE_TO_SENIOR",
            "RESTRICT_ACCOUNT", "BLOCK_TRANSACTION", "FILE_SAR", "CLOSE_CASE",
        )
        assert layer.state in cs.CASE_STATES
        assert layer.memory["case_id"] == evidence["case_id"]
        assert layer.memory["authority_decision"] == evidence.get("authority")
        assert layer.memory["regulatory_findings"] == evidence.get("regulatory_findings")
        assert is_append_only_extension([], layer.trail.events)
        checked += 1
    assert checked == len(files)