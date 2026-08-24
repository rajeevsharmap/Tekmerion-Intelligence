"""
langgraph_orchestration.py
=============================
CHECKPOINT 7 - LangGraph multi-agent orchestration layer over the
existing deterministic investigation pipeline.

### What this module is, and is not ###
This is an ORCHESTRATION layer, not a new decision-making system. Every
node below wraps exactly one already-existing, already-tested
deterministic function from Checkpoints 2-6
(`network_layer.generate_network_evidence`, `evidence_model.
build_evidence_items`/`compute_completeness`, `authority_policy.
assess_authority`, `jurisdiction.determine_case_jurisdiction`,
`regulatory_rules.evaluate_compliance_rules`, `investigation_auditor.
audit_investigation`, `case_completeness.compute_case_completeness`,
`regather_loop.run_regather_loop`, `action_pipeline.CaseActionLayer`
which itself sequences `next_best_action`/`audit_trail`/`case_state`/
`investigator_action`/`case_memory`/`sar_report`). No node recomputes,
duplicates, or second-guesses what that function already decided; a node
that received a bad/incomplete answer from its wrapped function returns
that answer as-is (visibly missing/incomplete), it never fabricates a
better one.

This mirrors exactly the sequence `run_pipeline.py` already runs
per-case, in one Python function; the only two things this module adds
on top of that are (1) an explicit graph state schema
(`InvestigationGraphState`) shaped for a real investigation UI/API and
(2) `langgraph`'s `StateGraph` as the execution engine, so the pipeline
is expressed as inspectable nodes/edges instead of one long function
body. It does NOT replace `run_pipeline.py` - `run_pipeline.py` remains
the fast, non-LangGraph batch path used to regenerate
`pipeline_output/`; this module is the per-case, UI/API-facing path used
by `main.py`'s new endpoints.

### LLM usage boundary (Phase 11) ###
This module makes NO LLM calls and imports no LLM client. The
repository's `agents/evidence_builder.py`,
`agents/{scammer,legitimate}_hypothesis_agent.py`, and
`agents/contradiction_agent.py` ARE LLM-assisted, but a repository audit
(see docs/backend_implementation_status.md) confirmed they are wired
only into `eval_pipeline.py` (an offline evaluation path, itself not yet
implemented - `eval_pipeline.py` is empty), never into the live
`run_pipeline.py` chain. This checkpoint deliberately does not change
that: wiring an LLM-assisted "Investigation Agent" into a graph that
Phase 11 requires to never let an LLM decide jurisdiction, regulatory
applicability, authority, completeness, evidence provenance, or whether
a transaction/SAR fact is true, would risk exactly the "LLM-driven black
box" the task instructions prohibit, especially with no reliable way in
this environment to guarantee the LLM call is sandboxed from those
decisions. The "Investigation Agent" node below therefore maps to the
deterministic evidence-synthesis step that already exists
(`evidence_model.build_evidence_items`) rather than to the LLM agents/
modules, which remain untouched, unwired narrative/summarization tooling
available for a future, explicitly-scoped narrative-only integration.

### Human review boundary (Phase 12) ###
HUMAN_REVIEW is a real graph state, not a simulated node response. The
graph's automated nodes run through to the point `action_pipeline.
CaseActionLayer` places the case in `case_state.HUMAN_REVIEW` (or leaves
it in `INVESTIGATING` if evidence is still incomplete after the bounded
re-gather below) and then the graph ENDS - no node "plays investigator".
`resume_after_human_review()` at the bottom of this module is the only
way the case advances past that point, and it does so by calling the
exact same `CaseActionLayer.complete_human_review`/`submit_action`
methods Checkpoint 6 already built and tested; this module adds no new
authorization logic.
"""
from typing import Any, Dict, List, Optional, TypedDict

from data_store import DataStore
from detection_layer import run_detection_pipeline, bundle_alerts_into_cases
from network_layer import generate_network_evidence, wrap_as_evidence
from evidence_model import build_evidence_items, compute_completeness
from authority_policy import assess_authority, AUTHORITY_POLICY
from jurisdiction import determine_case_jurisdiction
from regulatory_rules import evaluate_compliance_rules
from investigation_auditor import audit_investigation
from case_completeness import compute_case_completeness
from regather_loop import run_regather_loop
from action_pipeline import CaseActionLayer
import case_state as cs

MAX_REGATHER_HOPS = 1  # bounded re-gather - see route_after_completeness()


# ----------------------------------------------------------------------
# Phase 4: explicit graph state schema
# ----------------------------------------------------------------------
class InvestigationGraphState(TypedDict, total=False):
    # CASE
    case_id: str
    alert_ids: List[str]
    case_status: str
    typology: str
    case: Dict[str, Any]
    case_alerts: List[Dict[str, Any]]

    # EVIDENCE
    network_evidence: Dict[str, Any]      # raw generate_network_evidence() output
    evidence: Dict[str, Any]              # wrap_as_evidence() Evidence Store record
    evidence_items: List[Dict[str, Any]]
    completeness: Dict[str, Any]

    # NETWORK (graph-ready; see case_data_access.ScopedDataAccess.get_related_network
    # for the investigator-scoped view served to the API/agents)
    graph: Optional[Dict[str, Any]]

    # REGULATORY / AUDIT
    jurisdiction: Dict[str, Any]
    regulatory_findings: List[Dict[str, Any]]
    auditor: Dict[str, Any]
    case_completeness: Dict[str, Any]
    regather: Optional[Dict[str, Any]]
    regather_hops: int

    # ACTION / LIFECYCLE
    authority: Dict[str, Any]
    next_best_action: Dict[str, Any]
    case_state: str
    audit_trail: List[Dict[str, Any]]
    case_memory: Dict[str, Any]
    sar_report: Optional[Dict[str, Any]]

    # internal handle - not JSON-serialized for the API; the CaseActionLayer
    # instance is kept so resume_after_human_review() can call its methods
    # without rebuilding state from scratch.
    _action_layer: Any


# ----------------------------------------------------------------------
# Phase 10: node functions - one per orchestration responsibility.
# Each node takes and returns an InvestigationGraphState dict, mutating
# only the keys it owns, per LangGraph's state-merge convention.
# ----------------------------------------------------------------------
def case_intake_node(state: InvestigationGraphState) -> dict:
    """Loads already-produced case context (case dict + its own alerts).
    Gathers nothing new: `case`/`case_alerts` must already be present in
    the incoming state (built by the caller from run_pipeline.py's own
    detection/bundling output, or from a persisted pipeline_output/
    case+alert lookup) - this node never re-runs detection or bundling."""
    case = state["case"]
    return {
        "case_id": case["case_id"],
        "alert_ids": case.get("alert_ids", []),
        "case_status": case.get("status"),
        "typology": case.get("primary_trigger"),
    }


def evidence_agent_node(state: InvestigationGraphState, store: DataStore) -> dict:
    """Gathers authorized evidence for the case via the existing
    deterministic network layer - no unrestricted dataset access; this
    node only ever touches the one case passed in."""
    case = state["case"]
    network_evidence = generate_network_evidence(store, case)
    evidence = wrap_as_evidence(network_evidence)
    return {"network_evidence": network_evidence, "evidence": evidence}


def network_agent_node(state: InvestigationGraphState) -> dict:
    """Exposes the reconstructed network in a frontend-ready structure
    (Phase 8) - never fabricates edges; graph-based typologies get real
    nodes/edges, timeline typologies get `graph: None` (there is no graph
    to show, and this node does not force one into existence)."""
    data = state["evidence"].get("data", {})
    if "nodes" in data and "edges" in data:
        graph = {
            "nodes": data["nodes"],
            "edges": data["edges"],
            "metadata": {
                "typology": state["evidence"]["typology"],
                "visualization_type": data.get("visualization_type"),
                "root_account": data.get("root_account"),
                "max_depth": data.get("max_depth"),
            },
        }
    else:
        graph = None
    return {"graph": graph}


def investigation_agent_node(state: InvestigationGraphState, store: DataStore) -> dict:
    """Synthesizes gathered evidence into typed, weighted evidence items
    and a deterministic completeness score - the existing
    `evidence_model.py` logic (Checkpoint 2), not an LLM. This is the
    node the conceptual "Investigation Agent" in the task brief maps to;
    see the module docstring for why the LLM-based agents/ modules are
    NOT used here."""
    case = state["case"]
    network_evidence = state["network_evidence"]
    evidence_items = build_evidence_items(store, case, network_evidence)
    completeness = compute_completeness(evidence_items)
    evidence = dict(state["evidence"])
    evidence["evidence_items"] = evidence_items
    evidence["completeness"] = completeness
    return {"evidence_items": evidence_items, "completeness": completeness, "evidence": evidence}


def jurisdiction_agent_node(state: InvestigationGraphState, store: DataStore) -> dict:
    case = state["case"]
    account = store.accounts_by_id.get(case["account_id"])
    jurisdiction_context = determine_case_jurisdiction(
        case, net=state["network_evidence"], account=account, store=store,
    )
    return {"jurisdiction": jurisdiction_context}


def regulatory_agent_node(state: InvestigationGraphState, store: DataStore) -> dict:
    case = state["case"]
    account = store.accounts_by_id.get(case["account_id"])
    regulatory_findings = evaluate_compliance_rules(
        case, state["evidence_items"], state["completeness"],
        net=state["network_evidence"], account=account, store=store,
        jurisdiction_context=state["jurisdiction"],
    )
    return {"regulatory_findings": regulatory_findings}


def auditor_node(state: InvestigationGraphState, store: DataStore) -> dict:
    case = state["case"]
    account = store.accounts_by_id.get(case["account_id"])
    authority_decision = state.get("authority")
    if authority_decision is None:
        case_alerts = state.get("case_alerts", [])
        authority_decision = assess_authority(
            case, state["evidence_items"], state["completeness"],
            net=state["network_evidence"], account=account, case_alerts=case_alerts,
        )
    auditor_result = audit_investigation(
        case, state["evidence_items"], state["completeness"],
        net=state["network_evidence"], account=account,
        regulatory_findings=state["regulatory_findings"], authority_decision=authority_decision,
        structural_gap_reasons=AUTHORITY_POLICY.get("structural_gap_reasons", ()),
        jurisdiction_context=state["jurisdiction"],
    )
    return {"auditor": auditor_result, "authority": authority_decision}


def completeness_agent_node(state: InvestigationGraphState) -> dict:
    case = state["case"]
    case_completeness = compute_case_completeness(
        case, state["evidence_items"], state["completeness"],
        regulatory_findings=state["regulatory_findings"], auditor_result=state["auditor"],
        structural_gap_reasons=AUTHORITY_POLICY.get("structural_gap_reasons", ()),
        jurisdiction_context=state["jurisdiction"],
    )
    return {"case_completeness": case_completeness}


def regather_agent_node(state: InvestigationGraphState, store: DataStore) -> dict:
    """Bounded evidence recovery - never loops unboundedly (see
    `route_after_completeness` for the `MAX_REGATHER_HOPS` bound). Re-runs
    only the downstream stages that depend on evidence/completeness,
    exactly as run_pipeline.py already does; never re-runs
    detection/case-bundling."""
    case = state["case"]
    account = store.accounts_by_id.get(case["account_id"])
    regather_result = run_regather_loop(
        store, case, state["evidence_items"], state["completeness"],
        structural_gap_reasons=AUTHORITY_POLICY.get("structural_gap_reasons", ()),
    )
    evidence_items = state["evidence_items"]
    completeness = state["completeness"]
    network_evidence = state["network_evidence"]
    if regather_result["final_disposition"] != "no_regather_needed":
        evidence_items = regather_result["final_evidence_items"]
        completeness = regather_result["final_completeness"]
        if regather_result["final_net"] is not None:
            network_evidence = regather_result["final_net"]

    regulatory_findings = evaluate_compliance_rules(
        case, evidence_items, completeness, net=network_evidence, account=account,
        store=store, jurisdiction_context=state["jurisdiction"],
    )
    auditor_result = audit_investigation(
        case, evidence_items, completeness, net=network_evidence, account=account,
        regulatory_findings=regulatory_findings, authority_decision=state.get("authority"),
        structural_gap_reasons=AUTHORITY_POLICY.get("structural_gap_reasons", ()),
        jurisdiction_context=state["jurisdiction"],
    )
    case_completeness = compute_case_completeness(
        case, evidence_items, completeness, regulatory_findings=regulatory_findings,
        auditor_result=auditor_result, structural_gap_reasons=AUTHORITY_POLICY.get("structural_gap_reasons", ()),
        jurisdiction_context=state["jurisdiction"],
    )
    evidence = dict(state["evidence"])
    evidence["evidence_items"] = evidence_items
    evidence["completeness"] = completeness
    return {
        "evidence": evidence,
        "evidence_items": evidence_items,
        "completeness": completeness,
        "network_evidence": network_evidence,
        "regulatory_findings": regulatory_findings,
        "auditor": auditor_result,
        "case_completeness": case_completeness,
        "regather": regather_result,
        "regather_hops": state.get("regather_hops", 0) + 1,
    }


def authority_action_node(state: InvestigationGraphState) -> dict:
    """Builds the final per-case evidence dict exactly as run_pipeline.py
    does, then delegates Next-Best-Action -> Audit Trail -> Human Review
    queueing -> Case Memory to `action_pipeline.CaseActionLayer`
    (Checkpoint 6, unmodified). This node does not decide authority - it
    only assembles the already-decided pieces for CaseActionLayer, which
    itself never re-decides authority either (it consumes
    evidence["authority"])."""
    case = state["case"]
    evidence = dict(state["evidence"])
    evidence.update({
        "evidence_items": state["evidence_items"],
        "completeness": state["completeness"],
        "authority": state["authority"],
        "jurisdiction": state["jurisdiction"],
        "regulatory_findings": state["regulatory_findings"],
        "auditor": state["auditor"],
        "case_completeness": state["case_completeness"],
        "regather": state.get("regather"),
    })
    layer = CaseActionLayer(case, evidence, case_alerts=state.get("case_alerts", []))
    return {
        "next_best_action": layer.recommendation,
        "case_state": layer.state,
        "audit_trail": layer.trail.to_list(),
        "case_memory": layer.memory,
        "sar_report": layer.sar_report,
        "_action_layer": layer,
    }


# ----------------------------------------------------------------------
# Phase 3 conditional routing
# ----------------------------------------------------------------------
def route_after_completeness(state: InvestigationGraphState) -> str:
    """'evidence complete?' branch. Bounded: a case that is STILL
    incomplete after MAX_REGATHER_HOPS proceeds to authority/action
    anyway, visibly incomplete (case_completeness["status"] stays
    "incomplete") rather than looping forever - this matches
    `action_pipeline.CaseActionLayer._advance_to_review_or_hold`, which
    already keeps such a case in INVESTIGATING rather than queuing a
    fabricated human review."""
    if state["case_completeness"]["status"] == "complete":
        return "authority_action"
    if state.get("regather_hops", 0) >= MAX_REGATHER_HOPS:
        return "authority_action"
    return "regather"


# ----------------------------------------------------------------------
# Phase 3/4: graph construction. langgraph is imported lazily inside this
# function (not at module import time) so every node function above, and
# case_data_access.py, remain importable/unit-testable in an environment
# without the `langgraph` package installed - only *building/running the
# compiled graph* requires it.
# ----------------------------------------------------------------------
def build_graph(store: DataStore):
    """Compiles the CASE -> ... -> AUTHORITY_ACTION LangGraph described in
    the task brief. Returns a compiled langgraph graph; callers invoke it
    with `graph.invoke({"case": case, "case_alerts": case_alerts})` and
    inspect the returned InvestigationGraphState. Execution always stops
    at HUMAN_REVIEW/INVESTIGATING (whichever `authority_action_node`
    lands on) - see `resume_after_human_review()` for what happens next."""
    from langgraph.graph import StateGraph, END

    graph = StateGraph(InvestigationGraphState)

    graph.add_node("case_intake", case_intake_node)
    graph.add_node("evidence_agent", lambda s: evidence_agent_node(s, store))
    graph.add_node("network_agent", network_agent_node)
    graph.add_node("investigation_agent", lambda s: investigation_agent_node(s, store))
    graph.add_node("jurisdiction_agent", lambda s: jurisdiction_agent_node(s, store))
    graph.add_node("regulatory_agent", lambda s: regulatory_agent_node(s, store))
    graph.add_node("auditor", lambda s: auditor_node(s, store))
    graph.add_node("completeness", completeness_agent_node)
    graph.add_node("regather", lambda s: regather_agent_node(s, store))
    graph.add_node("authority_action", authority_action_node)

    graph.set_entry_point("case_intake")
    graph.add_edge("case_intake", "evidence_agent")
    graph.add_edge("evidence_agent", "network_agent")
    graph.add_edge("network_agent", "investigation_agent")
    graph.add_edge("investigation_agent", "jurisdiction_agent")
    graph.add_edge("jurisdiction_agent", "regulatory_agent")
    graph.add_edge("regulatory_agent", "auditor")
    graph.add_edge("auditor", "completeness")
    graph.add_conditional_edges(
        "completeness", route_after_completeness,
        {"regather": "regather", "authority_action": "authority_action"},
    )
    # re-evaluated evidence loops back through completeness, bounded by
    # MAX_REGATHER_HOPS in route_after_completeness (never unconditional).
    graph.add_edge("regather", "completeness")
    graph.add_edge("authority_action", END)

    return graph.compile()


def run_case_graph(store: DataStore, case: Dict[str, Any], case_alerts: List[Dict[str, Any]] = None):
    """Convenience entry point: build+invoke the graph for a single case.
    Returns the final InvestigationGraphState (minus the internal
    `_action_layer` handle, which callers needing to resume human review
    should get via `run_case_graph_with_layer` instead)."""
    graph = build_graph(store)
    result = graph.invoke({"case": case, "case_alerts": case_alerts or [], "regather_hops": 0})
    return result


def resume_after_human_review(state: InvestigationGraphState, reviewer_id, investigator_decision,
                               decision_reason, investigator_id, requested_action, action_reason,
                               override_reason=None):
    """The ONLY way a case advances past HUMAN_REVIEW (Phase 12). Calls
    the exact same `CaseActionLayer` methods Checkpoint 6 already built;
    this function adds no new authorization/decision logic of its own."""
    layer = state.get("_action_layer")
    if layer is None:
        raise ValueError("state has no attached CaseActionLayer - was it produced by run_case_graph()?")
    layer.complete_human_review(reviewer_id, investigator_decision, decision_reason)
    action_record = layer.submit_action(investigator_id, requested_action, action_reason,
                                         override_reason=override_reason)
    updated = dict(state)
    updated.update({
        "case_state": layer.state,
        "audit_trail": layer.trail.to_list(),
        "case_memory": layer.memory,
        "sar_report": layer.sar_report,
        "_action_layer": layer,
    })
    return updated, action_record


def serializable_state(state: InvestigationGraphState) -> dict:
    """Strips the internal `_action_layer` handle before the state is
    returned over the API / persisted / logged - it is a live Python
    object (holding an AuditTrail instance), never JSON-serializable and
    never meant to leave this process."""
    return {k: v for k, v in state.items() if k != "_action_layer"}