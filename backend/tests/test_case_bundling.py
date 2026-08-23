"""
tests/test_case_bundling.py
==============================
CHECKPOINT 3, Section 9: the 10 required case-correlation tests, plus the
account-swap causal-linkage regression guard (TEST 10) and one extra
positive-correlation test that documents the counterpart of TEST 3 (cross-
typology alerts DO merge when they share a real, observable evidence
anchor - this is the exact real-data pattern found in mock_data/, not a
hypothetical).

Most tests use small, hand-built alert dicts rather than the full
DataStore/generate_mock_data.py pipeline, so the correlation POLICY itself
(bundle_alerts_into_cases / _pairwise_correlation / _split_cluster_by_
correlation) can be tested precisely and fast, independent of whatever the
mock generator happens to produce this run. A handful of tests also run
against the real, checked-in mock_data/ to prove the policy holds on real
generated output, not just fixtures.
"""
import os

import pytest

from data_store import DataStore
from detection_layer import (
    run_detection_pipeline,
    bundle_alerts_into_cases,
    CASE_BUNDLE_WINDOW_HOURS,
)

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK_DATA_DIR = os.path.join(BACKEND_DIR, "mock_data")


# ----------------------------------------------------------------------
# Fixture helpers - minimal alert dicts carrying only what
# bundle_alerts_into_cases()/its helpers actually read.
# ----------------------------------------------------------------------
def _alert(account_id, typology, created_at, transaction_id, alert_score=40,
           relevant_transaction_ids=None, evidence_signals=None):
    return {
        "alert_id": f"ALERT-{typology.upper()[:4]}-{account_id}-{created_at}",
        "account_id": account_id,
        "transaction_id": transaction_id,
        "relevant_transaction_ids": relevant_transaction_ids or [transaction_id],
        "typology": typology,
        "triggered": True,
        "alert_score": alert_score,
        "severity": "medium",
        "triggering_rules": ["X-001"],
        "evidence_signals": evidence_signals or [{"signal": "dummy_signal", "value": True}],
        "recommended_initial_action": "monitor",
        "case_required": True,
        "created_at": created_at,
    }


@pytest.fixture
def real_store():
    assert os.path.isdir(MOCK_DATA_DIR), "mock_data/ must be generated before running tests"
    return DataStore(MOCK_DATA_DIR)


@pytest.fixture
def real_alerts_and_cases(real_store):
    alerts = run_detection_pipeline(real_store)
    cases = bundle_alerts_into_cases(alerts)
    return real_store, alerts, cases


# ----------------------------------------------------------------------
# TEST 1: Multiple alerts for the same account can become one case.
# ----------------------------------------------------------------------
def test_multiple_same_typology_alerts_same_account_become_one_case():
    alerts = [
        _alert("ACC001", "smurfing", "2025-01-01T00:00:00", "TXN001"),
        _alert("ACC001", "smurfing", "2025-01-01T02:00:00", "TXN002"),
    ]
    cases = bundle_alerts_into_cases(alerts)
    assert len(cases) == 1
    assert set(cases[0]["alert_ids"]) == {alerts[0]["alert_id"], alerts[1]["alert_id"]}
    assert cases[0]["account_id"] == "ACC001"


# ----------------------------------------------------------------------
# TEST 2: Alerts outside the configured correlation window do not
# automatically become one case.
# ----------------------------------------------------------------------
def test_alerts_outside_window_stay_separate_cases():
    alerts = [
        _alert("ACC002", "smurfing", "2025-01-01T00:00:00", "TXN010"),
        # 48h later - well outside the default 24h window, same typology,
        # same account: must NOT be merged.
        _alert("ACC002", "smurfing", "2025-01-03T00:00:00", "TXN011"),
    ]
    cases = bundle_alerts_into_cases(alerts, window_hours=CASE_BUNDLE_WINDOW_HOURS)
    assert len(cases) == 2
    assert {tuple(c["alert_ids"]) for c in cases} == {
        (alerts[0]["alert_id"],), (alerts[1]["alert_id"],)
    }


def test_correlation_window_is_configurable():
    """The window itself is a parameter, not a hardcoded constant - passing
    a smaller window splits alerts that the default window would merge."""
    alerts = [
        _alert("ACC002B", "smurfing", "2025-01-01T00:00:00", "TXN010"),
        _alert("ACC002B", "smurfing", "2025-01-01T10:00:00", "TXN011"),
    ]
    merged = bundle_alerts_into_cases(alerts, window_hours=24)
    assert len(merged) == 1
    split = bundle_alerts_into_cases(alerts, window_hours=2)
    assert len(split) == 2


# ----------------------------------------------------------------------
# TEST 3: Different typologies are not merged merely because they share an
# account, unless the documented policy explicitly allows that correlation
# (i.e. a shared observable evidence anchor).
# ----------------------------------------------------------------------
def test_different_typologies_same_account_same_window_but_no_shared_evidence_stay_separate():
    alerts = [
        _alert("ACC003", "smurfing", "2025-01-01T00:00:00", "TXN020"),
        # Same account, same 24h window, DIFFERENT typology, and a
        # completely different, non-overlapping transaction. Per policy this
        # must NOT be silently merged just because it landed in the window.
        _alert("ACC003", "account_swap", "2025-01-01T05:00:00", "TXN021"),
    ]
    cases = bundle_alerts_into_cases(alerts)
    assert len(cases) == 2, "unrelated typologies must not merge on account+window alone"
    for c in cases:
        assert c["bundle_reason"] == ["single_alert_case"]


def test_different_typologies_DO_merge_when_they_share_a_transaction_anchor():
    """The positive counterpart of TEST 3, and the exact real-data pattern
    seen in mock_data/ (smurfing's rapid-onward-transfer anchor and
    money_mule's pass-through anchor landing on the identical transaction):
    cross-typology correlation IS allowed when there's a real, observable
    shared-evidence anchor, and the case must record that as the reason."""
    alerts = [
        _alert("ACC004", "smurfing", "2025-01-01T00:00:00", "TXN030",
               relevant_transaction_ids=["TXN028", "TXN029", "TXN030"]),
        _alert("ACC004", "money_mule", "2025-01-01T01:00:00", "TXN030",
               relevant_transaction_ids=["TXN030", "TXN031"]),
    ]
    cases = bundle_alerts_into_cases(alerts)
    assert len(cases) == 1
    case = cases[0]
    assert set(case["typologies"]) == {"smurfing", "money_mule"}
    assert "shared_transaction_chain" in case["bundle_reason"]
    assert "same_typology" not in case["bundle_reason"]


def test_three_alerts_two_correlated_one_not_splits_correctly():
    """A more realistic mixed case: two smurfing alerts (same typology - merge)
    plus one account_swap alert on the same account in the same window with
    no shared transaction (must split out on its own)."""
    alerts = [
        _alert("ACC005", "smurfing", "2025-01-01T00:00:00", "TXN040"),
        _alert("ACC005", "smurfing", "2025-01-01T03:00:00", "TXN041"),
        _alert("ACC005", "account_swap", "2025-01-01T06:00:00", "TXN099"),
    ]
    cases = bundle_alerts_into_cases(alerts)
    assert len(cases) == 2
    sizes = sorted(len(c["alert_ids"]) for c in cases)
    assert sizes == [1, 2]
    smurf_case = next(c for c in cases if len(c["alert_ids"]) == 2)
    swap_case = next(c for c in cases if len(c["alert_ids"]) == 1)
    assert smurf_case["typologies"] == ["smurfing"]
    assert swap_case["typologies"] == ["account_swap"]
    assert swap_case["bundle_reason"] == ["single_alert_case"]


# ----------------------------------------------------------------------
# TEST 4: Bundle reasons are deterministic and explainable.
# ----------------------------------------------------------------------
def test_bundle_reasons_are_deterministic_across_repeated_calls():
    alerts = [
        _alert("ACC006", "smurfing", "2025-01-01T00:00:00", "TXN050"),
        _alert("ACC006", "smurfing", "2025-01-01T02:00:00", "TXN051"),
    ]
    run1 = bundle_alerts_into_cases([dict(a) for a in alerts])
    run2 = bundle_alerts_into_cases([dict(a) for a in alerts])
    assert run1[0]["bundle_reason"] == run2[0]["bundle_reason"]
    # And explainable: every reason token is one of the documented,
    # structured vocabulary - never a free-text sentence.
    known_reasons = {"same_primary_account", "within_case_window", "same_typology",
                      "shared_transaction_chain", "single_alert_case"}
    for c in run1:
        assert set(c["bundle_reason"]) <= known_reasons
        assert c["bundle_reason"] == sorted(c["bundle_reason"]), "bundle_reason must be deterministically ordered"


def test_bundle_reason_present_on_every_case_real_data(real_alerts_and_cases):
    _, _, cases = real_alerts_and_cases
    assert cases, "expected at least one live case on the checked-in mock_data/ fixture"
    for c in cases:
        assert "bundle_reason" in c and isinstance(c["bundle_reason"], list) and c["bundle_reason"]
        assert "correlation_window_hours" in c


# ----------------------------------------------------------------------
# TEST 5: Ground-truth network IDs are not required for bundling.
# ----------------------------------------------------------------------
def test_bundling_never_needs_a_network_id_field():
    """Alerts carrying no network_id/fraud-network field of any kind still
    bundle correctly - bundle_alerts_into_cases() never looks for one."""
    alerts = [
        _alert("ACC007", "reverse_smurfing", "2025-01-01T00:00:00", "TXN060"),
        _alert("ACC007", "reverse_smurfing", "2025-01-01T01:00:00", "TXN061"),
    ]
    for a in alerts:
        assert "network_id" not in a and "fraud_network_id" not in a
    cases = bundle_alerts_into_cases(alerts)
    assert len(cases) == 1
    assert "network_id" not in cases[0] and "fraud_network_id" not in cases[0]


# ----------------------------------------------------------------------
# TEST 6: One fraud scenario can produce multiple live alerts.
# (Verified in live terms: the live pipeline is capable of raising more than
# one alert against the same account, without ever consulting how many
# ground-truth scenarios exist - that mapping is evaluation's job, not
# detection's, per ARCHITECTURE.md's "Alert != case != fraud network".)
# ----------------------------------------------------------------------
def test_one_account_can_produce_multiple_live_alerts(real_alerts_and_cases):
    _, alerts, _ = real_alerts_and_cases
    by_account = {}
    for a in alerts:
        by_account.setdefault(a["account_id"], []).append(a)
    multi = {acc: al for acc, al in by_account.items() if len(al) > 1}
    assert multi, "expected at least one account with multiple live alerts on real generated data"


def test_synthetic_multi_alert_account_yields_multi_alert_case():
    alerts = [
        _alert("ACC008", "money_mule", "2025-01-01T00:00:00", "TXN070"),
        _alert("ACC008", "money_mule", "2025-01-01T01:00:00", "TXN071"),
        _alert("ACC008", "money_mule", "2025-01-01T02:00:00", "TXN072"),
    ]
    cases = bundle_alerts_into_cases(alerts)
    assert len(cases) == 1
    assert len(cases[0]["alert_ids"]) == 3


# ----------------------------------------------------------------------
# TEST 7: Live cases are generated exclusively from live alerts.
# ----------------------------------------------------------------------
def test_cases_reference_only_real_input_alert_ids(real_alerts_and_cases):
    _, alerts, cases = real_alerts_and_cases
    alert_ids = {a["alert_id"] for a in alerts}
    for c in cases:
        for aid in c["alert_ids"]:
            assert aid in alert_ids, f"case {c['case_id']} references unknown alert_id {aid}"
        # every alert in a case must actually belong to that case's account
        case_alerts = [a for a in alerts if a["alert_id"] in c["alert_ids"]]
        assert all(a["account_id"] == c["account_id"] for a in case_alerts)


def test_every_triggered_alert_is_linked_to_exactly_one_case():
    alerts = [
        _alert("ACC009", "smurfing", "2025-01-01T00:00:00", "TXN080"),
        _alert("ACC009", "account_swap", "2025-01-01T01:00:00", "TXN081"),
    ]
    bundle_alerts_into_cases(alerts)  # mutates alerts in place with linked_case_id
    for a in alerts:
        assert a.get("linked_case_id"), f"alert {a['alert_id']} was never linked to a case"


# ----------------------------------------------------------------------
# TEST 8: Repeated runs produce the same alert/case output (determinism).
# ----------------------------------------------------------------------
def test_repeated_pipeline_runs_produce_identical_alerts_and_cases():
    import json
    store1 = DataStore(MOCK_DATA_DIR)
    alerts1 = run_detection_pipeline(store1)
    cases1 = bundle_alerts_into_cases(alerts1)

    store2 = DataStore(MOCK_DATA_DIR)
    alerts2 = run_detection_pipeline(store2)
    cases2 = bundle_alerts_into_cases(alerts2)

    assert json.dumps(alerts1, sort_keys=True, default=str) == \
        json.dumps(alerts2, sort_keys=True, default=str), \
        "alert_id/content must be identical across repeated runs on identical input data"
    assert json.dumps(cases1, sort_keys=True, default=str) == \
        json.dumps(cases2, sort_keys=True, default=str), \
        "case_id/bundle_reason/content must be identical across repeated runs"


def test_bundling_is_deterministic_given_a_fixed_alert_list():
    """Isolates determinism of bundle_alerts_into_cases() itself (as opposed
    to the full detection->bundling chain above) against a hand-built,
    order-randomized alert list."""
    import json
    import random as _random

    alerts = [
        _alert("ACC010", "smurfing", "2025-01-01T00:00:00", "TXN090"),
        _alert("ACC010", "smurfing", "2025-01-01T02:00:00", "TXN091"),
        _alert("ACC010", "account_swap", "2025-01-01T10:00:00", "TXN092"),
        _alert("ACC011", "money_mule", "2025-01-02T00:00:00", "TXN093"),
    ]
    shuffled = alerts[:]
    _random.Random(7).shuffle(shuffled)

    cases_a = bundle_alerts_into_cases([dict(a) for a in alerts])
    cases_b = bundle_alerts_into_cases([dict(a) for a in shuffled])

    key = lambda cs: sorted(json.dumps(c, sort_keys=True) for c in cs)
    assert key(cases_a) == key(cases_b), "bundling must not depend on input ordering"


# ----------------------------------------------------------------------
# TEST 9: No live module reads ground truth.
# (Covered exhaustively in tests/test_ground_truth_isolation.py - this is a
# lightweight, local corroborating check that bundle_alerts_into_cases()
# specifically never requires/reads a ground_truth_* key on its input.)
# ----------------------------------------------------------------------
def test_bundling_ignores_unexpected_ground_truth_looking_keys():
    """Even if an alert dict WERE to carry a stray ground_truth_-prefixed
    key (e.g. from being constructed sloppily upstream), bundling must
    ignore it - it must never be read, matched on, or copied onto the case."""
    a1 = _alert("ACC012", "smurfing", "2025-01-01T00:00:00", "TXN100")
    a2 = _alert("ACC012", "smurfing", "2025-01-01T01:00:00", "TXN101")
    a1["ground_truth_network_id"] = "GT-SMURF-999"
    a2["ground_truth_network_id"] = "GT-SMURF-000"  # deliberately DIFFERENT
    cases = bundle_alerts_into_cases([a1, a2])
    # Still merges on same_typology/window alone - the (deliberately
    # mismatched) ground_truth_network_id must have no bearing whatsoever.
    assert len(cases) == 1
    assert not any("ground_truth" in k for k in cases[0].keys())


# ----------------------------------------------------------------------
# TEST 10: Existing account-swap causal linkage remains intact.
# ----------------------------------------------------------------------
def test_account_swap_causal_linkage_intact_on_real_data(real_alerts_and_cases):
    """Regression guard (per checkpoint Section 3/9): every detected
    account_swap alert must be anchored to a real transaction belonging to
    that same account, and must have fired from at least two independent
    rule families (never a single weak signal) - the causal chain connecting
    device/geo/transaction/beneficiary for one account_id, which this
    checkpoint did not touch and must not have broken."""
    store, alerts, _ = real_alerts_and_cases
    swap_alerts = [a for a in alerts if a["typology"] == "account_swap"]
    assert swap_alerts, "expected at least one account_swap alert on the checked-in mock_data/ fixture"
    for a in swap_alerts:
        txn = store.txn_by_id.get(a["transaction_id"])
        assert txn is not None, f"{a['alert_id']} anchors to a transaction not present in the DataStore"
        assert txn["sender_account_id"] == a["account_id"], \
            "account_swap alert's anchor transaction must be sent BY the flagged account"
        assert len(a["triggering_rules"]) >= 2, \
            "account_swap must never trigger on a single weak signal (AS-008 or >=2 rule families)"