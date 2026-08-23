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