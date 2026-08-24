"""
tests/test_llm_pii_sanitizer.py
==================================
CHECKPOINT 7 (LLM PII boundary) test suite: agents/llm_pii_sanitizer.py,
plus its wiring into the three actual external-LLM call sites
(agents/scammer_hypothesis_agent.py, agents/legitimate_hypothesis_agent.py,
agents/contradiction_agent.py - confirmed to be the ONLY three live
`client.models.generate_content` call sites in the codebase via
`grep -rn "generate_content" backend/`; no other module makes an
external LLM call, so no other boundary is needed).

Two kinds of tests here:
  1. Unit tests against agents/llm_pii_sanitizer.py directly - pure
     functions, zero external dependencies (stdlib `copy`/`hashlib`/`re`
     only), so these exercise the actual masking/pseudonymization logic
     with no mocking required.
  2. Integration tests against the two hypothesis agents - mock
     `client.models.generate_content` (the only network boundary in
     those modules) and assert on the ACTUAL outbound payload text,
     confirming raw PII never reaches the call, not just that the
     sanitizer function works in isolation.

contradiction_agent.py deliberately has NO test here for "does it mask
evidence" - it doesn't take an `evidence` dict at all (see its
signature: `resolve_contradiction(scammer_result, legitimate_result,
typology)`). It only receives the two hypothesis agents' own JSON
responses, which by construction can only reference masked pseudonyms
(the hypothesis agents never saw raw evidence themselves). There is
therefore no raw-evidence path into contradiction_agent to mask.

Ground-truth isolation: nothing in this file imports or references
mock_data/ground_truth_*.csv or any evaluation-only fixture - the
sanitizer operates purely on the evidence dict already handed to it by
evidence_builder.gather_evidence(), which itself has no ground-truth
access (see test_ground_truth_isolation.py).
"""
import copy
import json
from unittest import mock

import pytest

from agents import llm_pii_sanitizer as pii


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
def _evidence(**overrides):
    base = {
        "case_id": "CASE-T1",
        "account_id": "ACC001",
        "typology": "account_swap",
        "account_profile": {
            "risk_rating": "medium", "kyc_status": "verified",
            "account_type": "savings", "avg_monthly_txn_amount": 50000,
        },
        "detection_alerts": [{"alert_id": "ALERT-1", "typology": "account_swap"}],
        "network_evidence": {
            "nodes": ["ACC001", "ACC002"],
            "note": "funds moved from ACC001 to ACC002 via BENE005",
        },
        "beneficiaries": [
            {"beneficiary_id": "BENE005", "beneficiary_name": "Ramesh Kumar",
             "relationship_to_account_holder": "friend", "date_added": "2026-08-01",
             "is_first_time_beneficiary": True, "is_verified": True,
             "beneficiary_risk_flag": False, "total_transfers_to_date": 1},
        ],
        "devices": [
            {"device_id": "DEV002", "device_type": "mobile", "is_trusted_device": False,
             "sim_change_detected": True, "jailbroken_rooted": False},
        ],
        "geo_events": [
            {"geo_event_id": "GEO-9", "is_vpn_or_proxy": True, "registered_country_match": False,
             "distance_from_last_location_km": 4200.0},
        ],
        "recent_transactions": [
            {"transaction_id": "TXN-1", "sender_account_id": "ACC001",
             "receiver_account_id": "ACC002", "amount": 250000, "beneficiary_id": "BENE005",
             "device_id": "DEV002", "geo_event_id": "GEO-9"},
        ],
    }
    base.update(overrides)
    return base


def _derived_signals(**overrides):
    base = {
        "typology": "account_swap",
        "amount_anomalies": [{"transaction_id": "TXN-1", "amount": 250000, "ratio_to_account_baseline": 5.0}],
        "device_signals": [{"device_id": "DEV002", "sim_change_detected": True}],
        "beneficiary_signals": [{"beneficiary_id": "BENE005", "hours_since_added": 12.0}],
    }
    base.update(overrides)
    return base


def _contains_raw_id(text: str) -> bool:
    return bool(pii._ID_PATTERN.search(text))


# ----------------------------------------------------------------------
# 1. Unit tests: agents/llm_pii_sanitizer.py itself
# ----------------------------------------------------------------------
class TestSanitizeEvidenceForLLM:
    def test_no_raw_pii_in_masked_payload(self):
        ev = _evidence()
        masked, _ = pii.sanitize_evidence_for_llm(ev)
        dumped = json.dumps(masked, default=str)
        assert not _contains_raw_id(dumped)
        assert "Ramesh Kumar" not in dumped

    def test_original_evidence_not_mutated(self):
        ev = _evidence()
        original = copy.deepcopy(ev)
        pii.sanitize_evidence_for_llm(ev)
        assert ev == original

    def test_deterministic_pseudonyms_repeated_execution(self):
        ev = _evidence()
        masked1, map1 = pii.sanitize_evidence_for_llm(ev)
        masked2, map2 = pii.sanitize_evidence_for_llm(ev)
        assert masked1 == masked2
        assert map1 == map2

    def test_same_source_value_maps_consistently_within_payload(self):
        # ACC001 appears in account_id, network_evidence.nodes, and
        # recent_transactions[0].sender_account_id - all three must
        # resolve to the identical pseudonym.
        ev = _evidence()
        masked, mapping = pii.sanitize_evidence_for_llm(ev)
        expected = mapping["ACC001"]
        assert masked["account_id"] == expected
        assert masked["network_evidence"]["nodes"][0] == expected
        assert masked["recent_transactions"][0]["sender_account_id"] == expected
        # and appears (masked) inside the free-text note too
        assert expected in masked["network_evidence"]["note"]

    def test_different_values_do_not_collide(self):
        ev = _evidence()
        _, mapping = pii.sanitize_evidence_for_llm(ev)
        assert mapping["ACC001"] != mapping["ACC002"]
        assert len(set(mapping.values())) == len(mapping)

    def test_nested_evidence_is_sanitized(self):
        # network_evidence is an arbitrarily-nested dict/list structure;
        # confirm masking recurses into it, not just top-level fields.
        ev = _evidence(network_evidence={
            "paths": [{"hops": [{"account_id": "ACC001"}, {"account_id": "ACC002"}]}],
        })
        masked, mapping = pii.sanitize_evidence_for_llm(ev)
        assert masked["network_evidence"]["paths"][0]["hops"][0]["account_id"] == mapping["ACC001"]
        assert masked["network_evidence"]["paths"][0]["hops"][1]["account_id"] == mapping["ACC002"]

    def test_missing_or_null_values_remain_valid(self):
        ev = _evidence(network_evidence=None)
        ev["beneficiaries"][0]["beneficiary_name"] = None
        masked, _ = pii.sanitize_evidence_for_llm(ev)
        assert masked["network_evidence"] is None
        assert masked["beneficiaries"][0]["beneficiary_name"] is None

    def test_none_evidence_returns_none(self):
        masked, mapping = pii.sanitize_evidence_for_llm(None)
        assert masked is None
        assert mapping == {}

    def test_non_pii_analytical_fields_remain_intact(self):
        ev = _evidence()
        masked, _ = pii.sanitize_evidence_for_llm(ev)
        assert masked["typology"] == "account_swap"
        assert masked["account_profile"]["risk_rating"] == "medium"
        assert masked["account_profile"]["avg_monthly_txn_amount"] == 50000
        assert masked["recent_transactions"][0]["amount"] == 250000
        assert masked["devices"][0]["sim_change_detected"] is True
        assert masked["geo_events"][0]["is_vpn_or_proxy"] is True

    def test_beneficiary_name_reuses_sibling_id_pseudonym(self):
        ev = _evidence()
        masked, mapping = pii.sanitize_evidence_for_llm(ev)
        assert masked["beneficiaries"][0]["beneficiary_name"] == mapping["BENE005"]

    def test_beneficiary_name_without_sibling_id_gets_hash_pseudonym(self):
        ev = {"beneficiary_name": "Jane Doe"}
        masked, mapping = pii.sanitize_evidence_for_llm(ev)
        assert masked["beneficiary_name"].startswith("PERSON_")
        assert masked["beneficiary_name"] == pii._pseudonym_for_name("Jane Doe")
        # no raw id existed, so the id map is empty
        assert mapping == {}

    def test_two_different_names_get_different_pseudonyms(self):
        ev1 = {"beneficiary_name": "Jane Doe"}
        ev2 = {"beneficiary_name": "John Smith"}
        m1, _ = pii.sanitize_evidence_for_llm(ev1)
        m2, _ = pii.sanitize_evidence_for_llm(ev2)
        assert m1["beneficiary_name"] != m2["beneficiary_name"]

    def test_resolve_pseudonym_round_trips(self):
        ev = _evidence()
        _, mapping = pii.sanitize_evidence_for_llm(ev)
        pseudonym = mapping["ACC001"]
        assert pii.resolve_pseudonym(pseudonym, mapping) == "ACC001"

    def test_resolve_pseudonym_unknown_returns_none(self):
        assert pii.resolve_pseudonym("ACCOUNT_999", {}) is None


class TestSanitizePairForLLM:
    def test_evidence_and_signals_share_one_pseudonym_map(self):
        ev = _evidence()
        signals = _derived_signals()
        masked_ev, masked_signals, mapping = pii.sanitize_pair_for_llm(ev, signals)
        # ACC/BENE/DEV ids referenced in both halves resolve identically
        assert masked_ev["recent_transactions"][0]["device_id"] == mapping["DEV002"]
        assert masked_signals["device_signals"][0]["device_id"] == mapping["DEV002"]
        assert masked_ev["beneficiaries"][0]["beneficiary_id"] == mapping["BENE005"]
        assert masked_signals["beneficiary_signals"][0]["beneficiary_id"] == mapping["BENE005"]

    def test_no_raw_pii_in_either_masked_half(self):
        ev = _evidence()
        signals = _derived_signals()
        masked_ev, masked_signals, _ = pii.sanitize_pair_for_llm(ev, signals)
        assert not _contains_raw_id(json.dumps(masked_ev, default=str))
        assert not _contains_raw_id(json.dumps(masked_signals, default=str))

    def test_neither_original_object_is_mutated(self):
        ev, signals = _evidence(), _derived_signals()
        ev_copy, signals_copy = copy.deepcopy(ev), copy.deepcopy(signals)
        pii.sanitize_pair_for_llm(ev, signals)
        assert ev == ev_copy
        assert signals == signals_copy

    def test_none_evidence_or_signals_pass_through_as_none(self):
        masked_ev, masked_signals, mapping = pii.sanitize_pair_for_llm(None, None)
        assert masked_ev is None
        assert masked_signals is None
        assert mapping == {}


# ----------------------------------------------------------------------
# 2. Integration tests: hypothesis agents actually use the sanitized
#    payload for the real outbound LLM call, not just the ability to
#    call sanitize_pair_for_llm() somewhere unused.
# ----------------------------------------------------------------------
def _mock_genai_response(payload: dict):
    resp = mock.Mock()
    resp.text = json.dumps(payload)
    return resp


class TestHypothesisAgentsUseSanitizedPayload:
    def test_scammer_agent_sends_no_raw_pii_to_llm(self):
        from agents import scammer_hypothesis_agent as agent

        fake_response = _mock_genai_response(
            {"hypothesis": "scammer", "confidence": 80, "narrative": "test",
             "supporting_evidence": ["sim change detected"]})
        with mock.patch.object(agent.client.models, "generate_content", return_value=fake_response) as m:
            agent.evaluate_scammer_hypothesis(_evidence())

        assert m.called
        sent_contents = m.call_args.kwargs.get("contents") or m.call_args.args[0]
        assert not _contains_raw_id(sent_contents)
        assert "Ramesh Kumar" not in sent_contents

    def test_legitimate_agent_sends_no_raw_pii_to_llm(self):
        from agents import legitimate_hypothesis_agent as agent

        fake_response = _mock_genai_response(
            {"hypothesis": "legitimate", "confidence": 60, "narrative": "test",
             "supporting_evidence": ["established beneficiary"]})
        with mock.patch.object(agent.client.models, "generate_content", return_value=fake_response) as m:
            agent.evaluate_legitimate_hypothesis(_evidence())

        assert m.called
        sent_contents = m.call_args.kwargs.get("contents") or m.call_args.args[0]
        assert not _contains_raw_id(sent_contents)
        assert "Ramesh Kumar" not in sent_contents

    def test_scammer_agent_still_returns_the_llm_result_untouched(self):
        # sanitization must not corrupt/alter the agent's return contract
        from agents import scammer_hypothesis_agent as agent

        fake_response = _mock_genai_response(
            {"hypothesis": "scammer", "confidence": 91, "narrative": "n",
             "supporting_evidence": ["e1"]})
        with mock.patch.object(agent.client.models, "generate_content", return_value=fake_response):
            result = agent.evaluate_scammer_hypothesis(_evidence())

        assert result == {"hypothesis": "scammer", "confidence": 91, "narrative": "n",
                           "supporting_evidence": ["e1"]}

    def test_investigator_visible_evidence_object_is_the_real_one(self):
        # The raw `evidence` dict callers already hold (e.g. case storage,
        # audit trail, SAR generation) is the SAME object passed in - the
        # sanitizer only ever produces a separate copy for the LLM call,
        # it never replaces what the rest of the pipeline sees.
        from agents import scammer_hypothesis_agent as agent

        ev = _evidence()
        fake_response = _mock_genai_response(
            {"hypothesis": "scammer", "confidence": 50, "narrative": "n", "supporting_evidence": []})
        with mock.patch.object(agent.client.models, "generate_content", return_value=fake_response):
            agent.evaluate_scammer_hypothesis(ev)
        assert ev["beneficiaries"][0]["beneficiary_name"] == "Ramesh Kumar"
        assert ev["account_id"] == "ACC001"


class TestContradictionAgentHasNoRawEvidencePath:
    def test_resolve_contradiction_signature_takes_no_raw_evidence(self):
        import inspect

        from agents import contradiction_agent as agent

        params = list(inspect.signature(agent.resolve_contradiction).parameters)
        assert "evidence" not in params

    def test_resolve_contradiction_only_forwards_hypothesis_results(self):
        from agents import contradiction_agent as agent

        scammer_result = {"hypothesis": "scammer", "confidence": 80,
                           "narrative": "n", "supporting_evidence": ["e"]}
        legitimate_result = {"hypothesis": "legitimate", "confidence": 40,
                              "narrative": "n", "supporting_evidence": ["e"]}
        fake_response = _mock_genai_response(
            {"favored_hypothesis": "scammer", "confidence": 75,
             "reasoning": "r", "deciding_factor": "e"})
        with mock.patch.object(agent.client.models, "generate_content", return_value=fake_response) as m:
            agent.resolve_contradiction(scammer_result, legitimate_result, typology="account_swap")

        sent_contents = m.call_args.kwargs.get("contents") or m.call_args.args[0]
        # only what the two hypothesis agents already returned is forwarded -
        # no evidence/beneficiary/account payload of any kind
        assert "beneficiary" not in sent_contents.lower()
        assert "account_profile" not in sent_contents


# ----------------------------------------------------------------------
# 3. Ground-truth isolation
# ----------------------------------------------------------------------
def test_sanitizer_module_has_no_ground_truth_dependency():
    import inspect

    src = inspect.getsource(pii)
    assert "ground_truth" not in src
    assert "ground-truth" not in src.lower().replace("ground_truth", "")