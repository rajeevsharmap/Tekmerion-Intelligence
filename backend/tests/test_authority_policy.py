"""
tests/test_authority_policy.py
=================================
CHECKPOINT 4 test suite: investigator authority / escalation policy engine
(authority_policy.py). Mirrors test_evidence_model.py's style - hand-built
fixtures for unit-level coverage of each policy dimension in isolation,
plus a couple of integration checks against the real pipeline.
"""
import os

import pytest

import authority_policy as ap
from data_store import DataStore
from detection_layer import run_detection_pipeline, bundle_alerts_into_cases
from network_layer import generate_network_evidence
from evidence_model import build_evidence_items, compute_completeness

MOCK_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mock_data")


# ----------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------
def _clean_smurfing_case_and_net(num_nodes=3, num_edges=2, max_depth=1, edge_amount=500.0):
    """A smurfing case with full, high-quality evidence and a small
    (non-complex) graph - the "everything is fine" baseline fixture."""
    case = {"case_id": "CASE-TEST-A1", "account_id": "ACC1", "primary_trigger": "smurfing"}
    nodes = [{"data": {"id": f"N{i}"}} for i in range(num_nodes)]
    edges = [{"data": {"id": f"T{i}", "amount": edge_amount, "depth": max_depth}} for i in range(num_edges)]
    net = {
        "typology": "smurfing",
        "account_id": "ACC1",
        "patterns": [{"type": "rapid_onward_transfer"}],
        "evidence": {"nodes": nodes, "edges": edges},
    }
    return case, net


class FakeStore:
    def __init__(self, accounts_by_id=None, bene_by_account=None, devices_by_account=None, geo_by_account=None):
        self.accounts_by_id = accounts_by_id or {}
        self.bene_by_account = bene_by_account or {}
        self.devices_by_account = devices_by_account or {}
        self.geo_by_account = geo_by_account or {}


def _full_evidence_smurfing():
    """evidence_items where every required smurfing evidence type is
    available and high-quality, via the real evidence_model.py checkers -
    so completeness/confidence legitimately land above the junior
    threshold (never hand-faked)."""
    store = FakeStore(
        bene_by_account={"ACC1": [{"beneficiary_id": "BEN1", "is_verified": True}]},
        devices_by_account={"ACC1": [{"device_id": "DEV1", "is_trusted_device": True}]},
        geo_by_account={"ACC1": [{"geo_event_id": "GEO1"}, {"geo_event_id": "GEO2"}]},
    )
    case, net = _clean_smurfing_case_and_net()
    net["evidence"]["nodes"] = [{"data": {"id": f"N{i}"}} for i in range(4)]
    net["patterns"] = [{"type": "rapid_onward_transfer"}]
    items = build_evidence_items(store, case, net)
    # source_of_funds is never modeled in this dataset (evidence_model.py's
    # own documented, honest gap) - real completeness for smurfing can only
    # ever reach 85% (1 - 0.15 weight), which is still comfortably above the
    # policy's 70% junior threshold.
    completeness = compute_completeness(items)
    return case, net, items, completeness


@pytest.fixture
def real_store():
    assert os.path.isdir(MOCK_DATA_DIR), "mock_data/ must be generated before running tests (see README)"
    return DataStore(MOCK_DATA_DIR)


@pytest.fixture
def real_cases(real_store):
    alerts = run_detection_pipeline(real_store)
    return real_store, alerts, bundle_alerts_into_cases(alerts)


# ----------------------------------------------------------------------
# 1. Config sanity
# ----------------------------------------------------------------------
def test_all_four_typologies_have_a_configured_risk_level():
    for typology in ("smurfing", "reverse_smurfing", "money_mule", "account_swap"):
        assert typology in ap.AUTHORITY_POLICY["typology_risk"]


def test_policy_thresholds_are_data_not_scattered_constants():
    """The policy dict is the single source of truth for every threshold
    used by assess_authority - spot-check the keys the rest of this suite
    relies on actually existing, so a future edit that removes one fails
    loudly here rather than via a confusing KeyError deep in the module."""
    p = ap.AUTHORITY_POLICY
    assert "minimum_completeness" in p["junior"]
    assert "minimum_confidence" in p["junior"]
    assert set(p["senior_triggers"]) == {
        "critical_missing_evidence", "high_risk_typology", "high_value_transaction",
        "complex_network", "unresolved_contradiction",
    }


# ----------------------------------------------------------------------
# 2. TEST 1 - high completeness + sufficient confidence + low-risk case -> junior
# ----------------------------------------------------------------------
def test_clean_low_risk_case_is_junior():
    case, net, items, completeness = _full_evidence_smurfing()
    result = ap.assess_authority(case, items, completeness, net=net, account={"avg_monthly_txn_amount": 1000.0})
    assert result["authority_tier"] == "junior"
    assert result["can_resolve"] is True
    assert result["decision"] == "junior_authorized"
    assert result["reasons"] == ["no_critical_gap", "sufficient_evidence", "sufficient_confidence"]


# ----------------------------------------------------------------------
# 3. TEST 2 - critical evidence missing -> senior
# ----------------------------------------------------------------------
def test_critical_evidence_missing_forces_senior():
    case = {"case_id": "CASE-TEST-A2", "account_id": "ACC1", "primary_trigger": "smurfing"}
    net = {"typology": "smurfing", "account_id": "ACC1", "patterns": [], "evidence": {"nodes": [], "edges": []}}
    store = FakeStore()  # nothing available anywhere -> everything missing, several critical
    items = build_evidence_items(store, case, net)
    completeness = compute_completeness(items)
    result = ap.assess_authority(case, items, completeness, net=net)
    assert result["authority_tier"] == "senior"
    assert "critical_evidence_missing" in result["reasons"]
    assert "critical_evidence_gap" in result["risk_factors"]


# ----------------------------------------------------------------------
# 4. TEST 3 - high-risk typology -> senior according to policy
# ----------------------------------------------------------------------
def test_high_risk_typology_forces_senior_even_with_full_evidence():
    store = FakeStore(
        bene_by_account={"ACC1": [{"beneficiary_id": "BEN1", "is_verified": True}]},
        devices_by_account={"ACC1": [{"device_id": "DEV1", "is_trusted_device": True}]},
        geo_by_account={"ACC1": [{"geo_event_id": "GEO1"}]},
    )
    case = {"case_id": "CASE-TEST-A3", "account_id": "ACC1", "primary_trigger": "money_mule"}
    net = {
        "typology": "money_mule", "account_id": "ACC1",
        "patterns": ["rapid_fund_pass_through"],
        "evidence": {
            "transactions": [{"transaction_id": "T1", "direction": "in", "amount": 100.0},
                              {"transaction_id": "T2", "direction": "in", "amount": 100.0},
                              {"transaction_id": "T3", "direction": "in", "amount": 100.0},
                              {"transaction_id": "T4", "direction": "out", "amount": 250.0}],
            "summary": {"median_inbound_to_outbound_minutes": 12.0, "total_inbound": 300.0, "total_outbound": 250.0},
        },
    }
    items = build_evidence_items(store, case, net)
    completeness = compute_completeness(items)
    result = ap.assess_authority(case, items, completeness, net=net, account={"avg_monthly_txn_amount": 10000.0})
    assert result["authority_tier"] == "senior"
    assert "high_risk_typology" in result["reasons"]
    assert result["policy_inputs"]["typology_risk"] == "high"


# ----------------------------------------------------------------------
# 5. TEST 4 - high-value transaction -> senior according to policy
# ----------------------------------------------------------------------
def test_high_value_transaction_forces_senior():
    case, net, items, completeness = _full_evidence_smurfing()
    net["evidence"]["edges"] = [{"data": {"id": "T1", "amount": 50000.0, "depth": 1}}]
    result = ap.assess_authority(case, items, completeness, net=net, account={"avg_monthly_txn_amount": 1000.0})
    assert result["authority_tier"] == "senior"
    assert "high_value_transaction" in result["reasons"]


def test_high_value_transaction_via_baseline_multiplier():
    """Below the absolute threshold but well above 5x the account's own
    baseline should still trigger - both paths in _is_high_value_transaction
    are exercised, not just the absolute one."""
    case, net, items, completeness = _full_evidence_smurfing()
    net["evidence"]["edges"] = [{"data": {"id": "T1", "amount": 600.0, "depth": 1}}]
    result = ap.assess_authority(case, items, completeness, net=net, account={"avg_monthly_txn_amount": 100.0})
    assert "high_value_transaction" in result["reasons"]


def test_account_swap_high_value_reuses_network_layer_pattern():
    """Section 5E: for account_swap, reuse network_layer.py's own
    already-computed high_value_transaction pattern rather than
    recomputing a second version of the same check."""
    case = {"case_id": "CASE-TEST-A4", "account_id": "ACC1", "primary_trigger": "account_swap"}
    net_hit = {"typology": "account_swap", "patterns": ["high_value_transaction"], "evidence": {}}
    net_miss = {"typology": "account_swap", "patterns": [], "evidence": {}}
    assert ap._is_high_value_transaction(net_hit, None, "account_swap", ap.AUTHORITY_POLICY["high_value_transaction"]) is True
    assert ap._is_high_value_transaction(net_miss, None, "account_swap", ap.AUTHORITY_POLICY["high_value_transaction"]) is False


# ----------------------------------------------------------------------
# 6. TEST 5 - complex network -> senior according to policy where applicable
# ----------------------------------------------------------------------
def test_complex_network_forces_senior_for_graph_typologies():
    case, net, items, completeness = _full_evidence_smurfing()
    net["evidence"]["nodes"] = [{"data": {"id": f"N{i}"}} for i in range(10)]  # over node_threshold
    result = ap.assess_authority(case, items, completeness, net=net, account={"avg_monthly_txn_amount": 1000.0})
    assert result["authority_tier"] == "senior"
    assert "complex_network" in result["reasons"]


def test_network_complexity_never_applied_to_money_mule_or_account_swap():
    """Section 5F: money_mule/account_swap are timelines, not graphs -
    _is_complex_network must always be False for them, no matter what's in
    `net["evidence"]`."""
    config = ap.AUTHORITY_POLICY["network_complexity"]
    fake_graph_shaped_net = {"evidence": {"nodes": list(range(50)), "edges": list(range(50))}}
    assert ap._is_complex_network(fake_graph_shaped_net, "money_mule", config) is False
    assert ap._is_complex_network(fake_graph_shaped_net, "account_swap", config) is False


# ----------------------------------------------------------------------
# 7. TEST 6 - unresolved / material contradiction -> senior
# ----------------------------------------------------------------------
@pytest.mark.parametrize("state", ["unresolved", "material_conflict"])
def test_unresolved_contradiction_forces_senior(state):
    case, net, items, completeness = _full_evidence_smurfing()
    result = ap.assess_authority(case, items, completeness, net=net,
                                  account={"avg_monthly_txn_amount": 1000.0}, contradiction_state=state)
    assert result["authority_tier"] == "senior"
    assert "unresolved_contradiction" in result["reasons"]


@pytest.mark.parametrize("state", [None, "not_evaluated", "no_contradiction", "resolved"])
def test_settled_or_unevaluated_contradiction_does_not_force_senior(state):
    """A contradiction state that was never evaluated (the honest default
    when run_pipeline.py has no contradiction-agent output to pass in yet -
    see module docstring) must NOT be treated identically to a real,
    unresolved conflict."""
    case, net, items, completeness = _full_evidence_smurfing()
    result = ap.assess_authority(case, items, completeness, net=net,
                                  account={"avg_monthly_txn_amount": 1000.0}, contradiction_state=state)
    assert "unresolved_contradiction" not in result["reasons"]


# ----------------------------------------------------------------------
# 8. TEST 7 - moderate missing evidence without other senior trigger -> policy-dependent, deterministic
# ----------------------------------------------------------------------
def test_moderate_only_missing_evidence_stays_junior_if_above_threshold():
    """geo_information (weight 0.10, moderate) missing, everything else
    available -> completeness still clears the 70% junior bar and no
    critical gap exists, so this should NOT force senior."""
    store = FakeStore(
        bene_by_account={"ACC1": [{"beneficiary_id": "BEN1", "is_verified": True}]},
        devices_by_account={"ACC1": [{"device_id": "DEV1", "is_trusted_device": True}]},
        geo_by_account={},  # only moderate-weight gap
    )
    case, net = _clean_smurfing_case_and_net(num_nodes=4)
    items = build_evidence_items(store, case, net)
    completeness = compute_completeness(items)
    geo_item = next(i for i in items if i["evidence_type"] == "geo_information")
    assert geo_item["missing_reason"]["severity"] == "moderate"  # sanity on the fixture itself
    result = ap.assess_authority(case, items, completeness, net=net, account={"avg_monthly_txn_amount": 1000.0})
    assert "critical_evidence_missing" not in result["reasons"]
    # deterministic either way - re-run and confirm identical result
    result2 = ap.assess_authority(case, items, completeness, net=net, account={"avg_monthly_txn_amount": 1000.0})
    assert result == result2


# ----------------------------------------------------------------------
# 9. TEST 8 / 9 - low completeness / low confidence -> senior or non-resolvable
# ----------------------------------------------------------------------
def test_low_completeness_forces_senior():
    case, net, items, completeness = _full_evidence_smurfing()
    completeness = dict(completeness, weighted_score=40.0)  # below the 70 threshold
    result = ap.assess_authority(case, items, completeness, net=net, account={"avg_monthly_txn_amount": 1000.0})
    assert result["authority_tier"] == "senior"
    assert "evidence_below_required_threshold" in result["reasons"]


def test_low_confidence_forces_senior():
    case, net, items, completeness = _full_evidence_smurfing()
    result = ap.assess_authority(case, items, completeness, net=net,
                                  account={"avg_monthly_txn_amount": 1000.0}, confidence=0.1)
    assert result["authority_tier"] == "senior"
    assert "confidence_below_required_threshold" in result["reasons"]
    assert result["confidence_source"] == "supplied"


def test_no_requirement_table_typology_is_low_completeness_senior():
    """Checkpoint 2's own honest 'unclassified typology -> weighted_score
    None' case must not silently default to junior - None must be treated
    as below-threshold, never as vacuously sufficient."""
    case = {"case_id": "CASE-TEST-A5", "account_id": "ACC1", "primary_trigger": "behavioral_deviation"}
    completeness = {"weighted_score": None, "simple_score": None, "required_count": 0,
                     "available_count": 0, "missing": [], "method": "no_requirement_table_for_typology"}
    result = ap.assess_authority(case, [], completeness, net=None)
    assert result["authority_tier"] == "senior"
    assert "evidence_below_required_threshold" in result["reasons"]


# ----------------------------------------------------------------------
# 10. TEST 10 - multiple senior triggers -> one deterministic decision, multiple reason codes
# ----------------------------------------------------------------------
def test_multiple_senior_triggers_all_recorded():
    case = {"case_id": "CASE-TEST-A6", "account_id": "ACC1", "primary_trigger": "money_mule"}
    net = {"typology": "money_mule", "patterns": [], "evidence": {"transactions": [], "summary": {}}}
    store = FakeStore()
    items = build_evidence_items(store, case, net)
    completeness = compute_completeness(items)
    result = ap.assess_authority(case, items, completeness, net=net, contradiction_state="unresolved")
    assert result["authority_tier"] == "senior"
    assert result["decision"] == "senior_review_required"
    assert "critical_evidence_missing" in result["reasons"]
    assert "high_risk_typology" in result["reasons"]
    assert "unresolved_contradiction" in result["reasons"]
    assert len(result["reasons"]) >= 3


# ----------------------------------------------------------------------
# 11. TEST 11 - determinism: identical inputs -> identical output
# ----------------------------------------------------------------------
def test_identical_inputs_produce_identical_output():
    case, net, items, completeness = _full_evidence_smurfing()
    account = {"avg_monthly_txn_amount": 1000.0}
    results = [ap.assess_authority(case, items, completeness, net=net, account=account) for _ in range(5)]
    assert all(r == results[0] for r in results)


# ----------------------------------------------------------------------
# 12. TEST 12 - never reads ground truth (static guard, in addition to the
#     repo-wide AST scan in test_ground_truth_isolation.py which this
#     checkpoint adds authority_policy.py to)
# ----------------------------------------------------------------------
def test_source_never_mentions_ground_truth_outside_docstrings():
    src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "authority_policy.py")
    with open(src_path) as f:
        lines = f.readlines()
    in_docstring = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('"""'):
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        assert "ground_truth" not in line.lower()
        assert "fraud_network" not in line.lower()


# ----------------------------------------------------------------------
# 13. TEST 13 - authority engine never mutates its inputs / detection output
# ----------------------------------------------------------------------
def test_assess_authority_does_not_mutate_inputs():
    case, net, items, completeness = _full_evidence_smurfing()
    case_copy, net_copy, items_copy, completeness_copy = (
        dict(case), {**net, "evidence": {k: list(v) for k, v in net["evidence"].items()}},
        [dict(i) for i in items], dict(completeness),
    )
    ap.assess_authority(case, items, completeness, net=net, account={"avg_monthly_txn_amount": 1000.0})
    assert case == case_copy
    assert net["evidence"]["nodes"] == net_copy["evidence"]["nodes"]
    assert net["evidence"]["edges"] == net_copy["evidence"]["edges"]
    assert items == items_copy
    assert completeness == completeness_copy


# ----------------------------------------------------------------------
# 18. TEST 18 - no randomness anywhere in this module (same static-scan
#     style as evidence_model.py::test_completeness_never_uses_random_module)
# ----------------------------------------------------------------------
def test_never_uses_random_module():
    assert "random" not in dir(ap)
    src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "authority_policy.py")
    with open(src_path) as f:
        src = f.read()
    assert "import random" not in src
    in_docstring = False
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""'):
            in_docstring = not in_docstring
            continue
        if in_docstring or stripped.startswith("#"):
            continue
        assert "random." not in line, f"unexpected random.* call outside docstring: {line!r}"


def test_never_uses_uuid_or_wall_clock():
    src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "authority_policy.py")
    with open(src_path) as f:
        src = f.read()
    assert "import uuid" not in src
    assert "datetime.now(" not in src


# ----------------------------------------------------------------------
# Integration: real DataStore + real detected/bundled cases
# ----------------------------------------------------------------------
def test_authority_on_real_cases_is_deterministic_and_well_formed(real_cases):
    store, alerts, cases = real_cases
    checked = 0
    for case in cases[:10]:
        net = generate_network_evidence(store, case)
        items = build_evidence_items(store, case, net)
        completeness = compute_completeness(items)
        case_alerts = [a for a in alerts if a["alert_id"] in case["alert_ids"]]
        account = store.accounts_by_id.get(case["account_id"])

        r1 = ap.assess_authority(case, items, completeness, net=net, account=account, case_alerts=case_alerts)
        r2 = ap.assess_authority(case, items, completeness, net=net, account=account, case_alerts=case_alerts)
        assert r1 == r2, "authority decision must be deterministic on real pipeline output"

        assert r1["authority_tier"] in ("junior", "senior")
        assert r1["can_resolve"] == (r1["authority_tier"] == "junior")
        assert r1["decision"] in ("junior_authorized", "senior_review_required")
        assert isinstance(r1["reasons"], list) and r1["reasons"]
        assert r1["policy_version"] == "v1"
        assert 0.0 <= r1["confidence"] <= 1.0
        checked += 1
    assert checked > 0


def test_typology_specific_behavior_preserved_on_real_cases(real_cases):
    """Section 17: smurfing/reverse_smurfing can use network complexity;
    money_mule/account_swap never do, even on real generated data."""
    store, alerts, cases = real_cases
    for case in cases:
        if case["primary_trigger"] not in ("money_mule", "account_swap"):
            continue
        net = generate_network_evidence(store, case)
        assert ap._is_complex_network(net, case["primary_trigger"], ap.AUTHORITY_POLICY["network_complexity"]) is False


def test_run_pipeline_persists_authority_field(tmp_path):
    """Integration: run_pipeline.run_pipeline() (Checkpoint 4's actual
    integration point) attaches a well-formed `authority` block to every
    persisted evidence record, additive to the existing completeness/
    evidence_items fields - nothing removed, nothing renamed."""
    import run_pipeline as rp
    out_dir = tmp_path / "pipeline_output"
    store, alerts, cases, evidence_records = rp.run_pipeline(data_dir=MOCK_DATA_DIR, out_dir=str(out_dir))
    assert evidence_records, "expected at least one evidence record on the checked-in mock_data/ fixture"
    for e in evidence_records:
        assert "authority" in e
        auth = e["authority"]
        assert auth["case_id"] == e["case_id"]
        assert auth["authority_tier"] in ("junior", "senior")
        assert auth["policy_version"] == "v1"
        # additive - the fields Checkpoints 2/3 already guaranteed are untouched
        assert "completeness" in e
        assert "evidence_items" in e
        assert "data" in e