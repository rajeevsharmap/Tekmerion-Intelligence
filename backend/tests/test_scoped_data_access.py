"""
tests/test_scoped_data_access.py
====================================
CHECKPOINT 7 - dedicated tests for case_data_access.py (ScopedDataAccess /
mask_account / case_account_scope / ScopeViolationError).

This suite exists because tests/test_checkpoint7.py, as audited at the
start of this session, exercises sar_report.py but has no dedicated
coverage of case_data_access.py. This file fills that gap.

Two fixture sources, same split style as test_checkpoint7.py:
  1. Small hand-built store/case/evidence dicts - exercise each rule
     directly and cheaply (junior depth-1, senior full scope, masking,
     violations).
  2. The REAL, checked-in pipeline_output/{cases.json,evidence/*.json}
     plus a real DataStore over mock_data/ - confirms the module behaves
     correctly against genuine upstream Checkpoint 3-6 output, not just
     synthetic fixtures. Skips gracefully if pipeline_output/ is absent.
"""
import glob
import json
import os

import pytest

from case_data_access import (
    ScopedDataAccess,
    ScopeViolationError,
    case_account_scope,
    mask_account,
    PII_FIELDS,
)
from data_store import DataStore

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK_DATA_DIR = os.path.join(BACKEND_DIR, "mock_data")
PIPELINE_OUT_DIR = os.path.join(BACKEND_DIR, "pipeline_output")
EVIDENCE_DIR = os.path.join(PIPELINE_OUT_DIR, "evidence")


# ----------------------------------------------------------------------
# Hand-built fixtures
# ----------------------------------------------------------------------

class _FakeStore:
    """Minimal stand-in exposing only what ScopedDataAccess reads off a
    DataStore, so these unit tests don't depend on mock_data/ contents."""

    def __init__(self, accounts, devices=None, geo=None, txns=None):
        self.accounts_by_id = accounts
        self.devices_by_account = devices or {}
        self.geo_by_account = geo or {}
        self.txn_by_id = txns or {}


def _account(acc_id, **overrides):
    base = {
        "account_id": acc_id,
        "customer_name": f"Customer {acc_id}",
        "occupation": "Engineer",
        "annual_income": "1200000",
        "home_branch": "MG Road",
        "kyc_status": "verified",
        "risk_rating": "medium",
        "account_type": "savings",
    }
    base.update(overrides)
    return base


ROOT = "ACC-ROOT"
DIRECT = "ACC-DIRECT"
INDIRECT = "ACC-INDIRECT"
OUTSIDE = "ACC-OUTSIDE"

FIXTURE_CASE = {"case_id": "CASE-FIXTURE-1", "account_id": ROOT}

FIXTURE_EVIDENCE = {
    "source_transactions": [
        {"transaction_id": "TXN-1", "sender_account_id": ROOT, "receiver_account_id": DIRECT},
        {"transaction_id": "TXN-2", "sender_account_id": DIRECT, "receiver_account_id": INDIRECT},
    ],
    "data": {
        "nodes": [
            {"data": {"id": ROOT}},
            {"data": {"id": DIRECT}},
            {"data": {"id": INDIRECT}},
        ],
        "edges": [
            {"data": {"source": ROOT, "target": DIRECT}},
            {"data": {"source": DIRECT, "target": INDIRECT}},
        ],
    },
}

FIXTURE_STORE = _FakeStore(
    accounts={
        ROOT: _account(ROOT),
        DIRECT: _account(DIRECT),
        INDIRECT: _account(INDIRECT),
        OUTSIDE: _account(OUTSIDE),
    },
    devices={ROOT: [{"device_id": "DEV-1"}], INDIRECT: [{"device_id": "DEV-2"}]},
    geo={ROOT: [{"geo_id": "GEO-1"}], INDIRECT: [{"geo_id": "GEO-2"}]},
    txns={
        "TXN-1": {"transaction_id": "TXN-1", "sender_account_id": ROOT, "receiver_account_id": DIRECT},
        "TXN-2": {"transaction_id": "TXN-2", "sender_account_id": DIRECT, "receiver_account_id": INDIRECT},
        "TXN-OUTSIDE": {"transaction_id": "TXN-OUTSIDE", "sender_account_id": OUTSIDE, "receiver_account_id": "ACC-OTHER"},
    },
)


# ----------------------------------------------------------------------
# case_account_scope
# ----------------------------------------------------------------------

def test_case_account_scope_includes_root_nodes_and_transaction_parties():
    scope = case_account_scope(FIXTURE_CASE, FIXTURE_EVIDENCE)
    assert scope == {ROOT, DIRECT, INDIRECT}


def test_case_account_scope_never_includes_unrelated_accounts():
    scope = case_account_scope(FIXTURE_CASE, FIXTURE_EVIDENCE)
    assert OUTSIDE not in scope


def test_case_account_scope_handles_missing_evidence_gracefully():
    scope = case_account_scope(FIXTURE_CASE, {})
    assert scope == {ROOT}


# ----------------------------------------------------------------------
# mask_account / PII boundary
# ----------------------------------------------------------------------

def test_mask_account_redacts_pii_for_non_senior():
    acc = _account(ROOT)
    masked = mask_account(acc, "junior")
    for field in PII_FIELDS:
        assert masked[field] != acc[field]
        assert masked[field].startswith("REDACTED-")


def test_mask_account_preserves_pii_for_senior():
    acc = _account(ROOT)
    masked = mask_account(acc, "senior")
    for field in PII_FIELDS:
        assert masked[field] == acc[field]


def test_mask_account_never_redacts_non_pii_risk_fields():
    acc = _account(ROOT)
    masked = mask_account(acc, "junior")
    for field in ("kyc_status", "risk_rating", "account_type"):
        assert masked[field] == acc[field]


def test_mask_account_does_not_mutate_input():
    acc = _account(ROOT)
    original = dict(acc)
    mask_account(acc, "junior")
    assert acc == original


def test_mask_account_deterministic_across_calls():
    acc = _account(ROOT)
    m1 = mask_account(acc, "junior")
    m2 = mask_account(acc, "junior")
    assert m1 == m2


def test_mask_account_handles_none():
    assert mask_account(None, "junior") is None


# ----------------------------------------------------------------------
# ScopedDataAccess - junior vs senior scope
# ----------------------------------------------------------------------

def test_junior_scope_is_depth_one_only():
    sda = ScopedDataAccess(FIXTURE_STORE, FIXTURE_CASE, FIXTURE_EVIDENCE, role="junior")
    accounts = {a["account_id"] for a in sda.get_case_accounts()}
    assert accounts == {ROOT, DIRECT}
    assert INDIRECT not in accounts


def test_senior_scope_is_full_case_scope():
    sda = ScopedDataAccess(FIXTURE_STORE, FIXTURE_CASE, FIXTURE_EVIDENCE, role="senior")
    accounts = {a["account_id"] for a in sda.get_case_accounts()}
    assert accounts == {ROOT, DIRECT, INDIRECT}


def test_junior_accounts_are_masked():
    sda = ScopedDataAccess(FIXTURE_STORE, FIXTURE_CASE, FIXTURE_EVIDENCE, role="junior")
    for acc in sda.get_case_accounts():
        assert acc["customer_name"].startswith("REDACTED-")


def test_senior_accounts_are_unmasked():
    sda = ScopedDataAccess(FIXTURE_STORE, FIXTURE_CASE, FIXTURE_EVIDENCE, role="senior")
    for acc in sda.get_case_accounts():
        assert not acc["customer_name"].startswith("REDACTED-")


def test_unknown_role_defaults_to_least_privilege_junior():
    sda = ScopedDataAccess(FIXTURE_STORE, FIXTURE_CASE, FIXTURE_EVIDENCE, role="nonsense")
    assert sda.role == "junior"
    accounts = {a["account_id"] for a in sda.get_case_accounts()}
    assert accounts == {ROOT, DIRECT}


# ----------------------------------------------------------------------
# ScopeViolationError enforcement
# ----------------------------------------------------------------------

def test_junior_cannot_request_devices_outside_depth_one_scope():
    sda = ScopedDataAccess(FIXTURE_STORE, FIXTURE_CASE, FIXTURE_EVIDENCE, role="junior")
    with pytest.raises(ScopeViolationError):
        sda.get_devices_for_accounts([INDIRECT])


def test_senior_can_request_devices_anywhere_in_case_scope():
    sda = ScopedDataAccess(FIXTURE_STORE, FIXTURE_CASE, FIXTURE_EVIDENCE, role="senior")
    devices = sda.get_devices_for_accounts([INDIRECT])
    assert devices == [{"device_id": "DEV-2"}]


def test_no_role_can_request_geo_events_outside_case_scope_entirely():
    sda = ScopedDataAccess(FIXTURE_STORE, FIXTURE_CASE, FIXTURE_EVIDENCE, role="senior")
    with pytest.raises(ScopeViolationError):
        sda.get_geo_events_for_accounts([OUTSIDE])


def test_scope_cannot_be_expanded_by_manipulating_the_account_ids_argument():
    """A caller cannot widen its own scope just by passing extra account
    ids to a request method - the scope is fixed at construction time
    from case-derived data, not the arguments to individual calls."""
    sda = ScopedDataAccess(FIXTURE_STORE, FIXTURE_CASE, FIXTURE_EVIDENCE, role="junior")
    with pytest.raises(ScopeViolationError):
        sda.get_devices_for_accounts([ROOT, DIRECT, OUTSIDE])


def test_get_transaction_outside_case_evidence_and_scope_raises():
    sda = ScopedDataAccess(FIXTURE_STORE, FIXTURE_CASE, FIXTURE_EVIDENCE, role="senior")
    with pytest.raises(ScopeViolationError):
        sda.get_transaction("TXN-OUTSIDE")


def test_get_transaction_within_case_evidence_succeeds():
    sda = ScopedDataAccess(FIXTURE_STORE, FIXTURE_CASE, FIXTURE_EVIDENCE, role="junior")
    txn = sda.get_transaction("TXN-1")
    assert txn["transaction_id"] == "TXN-1"


# ----------------------------------------------------------------------
# Network scoping
# ----------------------------------------------------------------------

def test_junior_network_view_excludes_indirect_nodes_and_their_edges():
    sda = ScopedDataAccess(FIXTURE_STORE, FIXTURE_CASE, FIXTURE_EVIDENCE, role="junior")
    net = sda.get_related_network()
    node_ids = {(n.get("data") or {}).get("id") for n in net["nodes"]}
    assert node_ids == {ROOT, DIRECT}
    # the DIRECT->INDIRECT edge must not leak since INDIRECT is filtered out
    for e in net["edges"]:
        d = e.get("data") or {}
        assert d.get("source") in node_ids and d.get("target") in node_ids


def test_senior_network_view_includes_full_case_graph():
    sda = ScopedDataAccess(FIXTURE_STORE, FIXTURE_CASE, FIXTURE_EVIDENCE, role="senior")
    net = sda.get_related_network()
    node_ids = {(n.get("data") or {}).get("id") for n in net["nodes"]}
    assert node_ids == {ROOT, DIRECT, INDIRECT}
    assert len(net["edges"]) == 2


# ----------------------------------------------------------------------
# Provenance - evidence/transactions are re-exposed, never re-gathered
# ----------------------------------------------------------------------

def test_get_case_transactions_returns_exact_evidence_list_not_a_fresh_query():
    sda = ScopedDataAccess(FIXTURE_STORE, FIXTURE_CASE, FIXTURE_EVIDENCE, role="senior")
    assert sda.get_case_transactions() == FIXTURE_EVIDENCE["source_transactions"]


def test_get_evidence_returns_the_original_evidence_object():
    sda = ScopedDataAccess(FIXTURE_STORE, FIXTURE_CASE, FIXTURE_EVIDENCE, role="junior")
    assert sda.get_evidence() is FIXTURE_EVIDENCE


def test_missing_account_in_scope_is_silently_skipped_not_fabricated():
    """An account_id that is in scope but absent from the store (e.g. a
    counterparty never onboarded) must be skipped, never invented."""
    store = _FakeStore(accounts={ROOT: _account(ROOT)})  # DIRECT/INDIRECT absent
    sda = ScopedDataAccess(store, FIXTURE_CASE, FIXTURE_EVIDENCE, role="senior")
    accounts = sda.get_case_accounts()
    assert {a["account_id"] for a in accounts} == {ROOT}


# ----------------------------------------------------------------------
# Determinism
# ----------------------------------------------------------------------

def test_scope_computation_is_deterministic_across_construction():
    sda1 = ScopedDataAccess(FIXTURE_STORE, FIXTURE_CASE, FIXTURE_EVIDENCE, role="senior")
    sda2 = ScopedDataAccess(FIXTURE_STORE, FIXTURE_CASE, FIXTURE_EVIDENCE, role="senior")
    assert sda1._authorized_scope == sda2._authorized_scope
    assert sda1.get_case_accounts() == sda2.get_case_accounts()


# ----------------------------------------------------------------------
# Real pipeline_output / real DataStore integration
# ----------------------------------------------------------------------

@pytest.mark.skipif(not os.path.isdir(EVIDENCE_DIR), reason="pipeline_output/evidence not present - run run_pipeline.py first")
def test_real_case_junior_scope_never_exceeds_senior_scope():
    store = DataStore(MOCK_DATA_DIR)
    cases = {c["case_id"]: c for c in json.load(open(os.path.join(PIPELINE_OUT_DIR, "cases.json")))}
    files = sorted(glob.glob(os.path.join(EVIDENCE_DIR, "*.json")))
    assert files, "expected persisted evidence files from a real pipeline run"
    checked = 0
    for path in files:
        evidence = json.load(open(path))
        case_id = evidence.get("case_id")
        case = cases.get(case_id)
        if not case:
            continue
        junior = ScopedDataAccess(store, case, evidence, role="junior")
        senior = ScopedDataAccess(store, case, evidence, role="senior")
        junior_scope = junior._authorized_scope
        senior_scope = senior._authorized_scope
        assert junior_scope.issubset(senior_scope)
        assert case["account_id"] in junior_scope
        checked += 1
    assert checked == len(files)


@pytest.mark.skipif(not os.path.isdir(EVIDENCE_DIR), reason="pipeline_output/evidence not present - run run_pipeline.py first")
def test_real_case_junior_view_masks_pii_senior_does_not():
    store = DataStore(MOCK_DATA_DIR)
    cases = {c["case_id"]: c for c in json.load(open(os.path.join(PIPELINE_OUT_DIR, "cases.json")))}
    files = sorted(glob.glob(os.path.join(EVIDENCE_DIR, "*.json")))
    path = files[0]
    evidence = json.load(open(path))
    case = cases[evidence["case_id"]]
    junior = ScopedDataAccess(store, case, evidence, role="junior")
    senior = ScopedDataAccess(store, case, evidence, role="senior")
    junior_accounts = junior.get_case_accounts()
    senior_accounts = senior.get_case_accounts()
    assert junior_accounts, "expected at least the root account in scope"
    for acc in junior_accounts:
        for field in PII_FIELDS:
            if field in acc and acc[field] is not None:
                assert str(acc[field]).startswith("REDACTED-")
    for acc in senior_accounts:
        for field in PII_FIELDS:
            assert not (isinstance(acc.get(field), str) and acc[field].startswith("REDACTED-"))


@pytest.mark.skipif(not os.path.isdir(EVIDENCE_DIR), reason="pipeline_output/evidence not present - run run_pipeline.py first")
def test_real_case_scope_violation_raised_for_definitely_unrelated_account():
    store = DataStore(MOCK_DATA_DIR)
    cases = {c["case_id"]: c for c in json.load(open(os.path.join(PIPELINE_OUT_DIR, "cases.json")))}
    files = sorted(glob.glob(os.path.join(EVIDENCE_DIR, "*.json")))
    # pick two different cases so case B's root account is (almost
    # certainly) outside case A's scope
    ev_a = json.load(open(files[0]))
    ev_b = json.load(open(files[1]))
    case_a = cases[ev_a["case_id"]]
    case_b = cases[ev_b["case_id"]]
    sda = ScopedDataAccess(store, case_a, ev_a, role="senior")
    if case_b["account_id"] in sda._authorized_scope:
        pytest.skip("chosen cases happen to share scope on this dataset; not a failure")
    with pytest.raises(ScopeViolationError):
        sda.get_devices_for_accounts([case_b["account_id"]])