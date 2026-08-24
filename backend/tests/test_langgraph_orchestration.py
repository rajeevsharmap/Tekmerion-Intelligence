"""
tests/test_langgraph_orchestration.py
=========================================
CHECKPOINT 7 - dedicated tests for langgraph_orchestration.py.

Audited state at the start of this session: tests/test_checkpoint7.py
imports only sar_report/case_memory/action_pipeline/case_state - it has
NO coverage of langgraph_orchestration.py at all. This file fills that
gap for everything that is possible to verify WITHOUT the `langgraph`
package installed.

Environment note (see docs/backend_implementation_status.md): this
sandbox has no network access and the repository's checked-in `venv/`
is a Windows virtualenv (compiled `.pyd` extensions for
`pydantic_core`, which `langgraph`/`langchain_core` import transitively).
`import langgraph.graph` therefore fails in THIS environment with
`ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'`.
That failure is a platform/packaging issue, not a code defect - the
same `venv/` was built for and presumably verified on Windows per the
prior session's own documented invocation notes.

Because `langgraph_orchestration.py` lazily imports `langgraph` only
inside `build_graph()` (by explicit design - see that function's
docstring), every node function, the routing function, and
`resume_after_human_review()` are plain Python and fully testable here
without the `langgraph` package. What CANNOT be verified in this
environment is the actual `langgraph.graph.StateGraph` build/compile/
invoke machinery itself - `test_build_graph_requires_langgraph_package`
below documents that gap explicitly rather than silently skipping it.

To approximate a real graph invocation without the `langgraph` package,
`_run_case_manually()` below chains the same node functions in the same
order/routing `build_graph()` wires up. This exercises the real
business logic (evidence, jurisdiction, regulatory, auditor,
completeness, bounded re-gather, authority/action) end-to-end against
real pipeline_output data; it does not exercise LangGraph's own
execution engine.
"""
import glob
import json
import os

import pytest

import case_state as cs
from data_store import DataStore
from langgraph_orchestration import (
    InvestigationGraphState,
    MAX_REGATHER_HOPS,
    case_intake_node,
    evidence_agent_node,
    network_agent_node,
    investigation_agent_node,
    jurisdiction_agent_node,
    regulatory_agent_node,
    auditor_node,
    completeness_agent_node,
    regather_agent_node,
    authority_action_node,
    route_after_completeness,
    resume_after_human_review,
    serializable_state,
)
from investigator_action import OverrideReasonRequiredError

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK_DATA_DIR = os.path.join(BACKEND_DIR, "mock_data")
PIPELINE_OUT_DIR = os.path.join(BACKEND_DIR, "pipeline_output")
EVIDENCE_DIR = os.path.join(PIPELINE_OUT_DIR, "evidence")

pytestmark = pytest.mark.skipif(
    not os.path.isdir(EVIDENCE_DIR),
    reason="pipeline_output/evidence not present - run run_pipeline.py first",
)


def _load_real_case(index=0):
    store = DataStore(MOCK_DATA_DIR)
    cases = {c["case_id"]: c for c in json.load(open(os.path.join(PIPELINE_OUT_DIR, "cases.json")))}
    files = sorted(glob.glob(os.path.join(EVIDENCE_DIR, "*.json")))
    evidence = json.load(open(files[index]))
    case = cases[evidence["case_id"]]
    return store, case


def _run_case_manually(store, case, case_alerts=None):
    """Chains the same node functions build_graph() wires up, in the
    same order/conditional-routing, without requiring the `langgraph`
    package. See module docstring."""
    state: InvestigationGraphState = {"case": case, "case_alerts": case_alerts or [], "regather_hops": 0}
    state.update(case_intake_node(state))
    state.update(evidence_agent_node(state, store))
    state.update(network_agent_node(state))
    state.update(investigation_agent_node(state, store))
    state.update(jurisdiction_agent_node(state, store))
    state.update(regulatory_agent_node(state, store))
    state.update(auditor_node(state, store))
    state.update(completeness_agent_node(state))

    hops = 0
    while True:
        route = route_after_completeness(state)
        if route == "authority_action":
            break
        assert hops < MAX_REGATHER_HOPS, "route_after_completeness must be bounded by MAX_REGATHER_HOPS"
        state.update(regather_agent_node(state, store))
        state.update(completeness_agent_node(state))
        hops += 1

    state.update(authority_action_node(state))
    return state


# ----------------------------------------------------------------------
# Individual node functions against real case data
# ----------------------------------------------------------------------

def test_case_intake_node_never_invents_case_data():
    store, case = _load_real_case(0)
    out = case_intake_node({"case": case})
    assert out["case_id"] == case["case_id"]
    assert out["typology"] == case.get("primary_trigger")
    assert out["alert_ids"] == case.get("alert_ids", [])


def test_evidence_agent_node_produces_real_network_evidence():
    store, case = _load_real_case(0)
    out = evidence_agent_node({"case": case}, store)
    assert "network_evidence" in out and "evidence" in out
    assert out["evidence"]["typology"] in ("money_mule", "smurfing", "account_swap", "reverse_smurfing")


def test_network_agent_node_none_for_timeline_typology_real_graph_for_network_typology():
    store, cases_by_id = DataStore(MOCK_DATA_DIR), {c["case_id"]: c for c in json.load(open(os.path.join(PIPELINE_OUT_DIR, "cases.json")))}
    files = sorted(glob.glob(os.path.join(EVIDENCE_DIR, "*.json")))
    seen_none = seen_graph = False
    for path in files:
        evidence = json.load(open(path))
        case = cases_by_id.get(evidence["case_id"])
        if not case:
            continue
        state = {"evidence": evidence}
        out = network_agent_node(state)
        if case["primary_trigger"] in ("smurfing", "reverse_smurfing"):
            if out["graph"] is not None:
                seen_graph = True
        else:
            if out["graph"] is None:
                seen_none = True
    assert seen_graph, "expected at least one graph-based typology to yield a real graph"
    assert seen_none, "expected at least one timeline typology to yield graph=None (never fabricated)"


def test_route_after_completeness_routes_complete_to_authority_action():
    state = {"case_completeness": {"status": "complete"}, "regather_hops": 0}
    assert route_after_completeness(state) == "authority_action"


def test_route_after_completeness_routes_incomplete_under_bound_to_regather():
    state = {"case_completeness": {"status": "incomplete"}, "regather_hops": 0}
    assert route_after_completeness(state) == "regather"


def test_route_after_completeness_bounded_at_max_hops_proceeds_anyway():
    """Even a case that is STILL incomplete after MAX_REGATHER_HOPS must
    proceed to authority_action rather than loop forever."""
    state = {"case_completeness": {"status": "incomplete"}, "regather_hops": MAX_REGATHER_HOPS}
    assert route_after_completeness(state) == "authority_action"


# ----------------------------------------------------------------------
# Full manual chain against real cases (approximates graph invocation)
# ----------------------------------------------------------------------

def test_manual_chain_reaches_human_review_or_investigating_never_beyond():
    """No node may play investigator: the chain must stop at HUMAN_REVIEW
    (complete evidence) or INVESTIGATING (still incomplete after the
    bounded re-gather), never at an executed/closed state."""
    store, case = _load_real_case(0)
    state = _run_case_manually(store, case)
    assert state["case_state"] in (cs.HUMAN_REVIEW, cs.INVESTIGATING)


def test_manual_chain_regather_hops_never_exceeds_bound():
    store = DataStore(MOCK_DATA_DIR)
    cases = {c["case_id"]: c for c in json.load(open(os.path.join(PIPELINE_OUT_DIR, "cases.json")))}
    files = sorted(glob.glob(os.path.join(EVIDENCE_DIR, "*.json")))
    for path in files:
        evidence = json.load(open(path))
        case = cases.get(evidence["case_id"])
        if not case:
            continue
        state = _run_case_manually(store, case)
        assert state.get("regather_hops", 0) <= MAX_REGATHER_HOPS


_WALLCLOCK_KEYS = {"timestamp", "generated_at"}


def _strip_wallclock_timestamps(obj):
    """Recursively drop dict keys that record real wall-clock time
    ("timestamp" on audit events, "generated_at" on network evidence).
    Those legitimately differ between two otherwise identical invocations
    run microseconds apart - everything else (IDs, evidence, routing,
    case_state, recommendations, ...) must still match exactly for the
    pipeline to be considered deterministic."""
    if isinstance(obj, dict):
        return {k: _strip_wallclock_timestamps(v) for k, v in obj.items() if k not in _WALLCLOCK_KEYS}
    if isinstance(obj, list):
        return [_strip_wallclock_timestamps(v) for v in obj]
    return obj


def test_manual_chain_deterministic_repeated_invocation():
    store, case = _load_real_case(0)
    s1 = _run_case_manually(store, case)
    s2 = _run_case_manually(store, case)
    ser1 = _strip_wallclock_timestamps(serializable_state(s1))
    ser2 = _strip_wallclock_timestamps(serializable_state(s2))
    assert json.dumps(ser1, sort_keys=True, default=str) == json.dumps(ser2, sort_keys=True, default=str)


def test_manual_chain_preserves_deterministic_case_id():
    store, case = _load_real_case(0)
    state = _run_case_manually(store, case)
    assert state["case_id"] == case["case_id"]
    assert state["evidence"]["case_id"] == case["case_id"] if "case_id" in state["evidence"] else True


def test_serializable_state_strips_internal_action_layer_handle():
    store, case = _load_real_case(0)
    state = _run_case_manually(store, case)
    ser = serializable_state(state)
    assert "_action_layer" not in ser
    json.dumps(ser, default=str)  # must not raise - confirms it's actually JSON-safe


# ----------------------------------------------------------------------
# Human review boundary + resume + authorization enforcement
# ----------------------------------------------------------------------

def _first_complete_case():
    store = DataStore(MOCK_DATA_DIR)
    cases = {c["case_id"]: c for c in json.load(open(os.path.join(PIPELINE_OUT_DIR, "cases.json")))}
    files = sorted(glob.glob(os.path.join(EVIDENCE_DIR, "*.json")))
    for path in files:
        evidence = json.load(open(path))
        case = cases.get(evidence["case_id"])
        if not case:
            continue
        state = _run_case_manually(store, case)
        if state["case_state"] == cs.HUMAN_REVIEW:
            return state
    return None


def test_resume_after_human_review_requires_attached_action_layer():
    with pytest.raises(ValueError):
        resume_after_human_review({}, "INV-S001", "confirmed_concern", "reason", "INV-S001", "MONITOR", "reason")


def test_junior_cannot_authorize_a_senior_only_action_via_resume():
    state = _first_complete_case()
    if state is None:
        pytest.skip("no case on this dataset reached HUMAN_REVIEW - see known dataset limitation")
    updated, action_record = resume_after_human_review(
        state, reviewer_id="INV-S001", investigator_decision="confirmed_concern",
        decision_reason="test: confirming for authorization boundary check",
        investigator_id="INV-J001", requested_action="BLOCK_TRANSACTION",
        action_reason="junior attempting a senior-gated action",
    )
    assert action_record["authorized"] is False
    assert updated["case_state"] != cs.CLOSED


def test_senior_can_authorize_action_via_resume_and_case_state_advances():
    state = _first_complete_case()
    if state is None:
        pytest.skip("no case on this dataset reached HUMAN_REVIEW - see known dataset limitation")
    # Authorize the action the system actually recommended for this case
    # (not a hardcoded guess) - that's what this test is verifying: a
    # senior CAN authorize, not the separate override-reason requirement
    # for deviating from the recommendation (covered below).
    recommended = state["next_best_action"]["recommended_action"]
    updated, action_record = resume_after_human_review(
        state, reviewer_id="INV-S001", investigator_decision="confirmed_concern",
        decision_reason="test: confirming for senior authorization check",
        investigator_id="INV-S001", requested_action=recommended,
        action_reason="senior authorizing the recommended action",
    )
    assert action_record["authorized"] is True
    assert updated["case_state"] in (cs.CLOSED, cs.ACTION_EXECUTED, cs.ESCALATED)


def test_resume_requires_override_reason_when_deviating_from_recommendation():
    state = _first_complete_case()
    if state is None:
        pytest.skip("no case on this dataset reached HUMAN_REVIEW - see known dataset limitation")
    recommended = state["next_best_action"]["recommended_action"]
    deviating_action = "MONITOR" if recommended != "MONITOR" else "CLEAR"
    with pytest.raises(OverrideReasonRequiredError):
        resume_after_human_review(
            state, reviewer_id="INV-S001", investigator_decision="confirmed_concern",
            decision_reason="test: deviation without override reason",
            investigator_id="INV-S001", requested_action=deviating_action,
            action_reason="senior deviating from recommendation, no override_reason supplied",
        )


def test_human_review_is_never_bypassed_by_manual_chain_alone():
    """The manual chain (mirroring the graph) must never itself reach a
    CLOSED/ACTION_EXECUTED state - only resume_after_human_review, acting
    on an explicit investigator decision, can advance a case that far."""
    store = DataStore(MOCK_DATA_DIR)
    cases = {c["case_id"]: c for c in json.load(open(os.path.join(PIPELINE_OUT_DIR, "cases.json")))}
    files = sorted(glob.glob(os.path.join(EVIDENCE_DIR, "*.json")))
    for path in files:
        evidence = json.load(open(path))
        case = cases.get(evidence["case_id"])
        if not case:
            continue
        state = _run_case_manually(store, case)
        assert state["case_state"] not in (cs.CLOSED, cs.ACTION_EXECUTED)


# ----------------------------------------------------------------------
# Environment gap - documented, not hidden
# ----------------------------------------------------------------------

def test_build_graph_requires_langgraph_package():
    """Documents (rather than silently skipping) that this sandbox
    cannot build/compile/invoke the real langgraph.StateGraph: the
    checked-in venv/ is Windows-only (compiled pydantic_core .pyd) and
    this environment has no network access to install a Linux build.
    XFAIL here is the honest signal - see
    docs/backend_implementation_status.md's Checkpoint 7 section."""
    store = DataStore(MOCK_DATA_DIR)
    from langgraph_orchestration import build_graph
    try:
        build_graph(store)
    except ModuleNotFoundError as e:
        pytest.xfail(f"langgraph package unusable in this sandbox: {e}")