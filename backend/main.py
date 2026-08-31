"""
main.py
=========
FastAPI application entry point.

CHECKPOINT 7 additions: a read-oriented investigation API over the
already-persisted `pipeline_output/` produced by `run_pipeline.py`
(alerts/cases/evidence, the latter including Checkpoint 4-7's
authority/regulatory/auditor/completeness/next_best_action/audit_trail/
case_memory/sar_report fields - see run_pipeline.py). Every GET endpoint
below reads that persisted JSON; none of them recompute anything.

Two human-in-the-loop endpoints (`POST /cases/{case_id}/human-review`,
`POST /cases/{case_id}/action`) are the only ones that mutate anything,
and they do so by rebuilding that case's `action_pipeline.CaseActionLayer`
from its persisted evidence, calling its existing, unmodified
`complete_human_review`/`submit_action` methods (Checkpoint 6), and then
re-persisting the same evidence file with the layer's updated
`case_state`/`human_review`/`investigator_action`/`audit_trail`/
`case_memory`/`sar_report` fields - so a case correctly stays in
ACTION_PENDING between the two calls instead of resetting on every
request. This file adds no new authorization logic of its own.

Previously this file only exposed a single health-check route
(`GET /`, retained below, unchanged).
"""
import glob
import json
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import case_state as cs
from data_store import DataStore
from action_pipeline import CaseActionLayer
from case_data_access import ScopedDataAccess
from investigator_action import resolve_investigator

app = FastAPI(title="Fraud Investigation Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BACKEND_DIR, "mock_data")
OUT_DIR = os.path.join(BACKEND_DIR, "pipeline_output")
EVIDENCE_DIR = os.path.join(OUT_DIR, "evidence")
CASES_BUNDLE_PATH = os.path.join(OUT_DIR, "cases.json")

# Loaded once at process start - the same read-only bank-source dataset
# run_pipeline.py already read to produce pipeline_output/. Endpoints
# never mutate this; case-scoped access always goes through
# case_data_access.ScopedDataAccess, never this object directly.
_store = None


def get_store():
    global _store
    if _store is None:
        _store = DataStore(DATA_DIR)
    return _store


# In-process cache of live CaseActionLayer instances, keyed by case_id.
# CaseActionLayer.__init__ always deterministically re-derives case_state
# from scratch (SUSPECTED -> ... -> HUMAN_REVIEW/INVESTIGATING) from the
# evidence it's given - it has no way to know a human review was already
# completed against a PRIOR instance unless that same instance (or an
# equivalent replay of its calls) is reused. Keeping the live instance
# here - rather than reconstructing a fresh one from disk on every
# request - is what lets `POST .../human-review` followed by
# `POST .../action` work as two real HTTP calls instead of one
# unrealistic same-request flow. This is an explicit, documented
# limitation: it does not survive a process restart (a production
# deployment would persist CaseActionLayer's constituent state - review/
# action/audit trail - in a real datastore instead of this in-memory
# dict); see docs/backend_implementation_status.md.
_LIVE_LAYERS = {}


@app.get("/")
def hello():
    return {
        "message": "Backend is alive",
        "status": "ok",
    }


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------
def _evidence_path(case_id):
    return os.path.join(EVIDENCE_DIR, f"{case_id}.json")


def _load_evidence(case_id):
    path = _evidence_path(case_id)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"no persisted case found for case_id={case_id!r}")
    with open(path) as f:
        return json.load(f)


def _save_evidence(case_id, evidence):
    with open(_evidence_path(case_id), "w") as f:
        json.dump(evidence, f, indent=2, default=str)


def _load_all_cases():
    out = []
    for path in sorted(glob.glob(os.path.join(EVIDENCE_DIR, "*.json"))):
        with open(path) as f:
            out.append(json.load(f))
    return out


def _load_case_bundles():
    """Loads `pipeline_output/cases.json` - the detection agent's own
    alert-bundling record (one entry per case, with the `alert_ids` it
    bundled together and when that bundle was created). This is the
    only place that bundle count/timestamp is persisted; the per-case
    evidence files in EVIDENCE_DIR do not repeat it. Returns a dict
    keyed by case_id so callers can merge it onto evidence-derived case
    data without guessing at a shape. Missing file -> {} (never invents
    bundle data for a case that doesn't have any)."""
    if not os.path.isfile(CASES_BUNDLE_PATH):
        return {}
    with open(CASES_BUNDLE_PATH) as f:
        bundles = json.load(f)
    return {b["case_id"]: b for b in bundles}


def _case_from_evidence(evidence):
    """Reconstructs the minimal `case` dict CaseActionLayer needs, from
    an already-persisted evidence record - never invents a field that
    isn't already there."""
    return {
        "case_id": evidence["case_id"],
        "account_id": evidence["account_id"],
        "primary_trigger": evidence["typology"],
        "status": "open",
    }


def _get_or_build_action_layer(case_id, evidence):
    """Returns the live CaseActionLayer for this case, reusing
    `_LIVE_LAYERS` across requests within this process's lifetime so a
    completed human review is still in effect on the subsequent
    `/action` call - see `_LIVE_LAYERS`'s own docstring above."""
    layer = _LIVE_LAYERS.get(case_id)
    if layer is None:
        case = _case_from_evidence(evidence)
        layer = CaseActionLayer(case, evidence, case_alerts=[])
        _LIVE_LAYERS[case_id] = layer
    return layer


def _resolve_role(investigator_id):
    identity = resolve_investigator(investigator_id)
    if identity is None:
        raise HTTPException(status_code=401, detail=f"unrecognized investigator_id={investigator_id!r}")
    return identity["role"]


def _persist_layer(case_id, evidence, layer):
    evidence["case_state"] = layer.state
    evidence["next_best_action"] = layer.recommendation
    evidence["audit_trail"] = layer.trail.to_list()
    evidence["case_memory"] = layer.memory
    evidence["sar_report"] = layer.sar_report
    evidence["human_review"] = layer.human_review
    evidence["investigator_action"] = layer.investigator_action
    _save_evidence(case_id, evidence)
    return evidence


# ----------------------------------------------------------------------
# Phase 15: investigation API
# ----------------------------------------------------------------------
@app.get("/cases")
def list_cases():
    """GET /cases - case list for the dashboard (Suspected/Audit-Ready/
    Escalated/Reference views filter this client-side on `case_state`,
    matching the existing frontend's four-view split).

    Also merges in each case's own bundle record from
    `pipeline_output/cases.json` (`alert_ids`/`alert_count`/
    `created_at`/`bundle_reason`) - the detection agent's record of
    which alerts it bundled into this case. Previously this endpoint
    exposed no way to tell how many alerts (or which ones) had been
    bundled into a given case, even though that data was already being
    persisted by the pipeline; the Suspected dashboard view needs it to
    show real bundled-alert counts instead of placeholder ones."""
    bundles = _load_case_bundles()
    cases = []
    for evidence in _load_all_cases():
        case_id = evidence["case_id"]
        bundle = bundles.get(case_id, {})
        alert_ids = bundle.get("alert_ids", [])
        cases.append({
            "case_id": case_id,
            "account_id": evidence["account_id"],
            "typology": evidence["typology"],
            "case_state": evidence.get("case_state"),
            "case_completeness_status": (evidence.get("case_completeness") or {}).get("status"),
            "recommended_action": (evidence.get("next_best_action") or {}).get("recommended_action"),
            "sar_status": (evidence.get("sar_report") or {}).get("status"),
            "alert_ids": alert_ids,
            "alert_count": len(alert_ids),
            "created_at": bundle.get("created_at"),
            "bundle_reason": bundle.get("bundle_reason"),
        })
    return {"cases": cases, "count": len(cases)}


@app.get("/cases/{case_id}")
def get_case(case_id: str):
    """GET /cases/{case_id} - full case summary: alert reason, typology,
    risk (authority), completeness/re-gather, jurisdiction, recommended
    action, case state, SAR status - fields the deterministic pipeline
    already computed (Phase 7's list, summary level)."""
    evidence = _load_evidence(case_id)
    return {
        "case_id": evidence["case_id"],
        "account_id": evidence["account_id"],
        "typology": evidence["typology"],
        "alert_trigger": evidence.get("data", {}).get("visualization_type"),
        "confidence": evidence.get("confidence"),
        "case_state": evidence.get("case_state"),
        "completeness": evidence.get("completeness"),
        "case_completeness": evidence.get("case_completeness"),
        "regather": evidence.get("regather"),
        "jurisdiction": evidence.get("jurisdiction"),
        "authority": evidence.get("authority"),
        "next_best_action": evidence.get("next_best_action"),
        "sar_status": (evidence.get("sar_report") or {}).get("status"),
        "generated_at": evidence.get("generated_at"),
    }


@app.get("/cases/{case_id}/evidence")
def get_case_evidence(case_id: str):
    """GET /cases/{case_id}/evidence - evidence items with provenance,
    missing evidence, and this case's confidence/status - unmodified
    from evidence_model.py's own output."""
    evidence = _load_evidence(case_id)
    return {
        "case_id": case_id,
        "evidence_items": evidence.get("evidence_items", []),
        "completeness": evidence.get("completeness"),
        "source_transactions": evidence.get("source_transactions"),
        "network_scope": evidence.get("network_scope"),
    }


@app.get("/cases/{case_id}/network")
def get_case_network(case_id: str, role: str = "senior"):
    """GET /cases/{case_id}/network?role=junior|senior - Phase 8's
    frontend-ready graph structure, scoped by investigator role via
    case_data_access.ScopedDataAccess (never the full unfiltered graph
    for a junior view). Timeline-typology cases (money_mule/
    account_swap) have no graph - see /cases/{case_id}/timeline instead,
    per Phase 8's instruction not to force everything into a graph."""
    evidence = _load_evidence(case_id)
    data = evidence.get("data", {})
    if "nodes" not in data:
        return {"case_id": case_id, "graph": None,
                "reason": "this typology's evidence is a transaction/behavioral "
                          "timeline, not a graph - see /cases/{case_id}/timeline"}
    case = _case_from_evidence(evidence)
    access = ScopedDataAccess(get_store(), case, evidence, role=role)
    graph = access.get_related_network()
    graph["metadata"] = {
        "typology": evidence["typology"],
        "visualization_type": data.get("visualization_type"),
        "root_account": data.get("root_account"),
        "max_depth": data.get("max_depth"),
        "role_scope": access.role,
    }
    return {"case_id": case_id, "graph": graph}


@app.get("/cases/{case_id}/regulatory")
def get_case_regulatory(case_id: str):
    """GET /cases/{case_id}/regulatory - jurisdiction, rule evaluation
    (with each finding's own regulatory_context citations), auditor
    findings, completeness/re-gather status."""
    evidence = _load_evidence(case_id)
    return {
        "case_id": case_id,
        "jurisdiction": evidence.get("jurisdiction"),
        "regulatory_findings": evidence.get("regulatory_findings"),
        "auditor": evidence.get("auditor"),
        "case_completeness": evidence.get("case_completeness"),
        "regather": evidence.get("regather"),
    }


@app.get("/cases/{case_id}/audit")
def get_case_audit(case_id: str):
    """GET /cases/{case_id}/audit - the append-only audit_trail.py
    event list, unmodified."""
    evidence = _load_evidence(case_id)
    return {"case_id": case_id, "audit_trail": evidence.get("audit_trail", [])}


@app.get("/cases/{case_id}/actions")
def get_case_actions(case_id: str):
    """GET /cases/{case_id}/actions - next-best-action recommendation,
    case lifecycle state, and (once available) the human review /
    investigator action record."""
    evidence = _load_evidence(case_id)
    return {
        "case_id": case_id,
        "next_best_action": evidence.get("next_best_action"),
        "case_state": evidence.get("case_state"),
        "human_review": evidence.get("human_review"),
        "investigator_action": evidence.get("investigator_action"),
        "case_memory": evidence.get("case_memory"),
    }


@app.get("/cases/{case_id}/sar")
def get_case_sar(case_id: str):
    """GET /cases/{case_id}/sar - the structured SAR record (`None` for
    every case that has not reached an authorized FILE_SAR investigator
    action - see sar_report.py)."""
    evidence = _load_evidence(case_id)
    return {"case_id": case_id, "sar_report": evidence.get("sar_report")}


@app.get("/cases/{case_id}/timeline")
def get_case_timeline(case_id: str):
    """GET /cases/{case_id}/timeline - the audit trail re-expressed
    chronologically, plus (for timeline-typology cases) the underlying
    transaction/behavioral timeline data itself.

    `transaction_timeline` (money_mule's `data.transactions`) was the only
    typology-specific field originally exposed here. `behavioral_events`/
    `behavioral_summary` (account_swap's `data.events`/
    `data.behavioral_summary` - see network_layer.build_account_swap_timeline)
    are additive fields added for the frontend Checkpoint: account_swap has
    no graph (`/cases/{case_id}/network` returns `graph: None` for it, by
    design - see that endpoint's own docstring) and, before this addition,
    no endpoint exposed its timeline data at all. Both typologies' fields
    are simply `None` when not applicable to a given case - never
    fabricated, never repurposing the other typology's shape."""
    evidence = _load_evidence(case_id)
    events = sorted(evidence.get("audit_trail", []), key=lambda e: e.get("timestamp") or "")
    data = evidence.get("data", {}) or {}
    return {
        "case_id": case_id,
        "audit_timeline": events,
        "transaction_timeline": data.get("transactions"),
        "behavioral_events": data.get("events"),
        "behavioral_summary": data.get("behavioral_summary"),
    }


class HumanReviewRequest(BaseModel):
    reviewer_id: str
    investigator_decision: str
    decision_reason: str


@app.post("/cases/{case_id}/human-review")
def submit_human_review(case_id: str, body: HumanReviewRequest):
    """POST /cases/{case_id}/human-review - Phase 12's explicit human
    review boundary. Rebuilds this case's CaseActionLayer from its
    persisted evidence and calls the existing, unmodified
    `complete_human_review()` (Checkpoint 6) - no new authorization
    logic. Returns 409 if the case is not currently in HUMAN_REVIEW.
    Persists the resulting case_state/human_review back onto the
    evidence record so a later /action call sees ACTION_PENDING."""
    evidence = _load_evidence(case_id)
    layer = _get_or_build_action_layer(case_id, evidence)
    if layer.state != cs.HUMAN_REVIEW:
        raise HTTPException(status_code=409,
                             detail=f"case {case_id} is in state {layer.state}, not HUMAN_REVIEW")
    review = layer.complete_human_review(body.reviewer_id, body.investigator_decision, body.decision_reason)
    evidence = _persist_layer(case_id, evidence, layer)
    return {"case_id": case_id, "human_review": review, "case_state": layer.state}


class InvestigatorActionRequest(BaseModel):
    investigator_id: str
    requested_action: str
    reason: str
    override_reason: str = None


@app.post("/cases/{case_id}/action")
def submit_investigator_action(case_id: str, body: InvestigatorActionRequest):
    """POST /cases/{case_id}/action - Phase 9/12. Requires a human review
    to already have been completed (case_state == ACTION_PENDING,
    persisted by /human-review above). Every attempt, authorized or not,
    is recorded (Checkpoint 6's own requirement) and persisted."""
    evidence = _load_evidence(case_id)
    layer = _get_or_build_action_layer(case_id, evidence)
    if layer.state != cs.ACTION_PENDING:
        raise HTTPException(
            status_code=409,
            detail=(f"case {case_id} is in state {layer.state}, not ACTION_PENDING - "
                     "call /human-review first"),
        )
    action = layer.submit_action(body.investigator_id, body.requested_action, body.reason,
                                  override_reason=body.override_reason)
    evidence = _persist_layer(case_id, evidence, layer)
    return {
        "case_id": case_id,
        "action": action,
        "case_state": layer.state,
        "sar_report": layer.sar_report,
    }