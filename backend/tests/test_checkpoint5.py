"""
tests/test_checkpoint5.py
============================
CHECKPOINT 5 test suite: Regulatory Compliance Rule Engine, Regulatory
RAG, Investigation Auditor, Case Completeness Score, and the low-
completeness re-gather loop. Mirrors test_authority_policy.py's style -
hand-built fixtures (via a FakeStore, exercising the real
evidence_model.py checkers) for unit-level coverage of each stage in
isolation, plus integration checks against the real generated dataset.
"""
import os

import pytest

from data_store import DataStore
from detection_layer import run_detection_pipeline, bundle_alerts_into_cases
from network_layer import generate_network_evidence
from evidence_model import build_evidence_items, compute_completeness
from authority_policy import assess_authority, AUTHORITY_POLICY
from regulatory_corpus import REGULATORY_CORPUS
from regulatory_rag import retrieve_regulatory_context
from regulatory_rules import evaluate_compliance_rules
from investigation_auditor import audit_investigation
from case_completeness import compute_case_completeness, DEFAULT_THRESHOLD
from regather_loop import run_regather_loop, targeted_regather_requests, DEFAULT_MAX_ITERATIONS

MOCK_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mock_data")
STRUCTURAL_GAP_REASONS = AUTHORITY_POLICY.get("structural_gap_reasons", ())


# ----------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------
class FakeStore:
    def __init__(self, accounts_by_id=None, bene_by_account=None, devices_by_account=None, geo_by_account=None):
        self.accounts_by_id = accounts_by_id or {}
        self.bene_by_account = bene_by_account or {}
        self.devices_by_account = devices_by_account or {}
        self.geo_by_account = geo_by_account or {}


def _clean_smurfing_case_and_net():
    """Full, high-quality smurfing evidence with two independent
    structuring-relevant patterns (real corroboration, not a single
    anomaly) - the "confirmed_concern" baseline fixture."""
    case = {"case_id": "CASE-T5-SMURF-CLEAN", "account_id": "ACC1", "primary_trigger": "smurfing"}
    nodes = [{"data": {"id": f"N{i}"}} for i in range(4)]
    edges = [{"data": {"id": f"T{i}", "amount": 500.0, "depth": 1}} for i in range(3)]
    net = {
        "case_id": case["case_id"], "account_id": "ACC1", "typology": "smurfing",
        "patterns": [{"type": "many_to_one"}, {"type": "rapid_onward_transfer"}],
        "evidence": {"nodes": nodes, "edges": edges},
    }
    store = FakeStore(
        bene_by_account={"ACC1": [{"beneficiary_id": "BEN1", "is_verified": True}]},
        devices_by_account={"ACC1": [{"device_id": "DEV1", "is_trusted_device": True}]},
        geo_by_account={"ACC1": [{"geo_event_id": "GEO1"}, {"geo_event_id": "GEO2"}]},
        accounts_by_id={"ACC1": {"account_id": "ACC1", "avg_monthly_txn_amount": 1000.0, "registered_country": "India"}},
    )
    items = build_evidence_items(store, case, net)
    completeness = compute_completeness(items)
    return case, net, store, items, completeness


def _sparse_smurfing_case_and_net():
    """A smurfing case with a genuine, case-specific CRITICAL evidence
    gap (transaction_chain missing - no edges at all) that is NOT one of
    evidence_model.py's structural/dataset-wide reasons."""
    case = {"case_id": "CASE-T5-SMURF-SPARSE", "account_id": "ACC2", "primary_trigger": "smurfing"}
    net = {
        "case_id": case["case_id"], "account_id": "ACC2", "typology": "smurfing",
        "patterns": [],
        "evidence": {"nodes": [{"data": {"id": "ACC2"}}], "edges": []},
    }
    store = FakeStore(accounts_by_id={"ACC2": {"account_id": "ACC2", "avg_monthly_txn_amount": 1000.0, "registered_country": "India"}})
    items = build_evidence_items(store, case, net)
    completeness = compute_completeness(items)
    return case, net, store, items, completeness


def _account_swap_case_and_net(high_value=True, sim_change=True, impossible_travel=True,
                                account_id="ACC3", registered_country="India",
                                amount_high=1_500_000.0, amount_low=50.0):
    """`amount_high`/`amount_low` (NEW this checkpoint) default to values
    that straddle India's real PMLA Rule 3 CTR threshold (INR 10,00,000 -
    see regulatory_rules.CTR_THRESHOLDS_BY_JURISDICTION["IN"]), since the
    fixture's own registered_country now defaults to "India" and
    _evaluate_ctr is jurisdiction-aware - a bare INR 25,000 (the pre-
    jurisdiction fixture's old amount, calibrated against the old
    hardcoded $10,000-shaped assumption) would no longer cross the real
    India threshold and would silently make test_ctr_rule_confirms_
    concern_for_transaction_above_threshold assert something that isn't
    true anymore. Callers testing a different jurisdiction should pass
    `registered_country` and jurisdiction-appropriate amounts explicitly
    rather than relying on these defaults."""
    case = {"case_id": "CASE-T5-ATO", "account_id": account_id, "primary_trigger": "account_swap"}
    patterns = []
    if sim_change:
        patterns.append("sim_change_before_transaction")
    if impossible_travel:
        patterns.append("rapid_geographic_change")
    if high_value:
        patterns.append("high_value_transaction")
    events = [
        {"event_id": "TXN-A", "event_type": "transaction", "direction": "out",
         "amount": amount_high if high_value else amount_low, "timestamp": "2026-01-01T00:00:00"},
    ]
    net = {
        "case_id": case["case_id"], "account_id": account_id, "typology": "account_swap",
        "patterns": patterns,
        "evidence": {
            "events": events,
            "behavioral_summary": {"amount_deviation_ratio": 6.0},
        },
    }
    store = FakeStore(
        bene_by_account={account_id: [{"beneficiary_id": "BEN3", "is_verified": False, "is_first_time_beneficiary": True}]},
        devices_by_account={account_id: [{"device_id": "DEV3", "is_trusted_device": False}]},
        geo_by_account={account_id: [{"geo_event_id": "GEO3"}]},
        accounts_by_id={account_id: {"account_id": account_id, "avg_monthly_txn_amount": 1000.0,
                                      "registered_country": registered_country}},
    )
    if sim_change:
        # evidence_model._check_sim_change_evidence looks for a
        # "sim_change" event_type entry in evidence["events"].
        net["evidence"]["events"].append({"event_id": "DEV3-SIM", "event_type": "sim_change"})
    items = build_evidence_items(store, case, net)
    completeness = compute_completeness(items)
    return case, net, store, items, completeness


# ----------------------------------------------------------------------
# 1-2. Complete / incomplete case completeness
# ----------------------------------------------------------------------
def test_complete_case_scores_above_threshold_and_status_complete():
    case, net, store, items, completeness = _clean_smurfing_case_and_net()
    reg = evaluate_compliance_rules(case, items, completeness, net=net, store=store)
    audit = audit_investigation(case, items, completeness, net=net, regulatory_findings=reg,
                                 structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    cc = compute_case_completeness(case, items, completeness, regulatory_findings=reg, auditor_result=audit,
                                    structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    assert cc["score"] >= DEFAULT_THRESHOLD
    assert cc["status"] == "complete"
    assert cc["next_step"] == "continue"


def test_incomplete_case_scores_below_threshold():
    case, net, store, items, completeness = _sparse_smurfing_case_and_net()
    reg = evaluate_compliance_rules(case, items, completeness, net=net, store=store)
    audit = audit_investigation(case, items, completeness, net=net, regulatory_findings=reg,
                                 structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    cc = compute_case_completeness(case, items, completeness, regulatory_findings=reg, auditor_result=audit,
                                    structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    assert cc["status"] == "incomplete"
    assert cc["next_step"] == "re_gather"


# ----------------------------------------------------------------------
# 3-4. Critical vs non-critical missing evidence
# ----------------------------------------------------------------------
def test_critical_missing_evidence_forces_incomplete():
    case, net, store, items, completeness = _sparse_smurfing_case_and_net()
    critical = [m for m in completeness["missing"] if m["severity"] == "critical"]
    assert critical, "fixture must actually have a case-specific critical gap"
    reg = evaluate_compliance_rules(case, items, completeness, net=net, store=store)
    audit = audit_investigation(case, items, completeness, net=net, regulatory_findings=reg,
                                 structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    cc = compute_case_completeness(case, items, completeness, regulatory_findings=reg, auditor_result=audit,
                                    structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    assert cc["critical_missing_evidence"], "critical, non-structural gap must surface in critical_missing_evidence"
    assert cc["status"] == "incomplete"


def test_non_critical_missing_evidence_reduces_score_but_can_still_be_complete():
    # account_swap fixture missing only high_value_transaction (critical by
    # weight in evidence_model.py's table) removed to leave a MODERATE gap
    # instead: geo_information (weight 0.15 -> actually also critical in
    # this typology's table). Use money_mule instead, which has a
    # deliberately lower-weight (moderate) evidence type to leave missing.
    case = {"case_id": "CASE-T5-MULE-MODERATE", "account_id": "ACC4", "primary_trigger": "money_mule"}
    net = {
        "case_id": case["case_id"], "account_id": "ACC4", "typology": "money_mule",
        "patterns": [],
        "evidence": {
            "transactions": [
                {"transaction_id": "T1", "amount": 100.0, "direction": "in"},
                {"transaction_id": "T2", "amount": 100.0, "direction": "in"},
                {"transaction_id": "T3", "amount": 100.0, "direction": "in"},
                {"transaction_id": "T4", "amount": 90.0, "direction": "out"},
            ],
            "summary": {"median_inbound_to_outbound_minutes": 10, "total_inbound": 300.0, "total_outbound": 90.0},
        },
    }
    store = FakeStore(
        bene_by_account={"ACC4": [{"beneficiary_id": "BEN4", "is_verified": True}]},
        # geo_information (weight 0.075, moderate) deliberately left empty -
        # the only missing item.
        devices_by_account={"ACC4": [{"device_id": "DEV4", "is_trusted_device": True}]},
        accounts_by_id={"ACC4": {"account_id": "ACC4", "avg_monthly_txn_amount": 1000.0, "registered_country": "India"}},
    )
    items = build_evidence_items(store, case, net)
    completeness = compute_completeness(items)
    missing_severities = {m["severity"] for m in completeness["missing"]}
    assert missing_severities == {"moderate"}, completeness["missing"]

    reg = evaluate_compliance_rules(case, items, completeness, net=net, store=store)
    audit = audit_investigation(case, items, completeness, net=net, regulatory_findings=reg,
                                 structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    cc = compute_case_completeness(case, items, completeness, regulatory_findings=reg, auditor_result=audit,
                                    structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    assert cc["score"] < 100.0  # the gap is real and reflected
    assert not cc["critical_missing_evidence"]  # but it never escalated to critical
    assert cc["status"] == "complete"  # policy: moderate-only gaps don't block completeness


# ----------------------------------------------------------------------
# 5. Structural/dataset-wide gaps must not invalidate every case
# ----------------------------------------------------------------------
def test_structural_dataset_wide_gap_does_not_force_incomplete():
    """source_of_funds is evidence_model.py's own permanently-unavailable,
    dataset-wide gap (every smurfing case has it missing). A case with
    EVERY OTHER requirement satisfied must still be able to reach
    "complete" - the structural gap alone must not cap it."""
    case, net, store, items, completeness = _clean_smurfing_case_and_net()
    sof = [i for i in items if i["evidence_type"] == "source_of_funds"]
    assert sof and not sof[0]["available"]
    assert sof[0]["missing_reason"]["reason"] in STRUCTURAL_GAP_REASONS

    reg = evaluate_compliance_rules(case, items, completeness, net=net, store=store)
    audit = audit_investigation(case, items, completeness, net=net, regulatory_findings=reg,
                                 structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    cc = compute_case_completeness(case, items, completeness, regulatory_findings=reg, auditor_result=audit,
                                    structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    assert cc["status"] == "complete", cc
    assert "structural_dataset_wide_gap_excluded_from_score" in cc["reasons"]
    assert not any(m["evidence_type"] == "source_of_funds" for m in cc["critical_missing_evidence"])


def test_real_dataset_has_both_structural_gap_and_complete_cases():
    """Integration proof on the real generated dataset: source_of_funds is
    missing on effectively every smurfing/reverse_smurfing/money_mule
    case, yet at least one such case still reaches "complete"."""
    store = DataStore(MOCK_DATA_DIR)
    alerts = run_detection_pipeline(store)
    cases = bundle_alerts_into_cases(alerts)
    saw_complete = False
    for case in cases:
        net = generate_network_evidence(store, case)
        items = build_evidence_items(store, case, net)
        completeness = compute_completeness(items)
        if not items:
            continue
        reg = evaluate_compliance_rules(case, items, completeness, net=net,
                                         account=store.accounts_by_id.get(case["account_id"]), store=store)
        audit = audit_investigation(case, items, completeness, net=net, regulatory_findings=reg,
                                     structural_gap_reasons=STRUCTURAL_GAP_REASONS)
        cc = compute_case_completeness(case, items, completeness, regulatory_findings=reg, auditor_result=audit,
                                        structural_gap_reasons=STRUCTURAL_GAP_REASONS)
        if cc["status"] == "complete":
            saw_complete = True
    assert saw_complete, "expected at least one real case to reach status=complete despite the structural gap"


# ----------------------------------------------------------------------
# 6. Regulatory rule correctly identifies an applicable concern
# ----------------------------------------------------------------------
def test_ctr_rule_confirms_concern_for_transaction_above_threshold():
    """India-jurisdiction case (fixture default): a transaction above the
    real PMLA Rule 3 threshold (INR 10,00,000) must confirm the concern -
    NOT the old hardcoded $10,000-shaped assumption."""
    case, net, store, items, completeness = _account_swap_case_and_net(high_value=True)
    reg = evaluate_compliance_rules(case, items, completeness, net=net, store=store)
    ctr = next(r for r in reg if r["rule_id"] == "RULE-CTR-001")
    assert ctr["status"] == "confirmed_concern"
    assert ctr["supporting_evidence"]
    assert ctr["supporting_evidence"][0]["observed_value"] >= 1_000_000.0
    assert ctr["supporting_evidence"][0]["currency"] == "INR"


def test_ctr_rule_no_breach_below_threshold():
    case, net, store, items, completeness = _account_swap_case_and_net(high_value=False)
    reg = evaluate_compliance_rules(case, items, completeness, net=net, store=store)
    ctr = next(r for r in reg if r["rule_id"] == "RULE-CTR-001")
    assert ctr["status"] == "no_identified_breach"


def test_ctr_rule_us_jurisdiction_uses_usd_threshold_not_inr():
    """A US-registered account's CTR evaluation must use the real BSA/31
    CFR 1010.311 $10,000 threshold - never the India INR 10,00,000 figure,
    and never compare a USD amount against an INR-shaped number."""
    case, net, store, items, completeness = _account_swap_case_and_net(
        high_value=True, registered_country="United States",
        amount_high=15_000.0,  # above the US $10,000 threshold, far below India's INR figure
    )
    net["evidence"]["events"][0]["currency"] = "USD"
    reg = evaluate_compliance_rules(case, items, completeness, net=net, store=store)
    ctr = next(r for r in reg if r["rule_id"] == "RULE-CTR-001")
    assert ctr["status"] == "confirmed_concern"
    assert ctr["supporting_evidence"][0]["currency"] == "USD"
    assert ctr["supporting_evidence"][0]["observed_value"] >= 10_000.0
    assert ctr["supporting_evidence"][0]["observed_value"] < 1_000_000.0  # sanity: not the INR-scale figure


def test_ctr_rule_unknown_jurisdiction_is_insufficient_evidence_not_a_guess():
    """No recognized registered_country -> the rule must not guess which
    threshold/currency applies; it must report insufficient_evidence."""
    case, net, store, items, completeness = _account_swap_case_and_net(
        high_value=True, registered_country="Narnia", amount_high=5_000_000.0,
    )
    reg = evaluate_compliance_rules(case, items, completeness, net=net, store=store)
    ctr = next(r for r in reg if r["rule_id"] == "RULE-CTR-001")
    assert ctr["status"] == "insufficient_evidence"


def test_ctr_rule_currency_mismatch_never_silently_converted():
    """An India-jurisdiction case whose only gathered transaction amount
    is in USD must not be compared against the INR threshold by silent
    conversion - it must be insufficient_evidence."""
    case, net, store, items, completeness = _account_swap_case_and_net(
        high_value=True, registered_country="India", amount_high=1_500_000.0,
    )
    net["evidence"]["events"][0]["currency"] = "USD"
    reg = evaluate_compliance_rules(case, items, completeness, net=net, store=store)
    ctr = next(r for r in reg if r["rule_id"] == "RULE-CTR-001")
    assert ctr["status"] == "insufficient_evidence"


def test_structuring_rule_requires_two_corroborating_patterns_for_confirmed():
    case, net, store, items, completeness = _clean_smurfing_case_and_net()
    reg = evaluate_compliance_rules(case, items, completeness, net=net, store=store)
    struct = next(r for r in reg if r["rule_id"] == "RULE-STRUCT-001")
    assert struct["status"] == "confirmed_concern"
    assert len(struct["supporting_evidence"]) >= 2


def test_structuring_rule_single_pattern_is_only_potentially_applicable():
    case, net, store, items, completeness = _clean_smurfing_case_and_net()
    net["patterns"] = [{"type": "many_to_one"}]  # only one signal now
    reg = evaluate_compliance_rules(case, items, completeness, net=net, store=store)
    struct = next(r for r in reg if r["rule_id"] == "RULE-STRUCT-001")
    assert struct["status"] == "potentially_applicable"


def test_regulatory_rule_never_claims_breach_from_a_bare_anomaly():
    """A single structuring-relevant anomaly, alone, must never be
    reported as "confirmed_concern" - only "potentially_applicable"."""
    case, net, store, items, completeness = _clean_smurfing_case_and_net()
    net["patterns"] = [{"type": "amount_fragmentation"}]
    reg = evaluate_compliance_rules(case, items, completeness, net=net, store=store)
    struct = next(r for r in reg if r["rule_id"] == "RULE-STRUCT-001")
    assert struct["status"] != "confirmed_concern"


# ----------------------------------------------------------------------
# 7-8. Unsupported regulatory claims / provenance
# ----------------------------------------------------------------------
def test_unsupported_regulatory_claim_is_flagged_by_auditor():
    case, net, store, items, completeness = _clean_smurfing_case_and_net()
    fabricated = [{
        "rule_id": "RULE-FAKE-001", "rule_name": "fake", "typology": "smurfing",
        "status": "confirmed_concern", "confidence": 0.9,
        "supporting_evidence": [],  # <- the defect this test targets
        "rationale": "no real evidence backs this", "regulatory_context": [], "case_id": case["case_id"],
    }]
    audit = audit_investigation(case, items, completeness, net=net, regulatory_findings=fabricated,
                                 structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    types = {i["issue_type"] for i in audit["issues"]}
    assert "unsupported_regulatory_claim" in types


def test_regulatory_context_carries_real_provenance():
    case, net, store, items, completeness = _clean_smurfing_case_and_net()
    reg = evaluate_compliance_rules(case, items, completeness, net=net, store=store)
    ctr = next(r for r in reg if r["rule_id"] == "RULE-CTR-001")
    assert ctr["regulatory_context"], "expected at least one retrieved corpus entry"
    for entry in ctr["regulatory_context"]:
        assert entry["source_type"] == "bundled_reference_corpus"
        assert entry["source_id"] in {e["source_id"] for e in REGULATORY_CORPUS}
        assert entry["citation"]


def test_rag_never_invents_a_source_outside_the_corpus():
    results = retrieve_regulatory_context({"typology": "smurfing", "signal_terms": {"structuring", "amount_fragmentation"}})
    valid_ids = {e["source_id"] for e in REGULATORY_CORPUS}
    assert results, "expected at least one real match"
    assert all(r["source_id"] in valid_ids for r in results)


def test_rag_returns_nothing_for_irrelevant_context():
    results = retrieve_regulatory_context({"typology": None, "signal_terms": {"totally_unrelated_made_up_term"}})
    assert results == []


# ----------------------------------------------------------------------
# 9. Auditor detects unsupported conclusion
# ----------------------------------------------------------------------
def test_auditor_flags_junior_authorization_conflicting_with_confirmed_regulatory_concern():
    case, net, store, items, completeness = _clean_smurfing_case_and_net()
    reg = evaluate_compliance_rules(case, items, completeness, net=net, store=store)
    assert any(r["status"] == "confirmed_concern" for r in reg)
    fake_authority = {"case_id": case["case_id"], "can_resolve": True, "decision": "junior_authorized"}
    audit = audit_investigation(case, items, completeness, net=net, regulatory_findings=reg,
                                 authority_decision=fake_authority, structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    types = {i["issue_type"] for i in audit["issues"]}
    assert "unsupported_conclusion" in types


def test_auditor_never_repeats_authority_decision_verbatim():
    """The auditor's issue list must never simply contain the authority
    decision's own `decision` string as an issue_type - it only reasons
    about it, never re-labels it."""
    case, net, store, items, completeness = _clean_smurfing_case_and_net()
    reg = evaluate_compliance_rules(case, items, completeness, net=net, store=store)
    fake_authority = {"case_id": case["case_id"], "can_resolve": False, "decision": "senior_review_required"}
    audit = audit_investigation(case, items, completeness, net=net, regulatory_findings=reg,
                                 authority_decision=fake_authority, structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    issue_types = {i["issue_type"] for i in audit["issues"]}
    assert "senior_review_required" not in issue_types
    assert "junior_authorized" not in issue_types


# ----------------------------------------------------------------------
# 10. Auditor detects contradictory evidence
# ----------------------------------------------------------------------
@pytest.mark.parametrize("state", ["unresolved", "material_conflict"])
def test_auditor_flags_unresolved_contradiction(state):
    case, net, store, items, completeness = _clean_smurfing_case_and_net()
    audit = audit_investigation(case, items, completeness, net=net, contradiction_state=state,
                                 structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    types = {i["issue_type"] for i in audit["issues"]}
    assert "contradictory_evidence" in types


def test_auditor_does_not_flag_resolved_or_not_evaluated_contradiction():
    case, net, store, items, completeness = _clean_smurfing_case_and_net()
    for state in (None, "not_evaluated", "no_contradiction", "resolved"):
        audit = audit_investigation(case, items, completeness, net=net, contradiction_state=state,
                                     structural_gap_reasons=STRUCTURAL_GAP_REASONS)
        types = {i["issue_type"] for i in audit["issues"]}
        assert "contradictory_evidence" not in types


# ----------------------------------------------------------------------
# 11-14. Re-gather loop
# ----------------------------------------------------------------------
def test_targeted_regather_requests_exclude_structural_gaps():
    case, net, store, items, completeness = _clean_smurfing_case_and_net()
    requests = targeted_regather_requests(completeness, STRUCTURAL_GAP_REASONS)
    assert not any(r["reason"] in STRUCTURAL_GAP_REASONS for r in requests)


def test_targeted_regather_requests_include_case_specific_gaps():
    case, net, store, items, completeness = _sparse_smurfing_case_and_net()
    requests = targeted_regather_requests(completeness, STRUCTURAL_GAP_REASONS)
    assert any(r["evidence_type"] == "transaction_chain" for r in requests)


def test_regather_loop_no_op_when_nothing_missing():
    case, net, store, items, completeness = _clean_smurfing_case_and_net()
    # force zero case-specific misses by stubbing completeness["missing"]
    clean_completeness = dict(completeness, missing=[
        m for m in completeness["missing"] if m["reason"] in STRUCTURAL_GAP_REASONS
    ])
    result = run_regather_loop(store, case, items, clean_completeness, structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    assert result["final_disposition"] == "no_regather_needed"
    assert result["iterations"] == []


def test_regather_loop_respects_max_iterations_cap():
    store = DataStore(MOCK_DATA_DIR)
    alerts = run_detection_pipeline(store)
    cases = bundle_alerts_into_cases(alerts)
    # find a real case with a genuine, persistent case-specific gap
    target = None
    for case in cases:
        net = generate_network_evidence(store, case)
        items = build_evidence_items(store, case, net)
        completeness = compute_completeness(items)
        if targeted_regather_requests(completeness, STRUCTURAL_GAP_REASONS):
            target = (case, items, completeness)
    assert target is not None, "expected at least one real case with a case-specific gap"
    case, items, completeness = target
    for cap in (1, 2):
        result = run_regather_loop(store, case, items, completeness, max_iterations=cap,
                                    structural_gap_reasons=STRUCTURAL_GAP_REASONS)
        assert len(result["iterations"]) <= cap
        assert result["max_iterations"] == cap


def test_regather_loop_records_before_after_and_disposition():
    store = DataStore(MOCK_DATA_DIR)
    alerts = run_detection_pipeline(store)
    cases = bundle_alerts_into_cases(alerts)
    for case in cases:
        net = generate_network_evidence(store, case)
        items = build_evidence_items(store, case, net)
        completeness = compute_completeness(items)
        requests = targeted_regather_requests(completeness, STRUCTURAL_GAP_REASONS)
        if not requests:
            continue
        result = run_regather_loop(store, case, items, completeness, structural_gap_reasons=STRUCTURAL_GAP_REASONS)
        assert result["final_disposition"] != "no_regather_needed"
        for it in result["iterations"]:
            assert set(it) >= {"iteration", "requested_evidence", "evidence_returned",
                                "completeness_before", "completeness_after"}
        assert result["final_disposition"] in (
            "resolved", "unresolved_after_max_iterations", "unresolved_no_further_evidence_available",
        )
        return
    pytest.skip("no real case with a case-specific gap found in this dataset run")


def test_failed_regather_produces_explicit_unresolved_state_never_fabricates():
    """A case whose missing evidence genuinely cannot be produced by
    widening the window (a truly empty account with no more data to find)
    must terminate honestly, not claim resolution."""
    case = {"case_id": "CASE-T5-EMPTY", "account_id": "ACC-EMPTY", "primary_trigger": "smurfing",
            "created_at": "2026-01-01T00:00:00"}
    net = {
        "case_id": case["case_id"], "account_id": "ACC-EMPTY", "typology": "smurfing",
        "patterns": [], "evidence": {"nodes": [], "edges": []},
    }
    store = DataStore(MOCK_DATA_DIR)  # real store, but this account has no real transactions
    items = build_evidence_items(store, case, net)
    completeness = compute_completeness(items)
    result = run_regather_loop(store, case, items, completeness, structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    assert result["final_disposition"] in ("unresolved_after_max_iterations", "unresolved_no_further_evidence_available")
    assert result["final_completeness"]["weighted_score"] is None or result["final_completeness"]["weighted_score"] < 100.0


# ----------------------------------------------------------------------
# 15. High completeness stops the loop
# ----------------------------------------------------------------------
def test_high_completeness_case_never_needs_regather():
    case, net, store, items, completeness = _clean_smurfing_case_and_net()
    reg = evaluate_compliance_rules(case, items, completeness, net=net, store=store)
    audit = audit_investigation(case, items, completeness, net=net, regulatory_findings=reg,
                                 structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    cc = compute_case_completeness(case, items, completeness, regulatory_findings=reg, auditor_result=audit,
                                    structural_gap_reasons=STRUCTURAL_GAP_REASONS)
    assert cc["status"] == "complete"
    assert cc["next_step"] == "continue"


# ----------------------------------------------------------------------
# 16-18. Regression: Checkpoint 4 authority + ground-truth isolation +
# full pipeline structural validity. (The full suite / isolation tests
# are exercised by running `pytest tests/`; this file adds one direct
# end-to-end structural check that the new fields integrate cleanly with
# the real live pipeline's per-case loop, mirroring run_pipeline.py.)
# ----------------------------------------------------------------------
def test_end_to_end_real_pipeline_produces_well_formed_checkpoint5_output():
    store = DataStore(MOCK_DATA_DIR)
    alerts = run_detection_pipeline(store)
    cases = bundle_alerts_into_cases(alerts)
    assert cases
    checked = 0
    for case in cases:
        net = generate_network_evidence(store, case)
        items = build_evidence_items(store, case, net)
        completeness = compute_completeness(items)
        account = store.accounts_by_id.get(case["account_id"])
        authority = assess_authority(case, items, completeness, net=net, account=account)
        reg = evaluate_compliance_rules(case, items, completeness, net=net, account=account, store=store)
        audit = audit_investigation(case, items, completeness, net=net, account=account,
                                     regulatory_findings=reg, authority_decision=authority,
                                     structural_gap_reasons=STRUCTURAL_GAP_REASONS)
        cc = compute_case_completeness(case, items, completeness, regulatory_findings=reg, auditor_result=audit,
                                        structural_gap_reasons=STRUCTURAL_GAP_REASONS)
        assert cc["status"] in ("complete", "incomplete")
        assert cc["next_step"] in ("continue", "re_gather")
        assert set(cc) >= {
            "score", "threshold", "status", "missing_evidence", "critical_missing_evidence",
            "satisfied_requirements", "failed_requirements", "reasons", "next_step",
        }
        for r in reg:
            assert r["status"] in ("confirmed_concern", "potentially_applicable",
                                    "no_identified_breach", "insufficient_evidence")
        checked += 1
    assert checked == len(cases)