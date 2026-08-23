"""
tests/test_evidence_model.py
===============================
CHECKPOINT 2 test suite: canonical EvidenceItem model + deterministic
completeness (evidence_model.py), plus regression guards on
wrap_as_evidence()/generate_network_evidence() (network_layer.py) that this
checkpoint builds on top of.

Two kinds of tests here:
  1. Unit tests against small hand-built `net`/`case`/fake-store fixtures -
     exercise each checker's available/missing branch directly, without
     needing a full DataStore.
  2. Integration tests against the real, checked-in mock_data/ (generated
     with a fixed `random.seed(42)` in generate_mock_data.py, so account
     counts/typology mix are stable across runs) - run the real Detection
     -> Case Intake -> Network Evidence chain and assert the evidence
     model behaves sanely on real pipeline output, not just fixtures.
"""
import os
import random

import pytest

from data_store import DataStore
from detection_layer import run_detection_pipeline, bundle_alerts_into_cases
from network_layer import generate_network_evidence, wrap_as_evidence
import evidence_model as em

MOCK_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mock_data")


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
class FakeStore:
    """Minimal stand-in exposing only what evidence_model.py's checkers
    actually read off a DataStore - keeps unit tests independent of CSV
    fixtures/generate_mock_data.py."""

    def __init__(self, accounts_by_id=None, bene_by_account=None, devices_by_account=None, geo_by_account=None):
        self.accounts_by_id = accounts_by_id or {}
        self.bene_by_account = bene_by_account or {}
        self.devices_by_account = devices_by_account or {}
        self.geo_by_account = geo_by_account or {}


@pytest.fixture
def real_store():
    assert os.path.isdir(MOCK_DATA_DIR), "mock_data/ must be generated before running tests (see README)"
    return DataStore(MOCK_DATA_DIR)


@pytest.fixture
def real_cases(real_store):
    alerts = run_detection_pipeline(real_store)
    return real_store, bundle_alerts_into_cases(alerts)


def _first_case_of_typology(cases, typology):
    return next((c for c in cases if c["primary_trigger"] == typology), None)


# ----------------------------------------------------------------------
# 1. TYPOLOGY_EVIDENCE_REQUIREMENTS config sanity
# ----------------------------------------------------------------------
def test_weights_sum_to_one():
    """Each typology's documented weighting scheme should sum to 1.0 -
    ARCHITECTURE.md requires weights be documented/configurable, and a
    scheme that silently doesn't sum to 1.0 would still work (compute_
    completeness normalizes), but should be caught here rather than
    discovered by a confusing completeness number later."""
    for typology, requirements in em.TYPOLOGY_EVIDENCE_REQUIREMENTS.items():
        total = round(sum(weight for _, weight in requirements), 6)
        assert total == 1.0, f"{typology} weights sum to {total}, not 1.0"


def test_every_required_evidence_type_has_a_checker():
    for typology, requirements in em.TYPOLOGY_EVIDENCE_REQUIREMENTS.items():
        for evidence_type, _ in requirements:
            assert evidence_type in em._CHECKERS, f"no checker registered for {typology}.{evidence_type}"


def test_known_typologies_covered():
    for typology in ("smurfing", "reverse_smurfing", "money_mule", "account_swap"):
        assert typology in em.TYPOLOGY_EVIDENCE_REQUIREMENTS


# ----------------------------------------------------------------------
# 2. build_evidence_items() - unclassified typology
# ----------------------------------------------------------------------
def test_unclassified_typology_returns_no_items():
    """generate_network_evidence()'s own explicit 'unclassified' fallback
    (any typology outside the 4 known ones, e.g. mock data's
    'behavioral_deviation' label) must not get a guessed/borrowed
    requirement table."""
    store = FakeStore()
    case = {"case_id": "CASE-TEST0001", "account_id": "ACC000001", "primary_trigger": "behavioral_deviation"}
    net = {"typology": "behavioral_deviation", "account_id": "ACC000001", "evidence": {}, "patterns": []}
    items = em.build_evidence_items(store, case, net)
    assert items == []
    completeness = em.compute_completeness(items)
    assert completeness["weighted_score"] is None
    assert completeness["method"] == "no_requirement_table_for_typology"


# ----------------------------------------------------------------------
# 3. build_evidence_items() - money_mule, hand-built net (unit-level)
# ----------------------------------------------------------------------
def test_money_mule_full_evidence_available():
    store = FakeStore(
        bene_by_account={"ACC1": [{"beneficiary_id": "BEN1", "is_verified": True}]},
        devices_by_account={"ACC1": [{"device_id": "DEV1", "is_trusted_device": True}]},
        geo_by_account={"ACC1": [{"geo_event_id": "GEO1"}, {"geo_event_id": "GEO2"}]},
    )
    case = {"case_id": "CASE-TEST0002", "account_id": "ACC1", "primary_trigger": "money_mule"}
    net = {
        "typology": "money_mule",
        "account_id": "ACC1",
        "patterns": ["rapid_fund_pass_through"],
        "evidence": {
            "transactions": [
                {"transaction_id": "T1", "direction": "in"},
                {"transaction_id": "T2", "direction": "in"},
                {"transaction_id": "T3", "direction": "in"},
                {"transaction_id": "T4", "direction": "out"},
            ],
            "summary": {"median_inbound_to_outbound_minutes": 12.0,
                        "total_inbound": 1000.0, "total_outbound": 900.0},
        },
    }
    items = em.build_evidence_items(store, case, net)
    by_type = {i["evidence_type"]: i for i in items}

    assert by_type["inbound_transaction_chain"]["available"] is True
    assert set(by_type["inbound_transaction_chain"]["source_record_ids"]) == {"T1", "T2", "T3"}
    assert by_type["outbound_transaction_chain"]["available"] is True
    assert by_type["pass_through_timing"]["available"] is True
    assert by_type["amount_retention_ratio"]["available"] is True
    assert by_type["beneficiary_information"]["available"] is True
    assert by_type["device_information"]["available"] is True
    assert by_type["geo_information"]["available"] is True
    # money_mule table has no counterparty_relationship checker input in this
    # fixture (no nodes/edges - that's a smurfing-graph-only field), so it's
    # correctly reported missing, not silently marked available.
    assert by_type["counterparty_relationship"]["available"] is False

    completeness = em.compute_completeness(items)
    assert completeness["method"] == "deterministic_weighted_availability"
    assert 0 < completeness["weighted_score"] < 100


def test_money_mule_all_evidence_missing():
    store = FakeStore()
    case = {"case_id": "CASE-TEST0003", "account_id": "ACC2", "primary_trigger": "money_mule"}
    net = {"typology": "money_mule", "account_id": "ACC2", "patterns": [], "evidence": {"transactions": [], "summary": {}}}
    items = em.build_evidence_items(store, case, net)
    completeness = em.compute_completeness(items)

    assert all(not i["available"] for i in items)
    assert completeness["weighted_score"] == 0.0
    assert completeness["simple_score"] == 0.0
    assert completeness["available_count"] == 0
    assert completeness["required_count"] == len(em.TYPOLOGY_EVIDENCE_REQUIREMENTS["money_mule"])
    assert len(completeness["missing"]) == len(items)


# ----------------------------------------------------------------------
# 4. Missing evidence is a structured object, never a free-text string
# ----------------------------------------------------------------------
def test_missing_evidence_is_structured_not_free_text():
    store = FakeStore()
    case = {"case_id": "CASE-TEST0004", "account_id": "ACC3", "primary_trigger": "smurfing"}
    net = {"typology": "smurfing", "account_id": "ACC3", "patterns": [], "evidence": {"nodes": [], "edges": []}}
    items = em.build_evidence_items(store, case, net)
    missing_items = [i for i in items if not i["available"]]
    assert missing_items  # smurfing has nothing available in this bare fixture
    for i in missing_items:
        assert isinstance(i["missing_reason"], dict)
        assert set(i["missing_reason"].keys()) == {"reason", "severity"}
        assert i["missing_reason"]["severity"] in ("critical", "moderate")
        assert isinstance(i["missing_reason"]["reason"], str) and i["missing_reason"]["reason"]


def test_severity_is_deterministic_function_of_weight():
    """severity must come from the item's configured weight, not be
    randomly or manually assigned per case - same evidence_type, same
    typology => same severity, always."""
    store = FakeStore()
    case = {"case_id": "CASE-TEST0005", "account_id": "ACC4", "primary_trigger": "smurfing"}
    net = {"typology": "smurfing", "account_id": "ACC4", "patterns": [], "evidence": {"nodes": [], "edges": []}}
    for _ in range(5):
        items = em.build_evidence_items(store, case, net)
        by_type = {i["evidence_type"]: i for i in items}
        # source_of_funds has weight 0.15 -> critical, every single time
        assert by_type["source_of_funds"]["missing_reason"]["severity"] == "critical"


# ----------------------------------------------------------------------
# 5. Determinism: same input -> same completeness, always (no randomness)
# ----------------------------------------------------------------------
def test_completeness_is_deterministic_across_repeated_calls():
    store = FakeStore(
        bene_by_account={"ACC5": [{"beneficiary_id": "BEN1", "is_verified": False}]},
    )
    case = {"case_id": "CASE-TEST0006", "account_id": "ACC5", "primary_trigger": "account_swap"}
    net = {
        "typology": "account_swap", "account_id": "ACC5",
        "patterns": ["high_value_transaction"],
        "evidence": {"events": [{"event_id": "E1", "event_type": "transaction", "direction": "out"}],
                     "behavioral_summary": {"amount_deviation_ratio": None}},
    }
    scores = set()
    for _ in range(10):
        items = em.build_evidence_items(store, case, net)
        scores.add(em.compute_completeness(items)["weighted_score"])
    assert len(scores) == 1, f"completeness should be identical across repeated calls, got {scores}"


def test_completeness_never_uses_random_module():
    """Static guard: evidence_model.py must not import Python's `random`
    module at all - completeness here must never be a random draw (that's
    explicitly reserved for generate_mock_data.py's ground truth only)."""
    assert "random" not in dir(em)
    src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evidence_model.py")
    with open(src_path) as f:
        src = f.read()
    assert "import random" not in src
    # code-level guard only (the module docstring legitimately discusses
    # generate_mock_data.py's random.gauss(...) usage in prose) - check
    # non-docstring/non-comment lines for an actual random.* call.
    in_docstring = False
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""'):
            in_docstring = not in_docstring
            continue
        if in_docstring or stripped.startswith("#"):
            continue
        assert "random." not in line, f"unexpected random.* call outside docstring: {line!r}"


# ----------------------------------------------------------------------
# 6. wrap_as_evidence() regression guard - docs/backend_implementation_status.md
#    (written by a prior session) claims wrap_as_evidence() drops account_id/
#    source_transactions/network_scope before persisting. Verified NOT true
#    of the current repository state before this checkpoint's changes - this
#    test pins that down so it can't silently regress going forward.
# ----------------------------------------------------------------------
def test_wrap_as_evidence_preserves_scope_fields():
    network_response = {
        "case_id": "CASE-X", "account_id": "ACC-X", "typology": "smurfing",
        "visualization_type": "network", "evidence": {"nodes": [], "edges": []},
        "patterns": [], "source_transactions": ["T1", "T2"], "generated_at": "2026-01-01T00:00:00",
        "network_scope": {"max_depth": 3, "time_window_hours": 24},
    }
    wrapped = wrap_as_evidence(network_response)
    assert wrapped["account_id"] == "ACC-X"
    assert wrapped["source_transactions"] == ["T1", "T2"]
    assert wrapped["network_scope"] == {"max_depth": 3, "time_window_hours": 24}


# ----------------------------------------------------------------------
# 7. Integration: real DataStore + real detected cases, one per typology
# ----------------------------------------------------------------------
@pytest.mark.parametrize("typology", ["smurfing", "reverse_smurfing", "money_mule", "account_swap"])
def test_build_evidence_items_on_real_case(real_cases, typology):
    store, cases = real_cases
    case = _first_case_of_typology(cases, typology)
    if case is None:
        pytest.skip(f"no live-detected case for typology={typology} in this mock_data generation")

    net = generate_network_evidence(store, case)
    items = em.build_evidence_items(store, case, net)
    completeness = em.compute_completeness(items)

    expected_types = {t for t, _ in em.TYPOLOGY_EVIDENCE_REQUIREMENTS[typology]}
    assert {i["evidence_type"] for i in items} == expected_types
    assert completeness["required_count"] == len(expected_types)
    assert completeness["available_count"] == sum(1 for i in items if i["available"])
    assert 0.0 <= completeness["weighted_score"] <= 100.0
    # every AVAILABLE item must carry at least one real source record id,
    # OR be one of the summary-only evidence types that has no per-record
    # id to point at (documented set below) - never "available" with
    # nothing backing it except a bare True/False.
    no_record_id_types = {"pass_through_timing", "amount_retention_ratio", "behavioral_baseline"}
    for i in items:
        if i["available"] and i["evidence_type"] not in no_record_id_types:
            assert i["source_record_ids"], f"{i['evidence_type']} marked available with zero source_record_ids"


def test_source_of_funds_always_missing_on_real_data():
    """source_of_funds documentation isn't modeled anywhere in this dataset
    (generate_mock_data.py never writes it) - must be honestly reported
    missing for every real smurfing/reverse_smurfing case, never guessed
    available."""
    store = DataStore(MOCK_DATA_DIR)
    alerts = run_detection_pipeline(store)
    cases = bundle_alerts_into_cases(alerts)
    checked = 0
    for case in cases:
        if case["primary_trigger"] not in ("smurfing", "reverse_smurfing"):
            continue
        net = generate_network_evidence(store, case)
        items = em.build_evidence_items(store, case, net)
        sof = next(i for i in items if i["evidence_type"] == "source_of_funds")
        assert sof["available"] is False
        assert sof["missing_reason"]["reason"] == "documentation_not_available"
        checked += 1
    assert checked > 0, "expected at least one smurfing/reverse_smurfing case in this mock_data generation"


def test_persisted_evidence_items_round_trip(tmp_path, real_cases):
    """Full loop: build items -> json.dumps -> json.loads, exactly as
    run_pipeline.py persists them, and confirm nothing is lost/mangled."""
    import json
    store, cases = real_cases
    case = cases[0]
    net = generate_network_evidence(store, case)
    evidence = wrap_as_evidence(net)
    items = em.build_evidence_items(store, case, net)
    evidence["evidence_items"] = items
    evidence["completeness"] = em.compute_completeness(items)

    out = tmp_path / "evidence.json"
    with open(out, "w") as f:
        json.dump(evidence, f, indent=2, default=str)
    with open(out) as f:
        reloaded = json.load(f)

    assert reloaded["evidence_items"] == items
    assert reloaded["completeness"] == evidence["completeness"]
    assert reloaded["account_id"] == evidence["account_id"]
    assert reloaded["source_transactions"] == evidence["source_transactions"]