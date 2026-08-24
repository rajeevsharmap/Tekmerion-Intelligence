"""
action_pipeline.py
=====================
CHECKPOINT 6 - orchestration layer wiring next_best_action.py,
audit_trail.py, case_state.py, investigator_action.py, and case_memory.py
together into the single flow the checkpoint spec describes:

    Next-Best-Action -> Audit Trail -> Human Review -> Investigator Action
    -> Case Memory

This module does not reimplement any of those five modules' own logic -
it only sequences calls to them and threads the resulting objects through
the case's lifecycle state (case_state.py). It CONSUMES the already-
computed Checkpoint 4/5 output (`evidence` - the per-case dict
run_pipeline.py already builds: `evidence_items`, `completeness`,
`authority`, `jurisdiction`, `regulatory_findings`, `auditor`,
`case_completeness`, `regather`) - it never recomputes any of those.

`CaseActionLayer` is a thin, explicit state container (not a hidden
global) - one instance per case. Every mutating method appends to the
case's AuditTrail and returns the updated CaseActionLayer; nothing is
executed silently, and every attempted action (authorized or not) is
recorded, per the checkpoint's security requirements.
"""
import case_state as cs
from audit_trail import AuditTrail
from next_best_action import recommend_next_best_action
from investigator_action import (
    create_human_review,
    record_investigator_action,
    OverrideReasonRequiredError,
)
from case_memory import build_case_memory, update_case_memory
from sar_report import build_sar_report


class InvalidActionLayerStateError(Exception):
    """Raised when a Checkpoint 6 action is attempted from a case_state
    that does not permit it (e.g. submitting an investigator action before
    a human review has started)."""


class CaseActionLayer:
    """One instance per case. Bundles the case's recommendation, audit
    trail, lifecycle state, and case memory, and sequences the
    Next-Best-Action -> Audit Trail -> Human Review -> Investigator Action
    -> Case Memory flow."""

    def __init__(self, case, evidence, case_alerts=None):
        self.case = case
        self.evidence = evidence
        self.case_alerts = case_alerts or []
        self.trail = AuditTrail(case["case_id"])
        self.state = cs.SUSPECTED
        self.recommendation = None
        self.human_review = None
        self.investigator_action = None
        self.sar_report = None  # CHECKPOINT 7 - see sar_report.py
        self.memory = None

        self._seed_upstream_events()
        self._generate_recommendation()
        self._advance_to_review_or_hold()
        self._build_memory()

    # ------------------------------------------------------------------
    # Internal sequencing
    # ------------------------------------------------------------------
    def _seed_upstream_events(self):
        """Record, in the audit trail, the upstream (Checkpoint 3-5)
        system events that already happened before this case reached
        Checkpoint 6 - the case/evidence/regulatory/auditor/completeness/
        authority computations themselves are NOT redone here, only
        logged, from the already-persisted `evidence` dict."""
        case_id = self.case["case_id"]
        self.trail.append("case_created", "system", "detection_layer",
                           after_state={"status": self.case.get("status")},
                           reason="case bundled from correlated alerts")
        for alert in self.case_alerts:
            self.trail.append("alert_created", "system", "detection_layer",
                               after_state={"alert_id": alert.get("alert_id")},
                               related_evidence_ids=[], reason="alert triggered by detection rules")

        self.state = cs.transition(self.state, cs.INVESTIGATING)

        evidence_ids = [i["evidence_id"] for i in self.evidence.get("evidence_items", [])]
        self.trail.append("evidence_gathered", "system", "network_layer",
                           after_state={"completeness": self.evidence.get("completeness")},
                           related_evidence_ids=evidence_ids,
                           reason="network/timeline evidence generated for case")

        regather = self.evidence.get("regather")
        if regather and regather.get("iterations"):
            self.trail.append("evidence_regathered", "system", "regather_loop",
                               before_state=None, after_state={"iterations": regather.get("iterations")},
                               reason="case_completeness was incomplete; targeted re-gather ran")

        self.trail.append("regulatory_evaluation", "system", "regulatory_rules",
                           after_state={"rule_count": len(self.evidence.get("regulatory_findings", []))},
                           reason="compliance rules evaluated against gathered evidence")
        self.trail.append("auditor_evaluation", "system", "investigation_auditor",
                           after_state=self.evidence.get("auditor"),
                           reason="independent structural audit of the investigation")
        self.trail.append("completeness_evaluation", "system", "case_completeness",
                           after_state=self.evidence.get("case_completeness"),
                           reason="case completeness scored")
        self.trail.append("authority_evaluation", "system", "authority_policy",
                           after_state=self.evidence.get("authority"),
                           reason="junior/senior routing decision (Checkpoint 4, consumed here)")

    def _generate_recommendation(self):
        self.recommendation = recommend_next_best_action(
            self.case,
            self.evidence.get("evidence_items", []),
            self.evidence.get("completeness", {}),
            self.evidence.get("case_completeness", {}),
            self.evidence.get("regulatory_findings", []),
            self.evidence.get("auditor", {}),
            self.evidence.get("authority", {}),
            net=None,
            case_alerts=self.case_alerts,
            jurisdiction_context=self.evidence.get("jurisdiction"),
        )
        self.trail.append("next_best_action_generated", "system", "next_best_action",
                           after_state=self.recommendation,
                           related_evidence_ids=self.recommendation.get("supporting_evidence_ids", []),
                           reason="deterministic recommendation generated")

    def _advance_to_review_or_hold(self):
        """A case whose evidence is still incomplete (recommended action
        REQUEST_MORE_INFORMATION) is NOT advanced to human review yet -
        it stays in INVESTIGATING until re-gathered evidence produces a
        complete case_completeness result (a later Checkpoint-5 re-run)."""
        completeness_status = (self.evidence.get("case_completeness") or {}).get("status")
        if completeness_status == "complete":
            self.state = cs.transition(self.state, cs.AUDIT_READY)
            self.state = cs.transition(self.state, cs.HUMAN_REVIEW)
            self.trail.append("human_review_started", "system", "action_pipeline",
                               after_state={"case_state": self.state},
                               reason="case is audit-ready; queued for human review")

    def _build_memory(self):
        self.memory = build_case_memory(
            self.case, self.evidence.get("jurisdiction"), self.evidence.get("evidence_items", []),
            self.evidence.get("completeness", {}), self.evidence.get("authority", {}),
            self.evidence.get("regulatory_findings", []), self.evidence.get("auditor", {}),
            self.evidence.get("case_completeness", {}), self.recommendation, self.state,
            audit_trail_events=self.trail.events,
        )

    # ------------------------------------------------------------------
    # Human-in-the-loop entry points
    # ------------------------------------------------------------------
    def complete_human_review(self, reviewer_id, investigator_decision, decision_reason,
                               evidence_reviewed=None, regulatory_rules_reviewed=None):
        """Step 4. Requires the case to already be in HUMAN_REVIEW (i.e.
        the case was audit-ready - `_advance_to_review_or_hold` already
        ran). `investigator_decision` is the reviewer's own conclusion
        (may equal or differ from `self.recommendation`); the actual
        authorized action still has to go through `submit_action` (Step 3
        applies there, not here) - a review can approve/reject/escalate
        the recommendation without yet attempting to execute anything."""
        if self.state != cs.HUMAN_REVIEW:
            raise InvalidActionLayerStateError(
                f"cannot complete human review from case_state {self.state!r}"
            )
        status = "approved" if investigator_decision == self.recommendation["recommended_action"] else "overridden"
        self.human_review = create_human_review(
            self.case, self.recommendation, reviewer_id, investigator_decision, decision_reason,
            evidence_reviewed=evidence_reviewed, regulatory_rules_reviewed=regulatory_rules_reviewed,
            status=status,
        )
        self.trail.append("human_review_completed", "investigator", reviewer_id,
                           before_state={"system_recommendation": self.recommendation["recommended_action"]},
                           after_state=self.human_review, reason=decision_reason)
        if status == "overridden":
            self.trail.append("recommendation_overridden", "investigator", reviewer_id,
                               before_state=self.recommendation["recommended_action"],
                               after_state=investigator_decision, reason=decision_reason)
        self.state = cs.transition(self.state, cs.ACTION_PENDING)
        self.memory = update_case_memory(self.memory, lifecycle_state=self.state,
                                          human_review=self.human_review,
                                          audit_trail_events=self.trail.events)
        return self.human_review

    def submit_action(self, investigator_id, requested_action, reason, override_reason=None):
        """Step 5/6. Requires the case to be in ACTION_PENDING (i.e. a
        human review has already been completed). Every attempt - even an
        unauthorized one - is recorded in the audit trail (Step 3's
        explicit requirement: "Do not silently reject it")."""
        if self.state != cs.ACTION_PENDING:
            raise InvalidActionLayerStateError(
                f"cannot submit an investigator action from case_state {self.state!r}"
            )
        self.trail.append("action_requested", "investigator", investigator_id,
                           after_state={"requested_action": requested_action}, reason=reason)
        try:
            action_record = record_investigator_action(
                self.case, self.recommendation, investigator_id, requested_action, reason,
                override_reason=override_reason,
            )
        except OverrideReasonRequiredError:
            raise

        self.investigator_action = action_record
        if action_record["authorized"]:
            self.trail.append("action_authorized", "system", "investigator_action",
                               after_state=action_record, reason=action_record["authorization_reason"])
            self.trail.append("action_executed", "system", "investigator_action",
                               after_state={"actual_action": action_record["actual_action"]},
                               reason="authorized action executed (simulated in the mock environment)")
            self.state = cs.transition(self.state, cs.ACTION_EXECUTED)

            # CHECKPOINT 7: an authorized, executed FILE_SAR action
            # generates a structured SAR record from already-computed
            # Checkpoint 4-6 output - no new evidence, no LLM, no
            # randomness. See sar_report.py for the full precondition
            # re-validation (this call never assumes the action above was
            # correctly gated; sar_report.py checks independently).
            if action_record["actual_action"] == "FILE_SAR":
                self.sar_report = build_sar_report(
                    self.case,
                    self.evidence.get("jurisdiction"),
                    self.evidence.get("regulatory_findings", []),
                    self.evidence.get("evidence_items", []),
                    self.evidence.get("auditor", {}),
                    action_record,
                )
                self.trail.append("sar_report_generated", "system", "sar_report",
                                   after_state={"sar_id": self.sar_report["sar_id"],
                                                "status": self.sar_report["status"]},
                                   related_evidence_ids=self.sar_report["supporting_evidence_ids"],
                                   reason=f"SAR record generated with status {self.sar_report['status']}")
            if requested_action == "ESCALATE_TO_SENIOR":
                self.trail.append("case_escalated", "investigator", investigator_id,
                                   after_state={"case_state": cs.ESCALATED}, reason=reason)
                self.state = cs.transition(self.state, cs.ESCALATED)
            else:
                self.state = cs.transition(self.state, cs.CLOSED)
                self.trail.append("case_closed", "system", "action_pipeline",
                                   after_state={"final_action": action_record["actual_action"]},
                                   reason="action executed; case closed")
        else:
            self.trail.append("action_rejected", "system", "investigator_action",
                               after_state=action_record, reason=action_record["authorization_reason"])
            self.state = cs.transition(self.state, cs.HUMAN_REVIEW)

        self.memory = update_case_memory(
            self.memory, lifecycle_state=self.state, investigator_action=action_record,
            audit_trail_events=self.trail.events,
            final_disposition=action_record["actual_action"] if action_record["authorized"] else None,
            sar_report=self.sar_report,
        )
        return action_record