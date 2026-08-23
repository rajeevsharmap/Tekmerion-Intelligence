"""
case_memory.py
=================
CHECKPOINT 6 - Case Memory.

    ... -> Investigator Action -> Audit Trail -> CASE MEMORY

The durable, historical investigation record for a case. Built by
reference/ID wherever the existing architecture already produces one
(evidence_items' `evidence_id`, regulatory rule results, audit trail
`event_id`s) - never duplicating full evidence blobs (Step 9's explicit
instruction).

Case Memory PRESERVES history rather than replacing it: `update_case_
memory()` only ever appends to `*_history` lists and updates the "most
recent" convenience pointer fields (`human_review`, `investigator_action`,
`lifecycle_state`) - it never removes or overwrites a prior entry. This is
what test_case_memory_retains_prior_actions / test_case_memory_does_not_
erase_historical_events (Checkpoint 6 test suite) verify.

### SAR preparation compatibility (Step 10) ###
This module does not build a SAR report. It DOES persist every field a
future SAR generator will need: typology, jurisdiction, regulatory
findings (with citations), evidence references, auditor findings,
completeness history, the system's recommended action, the investigator's
actual action + identity/role + authorization result + timestamp, and an
audit-trail reference. `case_summary`/free-text "suspicious activity
explanation" narrative generation is explicitly deferred to the later SAR
checkpoint (Step 10's own "do NOT build the complete SAR report generator
yet" instruction) - not fabricated here.
"""


def build_case_memory(case, jurisdiction_context, evidence_items, completeness,
                       authority_decision, regulatory_findings, auditor_result,
                       case_completeness, recommendation, lifecycle_state,
                       human_review=None, investigator_action=None,
                       audit_trail_events=None, final_disposition=None):
    """Create a new Case Memory record. One record per case; later stages
    call `update_case_memory()` on the returned dict, never re-create it
    from scratch (that would silently discard `*_history`)."""
    audit_trail_events = audit_trail_events or []
    memory = {
        "case_id": case["case_id"],
        "account_id": case.get("account_id"),
        "typology": case.get("primary_trigger"),
        "lifecycle_state": lifecycle_state,
        "lifecycle_history": [lifecycle_state],
        "jurisdiction": jurisdiction_context,
        "evidence_references": [i["evidence_id"] for i in (evidence_items or [])],
        "evidence_completeness": completeness,
        "case_completeness_history": [case_completeness],
        "authority_decision": authority_decision,
        "regulatory_findings": regulatory_findings,
        "auditor_findings": auditor_result,
        "recommended_action": recommendation,
        "human_review": human_review,
        "human_review_history": [human_review] if human_review else [],
        "investigator_action": investigator_action,
        "investigator_action_history": [investigator_action] if investigator_action else [],
        "audit_trail_ref": [e["event_id"] for e in audit_trail_events],
        "final_disposition": final_disposition or (
            (investigator_action or {}).get("actual_action")
        ),
    }
    return memory


def update_case_memory(memory, lifecycle_state=None, human_review=None,
                        investigator_action=None, case_completeness=None,
                        audit_trail_events=None, final_disposition=None):
    """Additive update: every argument is optional; when supplied it is
    APPENDED to the relevant `*_history` list (and mirrored into the
    matching "most recent" pointer field) - prior entries are never
    dropped or mutated. Returns a new dict (does not mutate `memory` in
    place), so a caller holding a reference to the previous snapshot still
    sees the unmodified prior state."""
    updated = dict(memory)

    if lifecycle_state is not None:
        updated["lifecycle_state"] = lifecycle_state
        updated["lifecycle_history"] = list(memory.get("lifecycle_history", [])) + [lifecycle_state]

    if human_review is not None:
        updated["human_review"] = human_review
        updated["human_review_history"] = list(memory.get("human_review_history", [])) + [human_review]

    if investigator_action is not None:
        updated["investigator_action"] = investigator_action
        updated["investigator_action_history"] = (
            list(memory.get("investigator_action_history", [])) + [investigator_action]
        )

    if case_completeness is not None:
        updated["case_completeness_history"] = (
            list(memory.get("case_completeness_history", [])) + [case_completeness]
        )

    if audit_trail_events:
        existing = set(memory.get("audit_trail_ref", []))
        new_ids = [e["event_id"] for e in audit_trail_events]
        updated["audit_trail_ref"] = list(memory.get("audit_trail_ref", [])) + [
            eid for eid in new_ids if eid not in existing
        ]

    if final_disposition is not None:
        updated["final_disposition"] = final_disposition

    return updated