"""
run_pipeline.py
==================
THE single end-to-end orchestration entry point for the live pipeline:

    DataStore
        -> Detection Layer            (run_detection_pipeline)
        -> suspected alerts (live)    (persist_alerts_csv)
        -> Case Bundler                (bundle_alerts_into_cases)
        -> cases (live)                (persist_cases_csv)
        -> for each case:
               case-specific network/timeline evidence
                                        (generate_network_evidence)
               Evidence Store record   (wrap_as_evidence)
               persist                 (one JSON file per case_id)

Run this instead of invoking detection_layer.py and network_layer.py
separately - this script IS the integration between them, in one process,
one DataStore instance, one pass.

    python3 run_pipeline.py

No ground-truth file is read anywhere in this script. mock_data/accounts.csv,
transactions.csv, devices.csv, geo_events.csv, beneficiaries.csv are the only
inputs (the bank's source-of-truth data) - mock_data/ground_truth_*.csv is
evaluation-only reference data, consumed exclusively by eval_pipeline.py,
never by this script. Detection Layer produces its own alerts/cases from
scratch by evaluating the real rules against the real DataStore.

Output (all under --out_dir, default "pipeline_output/"):
    suspected_alerts.csv / .json   - live Detection Agent output
    cases.csv / .json              - live Case Intake output
    evidence/{case_id}.json        - one case-scoped Evidence Store record
                                      per case, keyed by case_id

No investigator tier, no escalation flag, anywhere in this output - that's
a human-in-the-loop / action-stage decision this pipeline does not make.
"""
import argparse
import json
import os

from data_store import DataStore
from detection_layer import (
    run_detection_pipeline,
    bundle_alerts_into_cases,
    persist_alerts_csv,
    persist_cases_csv,
)
from network_layer import generate_network_evidence, wrap_as_evidence
from evidence_model import build_evidence_items, compute_completeness
from authority_policy import assess_authority, AUTHORITY_POLICY
from regulatory_rules import evaluate_compliance_rules
from investigation_auditor import audit_investigation
from case_completeness import compute_case_completeness
from regather_loop import run_regather_loop
from jurisdiction import determine_case_jurisdiction
from action_pipeline import CaseActionLayer


def run_pipeline(data_dir="mock_data", out_dir="pipeline_output"):
    os.makedirs(out_dir, exist_ok=True)
    evidence_dir = os.path.join(out_dir, "evidence")
    os.makedirs(evidence_dir, exist_ok=True)

    # ---- 1. Bank source-of-truth data ----------------------------------
    store = DataStore(data_dir)

    # ---- 2. Detection Layer: real rules against the real DataStore -----
    alerts = run_detection_pipeline(store)

    # ---- 3. Case Intake: bundle THOSE alerts (not ground truth) --------
    cases = bundle_alerts_into_cases(alerts)

    # ---- 4. Persist live Detection + Case Intake output -----------------
    persist_alerts_csv(alerts, os.path.join(out_dir, "suspected_alerts.csv"))
    persist_cases_csv(cases, os.path.join(out_dir, "cases.csv"))
    with open(os.path.join(out_dir, "suspected_alerts.json"), "w") as f:
        json.dump(alerts, f, indent=2, default=str)
    with open(os.path.join(out_dir, "cases.json"), "w") as f:
        json.dump(cases, f, indent=2, default=str)

    # ---- 5. Case-specific Network/Timeline Evidence, one case at a time -
    # Iterates ONLY over `cases` (the output of step 3, above) - never a
    # hardcoded or manually-picked case, never the ground-truth file. Each
    # case gets its own generate_network_evidence() call and its own
    # persisted file, keyed by case_id - never a shared/batched object.
    evidence_records = []
    for case in cases:
        network_evidence = generate_network_evidence(store, case)
        evidence = wrap_as_evidence(network_evidence)
        # CHECKPOINT 2: typed evidence items + deterministic completeness,
        # built from this case's actual network_evidence/store data (never
        # random) - additive fields on top of the unchanged `data` blob, so
        # nothing that already reads `data`/`source_transactions`/etc. is
        # affected. See evidence_model.py for the full evidence object model.
        evidence_items = build_evidence_items(store, case, network_evidence)
        evidence["evidence_items"] = evidence_items
        evidence["completeness"] = compute_completeness(evidence_items)
        # CHECKPOINT 4: investigator authority / escalation policy decision,
        # built from THIS case's already-computed evidence_items/completeness
        # (above) plus the real network evidence/account/alerts already in
        # scope here - never a second independent evidence-gathering pass,
        # never random, never read from ground truth. See authority_policy.py.
        case_alerts = [a for a in alerts if a["alert_id"] in case["alert_ids"]]
        account = store.accounts_by_id.get(case["account_id"])
        evidence["authority"] = assess_authority(
            case, evidence_items, evidence["completeness"],
            net=network_evidence, account=account, case_alerts=case_alerts,
        )

        # CHECKPOINT 5: Regulatory Compliance Rule Engine -> Regulatory RAG
        # -> Investigation Auditor -> Case Completeness Score -> [LOW ->
        # targeted re-gather | HIGH -> downstream auditor routing]. Every
        # stage reads only already-computed real evidence (evidence_items,
        # network_evidence, the authority decision above) - no ground
        # truth, no LLM call, no randomness. `contradiction_state` is
        # intentionally omitted here (same documented reason authority_
        # policy.py gives - see that module's docstring: the LLM
        # contradiction agent is not wired into this live per-case loop
        # yet), so the auditor's contradictory-evidence check degrades to
        # "not evaluated -> no issue" rather than guessing.
        structural_gap_reasons = AUTHORITY_POLICY.get("structural_gap_reasons", ())
        # JURISDICTION: determined once per case, from real account/
        # transaction/geo data only (jurisdiction.py) - never re-derived
        # per rule. It does not change across the re-gather loop below
        # (widening a transaction/network time window cannot change which
        # country an account is registered in), so the same
        # jurisdiction_context is reused for the post-regather
        # re-evaluation instead of being recomputed.
        jurisdiction_context = determine_case_jurisdiction(
            case, net=network_evidence, account=account, store=store,
        )
        regulatory_findings = evaluate_compliance_rules(
            case, evidence_items, evidence["completeness"],
            net=network_evidence, account=account, store=store,
            jurisdiction_context=jurisdiction_context,
        )
        auditor_result = audit_investigation(
            case, evidence_items, evidence["completeness"], net=network_evidence, account=account,
            regulatory_findings=regulatory_findings, authority_decision=evidence["authority"],
            structural_gap_reasons=structural_gap_reasons,
            jurisdiction_context=jurisdiction_context,
        )
        case_completeness = compute_case_completeness(
            case, evidence_items, evidence["completeness"],
            regulatory_findings=regulatory_findings, auditor_result=auditor_result,
            structural_gap_reasons=structural_gap_reasons,
            jurisdiction_context=jurisdiction_context,
        )

        regather_result = None
        if case_completeness["status"] == "incomplete":
            regather_result = run_regather_loop(
                store, case, evidence_items, evidence["completeness"],
                structural_gap_reasons=structural_gap_reasons,
            )
            if regather_result["final_disposition"] != "no_regather_needed":
                # Re-evaluate everything downstream of evidence/completeness
                # against the re-gathered evidence - never re-run Detection/
                # Case Bundling, only the targeted evidence + everything
                # that reads it.
                evidence_items = regather_result["final_evidence_items"]
                evidence["evidence_items"] = evidence_items
                evidence["completeness"] = regather_result["final_completeness"]
                if regather_result["final_net"] is not None:
                    network_evidence = regather_result["final_net"]
                regulatory_findings = evaluate_compliance_rules(
                    case, evidence_items, evidence["completeness"],
                    net=network_evidence, account=account, store=store,
                    jurisdiction_context=jurisdiction_context,
                )
                auditor_result = audit_investigation(
                    case, evidence_items, evidence["completeness"], net=network_evidence, account=account,
                    regulatory_findings=regulatory_findings, authority_decision=evidence["authority"],
                    structural_gap_reasons=structural_gap_reasons,
                    jurisdiction_context=jurisdiction_context,
                )
                case_completeness = compute_case_completeness(
                    case, evidence_items, evidence["completeness"],
                    regulatory_findings=regulatory_findings, auditor_result=auditor_result,
                    structural_gap_reasons=structural_gap_reasons,
                    jurisdiction_context=jurisdiction_context,
                )

        evidence["jurisdiction"] = jurisdiction_context
        evidence["regulatory_findings"] = regulatory_findings
        evidence["auditor"] = auditor_result
        evidence["case_completeness"] = case_completeness
        evidence["regather"] = regather_result
        # Clean output contract for the next checkpoint to consume -
        # additive, does not replace the fields above.
        evidence["next_stage"] = {
            "case_id": case["case_id"],
            "jurisdiction": jurisdiction_context,
            "completeness_result": case_completeness,
            "regulatory_findings": regulatory_findings,
            "evidence_references": [i["evidence_id"] for i in evidence_items],
            "auditor_decision": auditor_result,
            "recommended_next_stage": (
                "auditor_routing" if case_completeness["status"] == "complete"
                else "re_gather_evidence"
            ),
        }

        # CHECKPOINT 6: Next-Best-Action -> Audit Trail -> Human Review
        # (queued, not auto-decided) -> Investigator Action (not yet
        # attempted) -> Case Memory. Built from THIS case's already-
        # computed evidence/regulatory/auditor/completeness/authority
        # output above - no new evidence gathering, no ground truth, no
        # randomness, no LLM call. Every real case gets a deterministic
        # recommendation + seeded audit trail + initial lifecycle state +
        # case memory record; an actual human review/investigator action
        # is NOT fabricated here for every case (that would be inventing
        # investigator behavior) - see action_pipeline.py and
        # tests/test_checkpoint6.py / the Checkpoint 6 demo script for
        # representative junior/senior/override/rejected examples.
        action_layer = CaseActionLayer(case, evidence, case_alerts=case_alerts)
        evidence["next_best_action"] = action_layer.recommendation
        evidence["audit_trail"] = action_layer.trail.to_list()
        evidence["case_state"] = action_layer.state
        evidence["case_memory"] = action_layer.memory

        evidence_path = os.path.join(evidence_dir, f"{case['case_id']}.json")
        with open(evidence_path, "w") as f:
            json.dump(evidence, f, indent=2, default=str)
        evidence_records.append(evidence)

    return store, alerts, cases, evidence_records


def _typology_counts(items, key="typology"):
    counts = {}
    for i in items:
        counts[i.get(key)] = counts.get(i.get(key), 0) + 1
    return counts


def main():
    parser = argparse.ArgumentParser(description="Run the complete Detection -> Case -> Evidence pipeline in one pass.")
    parser.add_argument("--data_dir", default="mock_data")
    parser.add_argument("--out_dir", default="pipeline_output")
    parser.add_argument("--demo_case", action="store_true",
                         help="print the full account->alert->case->evidence chain for one real generated case")
    args = parser.parse_args()

    store, alerts, cases, evidence_records = run_pipeline(args.data_dir, args.out_dir)

    print("=" * 78)
    print("PIPELINE RUN COMPLETE")
    print("=" * 78)
    print(f"Accounts scanned      : {len(store.accounts)}")
    print(f"Alerts generated      : {len(alerts)}   {_typology_counts(alerts)}")
    print(f"Cases generated       : {len(cases)}   {_typology_counts(cases, 'primary_trigger')}")
    print(f"Evidence objects      : {len(evidence_records)}")

    viz_by_typology = {}
    for e in evidence_records:
        viz_by_typology.setdefault(e["typology"], set()).add(e["data"]["visualization_type"])
    print("\nVisualization type by typology (must be graph/graph/timeline/behavioral_transaction_timeline):")
    for typ, viz in sorted(viz_by_typology.items()):
        print(f"  {typ:<18} -> {sorted(viz)}")

    completeness_scores = [e["completeness"]["weighted_score"] for e in evidence_records
                            if e["completeness"]["weighted_score"] is not None]
    if completeness_scores:
        avg = round(sum(completeness_scores) / len(completeness_scores), 1)
        print(f"\nEvidence completeness (weighted, deterministic): avg={avg} "
              f"min={min(completeness_scores)} max={max(completeness_scores)} "
              f"(n={len(completeness_scores)}/{len(evidence_records)} cases with a typed requirement table)")

    cc_statuses = _typology_counts(
        [{"status": e["case_completeness"]["status"]} for e in evidence_records], "status"
    )
    regathered = sum(1 for e in evidence_records if e.get("regather") and e["regather"]["iterations"])
    print(f"\nCase completeness (Checkpoint 5, deterministic): {cc_statuses}  "
          f"({regathered}/{len(evidence_records)} cases triggered the re-gather loop)")

    nba_counts = _typology_counts(
        [{"action": e["next_best_action"]["recommended_action"]} for e in evidence_records], "action"
    )
    state_counts = _typology_counts(
        [{"state": e["case_state"]} for e in evidence_records], "state"
    )
    print(f"\nNext-Best-Action (Checkpoint 6, deterministic): {nba_counts}")
    print(f"Case lifecycle state after Checkpoint 6 seeding: {state_counts}")

    print(f"\nOutput files under {args.out_dir}/:")
    print(f"  suspected_alerts.csv, cases.csv (+ .json copies)")
    print(f"  evidence/{{case_id}}.json  x{len(evidence_records)}")

    if args.demo_case and cases:
        case = cases[0]
        case_alerts = [a for a in alerts if a["alert_id"] in case["alert_ids"]]
        evidence = next(e for e in evidence_records if e["case_id"] == case["case_id"])
        print("\n" + "=" * 78)
        print("FULL CHAIN DEMO: account -> alert_id -> case_id -> evidence")
        print("=" * 78)
        print(f"account_id   : {case['account_id']}")
        print(f"alert_id(s)  : {case['alert_ids']}")
        for a in case_alerts:
            print(f"    {a['alert_id']}: typology={a['typology']} rules={a['triggering_rules']} score={a['alert_score']}")
        print(f"case_id      : {case['case_id']}  (primary_trigger={case['primary_trigger']}, status={case['status']})")
        print(f"evidence_id  : {evidence['evidence_id']}  (case_id={evidence['case_id']}, "
              f"visualization_type={evidence['data']['visualization_type']})")


if __name__ == "__main__":
    main()