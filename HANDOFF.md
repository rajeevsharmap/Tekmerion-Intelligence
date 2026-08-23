# HANDOFF.md - What do I need to do right now?

> **Read `docs/backend_implementation_status.md` first** - it supersedes
> the "Immediate next steps" section below with a priority-ordered list
> validated against `docs/ARCHITECTURE.md` (the authoritative target
> architecture) via direct code inspection, including one real regression
> found that this file doesn't mention. This file is kept for its detailed
> narrative of what was tried and why; the status doc is the current
> source of truth for what to do next.

_Last updated: end of the session that fixed the end-to-end orchestration
gap (`run_pipeline.py`), removed junior/senior escalation logic from the
Detection Layer, fixed case-scoped evidence persistence, fixed the typology
dispatcher, and separated ground-truth data from live pipeline output._

## The exact task currently in progress

Nothing is mid-edit. The task just completed was a full architectural
review and fix against 13 specific violations the user identified (see
"What was broken and how it was fixed" below), followed by an actual
end-to-end run of the corrected pipeline for verification (not just a
code review - `run_pipeline.py` was executed against the real mock
dataset and its output was inspected). This doc + `README.md` +
`PROJECT_STATE.md` are the handoff checkpoint immediately after.

## What has already been completed

1. **`run_pipeline.py` created** - the single end-to-end orchestrator:
   `DataStore → run_detection_pipeline → bundle_alerts_into_cases → persist
   → for case in cases: generate_network_evidence → wrap_as_evidence →
   persist`, all in one process, one DataStore instance. This did not
   exist before this session - previously you had to run
   `detection_layer.py` then `network_layer.py` as two separate manual
   steps, and even then `network_layer.py`'s own `__main__` batched every
   case's evidence into one shared file.
2. **Detection Layer no longer decides investigator escalation.**
   `bundle_alerts_into_cases()` used to hardcode
   `assigned_investigator_tier: "junior"` and `escalated: False` on every
   case. Both removed. A case now only carries `status: "open"` at
   creation - grep confirms zero junior/senior/escalation logic remains in
   `detection_layer.py` outside of `classify_alert()`'s
   `initial_action: "escalate"` field, which is a per-alert severity
   recommendation (spec-mandated), not an investigator-tier decision.
3. **Ground truth and live pipeline output are now separate, unambiguous
   file sets.** `generate_mock_data.py` now writes
   `mock_data/ground_truth_cases.csv` / `ground_truth_alerts.csv` /
   `ground_truth_case_escalations.csv` (renamed from `cases.csv` /
   `suspected_alerts.csv` / `case_escalations.csv`, which previously
   collided in name with what should have been the live pipeline's own
   output but wasn't). `run_pipeline.py` writes the actual live output to
   `pipeline_output/suspected_alerts.csv` and `pipeline_output/cases.csv`,
   generated from scratch by evaluating the real rules against the real
   `DataStore` - it never reads any `ground_truth_*` file. `eval_pipeline.py`
   is the only script that reads ground truth, and only for scoring.
4. **Network Evidence Layer is genuinely case-scoped in persistence,
   not just in its function signature.** `generate_network_evidence(store,
   case)` was already a per-case call, but `network_layer.py`'s own
   `__main__` used to dump every case's evidence into one shared
   `network_evidence.json` array. `run_pipeline.py` now calls it once per
   case and writes one file per case (`pipeline_output/evidence/{case_id}.json`)
   - verified: 21 cases in, 21 evidence files out, every `case_id` in
   `cases.csv` has a matching evidence file, zero missing.
5. **Typology dispatcher rewritten as an explicit if/elif chain.** The old
   dispatcher had a double-negative boolean fallback
   (`typology == "money_mule" or typology not in (...)`) that happened to
   route the 4 known typologies correctly but would silently misroute any
   unrecognized typology (e.g. the mock data's `behavioral_deviation`
   label) into money-mule timeline treatment with no warning. Now: one
   explicit branch per typology, and an explicit, clearly-labeled
   `network_type: "unclassified"` fallback for anything else - verified by
   actually running the pipeline and checking `visualization_type` per
   typology (see table below).
6. **Account swap is now a genuine behavioral transaction-vs-time
   visualization, not just an event list.** Added
   `_compute_behavioral_summary()` - splits an account's transactions into
   "baseline" (outside a ±48h window around the anchor event) and "recent"
   (inside it), and computes average amount/frequency for each plus
   deviation ratios. `visualization_type` renamed from
   `security_transaction_timeline` to `behavioral_transaction_timeline` to
   stop it reading as graph-adjacent. Verified on a real case:
   `amount_deviation_ratio: 12.25` (baseline avg ₹39,926 vs. recent avg
   ₹489,260) - a real, computed number, not a placeholder.
7. **`wrap_as_evidence()` was silently dropping `source_transactions` and
   `network_scope`** - found while verifying the global-traversal-vs-case-
   scope distinction (point 8 below), not part of the user's original
   numbered list but a real gap in the same area. Fixed: both are now
   top-level fields on the persisted Evidence Store record, kept separate
   from `data` (the typology-specific evidence itself) specifically so the
   distinction between "this case's originating alerts" and "contextual
   transactions discovered via global traversal" stays inspectable in the
   output, not just true in theory.
8. **Global-traversal-vs-case-scope separation verified, not just
   assumed.** Confirmed on a real case: `case['alert_ids']` (2 alerts) vs.
   `evidence['source_transactions']` (15 transactions, globally discovered
   via BFS) are visibly different lists in the persisted output, and
   `network_layer.py` never mutates the `case` or `alert` objects it's
   given.
9. Reapplied two evidence-quality bug fixes from a prior session that this
   repo commit was missing before this session started: alert/case
   `created_at` timestamps now anchor to the real flagged transaction's
   timestamp (previously an independent random draw, which silently
   defeated all time-window filtering), and `evidence_builder.py` no
   longer falls back to attaching an account's *unrelated* real alerts to
   a case whose typology doesn't match anything `detect_all()` produces.
10. Fixed a stale path reference in `visualize_network.py`
    (`mock_data/detected_cases.json`, which no longer gets written by
    anything) to point at `pipeline_output/cases.json`.

## What remains

- **Investigation Auditor / real completeness scoring** - nothing computes
  a real completeness score anywhere in the live pipeline. `cases.csv`
  (both ground truth and live) either omits it or hardcodes it.
- **Regulatory Rule Engine / Regulatory RAG** - not started.
- **Investigation Auditor Agent, Case Completeness Score as a routing
  gate, and the "request more evidence / re-gather" loop back to the
  Evidence Gathering Agents** - not started. These are distinct from
  general "completeness scoring" mentioned above - see
  `PROJECT_STATE.md`'s "Progress against the fuller reference diagram"
  section for the full breakdown against the project's LangGraph
  multi-agent architecture diagram (Intake & Detection / LangGraph
  Multi-Agent State / Decision, Audit & Human Review lanes).
- **LangGraph is a declared dependency (`requirements.txt`) that is not
  actually used anywhere.** `grep -rn "langgraph\|StateGraph" backend/`
  returns zero matches. The three hypothesis/contradiction agents are
  currently called as plain sequential Python functions
  (`eval_pipeline.py`), not as nodes in a LangGraph `StateGraph` with
  conditional routing. If the project intends to actually use LangGraph
  for the auditor/completeness/re-gather loop, that's a structural rewrite
  of agent orchestration, not an add-on - see `PROJECT_STATE.md` before
  starting it.
- **UI Layer and Access Control Tiers (role-based evidence visibility)** -
  not designed, not built. Worth reading `PROJECT_STATE.md`'s note on this
  before building investigator auth/escalation - the reference diagram
  describes junior/senior as an access-control *routing* concern at the
  Human-in-the-Loop stage, not a field Case Intake or Detection Layer
  writes onto a case.
- **Human-in-the-loop action stage** - this is where investigator
  tier/escalation SHOULD live per the corrected architecture, and it does
  not exist as code yet. `mock_data/ground_truth_case_escalations.csv` is
  the only artifact that models what this stage's output would look like,
  and it's evaluation-only, never read by live code.
- **Next-Best-Action, Audit Trail, Case Memory** - not started.
- **A real Evidence Store** - `pipeline_output/evidence/{case_id}.json`
  files are a reasonable stand-in (genuinely case-keyed, one record per
  case) but there's no query layer, no `GET /api/cases/{case_id}/network-evidence`
  HTTP endpoint, no persistence beyond flat files.
- **`main.py` is still just a health-check stub** - `/` returns
  `{"status": "ok"}` and nothing else. `run_pipeline.py` is a script, not
  an API - wiring it behind FastAPI routes hasn't been attempted.
- **`eval_pipeline.py` has still never been run against the real Gemini
  API in this environment** (no network access to `googleapis.com`, no key
  available here). Only structural/plumbing validation has ever happened
  for that specific script, in an earlier session, via mocked LLM calls.
- **Junior/senior CSV-based investigator authentication** - mentioned by
  the user as a plan, still fully undesigned, and now explicitly confirmed
  to belong nowhere near Detection Layer or Case Intake when it is built.
- **Frontend / Cytoscape.js rendering** - not started; `network_layer.py`
  produces Cytoscape.js-ready JSON for smurfing/reverse_smurfing, but
  nothing renders it in a browser.

## Current bugs/errors

None known open as of this handoff, in the specific areas covered by this
session's review (orchestration, escalation logic, evidence persistence,
typology dispatch, case_id propagation). This was verified by actually
running `run_pipeline.py` end to end and inspecting the output - not just
reading the code. See "What was broken and how it was fixed" in
`PROJECT_STATE.md` for the full list with root causes.

**Known non-bug limitation, unrelated to this session's fixes, not
re-litigated here:** the mock generator still builds the *same* underlying
transaction pattern regardless of whether a case is later labeled `fraud`
or `legitimate` for the structuring typologies - this was diagnosed in a
prior session and remains unfixed. It affects `eval_pipeline.py` accuracy
ceiling, not anything touched in this session (Detection/Case/Evidence
persistence and orchestration).

## Which files are being worked on

None mid-edit right now. Files touched this session, all committed to
their final state:

- `backend/run_pipeline.py` (**new**)
- `backend/generate_mock_data.py`
- `backend/detection_layer.py`
- `backend/network_layer.py`
- `backend/visualize_network.py`
- `backend/eval_pipeline.py`
- `backend/agents/evidence_builder.py`

Not touched, unchanged from before: `backend/data_store.py`,
`backend/agents/scammer_hypothesis_agent.py`,
`backend/agents/legitimate_hypothesis_agent.py`,
`backend/agents/contradiction_agent.py`, `backend/main.py`, `frontend/`.

## What the previous agent tried

- Went through the user's 13-point numbered list literally, one at a time,
  reading the actual code before changing anything (not assuming the
  described bugs existed without confirming them against the file
  contents first - e.g. the "typology dispatcher bug" was traced through
  its actual boolean logic by hand before concluding it was a real-but-
  latent risk, not an active misrouting of `account_swap` specifically).
- Prioritized empirical verification over code review alone: after every
  structural change, re-ran `generate_mock_data.py` and/or
  `run_pipeline.py` and inspected the actual output files (not just "the
  code looks right") - e.g. confirmed 21/21 cases have a matching evidence
  file by joining `cases.csv` against the `evidence/` directory in Python,
  rather than asserting it.
- Found one additional bug not in the user's original list
  (`wrap_as_evidence()` dropping `source_transactions`/`network_scope`)
  while verifying point 8 (global-traversal-vs-case-scope separation) -
  fixed it in the same pass rather than leaving it for a future session,
  since it directly undermined the ability to *prove* that separation in
  the persisted output.
- Deliberately did NOT touch `mock_data/accounts.csv` /
  `transactions.csv` / `devices.csv` / `geo_events.csv` /
  `beneficiaries.csv` (the bank source-of-truth schema) or any of the
  detection rule dicts/scores in `detection_layer.py` - none of the 13
  points required it, and changing either would have risked invalidating
  work from earlier sessions for no benefit.

## What not to change

- **The `run_pipeline.py` orchestration order** - `DataStore` →
  `run_detection_pipeline` → `bundle_alerts_into_cases` → persist → loop
  over cases calling `generate_network_evidence` → `wrap_as_evidence` →
  persist. This is the architecture the user explicitly specified; don't
  reorder it or short-circuit it (e.g. don't generate evidence before
  cases exist, don't skip persisting alerts/cases before generating
  evidence).
- **`bundle_alerts_into_cases()` must never re-add investigator
  tier/escalation fields.** If a future task needs that concept, it
  belongs in a new human-in-the-loop/action-stage module, not here. This
  was explicitly re-confirmed as a requirement in two consecutive user
  messages this session - treat it as a hard constraint, not a stylistic
  preference.
- **`mock_data/ground_truth_*.csv` vs `pipeline_output/*.csv` must stay
  separate files, never merged, never read by the wrong consumer.**
  `eval_pipeline.py` reads ground truth (for scoring only).
  `run_pipeline.py` / `detection_layer.py` / `network_layer.py` must never
  read `ground_truth_*` files as input.
- **The explicit if/elif typology dispatcher in
  `generate_network_evidence()`** - don't collapse it back into a
  fallback-based structure for brevity; the explicitness is the fix.
- **`primary_trigger` semantics** (typology string, not a rule ID),
  **`CASE_BUNDLE_WINDOW_HOURS = 24`**, **`MAX_DEPTH = 3`**, **`min_rules=2`
  gating on smurfing/reverse_smurfing/money_mule**, and **the
  `gemini-3.6-flash` model name** - all carried over unchanged from prior
  sessions, still load-bearing, see `PROJECT_STATE.md` for why each one
  exists.

## Immediate next steps

1. Get a real `GEMINI_API_KEY` and run `python3 eval_pipeline.py --limit 5`
   as a smoke test against real ground truth - this has still never
   happened against the live API in any session so far.
2. Decide what "Evidence Store" should mean beyond flat JSON files per
   case - a query interface, even an in-process one
   (`get_evidence(case_id)` reading from `pipeline_output/evidence/`),
   would be a natural, low-risk next step before anything heavier.
3. Start the Investigation Auditor / completeness-score component - it's
   the next thing downstream of Evidence in the target architecture and
   nothing computes it today.
4. If/when investigator escalation is actually built, put it in a new,
   clearly-separate module (e.g. `human_review.py` or similar) that
   consumes `pipeline_output/cases.csv` + evidence files as input and
   produces its own escalation decision as output - never inside
   `detection_layer.py`.
5. Junior/senior CSV-based auth and the frontend are both still fully
   undesigned - flag to the user before starting either.

## How to verify the task is actually finished

```bash
cd backend
rm -rf mock_data pipeline_output __pycache__ agents/__pycache__

# 1. Regenerate bank data + ground truth
python3 generate_mock_data.py --outdir mock_data --num_accounts 220 --num_cases 38
ls mock_data/*.csv
# expect: accounts.csv, transactions.csv, devices.csv, geo_events.csv,
# beneficiaries.csv, ground_truth_cases.csv, ground_truth_alerts.csv,
# ground_truth_case_escalations.csv
# must NOT see: cases.csv, suspected_alerts.csv, case_escalations.csv
# (those names are retired)

# 2. Run the real end-to-end pipeline
python3 run_pipeline.py --demo_case
# expect: accounts scanned=220, alerts ~31, cases ~21, evidence objects == case count
# expect visualization types printed as:
#   smurfing -> network, reverse_smurfing -> network,
#   money_mule -> transaction_timeline, account_swap -> behavioral_transaction_timeline
# expect the demo_case block to print a real account_id -> alert_id(s) ->
# case_id -> evidence_id chain with no placeholders

# 3. Confirm every case has exactly one evidence file, no more, no less
python3 -c "
import csv, os
cases = list(csv.DictReader(open('pipeline_output/cases.csv')))
missing = [c['case_id'] for c in cases if not os.path.exists(f\"pipeline_output/evidence/{c['case_id']}.json\")]
assert not missing, missing
print(f'OK: all {len(cases)} cases have a matching evidence file')
"

# 4. Confirm zero escalation logic in Detection Layer
grep -ni "junior\|senior\|escalated" detection_layer.py
# every match should be either classify_alert()'s "initial_action": "escalate"
# (a severity label, not investigator assignment) or a comment explaining why
# tier/escalation fields were REMOVED - no field named assigned_investigator_tier
# or escalated should appear on any case dict

# 5. Confirm live output never touches ground truth
grep -n "ground_truth" run_pipeline.py detection_layer.py network_layer.py agents/evidence_builder.py
# expect: zero matches, or only in comments explaining the separation
```

The task is **not** finished until step 2 has actually been executed and
its printed numbers inspected - a code review alone does not confirm the
orchestration actually works.