"""
tests/test_checkpoint7.py
============================
CHECKPOINT 7 test suite: SAR (Suspicious Activity Report) generation
(sar_report.py), plus its integration into action_pipeline.py's
CaseActionLayer and case_memory.py's optional `sar_report` field.

Same split as test_checkpoint6.py:
  1. Unit tests against small hand-built regulatory/jurisdiction/auditor/
     investigator_action fixtures - exercise each precondition and status
     branch of build_sar_report() directly.
  2. Integration tests through CaseActionLayer.submit_action() - confirm
     the SAR record is generated, audited, and stored in case memory only
     when a real FILE_SAR action is authorized and executed.
  3. A regression/compatibility check against the REAL, checked-in
     `pipeline_output/evidence/*.json` - confirms Checkpoint 4-6 output
     is untouched by this checkpoint and that CaseActionLayer still runs
     cleanly end-to-end on genuine upstream data (skips gracefully if
     pipeline_output/ is absent).

Known, documented dataset property (same class of limitation as
Checkpoint 4's/Checkpoint 6's own test suites): on the checked-in mock
dataset, no real case's typology (money_mule/account_swap/smurfing/
reverse_smurfing) ever reaches FILE_SAR - next_best_action.py only
recommends FILE_SAR for a confirmed-concern case whose typology is
outside FUNDS_IN_MOTION_TYPOLOGIES/NETWORK_TYPOLOGIES, and this dataset's
four detector typologies are exactly those two groups. Every FILE_SAR
scenario below therefore uses a hand-built fixture, exactly as
test_checkpoint6.py already does for its own junior/CLEAR path.
"""
import glob
import json
import os

import pytest

import case_state as cs
from action_pipeline import CaseActionLayer
from sar_report import build_sar_report
from case_memory import build_case_memory, update_case_memory

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE_DIR = os.path.join(BACKEND_DIR, "pipeline_output", "evidence")


# ----------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------
def _case(case_id="CASE-T1", typology="money_mule", account_id="ACC1", status="open"):
    return {"case_id": case_id, "account_id": account_id, "primary_trigger": typology, "status": status}


def _citation(source_id, jurisdiction, citation="Some Act, Sec. 1", authority="FIU-IND"):
    return {"source_id": source_id, "citation": citation, "authority": authority,
            "jurisdiction": jurisdiction, "title": "test corpus entry", "topic": "test"}


def _finding(rule_id="RULE-CTR-001", status="confirmed_concern", citations=None, rationale="test rationale"):
    return {
        "rule_id": rule_id, "rule_name": f"Rule {rule_id}", "typology": "money_mule",
        "status": status, "confidence": 1.0, "supporting_evidence": [],
        "rationale": rationale, "regulatory_context": citations or [], "case_id": "CASE-T1",
    }


def _jurisdiction(tag="IN"):
    return {"case_id": "CASE-T1", "jurisdiction": tag, "base_jurisdiction": "IN" if tag != "US" else "US",
            "applicable_jurisdictions": [tag], "confidence": "high", "registered_country": "India",
            "cross_border_indicators": [], "basis": ["test fixture"]}


def _evidence_items(n=2):
    return [{"evidence_id": f"EVD-{i}", "evidence_type": f"type_{i}", "weight": 0.5,
              "available": True, "quality": "high"} for i in range(n)]


def _auditor(critical=0, issues=None):
    return {"case_id": "CASE-T1", "issues": issues or [], "issue_count": critical,
            "critical_issue_count": critical}


def _investigator_action(actual_action="FILE_SAR", authorized=True, investigator_id="INV-S001",
                          investigator_role="senior"):
    return {
        "action_id": "ACTION-TEST", "case_id": "CASE-T1", "investigator_id": investigator_id,
        "investigator_role": investigator_role, "recommended_action": "FILE_SAR",
        "requested_action": "FILE_SAR", "required_authority": "senior", "authorized": authorized,
        "authorization_reason": "AUTHORIZED" if authorized else "ACTION_REQUIRES_SENIOR_AUTHORITY",
        "actual_action": actual_action, "recommendation_followed": True, "override_reason": None,
        "reason": "test fixture", "supporting_evidence": [], "timestamp": "2026-01-01T00:00:00+00:00",
    }


FIXED_TS = "2026-01-01T00:00:00+00:00"


# ----------------------------------------------------------------------
# 1. Preconditions - each independently re-validated, never trusted
# ----------------------------------------------------------------------
def test_blocked_when_action_is_not_authorized_file_sar():
    case = _case()
    rec = build_sar_report(case, _jurisdiction(), [_finding()], _evidence_items(), _auditor(),
                            _investigator_action(actual_action="REJECTED_UNAUTHORIZED", authorized=False),
                            filed_at=FIXED_TS)
    assert rec["status"] == "BLOCKED_ACTION_NOT_AUTHORIZED"
    assert rec["filing_jurisdiction"] is None
    assert rec["legal_basis_citations"] == []


def test_blocked_when_requested_action_differs_from_file_sar():
    """A senior authorized and executed BLOCK_TRANSACTION, not FILE_SAR -
    this module must never file a report for a different action."""
    case = _case()
    action = _investigator_action(actual_action="BLOCK_TRANSACTION", authorized=True)
    rec = build_sar_report(case, _jurisdiction(), [_finding()], _evidence_items(), _auditor(),
                            action, filed_at=FIXED_TS)
    assert rec["status"] == "BLOCKED_ACTION_NOT_AUTHORIZED"


def test_blocked_when_no_confirmed_regulatory_concern():
    """No confirmed_concern finding at all (only potentially_applicable) -
    never fabricate a filing basis."""
    case = _case()
    findings = [_finding(status="potentially_applicable")]
    rec = build_sar_report(case, _jurisdiction(), findings, _evidence_items(), _auditor(),
                            _investigator_action(), filed_at=FIXED_TS)
    assert rec["status"] == "BLOCKED_INSUFFICIENT_REGULATORY_BASIS"


def test_blocked_when_no_regulatory_findings_at_all():
    case = _case()
    rec = build_sar_report(case, _jurisdiction(), [], _evidence_items(), _auditor(),
                            _investigator_action(), filed_at=FIXED_TS)
    assert rec["status"] == "BLOCKED_INSUFFICIENT_REGULATORY_BASIS"


def test_blocked_when_jurisdiction_unresolved():
    case = _case()
    rec = build_sar_report(case, _jurisdiction(tag="unknown"), [_finding()], _evidence_items(),
                            _auditor(), _investigator_action(), filed_at=FIXED_TS)
    assert rec["status"] == "BLOCKED_JURISDICTION_UNRESOLVED"


def test_blocked_when_jurisdiction_context_missing_entirely():
    case = _case()
    rec = build_sar_report(case, None, [_finding()], _evidence_items(), _auditor(),
                            _investigator_action(), filed_at=FIXED_TS)
    assert rec["status"] == "BLOCKED_JURISDICTION_UNRESOLVED"


# ----------------------------------------------------------------------
# 2. Successful FILED path, jurisdiction-sensitive
# ----------------------------------------------------------------------
def test_india_case_files_to_fiu_ind_with_in_citations_only():
    case = _case()
    finding = _finding(citations=[_citation("REG-PMLA-3", "IN"), _citation("REG-BSA-CTR", "US", authority="FinCEN")])
    rec = build_sar_report(case, _jurisdiction("IN"), [finding], _evidence_items(), _auditor(),
                            _investigator_action(), filed_at=FIXED_TS)
    assert rec["status"] == "FILED"
    assert rec["filing_jurisdiction"] == "IN"
    assert rec["regulator"] == "FIU-IND"
    citation_ids = {c["source_id"] for c in rec["legal_basis_citations"]}
    assert citation_ids == {"REG-PMLA-3"}
    assert rec["supplementary_cross_border_citations"] == []


def test_us_case_files_to_fincen():
    case = _case()
    finding = _finding(citations=[_citation("REG-BSA-CTR", "US", authority="FinCEN")])
    rec = build_sar_report(case, _jurisdiction("US"), [finding], _evidence_items(), _auditor(),
                            _investigator_action(), filed_at=FIXED_TS)
    assert rec["status"] == "FILED"
    assert rec["filing_jurisdiction"] == "US"
    assert rec["regulator"] == "FinCEN"


def test_cross_border_case_files_as_india_never_as_us():
    """Rule 4/5: a cross-border case must never silently become a US
    filing merely because it's international - it still files as IN
    (this dataset's base jurisdiction), with cross-border material only
    as SUPPLEMENTARY basis, never substituted for the India filing."""
    case = _case()
    finding = _finding(citations=[
        _citation("REG-PMLA-3", "IN"),
        _citation("REG-FEMA-LRS", "cross_border", authority="RBI"),
        _citation("REG-BSA-CTR", "US", authority="FinCEN"),
    ])
    rec = build_sar_report(case, _jurisdiction("cross_border"), [finding], _evidence_items(), _auditor(),
                            _investigator_action(), filed_at=FIXED_TS)
    assert rec["filing_jurisdiction"] == "IN"
    assert rec["regulator"] == "FIU-IND"
    main_ids = {c["source_id"] for c in rec["legal_basis_citations"]}
    supp_ids = {c["source_id"] for c in rec["supplementary_cross_border_citations"]}
    assert main_ids == {"REG-PMLA-3"}
    assert supp_ids == {"REG-FEMA-LRS"}
    assert "REG-BSA-CTR" not in main_ids and "REG-BSA-CTR" not in supp_ids


def test_empty_citation_list_is_reported_not_backfilled():
    """A confirmed_concern finding whose regulatory_context happens to
    carry no IN-tagged entry must produce an honest empty list, never a
    plausible-looking invented citation."""
    case = _case()
    finding = _finding(citations=[_citation("REG-BSA-CTR", "US", authority="FinCEN")])
    rec = build_sar_report(case, _jurisdiction("IN"), [finding], _evidence_items(), _auditor(),
                            _investigator_action(), filed_at=FIXED_TS)
    assert rec["status"] == "FILED"
    assert rec["legal_basis_citations"] == []


# ----------------------------------------------------------------------
# 3. Contradictory-evidence / auditor-warning path
# ----------------------------------------------------------------------
def test_critical_auditor_issue_downgrades_to_draft_requires_secondary_review():
    case = _case()
    finding = _finding(citations=[_citation("REG-PMLA-3", "IN")])
    auditor = _auditor(critical=1, issues=[{"issue_type": "contradictory_evidence", "severity": "critical"}])
    rec = build_sar_report(case, _jurisdiction("IN"), [finding], _evidence_items(), auditor,
                            _investigator_action(), filed_at=FIXED_TS)
    assert rec["status"] == "DRAFT_REQUIRES_SECONDARY_REVIEW"
    assert len(rec["auditor_warnings"]) == 1
    # still names the real filing jurisdiction/citations - a warning
    # flags the record for review, it does not blank out real content.
    assert rec["filing_jurisdiction"] == "IN"
    assert rec["legal_basis_citations"]


def test_moderate_auditor_issue_does_not_block_filing():
    case = _case()
    finding = _finding(citations=[_citation("REG-PMLA-3", "IN")])
    auditor = _auditor(critical=0, issues=[{"issue_type": "provenance_gap", "severity": "moderate"}])
    rec = build_sar_report(case, _jurisdiction("IN"), [finding], _evidence_items(), auditor,
                            _investigator_action(), filed_at=FIXED_TS)
    assert rec["status"] == "FILED"
    assert rec["auditor_warnings"] == []


# ----------------------------------------------------------------------
# 4. Determinism, provenance, no fabrication
# ----------------------------------------------------------------------
def test_deterministic_repeated_execution():
    case = _case()
    finding = _finding(citations=[_citation("REG-PMLA-3", "IN")])
    rec1 = build_sar_report(case, _jurisdiction("IN"), [finding], _evidence_items(), _auditor(),
                             _investigator_action(), filed_at=FIXED_TS)
    rec2 = build_sar_report(case, _jurisdiction("IN"), [finding], _evidence_items(), _auditor(),
                             _investigator_action(), filed_at=FIXED_TS)
    assert rec1 == rec2
    assert rec1["sar_id"] == rec2["sar_id"]


def test_sar_id_changes_when_confirmed_findings_differ():
    case = _case()
    finding_a = _finding(rule_id="RULE-A", citations=[_citation("REG-PMLA-3", "IN")])
    finding_b = _finding(rule_id="RULE-B", citations=[_citation("REG-PMLA-3", "IN")])
    rec_a = build_sar_report(case, _jurisdiction("IN"), [finding_a], _evidence_items(), _auditor(),
                              _investigator_action(), filed_at=FIXED_TS)
    rec_b = build_sar_report(case, _jurisdiction("IN"), [finding_b], _evidence_items(), _auditor(),
                              _investigator_action(), filed_at=FIXED_TS)
    assert rec_a["sar_id"] != rec_b["sar_id"]


def test_supporting_evidence_ids_come_from_real_available_evidence_only():
    case = _case()
    finding = _finding(citations=[_citation("REG-PMLA-3", "IN")])
    items = [{"evidence_id": "EVD-A", "available": True}, {"evidence_id": "EVD-B", "available": False}]
    rec = build_sar_report(case, _jurisdiction("IN"), [finding], items, _auditor(),
                            _investigator_action(), filed_at=FIXED_TS)
    assert rec["supporting_evidence_ids"] == ["EVD-A"]


def test_summary_only_reflects_confirmed_findings_not_all_findings():
    case = _case()
    confirmed = _finding(rule_id="RULE-CONF", status="confirmed_concern",
                          citations=[_citation("REG-PMLA-3", "IN")], rationale="corroborated pattern")
    potential = _finding(rule_id="RULE-POT", status="potentially_applicable", rationale="single weak signal")
    rec = build_sar_report(case, _jurisdiction("IN"), [confirmed, potential], _evidence_items(),
                            _auditor(), _investigator_action(), filed_at=FIXED_TS)
    assert "RULE-CONF" in rec["suspicious_activity_summary"]
    assert "RULE-POT" not in rec["suspicious_activity_summary"]


def test_subject_accounts_are_pseudonymous_ids_only():
    case = _case(account_id="ACC-042")
    finding = _finding(citations=[_citation("REG-PMLA-3", "IN")])
    rec = build_sar_report(case, _jurisdiction("IN"), [finding], _evidence_items(), _auditor(),
                            _investigator_action(), filed_at=FIXED_TS)
    assert rec["subject_accounts"] == ["ACC-042"]


def test_no_pdf_or_password_protection_fields_present():
    """Explicit scope check: this checkpoint does not produce a PDF or
    any password-protection artifact - both remain out of scope."""
    case = _case()
    finding = _finding(citations=[_citation("REG-PMLA-3", "IN")])
    rec = build_sar_report(case, _jurisdiction("IN"), [finding], _evidence_items(), _auditor(),
                            _investigator_action(), filed_at=FIXED_TS)
    for forbidden_key in ("pdf", "pdf_path", "password", "encrypted"):
        assert forbidden_key not in rec


# ----------------------------------------------------------------------
# 5. Integration: CaseActionLayer -> SAR generation on FILE_SAR
# ----------------------------------------------------------------------
def _sar_eligible_evidence():
    """A hand-built evidence dict shaped so next_best_action.py recommends
    FILE_SAR: complete, confirmed regulatory concern, typology outside
    both FUNDS_IN_MOTION_TYPOLOGIES and NETWORK_TYPOLOGIES."""
    return {
        "evidence_items": _evidence_items(),
        "completeness": {"weighted_score": 90.0, "simple_score": 90.0, "required_count": 2,
                          "available_count": 2, "missing": [], "method": "deterministic_weighted_availability"},
        "authority": {"case_id": "CASE-SAR1", "authority_tier": "senior", "can_resolve": False,
                      "decision": "senior_review_required", "reasons": ["high_risk_typology"],
                      "risk_factors": [], "missing_evidence": [], "policy_version": "v1",
                      "confidence": 0.9, "confidence_source": "supplied", "policy_inputs": {}},
        "jurisdiction": _jurisdiction("IN"),
        "regulatory_findings": [_finding(citations=[_citation("REG-PMLA-3", "IN")])],
        "auditor": _auditor(critical=0),
        "case_completeness": {"case_id": "CASE-SAR1", "score": 90.0, "threshold": 75.0, "status": "complete",
                               "missing_evidence": [], "critical_missing_evidence": [],
                               "satisfied_requirements": [], "failed_requirements": [],
                               "reasons": ["all_reachable_requirements_satisfied"], "next_step": "continue",
                               "components": {"evidence": 90.0, "regulatory": 100.0, "auditor": 100.0},
                               "jurisdiction_context": None},
        "regather": None,
    }


def test_case_action_layer_generates_sar_on_authorized_file_sar():
    case = _case(case_id="CASE-SAR1", typology="smurfing")  # typology outside FUNDS_IN_MOTION/NETWORK for FILE_SAR fallback path is irrelevant here since we assert directly on the FILE_SAR outcome path
    # Force a typology not in FUNDS_IN_MOTION_TYPOLOGIES/NETWORK_TYPOLOGIES
    # so next_best_action.py's confirmed-concern branch falls through to
    # its FILE_SAR fallback rather than BLOCK_TRANSACTION/RESTRICT_ACCOUNT.
    case["primary_trigger"] = "unlisted_typology"
    evidence = _sar_eligible_evidence()
    layer = CaseActionLayer(case, evidence, case_alerts=[])
    assert layer.recommendation["recommended_action"] == "FILE_SAR"
    assert layer.state == cs.HUMAN_REVIEW

    layer.complete_human_review("INV-S001", "FILE_SAR", "confirmed reportable activity")
    action = layer.submit_action("INV-S001", "FILE_SAR", "filing per confirmed CTR breach")

    assert action["authorized"] is True
    assert action["actual_action"] == "FILE_SAR"
    assert layer.sar_report is not None
    assert layer.sar_report["status"] == "FILED"
    assert layer.sar_report["filing_jurisdiction"] == "IN"
    assert layer.memory["sar_report"] == layer.sar_report
    assert layer.memory["sar_report_history"] == [layer.sar_report]
    assert any(e["event_type"] == "sar_report_generated" for e in layer.trail.events)
    # audit trail is append-only across this whole flow
    assert layer.state == cs.CLOSED


def test_case_action_layer_junior_cannot_file_sar_and_no_sar_generated():
    case = _case(case_id="CASE-SAR2", typology="unlisted_typology")
    evidence = _sar_eligible_evidence()
    layer = CaseActionLayer(case, evidence, case_alerts=[])
    layer.complete_human_review("INV-S001", "FILE_SAR", "confirmed reportable activity")
    action = layer.submit_action("INV-J001", "FILE_SAR", "attempting to file")

    assert action["authorized"] is False
    assert action["actual_action"] == "REJECTED_UNAUTHORIZED"
    assert layer.sar_report is None
    assert layer.memory["sar_report"] is None
    assert not any(e["event_type"] == "sar_report_generated" for e in layer.trail.events)
    assert layer.state == cs.HUMAN_REVIEW


def test_case_action_layer_non_sar_action_never_populates_sar_report():
    case = _case(case_id="CASE-SAR3", typology="money_mule")
    evidence = {
        "evidence_items": _evidence_items(),
        "completeness": {"weighted_score": 90.0, "simple_score": 90.0, "required_count": 2,
                          "available_count": 2, "missing": [], "method": "deterministic_weighted_availability"},
        "authority": {"case_id": "CASE-SAR3", "authority_tier": "senior", "can_resolve": False,
                      "decision": "senior_review_required", "reasons": ["high_risk_typology"],
                      "risk_factors": [], "missing_evidence": [], "policy_version": "v1",
                      "confidence": 0.9, "confidence_source": "supplied", "policy_inputs": {}},
        "jurisdiction": _jurisdiction("IN"),
        "regulatory_findings": [_finding(citations=[_citation("REG-PMLA-3", "IN")])],
        "auditor": _auditor(critical=0),
        "case_completeness": {"case_id": "CASE-SAR3", "score": 90.0, "threshold": 75.0, "status": "complete",
                               "missing_evidence": [], "critical_missing_evidence": [],
                               "satisfied_requirements": [], "failed_requirements": [],
                               "reasons": ["all_reachable_requirements_satisfied"], "next_step": "continue",
                               "components": {"evidence": 90.0, "regulatory": 100.0, "auditor": 100.0},
                               "jurisdiction_context": None},
        "regather": None,
    }
    layer = CaseActionLayer(case, evidence, case_alerts=[])
    assert layer.recommendation["recommended_action"] == "BLOCK_TRANSACTION"
    layer.complete_human_review("INV-S001", "BLOCK_TRANSACTION", "confirmed money mule concern")
    action = layer.submit_action("INV-S001", "BLOCK_TRANSACTION", "blocking per confirmed concern")
    assert action["authorized"] is True
    assert layer.sar_report is None
    assert layer.memory["sar_report"] is None
    assert layer.memory["sar_report_history"] == []


# ----------------------------------------------------------------------
# 6. case_memory.py optional sar_report field - backward compatible
# ----------------------------------------------------------------------
def test_build_case_memory_without_sar_report_defaults_to_none():
    case = _case()
    memory = build_case_memory(case, _jurisdiction("IN"), _evidence_items(), {}, {}, [], _auditor(),
                                {}, {"recommended_action": "MONITOR"}, cs.HUMAN_REVIEW)
    assert memory["sar_report"] is None
    assert memory["sar_report_history"] == []


def test_update_case_memory_appends_sar_report_without_dropping_prior_history():
    case = _case()
    memory = build_case_memory(case, _jurisdiction("IN"), _evidence_items(), {}, {}, [], _auditor(),
                                {}, {"recommended_action": "FILE_SAR"}, cs.ACTION_PENDING)
    sar1 = {"sar_id": "SAR-1", "status": "DRAFT_REQUIRES_SECONDARY_REVIEW"}
    updated1 = update_case_memory(memory, sar_report=sar1)
    assert updated1["sar_report"] == sar1
    assert updated1["sar_report_history"] == [sar1]

    sar2 = {"sar_id": "SAR-1", "status": "FILED"}
    updated2 = update_case_memory(updated1, sar_report=sar2)
    assert updated2["sar_report"] == sar2
    # prior history entry preserved, never overwritten in place
    assert updated2["sar_report_history"] == [sar1, sar2]
    # original snapshot untouched (update_case_memory never mutates in place)
    assert memory["sar_report"] is None


# ----------------------------------------------------------------------
# 7. Regression: real, checked-in Checkpoint 4-6 pipeline output is
# untouched by this checkpoint; CaseActionLayer still runs cleanly.
# ----------------------------------------------------------------------
@pytest.mark.skipif(not os.path.isdir(EVIDENCE_DIR), reason="pipeline_output/evidence not present - run run_pipeline.py first")
def test_real_pipeline_output_unaffected_by_checkpoint7_wiring():
    files = sorted(glob.glob(os.path.join(EVIDENCE_DIR, "*.json")))
    assert files, "expected at least one persisted evidence file"
    checked = 0
    sar_none_count = 0
    for path in files:
        with open(path) as f:
            evidence = json.load(f)
        case = {"case_id": evidence["case_id"], "account_id": evidence["account_id"],
                "primary_trigger": evidence["typology"], "status": "open"}
        layer = CaseActionLayer(case, evidence, case_alerts=[])
        # Checkpoint 4/5/6 shapes still present and untouched.
        assert layer.memory["authority_decision"] == evidence.get("authority")
        assert layer.memory["regulatory_findings"] == evidence.get("regulatory_findings")
        assert layer.state in cs.CASE_STATES
        # Checkpoint 7: every case gets a sar_report key (None unless a
        # FILE_SAR action was authorized-and-executed, which never
        # happens automatically here - only a real human review/action
        # sequence, not run on every case, can produce one).
        assert "sar_report" in layer.memory
        assert "sar_report_history" in layer.memory
        if layer.memory["sar_report"] is None:
            sar_none_count += 1
        checked += 1
    assert checked == len(files)
    # documented, known dataset property - see module docstring.
    assert sar_none_count == checked