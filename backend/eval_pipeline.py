"""
eval_pipeline.py
===================
Runs the full investigation pipeline (Path A / Path B / Contradiction Agent)
against every case in mock_data/cases.csv.

Terminal display behavior:
    - Shows each case immediately when it starts.
    - Runs Legitimate Hypothesis Agent and prints its output immediately.
    - Waits using the existing CALL_SPACING_SECONDS.
    - Runs Scammer Hypothesis Agent and prints its output immediately.
    - Waits using the existing CALL_SPACING_SECONDS.
    - Runs Contradiction Agent and prints its output immediately.
    - Shows the final verdict for that case.
    - Then moves to the next case.
    - Shows remaining/pending cases clearly.
    - Existing CSV result format is NOT changed.
    - Existing rate-limit timings are NOT changed.

Usage:
    python3 eval_pipeline.py
    python3 eval_pipeline.py --limit 10
    python3 eval_pipeline.py --typology money_mule
    python3 eval_pipeline.py --case_ids CASE000004,CASE000012
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone

from google.genai import errors as genai_errors

from data_store import DataStore
from agents.evidence_builder import gather_evidence
from agents.scammer_hypothesis_agent import evaluate_scammer_hypothesis
from agents.legitimate_hypothesis_agent import evaluate_legitimate_hypothesis
from agents.contradiction_agent import resolve_contradiction


# ----------------------------------------------------------------------
# IMPORTANT:
# Keep these values exactly as they are in the existing program.
# ----------------------------------------------------------------------

CALL_SPACING_SECONDS = 8

RESULTS_FILE = "mock_data/eval_results.csv"
CASES_FILE = "mock_data/cases.csv"
DATA_DIR = "mock_data"


# ----------------------------------------------------------------------
# Terminal display helpers
# ----------------------------------------------------------------------

WIDTH = 78


def print_separator(char="─"):
    print(char * WIDTH)


def print_header(title):
    print()
    print_separator("═")
    print(f"  {title}")
    print_separator("═")


def print_subheader(title):
    print()
    print(f"  {title}")
    print_separator("─")


def print_wait(seconds=CALL_SPACING_SECONDS):
    print(f"\n  ⏳ Waiting {seconds}s before the next Gemini call...")


def print_agent_output(agent_name, result):
    """
    Print one agent's output immediately after that API call completes.

    This does NOT modify the result object. It only changes how it is
    displayed in the terminal.
    """

    print_subheader(agent_name)

    confidence = result.get("confidence", "")
    narrative = result.get("narrative", "")

    if confidence != "":
        print(f"  Confidence : {confidence}")

    if narrative:
        print(f"\n  Narrative:")
        print(f"    {narrative}")

    supporting_evidence = result.get("supporting_evidence", [])

    if supporting_evidence:
        print("\n  Supporting evidence:")

        for evidence in supporting_evidence:
            print(f"    • {evidence}")


def print_contradiction_output(resolution):
    """
    Print contradiction agent output immediately after its API call.
    """

    print_subheader("CONTRADICTION AGENT")

    print(
        f"  Favored hypothesis : "
        f"{resolution.get('favored_hypothesis', '')}"
    )

    print(
        f"  Confidence         : "
        f"{resolution.get('confidence', '')}"
    )

    reasoning = resolution.get("reasoning", "")
    if reasoning:
        print("\n  Reasoning:")
        print(f"    {reasoning}")

    deciding_factor = resolution.get("deciding_factor", "")
    if deciding_factor:
        print("\n  Deciding factor:")
        print(f"    {deciding_factor}")


def print_case_start(case, evidence, expected, index, total):
    """
    Print the case context BEFORE making any Gemini calls.
    """

    print()
    print_separator("═")

    print(
        f"  CASE {index}/{total}   "
        f"{case['case_id']}   "
        f"({case['account_id']})"
    )

    print_separator("─")

    print(f"  Typology       : {evidence['typology']}")
    print(f"  Ground truth   : {case['ground_truth_label']}")
    print(f"  Expected       : {expected}")

    print_separator("═")

    print("\n  ▶ Starting investigation...")
    print("  ▶ Evidence gathered.")
    print("  ▶ Next: Legitimate Hypothesis Agent")


def print_case_verdict(
    case,
    expected,
    predicted,
    correct,
    case_number,
    total,
    remaining,
):
    """
    Print the final verdict immediately after contradiction resolution.
    """

    print()
    print_separator("═")

    if correct:
        status = "✓ CORRECT"
    else:
        status = "✗ WRONG"

    print(f"  CASE RESULT  {case['case_id']}")

    print_separator("─")

    print(f"  Expected      : {expected}")
    print(f"  Predicted     : {predicted}")
    print(f"  Result        : {status}")

    print_separator("─")

    print(
        f"  Progress      : {case_number}/{total} cases processed"
    )
    print(
        f"  Remaining     : {remaining} cases"
    )

    print_separator("═")


def print_error(error):
    """
    Human-friendly terminal error display.

    Does not alter the stored error representation.
    """

    print()
    print_separator("!")
    print("  ⚠ PIPELINE ERROR")
    print_separator("!")
    print(f"  {error}")
    print_separator("!")


def print_daily_quota_alert():
    """
    Prominent terminal alert for Gemini daily quota exhaustion.

    The behavior is intentionally explicit because the user needs to know
    that changing API keys is required before continuing.
    """

    print()
    print()
    print_separator("!")
    print_separator("!")

    print("  🚨  GEMINI DAILY QUOTA EXHAUSTED  🚨")

    print_separator("!")
    print_separator("!")

    print()
    print("  This Gemini API key has reached its daily quota.")
    print()
    print("  ACTION REQUIRED:")
    print("    1. Change GEMINI_API_KEY to another API key.")
    print("    2. Restart the evaluation command.")
    print()
    print("  Already-completed cases have been saved.")
    print("  They will NOT be rerun automatically.")
    print()
    print("  Example:")
    print("    set GEMINI_API_KEY=YOUR_NEW_KEY")
    print("    python eval_pipeline.py")
    print()

    print_separator("═")


# ----------------------------------------------------------------------
# Loading / persistence
# ----------------------------------------------------------------------

def load_cases(cases_file):
    with open(cases_file, newline="") as f:
        return list(csv.DictReader(f))


def load_existing_results(results_file):
    if not os.path.exists(results_file):
        return {}

    with open(results_file, newline="") as f:
        rows = list(csv.DictReader(f))

    return {r["case_id"]: r for r in rows}


RESULT_FIELDNAMES = [
    "case_id",
    "account_id",
    "typology",
    "ground_truth",
    "expected",
    "predicted",
    "correct",
    "scammer_confidence",
    "legitimate_confidence",
    "resolution_confidence",
    "deciding_factor",
    "evidence_signals",
]


def save_results(results_by_case, results_file):
    rows = list(results_by_case.values())

    if not rows:
        return

    with open(results_file, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=RESULT_FIELDNAMES
        )
        writer.writeheader()
        writer.writerows(rows)


# ----------------------------------------------------------------------
# Rate-limit-aware LLM call wrapper
#
# IMPORTANT:
# No timing values have been changed.
# ----------------------------------------------------------------------

def call_with_backoff(fn, *args, **kwargs):

    for attempt in range(3):

        try:
            result = fn(*args, **kwargs)

            # Keep existing spacing exactly as before.
            time.sleep(CALL_SPACING_SECONDS)

            return result

        except genai_errors.ClientError as e:

            if getattr(e, "code", None) == 429:

                if "PerDay" in str(e):

                    print_daily_quota_alert()

                    # Existing behavior:
                    # terminate so already-completed cases remain saved.
                    sys.exit(1)

                wait = 20 * (attempt + 1)

                print(
                    f"\n  ⚠ Rate limited (429). "
                    f"Waiting {wait}s before retry "
                    f"{attempt + 1}/3..."
                )

                time.sleep(wait)

            else:
                raise

        except genai_errors.ServerError:

            wait = 15 * (attempt + 1)

            print(
                f"\n  ⚠ Gemini server overloaded (503). "
                f"Waiting {wait}s before retry "
                f"{attempt + 1}/3..."
            )

            time.sleep(wait)

    raise RuntimeError("Failed after 3 retries.")


# ----------------------------------------------------------------------
# One case - staged execution
# ----------------------------------------------------------------------

def run_pipeline(store, case):
    """
    Run one case in this exact visible order:

        1. Gather evidence
        2. Legitimate Hypothesis Agent
        3. Wait (handled by call_with_backoff)
        4. Scammer Hypothesis Agent
        5. Wait (handled by call_with_backoff)
        6. Contradiction Agent
        7. Wait (handled by call_with_backoff)

    The returned data structures are unchanged.
    """

    # Evidence gathering is local/non-Gemini work.
    evidence = gather_evidence(store, case)

    as_of = datetime.now(timezone.utc)

    # --------------------------------------------------------------
    # PATH B FIRST: LEGITIMATE
    # --------------------------------------------------------------

    print("\n  ▶ Calling Legitimate Hypothesis Agent...")

    path_b = call_with_backoff(
        evaluate_legitimate_hypothesis,
        evidence,
        as_of,
    )

    print_agent_output(
        "PATH B — LEGITIMATE HYPOTHESIS",
        path_b,
    )

    print_wait()

    # --------------------------------------------------------------
    # PATH A SECOND: SCAMMER
    # --------------------------------------------------------------

    print("\n  ▶ Calling Scammer Hypothesis Agent...")

    path_a = call_with_backoff(
        evaluate_scammer_hypothesis,
        evidence,
        as_of,
    )

    print_agent_output(
        "PATH A — SCAMMER HYPOTHESIS",
        path_a,
    )

    print_wait()

    # --------------------------------------------------------------
    # CONTRADICTION AGENT
    # --------------------------------------------------------------

    print("\n  ▶ Calling Contradiction Agent...")

    resolution = call_with_backoff(
        resolve_contradiction,
        path_a,
        path_b,
        evidence["typology"],
    )

    print_contradiction_output(resolution)

    return evidence, path_a, path_b, resolution


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the hypothesis + contradiction pipeline "
            "against mock cases."
        )
    )

    parser.add_argument(
        "--data_dir",
        default=DATA_DIR
    )

    parser.add_argument(
        "--cases_file",
        default=CASES_FILE
    )

    parser.add_argument(
        "--results_file",
        default=RESULTS_FILE
    )

    parser.add_argument(
        "--typology",
        default=None,
        help=(
            "only run cases of this typology "
            "(smurfing/reverse_smurfing/money_mule/"
            "account_swap/behavioral_deviation)"
        )
    )

    parser.add_argument(
        "--case_ids",
        default=None,
        help=(
            "comma-separated case_ids to (re)run, "
            "ignoring the skip-already-done logic"
        )
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="cap the number of cases run (smoke test)"
    )

    args = parser.parse_args()

    # --------------------------------------------------------------
    # Initialization
    # --------------------------------------------------------------

    store = DataStore(args.data_dir)

    all_cases = load_cases(args.cases_file)

    results_by_case = load_existing_results(
        args.results_file
    )

    # --------------------------------------------------------------
    # Determine pending cases
    # --------------------------------------------------------------

    if args.case_ids:

        target_ids = {
            c.strip()
            for c in args.case_ids.split(",")
        }

        pending = [
            c
            for c in all_cases
            if c["case_id"] in target_ids
        ]

        print(
            f"\nTargeted retest mode: "
            f"re-running {len(pending)} specific case(s).\n"
        )

    else:

        pending = [
            c
            for c in all_cases
            if (
                c["case_id"] not in results_by_case
                or results_by_case[c["case_id"]]["predicted"] == "ERROR"
            )
        ]

        if args.typology:

            pending = [
                c
                for c in pending
                if c["primary_trigger"] == args.typology
            ]

        already_done = len(all_cases) - len(pending)

        print()
        print_separator("═")
        print("  EVALUATION RESUME")
        print_separator("─")
        print(f"  Total cases       : {len(all_cases)}")
        print(f"  Already completed : {already_done}")
        print(f"  Remaining         : {len(pending)}")
        print_separator("═")
        print()

    # --------------------------------------------------------------
    # Apply limit
    # --------------------------------------------------------------

    if args.limit:
        pending = pending[:args.limit]

    total_to_process = len(pending)

    if total_to_process == 0:

        print()
        print_separator("═")
        print("  ✓ NOTHING TO RUN")
        print_separator("─")
        print("  All selected cases are already solved.")
        print_separator("═")
        print()

    # --------------------------------------------------------------
    # Process cases one by one
    # --------------------------------------------------------------

    for i, case in enumerate(pending):

        case_number = i + 1

        remaining = total_to_process - case_number

        expected = (
            "scammer"
            if case["ground_truth_label"] == "fraud"
            else "legitimate"
        )

        # ----------------------------------------------------------
        # Gather evidence BEFORE displaying the case.
        # ----------------------------------------------------------

        try:

            evidence = gather_evidence(
                store,
                case
            )

        except Exception as e:

            predicted = "ERROR"
            correct = False

            path_a = {}
            path_b = {}

            resolution = {
                "deciding_factor": str(e)
            }

            print_error(e)

            results_by_case[case["case_id"]] = {
                "case_id": case["case_id"],
                "account_id": case["account_id"],
                "typology": case["primary_trigger"],
                "ground_truth": case["ground_truth_label"],
                "expected": expected,
                "predicted": predicted,
                "correct": correct,
                "scammer_confidence": "",
                "legitimate_confidence": "",
                "resolution_confidence": "",
                "deciding_factor": str(e),
                "evidence_signals": case.get(
                    "evidence_signals",
                    ""
                ),
            }

            save_results(
                results_by_case,
                args.results_file
            )

            continue

        # ----------------------------------------------------------
        # Case header
        # ----------------------------------------------------------

        print_case_start(
            case,
            evidence,
            expected,
            case_number,
            total_to_process,
        )

        try:

            as_of = datetime.now(timezone.utc)

            # ======================================================
            # PATH B — LEGITIMATE
            # ======================================================

            print(
                "\n  [1/3] LEGITIMATE HYPOTHESIS"
            )
            print(
                "  Sending request to Gemini..."
            )

            path_b = call_with_backoff(
                evaluate_legitimate_hypothesis,
                evidence,
                as_of,
            )

            print_agent_output(
                "PATH B — LEGITIMATE HYPOTHESIS",
                path_b,
            )

            # ======================================================
            # PATH A — SCAMMER
            # ======================================================

            print_wait()

            print(
                "\n  [2/3] SCAMMER HYPOTHESIS"
            )
            print(
                "  Sending request to Gemini..."
            )

            path_a = call_with_backoff(
                evaluate_scammer_hypothesis,
                evidence,
                as_of,
            )

            print_agent_output(
                "PATH A — SCAMMER HYPOTHESIS",
                path_a,
            )

            # ======================================================
            # CONTRADICTION
            # ======================================================

            print_wait()

            print(
                "\n  [3/3] CONTRADICTION AGENT"
            )
            print(
                "  Sending request to Gemini..."
            )

            resolution = call_with_backoff(
                resolve_contradiction,
                path_a,
                path_b,
                evidence["typology"],
            )

            print_contradiction_output(
                resolution
            )

            # ======================================================
            # FINAL VERDICT
            # ======================================================

            predicted = resolution[
                "favored_hypothesis"
            ]

            correct = predicted == expected

            print_case_verdict(
                case=case,
                expected=expected,
                predicted=predicted,
                correct=correct,
                case_number=case_number,
                total=total_to_process,
                remaining=remaining,
            )

        except Exception as e:

            predicted = "ERROR"
            correct = False

            path_a = locals().get(
                "path_a",
                {}
            )

            path_b = locals().get(
                "path_b",
                {}
            )

            resolution = {
                "deciding_factor": str(e)
            }

            print_error(e)

            print(
                "\n  This case will remain marked as ERROR "
                "and can be retried on the next run."
            )

        # ----------------------------------------------------------
        # Persist exactly the same result fields as before.
        # ----------------------------------------------------------

        results_by_case[case["case_id"]] = {
            "case_id": case["case_id"],
            "account_id": case["account_id"],
            "typology": case["primary_trigger"],
            "ground_truth": case["ground_truth_label"],
            "expected": expected,
            "predicted": predicted,
            "correct": correct,
            "scammer_confidence": path_a.get(
                "confidence",
                ""
            ),
            "legitimate_confidence": path_b.get(
                "confidence",
                ""
            ),
            "resolution_confidence": resolution.get(
                "confidence",
                ""
            ),
            "deciding_factor": resolution.get(
                "deciding_factor",
                ""
            ),
            "evidence_signals": case.get(
                "evidence_signals",
                ""
            ),
        }

        save_results(
            results_by_case,
            args.results_file
        )

        # ----------------------------------------------------------
        # Between cases
        # ----------------------------------------------------------

        if remaining > 0:

            print()
            print_separator("·")
            print(f"  ✓ Case {case['case_id']} saved.")
            print(f"  ⏭ Next case: " f"{pending[i + 1]['case_id']}")
            print(f"  📋 {remaining} case(s) remaining.")

            print_separator("·")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    total = len(results_by_case)

    scored = [
        r
        for r in results_by_case.values()
        if r["predicted"] != "ERROR"
    ]

    correct_count = sum(
        1
        for r in scored
        if str(r["correct"]) == "True"
    )

    errored = total - len(scored)

    print()
    print_separator("═")
    print("  EVALUATION COMPLETE")
    print_separator("═")

    print(
        f"  Overall : "
        f"{correct_count}/{len(scored)} correct"
    )

    print(
        f"  Errors  : "
        f"{errored}"
    )

    print(
        f"  Total   : "
        f"{total} cases on file"
    )

    print_separator("─")

    # ------------------------------------------------------------------
    # By typology
    # ------------------------------------------------------------------

    by_typology = {}

    for r in scored:

        t = r["typology"]

        by_typology.setdefault(
            t,
            [0, 0]
        )

        by_typology[t][1] += 1

        if str(r["correct"]) == "True":
            by_typology[t][0] += 1

    print("\n  BY TYPOLOGY")
    print_separator("─")

    for t, (c, n) in sorted(
        by_typology.items()
    ):

        print(
            f"  {t:<22} {c}/{n}"
        )

    # ------------------------------------------------------------------
    # Wrong cases
    # ------------------------------------------------------------------

    wrong = [
        r
        for r in scored
        if str(r["correct"]) != "True"
    ]

    if wrong:

        print()
        print_separator("─")
        print(
            f"  WRONG CASES ({len(wrong)})"
        )
        print_separator("─")

        for r in wrong:

            print(
                f"\n  {r['case_id']} "
                f"({r['account_id']}, {r['typology']})"
            )

            print(
                f"    Expected : {r['expected']}"
            )

            print(
                f"    Got      : {r['predicted']}"
            )

            print(
                f"    Deciding : "
                f"{r['deciding_factor']}"
            )

    # ------------------------------------------------------------------
    # Error cases
    # ------------------------------------------------------------------

    error_cases = [
        r
        for r in results_by_case.values()
        if r["predicted"] == "ERROR"
    ]

    if error_cases:
        print()
        print_separator("─")
        print(f"  CASES STILL NEEDING RETRY "f"({len(error_cases)})")
        print_separator("─")

        for r in error_cases:
            print(f"  • {r['case_id']} " f"({r['account_id']})")

    # ------------------------------------------------------------------
    # Final file location
    # ------------------------------------------------------------------

    print()
    print_separator("═")
    print(f"  Results saved to:")
    print(f"  {args.results_file}")
    print_separator("═")
    print()


if __name__ == "__main__":
    main()