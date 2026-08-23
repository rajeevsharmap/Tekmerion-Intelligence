"""
demo_checkpoint6.py
======================
CHECKPOINT 6 - representative-case demonstration (Step 14 of the
checkpoint spec).

Loads already-persisted, real evidence from `pipeline_output/evidence/`
(produced by a real `python3 run_pipeline.py` run - never regenerated or
hand-edited here) and walks a handful of representative cases through the
full Human Review -> Investigator Action flow, using the deterministic
test investigator identities in investigator_action.INVESTIGATOR_DIRECTORY.

No destructive action is executed against any real system - every
"action" here is the same structured, simulated record `action_pipeline.py`
always produces (see its own docstring); this script only chooses WHICH
investigator attempts WHICH action, to demonstrate:

  1. a senior-required action, executed by the senior it requires
  2. that same action, attempted by a junior investigator -> rejected,
     recorded, never silently dropped
  3. an escalation
  4. a senior investigator overriding the system's recommendation
  5. a junior-authorized action (hand-built fixture - see below)

### Why one fixture is hand-built ###
Every real case in the checked-in mock dataset routes to
`authority_tier: "senior"` (a documented Checkpoint 4 property: the mock
data's fraud-detection thresholds naturally produce higher-severity
alerts on every real generated case - see
docs/backend_implementation_status.md's Checkpoint 4 section). There is
therefore no real case on disk to demonstrate a junior-authorized action
against. Scenario 5 below uses the same kind of hand-built, clean
low-risk fixture test_authority_policy.py's own
`test_clean_low_risk_case_is_junior` already uses for the identical
reason - not fabricated evidence, just a fixture built the same
documented way Checkpoint 4's own test suite already does.

Run:
    python3 demo_checkpoint6.py
"""
import glob
import json
import os

from action_pipeline import CaseActionLayer
from investigator_action import OverrideReasonRequiredError
import case_state as cs

EVIDENCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_output", "evidence")


def _load(case_id):
    with open(os.path.join(EVIDENCE_DIR, f"{case_id}.json")) as f:
        return json.load(f)


def _find_case_with(predicate):
    for path in sorted(glob.glob(os.path.join(EVIDENCE_DIR, "*.json"))):
        with open(path) as f:
            evidence = json.load(f)
        if predicate(evidence):
            return evidence
    return None


def _case_from_evidence(evidence):
    return {"case_id": evidence["case_id"], "account_id": evidence["account_id"],
            "primary_trigger": evidence["typology"], "status": "open"}


def _print_header(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def scenario_senior_required_executed_by_senior():
    evidence = _find_case_with(
        lambda e: e.get("case_completeness", {}).get("status") == "complete"
        and any(r["status"] == "confirmed_concern" for r in e.get("regulatory_findings", []))
    )
    _print_header(f"SCENARIO 1: senior-required action, executed by a senior ({evidence['case_id']})")
    layer = CaseActionLayer(_case_from_evidence(evidence), evidence, case_alerts=[])
    print(f"typology={evidence['typology']}  recommended_action={layer.recommendation['recommended_action']}"
          f"  required_authority={layer.recommendation['required_authority']}")
    layer.complete_human_review("INV-S001", layer.recommendation["recommended_action"],
                                 "senior review: evidence and regulatory basis support the recommendation")
    action = layer.submit_action("INV-S001", layer.recommendation["recommended_action"],
                                  "executing per completed review")
    print(f"authorized={action['authorized']}  actual_action={action['actual_action']}  case_state={layer.state}")
    return evidence


def scenario_junior_attempts_senior_action_rejected(evidence):
    _print_header(f"SCENARIO 2: same case, attempted by a junior -> rejected ({evidence['case_id']})")
    layer = CaseActionLayer(_case_from_evidence(evidence), evidence, case_alerts=[])
    layer.complete_human_review("INV-J001", layer.recommendation["recommended_action"],
                                 "junior attempting to follow the recommendation")
    action = layer.submit_action("INV-J001", layer.recommendation["recommended_action"],
                                  "attempting to execute")
    print(f"authorized={action['authorized']}  authorization_reason={action['authorization_reason']}"
          f"  actual_action={action['actual_action']}  case_state={layer.state}")
    assert action["authorized"] is False
    assert layer.state == cs.HUMAN_REVIEW
    return layer


def scenario_escalation():
    evidence = _find_case_with(
        lambda e: e.get("case_completeness", {}).get("status") == "complete"
        and any(r["status"] == "potentially_applicable" for r in e.get("regulatory_findings", []))
    )
    _print_header(f"SCENARIO 3: escalation to senior ({evidence['case_id']})")
    layer = CaseActionLayer(_case_from_evidence(evidence), evidence, case_alerts=[])
    print(f"recommended_action={layer.recommendation['recommended_action']}")
    layer.complete_human_review("INV-S001", "ESCALATE_TO_SENIOR", "escalating for a second senior opinion")
    action = layer.submit_action(
        "INV-S001", "ESCALATE_TO_SENIOR", "escalating per review",
        override_reason="deviates from the system recommendation - wants a second senior "
                         "opinion before taking an irreversible action",
    )
    print(f"authorized={action['authorized']}  case_state={layer.state}")
    return layer


def scenario_override():
    evidence = _find_case_with(
        lambda e: e.get("case_completeness", {}).get("status") == "complete"
        and any(r["status"] == "confirmed_concern" for r in e.get("regulatory_findings", []))
    )
    _print_header(f"SCENARIO 4: senior overrides the recommendation ({evidence['case_id']})")
    layer = CaseActionLayer(_case_from_evidence(evidence), evidence, case_alerts=[])
    recommended = layer.recommendation["recommended_action"]
    print(f"system recommendation={recommended}")
    layer.complete_human_review("INV-S002", "ESCALATE_TO_SENIOR",
                                 "senior judgment: wants a second reviewer before acting")
    try:
        action = layer.submit_action("INV-S002", "ESCALATE_TO_SENIOR", "escalating instead of acting immediately",
                                      override_reason="wants corroboration from a second senior investigator "
                                                       "before an irreversible account action")
    except OverrideReasonRequiredError as exc:
        print(f"correctly rejected an override with no reason: {exc}")
        return
    print(f"recommendation_followed={action['recommendation_followed']}  override_reason={action['override_reason']!r}")
    print(f"case_state={layer.state}")


def scenario_junior_authorized_clean_case():
    _print_header("SCENARIO 5: junior-authorized action on a clean, hand-built low-risk case")
    case = {"case_id": "CASE-DEMO-JUNIOR", "account_id": "ACC-DEMO", "primary_trigger": "smurfing", "status": "open"}
    evidence = {
        "evidence_items": [
            {"evidence_id": "EVD-DEMO-1", "evidence_type": "transaction_chain", "weight": 0.5,
             "available": True, "quality": "high"},
            {"evidence_id": "EVD-DEMO-2", "evidence_type": "temporal_pattern", "weight": 0.5,
             "available": True, "quality": "high"},
        ],
        "completeness": {"weighted_score": 100.0, "simple_score": 100.0, "required_count": 2,
                          "available_count": 2, "missing": [], "method": "deterministic_weighted_availability"},
        "authority": {"case_id": "CASE-DEMO-JUNIOR", "authority_tier": "junior", "can_resolve": True,
                      "decision": "junior_authorized",
                      "reasons": ["no_critical_gap", "sufficient_evidence", "sufficient_confidence"],
                      "risk_factors": [], "missing_evidence": [], "policy_version": "v1",
                      "confidence": 0.95, "confidence_source": "derived_from_evidence_quality", "policy_inputs": {}},
        "jurisdiction": {"jurisdiction": "IN", "base_jurisdiction": "IN", "confidence": "high"},
        "regulatory_findings": [{"rule_id": "RULE-DEMO", "rule_name": "demo rule", "status": "no_identified_breach",
                                  "confidence": 1.0, "supporting_evidence": [], "rationale": "demo fixture"}],
        "auditor": {"case_id": "CASE-DEMO-JUNIOR", "issues": [], "issue_count": 0, "critical_issue_count": 0},
        "case_completeness": {"case_id": "CASE-DEMO-JUNIOR", "score": 100.0, "threshold": 75.0, "status": "complete",
                               "missing_evidence": [], "critical_missing_evidence": [],
                               "satisfied_requirements": ["transaction_chain", "temporal_pattern"],
                               "failed_requirements": [], "reasons": ["all_reachable_requirements_satisfied"],
                               "next_step": "continue", "components": {"evidence": 100.0, "regulatory": 100.0,
                                                                        "auditor": 100.0},
                               "jurisdiction_context": None},
        "regather": None,
    }
    layer = CaseActionLayer(case, evidence, case_alerts=[])
    print(f"recommended_action={layer.recommendation['recommended_action']}"
          f"  required_authority={layer.recommendation['required_authority']}")
    layer.complete_human_review("INV-J001", layer.recommendation["recommended_action"], "clean case, clearing")
    action = layer.submit_action("INV-J001", layer.recommendation["recommended_action"], "clearing per review")
    print(f"authorized={action['authorized']}  actual_action={action['actual_action']}  case_state={layer.state}")
    assert action["authorized"] is True


def main():
    evidence_1 = scenario_senior_required_executed_by_senior()
    scenario_junior_attempts_senior_action_rejected(evidence_1)
    scenario_escalation()
    scenario_override()
    scenario_junior_authorized_clean_case()
    print("\n" + "=" * 78)
    print("DEMO COMPLETE - no destructive action executed against any real system")
    print("=" * 78)


if __name__ == "__main__":
    main()