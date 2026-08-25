# backend_implementation_status.md

Session-continuity checkpoint. Read this + `docs/ARCHITECTURE.md` before
touching any Python code. `ARCHITECTURE.md` is what the system *should*
do; this file is what it *actually* does right now, verified by
inspection and by running real code this session — not assumed from
prior session notes. **A prior session's claim in this exact file (a
"regression" in `wrap_as_evidence()`) turned out to be stale/incorrect —
see section 9 below.** Treat every status in this file as re-verifiable:
run the commands in section 6 yourself if in doubt.

**This session implemented CHECKPOINT 3 (Detection + Alert + Case Bundling
Correction)**: corrected `bundle_alerts_into_cases()` to actually verify
correlation (not just account+time coincidence) and record a structured
`bundle_reason`; added `relevant_transaction_ids` to alerts (additive) so
that correlation signal exists; fixed a genuine determinism bug where
`alert_id`/`case_id` used `uuid.uuid4()` (non-deterministic, draws from
`os.urandom`, unaffected by `random.seed(42)`) — both are now deterministic
content hashes; added 30 new automated tests (ground-truth isolation +
case-correlation policy). Evidence-object generation
(`evidence_model.py`/`network_layer.py`) was explicitly left untouched
(out of scope; its own `evidence_id`/`generated_at` nondeterminism is a
known, documented limitation, not a Checkpoint 3 blocker).

*(Prior session implemented CHECKPOINT 2 (Evidence Architecture): a
canonical, typed `EvidenceItem` model, per-typology-configurable required
evidence, deterministic weighted completeness, structured missing
evidence, and a 17-test suite — all in `backend/evidence_model.py`, wired
additively into `run_pipeline.py`. Investigator authority, action routing,
and large-scale evaluation were explicitly NOT started and remain
`[NOT IMPLEMENTED]`.)*

## 1. Architecture

See `docs/ARCHITECTURE.md` for the full target architecture. Summary of
the 9-stage pipeline and current status:

| Stage | Status |
|---|---|
| Raw data (`DataStore`) | ✅ [IMPLEMENTED] [VERIFIED] |
| Detection (4 typology detectors) | ✅ [IMPLEMENTED] [VERIFIED] — alerts now also carry `relevant_transaction_ids` (additive, **NEW Checkpoint 3**) and a deterministic content-hash `alert_id` (**fixed, Checkpoint 3** — was `uuid.uuid4()`) |
| Alerts | ✅ [IMPLEMENTED] [VERIFIED] |
| Case bundling | ✅ [IMPLEMENTED] [VERIFIED] (**Checkpoint 3**) — temporal window (configurable, recorded as `correlation_window_hours`) + actual correlation check (same typology, or shared `relevant_transaction_ids`) before merging; structured, deterministic `bundle_reason` on every case; `case_id` is now a deterministic content hash (was `uuid.uuid4()`) |
| Investigation (evidence gathering) | 🟡 [PARTIALLY IMPLEMENTED] (two separate, unmerged entry points — see section 3; unchanged this checkpoint) |
| Evidence (structured object model) | ✅ [IMPLEMENTED] [VERIFIED] — typed `EvidenceItem`s (`backend/evidence_model.py`), additive to the existing `data` blob. Unchanged this checkpoint (out of scope). **Known limitation (unchanged):** `evidence_id`/`generated_at` are non-deterministic (`uuid.uuid4()`/`datetime.now()`) — see section 4. |
| Evidence completeness | ✅ [IMPLEMENTED] [VERIFIED] — deterministic, weighted, computed from real evidence every run; zero randomness in live code. Unchanged this checkpoint. |
| Investigator authority | ✅ [IMPLEMENTED] [VERIFIED] (**Checkpoint 4**) — deterministic policy engine (`backend/authority_policy.py`), wired into both live entry points (`run_pipeline.py`, `network_layer.py`'s standalone `__main__`); computes `junior`/`senior` from real evidence completeness, typology risk, high-value transaction, network complexity, and (if supplied) contradiction/confidence signals — never randomly assigned, never hardcoded, never decided by Detection/Case Intake. |
| Action / escalation | 🟡 [PARTIALLY IMPLEMENTED] — **updated, Checkpoint 6:** Next-Best-Action recommendation, authorization enforcement (junior vs. senior), audit trail, human review, investigator action (incl. mandatory-reason override), and case memory are now all implemented and wired into `run_pipeline.py` (`next_best_action.py` / `audit_trail.py` / `investigator_action.py` / `case_state.py` / `case_memory.py` / `action_pipeline.py`, 38 tests passing). **Still not implemented:** real execution of an authorized action against an actual external banking system (simulated only) and SAR report generation (explicitly deferred to a later checkpoint). |

## 2. Completed changes (CHECKPOINT 3, this session)

1. **`relevant_transaction_ids` added to every live alert** (additive —
   `transaction_id` unchanged/preserved) in `detection_layer.py`: the full
   set of transactions each detector actually examined (its rolling
   window/pass-through set), not just the single anchor transaction. Wired
   into smurfing, reverse_smurfing, money_mule (account_swap already
   anchors to a single transaction; left as-is). Exists so (a) alerts are
   independently explainable without re-deriving the detector's window,
   and (b) case bundling can check real evidence overlap instead of only
   account+time.
2. **`bundle_alerts_into_cases()` rewritten** to stop merging purely on
   `account_id` + 24h window. New two-step policy: (i) `_temporal_clusters()`
   — same greedy time-window clustering as before, factored out;
   (ii) `_split_cluster_by_correlation()` — within a temporal cluster,
   union-find over `_pairwise_correlation()` (same typology, or overlapping
   `relevant_transaction_ids`) splits out alerts that only coincide on
   account+time but aren't actually related. A single-alert case now says
   so honestly (`bundle_reason: ["single_alert_case"]`) instead of implying
   a correlation decision that was never made.
3. **`bundle_reason` (structured, deterministic) added to every case** —
   `sorted({"same_primary_account", "within_case_window"} | correlation
   reasons)`, or `["single_alert_case"]`. **`correlation_window_hours`
   added to every case** recording the (configurable) window actually
   used. Both new CSV columns are additive (appended), existing columns
   unchanged/preserved (`case_id`, `account_id`, `alert_ids`,
   `primary_trigger`, `status` all untouched per the governing instruction).
4. **Determinism bug fixed:** `alert_id`/`case_id` generation switched from
   `uuid.uuid4()` (drawn from `os.urandom`, silently non-deterministic —
   see section 9 for the empirical proof) to a SHA-256 content hash of each
   object's own already-deterministic fields. Verified: two full pipeline
   runs against identical `mock_data/` now produce byte-identical
   `suspected_alerts.json`/`cases.json` (via `json.dumps(...,
   sort_keys=True)` equality — not just matching counts).
5. **30 new automated tests added** (`tests/test_ground_truth_isolation.py`
   — 13 tests, AST-based static scan of every live module + a dynamic
   proof that deleting all `ground_truth_*.csv` files changes nothing
   about live output; `tests/test_case_bundling.py` — 17 tests, covering
   all 10 required TEST cases from the Checkpoint 3 spec plus the
   account-swap causal-linkage regression guard). Full suite: **47/47
   passing** (17 pre-existing from Checkpoint 2 + 30 new).
6. **`evidence_model.py` and `network_layer.py` deliberately NOT modified**
   — per the governing instruction, evidence generation is downstream of
   Case Bundling and out of this checkpoint's scope. Their pre-existing
   `uuid.uuid4()`/`datetime.now()` non-determinism in `evidence_id`/
   `generated_at` is documented as a known limitation (section 4), not
   silently left undocumented and not "fixed" outside scope.



1. **Verified documentation against actual code and a real pipeline run**
   before changing anything (per the governing instruction) — see section
   9 for what that verification found (a documented "regression" that
   does not reproduce).
2. **Created `backend/evidence_model.py`** — the canonical `EvidenceItem`
   representation and completeness model. See section 8 for the schema
   and section "Evidence object model implementation" below for detail.
3. **Wired it into `run_pipeline.py`** as an additive step: after
   `wrap_as_evidence(network_evidence)` runs (unchanged), each case now
   also gets `evidence_items` (typed evidence list) and `completeness`
   (deterministic score) attached before persisting to
   `pipeline_output/evidence/{case_id}.json`. The existing `data` blob,
   `account_id`, `source_transactions`, `network_scope` fields are
   byte-for-byte unchanged in shape.
4. **Also wired it into `network_layer.py`'s standalone `__main__`** (its
   own case-scoped evidence CLI, separate from `run_pipeline.py`) so both
   entry points produce the same evidence shape and don't silently
   diverge.
5. **Built `backend/tests/test_evidence_model.py`** — 17 tests, unit +
   integration, all passing. See section 5.
6. **Ran the full pipeline end-to-end from freshly regenerated mock data**
   and inspected real persisted evidence JSON on disk (not just in-memory
   return values). See sections 6 and "Verification" below.
7. **Investigated `eval_pipeline.py`'s history** (currently an intentionally
   emptied 0-byte file — see section 11) at the user's request, without
   restoring or modifying it.

## 3. Evidence object model implementation (NEW — Checkpoint 2)

`backend/evidence_model.py` is new, self-contained, and imports nothing
from `network_layer.py`/`detection_layer.py` except reading their output
(`DataStore`, the `net` dict `generate_network_evidence()` returns) — it
does not modify either of those modules' own logic.

**`TYPOLOGY_EVIDENCE_REQUIREMENTS`** — one weighted list of required
evidence types per typology, weights summing to 1.0 (enforced by
`test_weights_sum_to_one`), declared once as data, not scattered across
if/elif branches:

- `smurfing` / `reverse_smurfing`: reuse `ARCHITECTURE.md`'s Phase 7
  example weighting verbatim (`transaction_chain` 0.20, `temporal_pattern`
  0.15, `counterparty_relationship` 0.15, `beneficiary_information` 0.10,
  `device_information` 0.15, `geo_information` 0.10, `source_of_funds`
  0.15).
- `money_mule`: new table built from `ARCHITECTURE.md`'s "Required
  evidence" bullet list for money_mule, grouped to the granularity the
  codebase can actually check today (`inbound_transaction_chain` 0.20,
  `outbound_transaction_chain` 0.15, `pass_through_timing` 0.15,
  `amount_retention_ratio` 0.15, `counterparty_relationship` 0.10,
  `beneficiary_information` 0.10, `device_information` 0.075,
  `geo_information` 0.075).
- `account_swap`: new table, same approach (`device_information` 0.15,
  `sim_change_evidence` 0.15, `geo_information` 0.15,
  `impossible_travel` 0.10, `high_value_transaction` 0.15,
  `beneficiary_information` 0.15, `behavioral_baseline` 0.15).
- Unrecognized/"unclassified" typology (network_layer.py's own honest
  fallback): **no table exists** — `build_evidence_items()` returns `[]`,
  `compute_completeness()` reports `weighted_score: None,
  method: "no_requirement_table_for_typology"`. Never guessed, never
  borrowed from another typology.

**`build_evidence_items(store, case, net)`** — one checker function per
evidence_type (15 total), each reading real data off `store`
(`bene_by_account`, `devices_by_account`, `geo_by_account`) or `net`
(the raw `generate_network_evidence()` return — nodes/edges, transactions/
summary, or events/behavioral_summary depending on typology). Every
`EvidenceItem` carries real `source_record_ids` when available (real
transaction/account/beneficiary/device/geo IDs, not placeholders).

**`compute_completeness(evidence_items)`** — deterministic; no branch of
this function or its callees imports or calls Python's `random` module
(enforced by `test_completeness_never_uses_random_module`, a static
source-scan test). Returns both a weighted score (importance-weighted, the
"preferred version" per `ARCHITECTURE.md`) and a simple
available/required ratio (the "simple version"), so both are inspectable
on the persisted record.

**Missing evidence** is a structured object, never a free-text string:
`{"reason": "<snake_case reason>", "severity": "critical"|"moderate"}`,
where `severity` is a deterministic function of the item's configured
weight (`>= 0.15` → `critical`, else `moderate`) — same input always
produces the same severity, verified by
`test_severity_is_deterministic_function_of_weight`.

## 4. Remaining work

In priority order, matching `ARCHITECTURE.md`'s stage list. Item 2 (bundle
reason) from the prior version of this document is now DONE (Checkpoint
3); renumbered/updated accordingly:

1. **Investigator authority policy engine** — a real function computing
   junior/senior from completeness (real, computed by Checkpoint 2's
   `evidence_model.py`) + typology risk + confidence + contradiction
   state. Must not live in `detection_layer.py` (confirmed correct
   exclusion, preserve it). **Explicitly out of scope for Checkpoint 3 —
   Checkpoint 4.**
2. ~~Bundle reason on `bundle_alerts_into_cases()` output~~ — **DONE
   (Checkpoint 3).** See section 2 above and `ARCHITECTURE.md`'s "Bundle
   reason" section.
3. **Ground-truth network model with stable IDs** (`GT-SMURF-001` etc.,
   `fraud_networks.json`) — needed before network-level evaluation
   metrics can exist. Touches only `generate_mock_data.py` and
   evaluation code, never live detection/investigation code. Unchanged
   this checkpoint.
4. **Action/escalation stage** — depends on 1 above existing first.
5. **Missing-evidence-driven escalation policy** — the structured
   `missing_reason`/`severity` Checkpoint 2 added is the direct input
   this policy needs; the policy itself (what to do given a `critical`
   gap) is not yet built.
6. **`eval_pipeline.py` rebuild** — the file is currently intentionally
   empty (0 bytes; the user deleted the prior 984-line version because it
   did not meet expectations). See section 11 for the investigation of
   what existed, what was wrong with it, and what should/shouldn't be
   recovered. **Not touched, not restored, not rebuilt this session per
   explicit instruction.**
7. **LLM privacy/masking architecture** — lower priority; no prior version
   exists to restore (see `ARCHITECTURE.md`'s "API/LLM safety" section).
8. **Evidence-stage determinism** — ~~`evidence_id`/`generated_at` in
   `network_layer.py`/`evidence_model.py` use `uuid.uuid4()`/
   `datetime.now()`~~ **RESOLVED, see CHECKPOINT 7 below.** As of
   Checkpoint 7, `evidence_id` (`evidence_model.py`), `event_id`
   (`audit_trail.py`), and every other in-pipeline identifier are
   SHA-256 content hashes of their real inputs — `uuid.uuid4()` is not
   called anywhere in the live pipeline (confirmed by repo-wide grep;
   see Checkpoint 7's determinism write-up for the full trace and a
   double-run verification with zero diffs, `generated_at`/`timestamp`
   wall-clock fields aside).
9. **Broader correlation signals for case bundling** — the current
   correlation policy (Checkpoint 3) only has `typology` and
   `relevant_transaction_ids` to work with, because the `Alert` object
   doesn't carry beneficiary/device/geo IDs. Shared-beneficiary,
   shared-device, and network-connectivity correlation (all listed as
   *potential* dimensions in `ARCHITECTURE.md`'s "Bundle reason" section)
   remain unimplemented until either the alert schema is extended or case
   bundling is allowed to peek at investigation-stage data (the latter
   would blur the Detection/Case-Bundling vs. Investigation boundary and
   needs an explicit decision, not a silent workaround).
10. **Remaining test coverage** — 47/47 tests pass (17 evidence-model,
    Checkpoint 2; 17 case-bundling + 13 ground-truth-isolation, Checkpoint
    3). `ARCHITECTURE.md`'s full test list (section "Testing requirements")
    still has open items requiring the authority/escalation engine and
    network-layer/graph-reconstruction correctness checks (items 5, 6, 7,
    9 partially, 10, 11, 12, 14, 15, 16) — out of Checkpoint 3's scope.

## 5. Tests passed / failed

**47/47 passing** (`backend/tests/`, run via `cd backend && python3 -m
pytest tests/ -v`):

| Test file | Count | Result |
|---|---|---|
| `test_evidence_model.py` (Checkpoint 2) | 17 | all PASSED |
| `test_case_bundling.py` (**NEW, Checkpoint 3**) | 17 | all PASSED |
| `test_ground_truth_isolation.py` (**NEW, Checkpoint 3**) | 13 | all PASSED |

`test_case_bundling.py` covers all 10 required TEST cases from the
Checkpoint 3 spec (multi-alert same-account merging; alerts outside the
window staying separate; different typologies NOT merging on account+
window alone, and DOING so when they share a transaction anchor;
deterministic/explainable `bundle_reason`; no ground-truth/network_id
field required for bundling; one account producing multiple live alerts;
cases referencing only real input alert IDs; determinism of repeated
pipeline runs and of `bundle_alerts_into_cases()` given a fixed/shuffled
alert list; and the account-swap causal-linkage regression guard).

`test_ground_truth_isolation.py` covers: an AST-based static scan of every
live module (`data_store.py`, `detection_layer.py`, `evidence_model.py`,
`network_layer.py`, `run_pipeline.py`, `main.py`, all four `agents/*.py`)
for any ground-truth reference in actual code (imports, non-docstring
string literals, attribute access, identifiers — docstrings/comments
excluded, since those may legitimately describe the separation); a sanity
check that `ground_truth_*.csv` files actually exist (so the isolation
proof isn't vacuous); a dynamic proof that deleting every
`ground_truth_*.csv` from a copy of `mock_data/` produces byte-identical
live pipeline output; and a check that no live alert/case dict carries a
`ground_truth`-prefixed key.

This is 47/47 of all tests in the repository — not a subset.

## 6. Current pipeline command

```bash
cd backend
python3 generate_mock_data.py --outdir mock_data --num_accounts 220 --num_cases 38
python3 run_pipeline.py --demo_case
python3 -m pytest tests/ -v
```

**Note (Checkpoint 3):** the Checkpoint 3 spec's suggested command
(`python generate_mock_data.py --seed 42`) does not match the actual CLI —
there is no `--seed` flag (seed 42 is hardcoded at import time; see
`ARCHITECTURE.md`'s "Determinism & reproducibility" section, "Recommended
improvement" — unchanged, still not implemented). Ran the command above
instead, which is what the file's own prior sections already documented as
correct.

`run_pipeline.py` is committed to the repo (added in commit `88068ce`,
alongside this checkpoint's predecessor doc commit). This checkpoint's
changes to it are limited to the CSV field additions already described in
section 2 (item 3) — no existing behavior removed.

Evaluation (separate, not part of the live pipeline, and currently
non-functional — see section 11):
```bash
export GEMINI_API_KEY=...
python3 eval_pipeline.py --limit 10   # eval_pipeline.py is currently 0 bytes - see section 11
```

## 7. Current output locations

- `mock_data/accounts.csv`, `transactions.csv`, `devices.csv`,
  `geo_events.csv`, `beneficiaries.csv` — raw data.
- `mock_data/ground_truth_alerts.csv`, `ground_truth_cases.csv`,
  `ground_truth_case_escalations.csv` — ground truth (flat, prefixed).
- `pipeline_output/suspected_alerts.csv`/`.json`,
  `pipeline_output/cases.csv`/`.json`,
  `pipeline_output/evidence/{case_id}.json` — live pipeline output.
  **Re-confirmed this session: `run_pipeline.py` never reads any
  `ground_truth_*` file** (`grep -n "ground_truth" detection_layer.py
  run_pipeline.py evidence_model.py` → only explanatory comments, no
  reads). Evidence files now additionally carry `evidence_items` and
  `completeness` (see section 8).
- `eval_results.csv` (gitignored) — evaluation output, not currently
  produced (see section 11).

## 8. Important schema decisions

- `primary_trigger` on a case is the **typology string** (`"smurfing"`),
  never a rule ID — unchanged, still correct, still depended on by
  `evidence_model.py`'s dispatch (exact-string-match against
  `TYPOLOGY_EVIDENCE_REQUIREMENTS` keys).
- `ground_truth_*` filename prefix is load-bearing — `eval_pipeline.py`
  was the only script permitted to read files with this prefix (moot
  while that file is empty; the constraint should be preserved whenever
  it's rebuilt).
- Case objects (both live and ground-truth) carry **no**
  `assigned_investigator_tier`/`escalated` field — deliberate, confirmed
  still true this session (`grep -n "assigned_investigator_tier"
  backend/*.py backend/agents/*.py` → zero matches). Correct per
  `ARCHITECTURE.md`'s authority model; stays this way until the actual
  policy engine (remaining-work item 1) exists.
- **`wrap_as_evidence()`'s output schema is unchanged by this checkpoint**:
  `{evidence_id, case_id, account_id, evidence_type, typology, source,
  confidence, data, source_transactions, network_scope, generated_at}` —
  all fields present, verified against real persisted files (see section
  9). This checkpoint adds two NEW top-level keys on top of that,
  attached by `run_pipeline.py` after `wrap_as_evidence()` runs (not
  inside `wrap_as_evidence()` itself, keeping that function's existing
  contract untouched):
  - `evidence_items`: `list[EvidenceItem]`, one per typology-required
    evidence type. Each item:
    ```json
    {
      "evidence_id": "EVD-81A4204E",
      "case_id": "CASE-0E475C50",
      "evidence_type": "transaction_chain",
      "source": "network_evidence_layer",
      "source_record_ids": ["TXN000782", "TXN001444", "..."],
      "required": true,
      "weight": 0.2,
      "available": true,
      "quality": "high",
      "supports": ["smurfing"],
      "contradicts": []
    }
    ```
    or, for a missing item:
    ```json
    {
      "evidence_id": "EVD-C50F9727",
      "evidence_type": "source_of_funds",
      "source": "not_modeled_in_dataset",
      "source_record_ids": [],
      "required": true,
      "weight": 0.15,
      "available": false,
      "quality": null,
      "supports": [],
      "contradicts": [],
      "missing_reason": {"reason": "documentation_not_available", "severity": "critical"}
    }
    ```
  - `completeness`:
    ```json
    {
      "weighted_score": 85.0,
      "simple_score": 85.7,
      "required_count": 7,
      "available_count": 6,
      "missing": [{"evidence_type": "source_of_funds", "reason": "documentation_not_available", "severity": "critical"}],
      "method": "deterministic_weighted_availability"
    }
    ```
    For an unclassified typology: `{"weighted_score": null, "simple_score": null, "required_count": 0, "available_count": 0, "missing": [], "method": "no_requirement_table_for_typology"}`.

## 9. Regression claimed by a prior session — investigated, NOT reproduced

A prior version of this document claimed `wrap_as_evidence()` drops
`account_id`, `source_transactions`, and `network_scope` before
persisting, citing a specific file (`pipeline_output/evidence/
CASE-02B2367B.json`) as evidence. **That file no longer exists** (case
IDs are regenerated fresh — using a fixed random seed — every time
`generate_mock_data.py` runs, so exact case IDs are not stable across
sessions), so the original claim couldn't be re-checked against the
literal file cited. It was instead re-checked against:

1. **The function's source code** (`network_layer.py`, `wrap_as_evidence()`,
   currently lines 538–567): the returned dict explicitly includes
   `"account_id": network_response["account_id"]`,
   `"source_transactions": network_response["source_transactions"]`, and
   `"network_scope": network_response["network_scope"]` as top-level
   keys — not dropped.
2. **A real, freshly generated persisted evidence file** this session
   (`pipeline_output/evidence/CASE-0E475C50.json`): top-level keys are
   `['evidence_id', 'case_id', 'account_id', 'evidence_type', 'typology',
   'source', 'confidence', 'data', 'source_transactions', 'network_scope',
   'generated_at', 'evidence_items', 'completeness']` — all three
   previously-claimed-missing fields are present on disk.
3. **A dedicated regression-guard test**,
   `test_wrap_as_evidence_preserves_scope_fields`, added this session
   specifically to pin this down going forward — PASSED.

**Conclusion: [VERIFIED] not reproduced. `wrap_as_evidence()` is not
currently broken.** The most likely explanation is that a genuine fix
made in an earlier session (as the prior claim described) was
subsequently committed to `main` — commit `88068ce` shows
`network_layer.py` changed (+13/-5 lines) in the same commit that added
`run_pipeline.py`, consistent with the fix landing there — but the
documentation describing it as still-broken was never updated to match.
**This file previously repeated that stale claim; it has been corrected
here.** Lesson for future sessions: always re-verify a claimed regression
against current code and a fresh run before treating it as still open,
exactly as this session's governing instructions required.

## 10. Next recommended task

Checkpoint 2 is complete and verified. Per the governing instruction for
this session, **do not** start investigator authority, escalation/action
routing, or the evaluation rebuild without further instruction. When
authorized to continue, the natural next steps (see section 4) are, in
order: (1) investigator authority policy engine, since completeness now
exists as real input for it, or (3) the ground-truth network model, since
several evaluation metrics are blocked on it. `eval_pipeline.py`'s rebuild
(section 11) is a separate decision the user will make directly — this
document just records the investigation findings.

## 11. `eval_pipeline.py` history investigation (NOT restored, NOT rebuilt)

Per explicit instruction, `eval_pipeline.py` was **not modified**. It is
currently an intentionally empty 0-byte file (`git log --follow --
backend/eval_pipeline.py` shows it was 984 lines as of commit `be195e7`,
then removed to 0 bytes in commit `88068ce` — the same commit that added
`run_pipeline.py` and restructured the frontend — which is consistent
with the user's statement that this was an intentional deletion, not
accidental data loss). The historical version (984 lines) was inspected
at `git show be195e7:backend/eval_pipeline.py` and read in full. Findings:

### What useful evaluation logic existed

- **Resume/skip-already-done logic**: results keyed by `case_id` in
  `eval_results.csv`, reloaded on startup; a case already scored
  (not `"ERROR"`) is skipped on the next run. Lets a long, expensive LLM
  evaluation run be safely interrupted and resumed.
- **Rate-limit/quota-aware retry wrapper** (`call_with_backoff`):
  catches Gemini `ClientError` 429 (rate limit → exponential-ish backoff
  and retry, up to 3 attempts; daily-quota-specific 429 → prints a loud
  `print_daily_quota_alert()` and exits cleanly, preserving all
  already-saved results) and `ServerError` (503 → backoff and retry).
  This is real, correctly-scoped infrastructure with no obvious bugs.
- **Per-case error isolation**: an exception on one case (evidence
  gathering or an agent call) is caught, the case is marked `"ERROR"` in
  the results file, and the loop continues to the next case rather than
  crashing the whole run.
- **CLI ergonomics**: `--typology` (filter to one typology),
  `--case_ids` (targeted re-run of specific cases, bypassing the
  skip-already-done logic), `--limit` (smoke-test cap).
- **Verbose live terminal display**: each agent's confidence/narrative/
  supporting_evidence printed immediately as it completes, plus a
  per-case verdict and progress counter — genuinely useful for a human
  watching a long run, distinct from the machine-readable CSV output.
- **Summary reporting**: overall accuracy, per-typology accuracy
  breakdown, a listing of wrong cases with their `deciding_factor` (for
  debugging why the agents got a case wrong), and a listing of
  still-erroring cases to retry.
- **CSV result schema** (`RESULT_FIELDNAMES`): case_id, account_id,
  typology, ground_truth, expected, predicted, correct, three confidence
  scores, deciding_factor, evidence_signals — a reasonable, flat,
  spreadsheet-friendly shape.

### What was architecturally wrong or insufficient

This is the important part, and it's also exactly what
`ARCHITECTURE.md`'s own "Ground-truth evaluation methodology" section
already said before the file was deleted (that section was written
against the pre-deletion version and is still accurate):

1. **It measures exactly one of the twelve metrics `ARCHITECTURE.md`
   specifies** — whether the contradiction agent's `favored_hypothesis`
   matches `ground_truth_label` (fraud vs. legitimate), broken down by
   typology. It never measures detection precision/recall, alert-to-case
   bundling accuracy, network reconstruction accuracy, evidence-
   completeness accuracy, missing-evidence-identification accuracy, or
   junior/senior routing accuracy — all still open per `ARCHITECTURE.md`.
   (To be fair, several of those were impossible to measure at the time
   this file existed, since the real completeness/authority computations
   didn't exist yet — but the file's *structure* also gave no obvious
   place to add them later; scoring logic and terminal-display logic are
   fully interleaved throughout `run_pipeline()`/`main()`, not separated
   into independent, addable scorers.)
2. **It evaluates against `ground_truth_cases.csv` directly, never
   against the live pipeline's own detected cases**
   (`pipeline_output/cases.json`, produced by `run_pipeline.py`). This
   means it never measures whether Detection Layer / Case Bundling
   themselves found the right things — it assumes the case list is
   already correct and only scores the LLM hypothesis layer downstream of
   that assumption. Per `ARCHITECTURE.md`'s "Alert ≠ case ≠ fraud
   network" section, this conflates two genuinely different questions
   ("did detection find the right fraud networks" vs. "given a case,
   did the LLM agents call it correctly") that should be measured
   separately.
3. **No ground-truth network model exists to evaluate against** —
   `fraud_networks.json` with stable network IDs (remaining-work item 3)
   doesn't exist yet, so even if the file were rebuilt today, network-
   level detection recall still couldn't be computed. This is a
   dependency, not a flaw in the old file itself.
4. **Binary expected/predicted collapses a richer signal.** A case where
   the correct outcome is "insufficient evidence, escalate" isn't well
   represented by forcing a fraud/legitimate binary — the old file had no
   third outcome bucket, and (now that Checkpoint 2 makes real
   completeness available) there's a real completeness/missing-evidence
   signal this scoring never had access to check the agents' reasoning
   against.
5. **Terminal display and scoring/persistence logic are fully
   interleaved** (verbose `print_*` calls scattered through the same
   functions that compute `correct`/save the CSV) — reasonable for
   interactive use, but makes it hard to also run headless/in CI, or to
   swap in a different scoring methodology without touching the display
   code too.
6. **Tightly coupled to one LLM provider's exception types**
   (`google.genai.errors.ClientError`/`ServerError`) directly in the
   retry wrapper — fine for a single-provider hackathon MVP, but worth
   knowing if provider-agnostic evaluation is ever wanted.

### What should be recovered

These pieces had no design flaw found and are worth keeping close to
as-is when rebuilding:
- The resume/skip-already-done + `--case_ids`/`--typology`/`--limit`
  CLI ergonomics.
- The rate-limit/quota-aware `call_with_backoff` retry wrapper and
  `print_daily_quota_alert()` behavior — genuinely correct, working
  infrastructure; `ARCHITECTURE.md` itself already flagged this as
  "implemented... preserve it, don't rebuild it" before the file was
  deleted, and nothing found this session changes that assessment.
- Per-case error isolation (one bad case shouldn't kill the whole run).
- The general idea of a flat CSV results output, though the schema
  should be extended (see below).

### What should instead be rebuilt from scratch

- **The scoring methodology itself**, restructured around
  `ARCHITECTURE.md`'s full metric list rather than only hypothesis-agent
  accuracy — at minimum, once their prerequisites exist: detection
  precision/recall (needs remaining-work item 3, the ground-truth
  network model), evidence-completeness accuracy (**now possible** —
  Checkpoint 2's `evidence_model.py` produces a real, deterministic
  `completeness["weighted_score"]` that can finally be compared against
  `ground_truth_cases.csv`'s `completeness_score` column, which is new
  since this checkpoint and wasn't available when the old file was
  written), and junior/senior routing accuracy (needs the authority
  policy engine, remaining-work item 1).
- **A case-source decision**: whether to evaluate against
  `ground_truth_cases.csv` (current behavior) or against
  `run_pipeline.py`'s own live-detected cases cross-referenced with
  ground truth (needed to measure detection accuracy, not just downstream
  LLM accuracy) — likely both, as two distinct evaluation modes/outputs,
  not a replacement of one by the other.
- **Separation of scoring/persistence from terminal display** — so a
  non-interactive/scriptable path exists alongside the current verbose
  interactive one, and new metrics can be added without touching print
  formatting.
- **Whether/how to check agent-cited evidence against real
  `source_record_ids`** — the old file never validated that a hypothesis
  agent's `supporting_evidence` narrative actually corresponds to real
  evidence (vs. a plausible-sounding hallucination); Checkpoint 2's typed
  `evidence_items` with real `source_record_ids` makes this newly
  possible to check, and it didn't exist as a checkable thing before.

This investigation is documentation only — no code from the historical
version has been copied into the live repository.

## Checkpoints

**CHECKPOINT 1: MDS architecture update.** Added `docs/ARCHITECTURE.md`
and this file. No Python files touched in that session; `run_pipeline.py`
committed alongside it (unchanged implementation code carried over, not
new work product of that session).

**CHECKPOINT 2: Evidence Architecture.** Adds
`backend/evidence_model.py` (canonical `EvidenceItem` model +
deterministic weighted completeness), wires it additively into
`run_pipeline.py` and `network_layer.py`'s standalone `__main__`, adds
`backend/tests/test_evidence_model.py` (17 tests, all passing),
corrects a stale "regression" claim from this document's prior version
(section 9), and documents (without touching) `eval_pipeline.py`'s
deletion history (section 11). Verified via a fresh end-to-end pipeline
run (220 accounts → 31 alerts → 21 cases → 21 evidence records, all with
typed evidence items and a deterministic completeness score) and direct
inspection of real persisted evidence JSON on disk. Investigator
authority, action/escalation, and the evaluation rebuild were explicitly
NOT started that session.

**CHECKPOINT 3 (this session): Detection + Alert + Case Bundling
Correction. [COMPLETE] [VERIFIED].** Adds `relevant_transaction_ids` to
live alerts (additive); rewrites `bundle_alerts_into_cases()` to require
actual correlation (same typology, or a shared transaction anchor) before
merging alerts beyond a single-alert case, instead of merging on
account+time-window alone; adds structured, deterministic `bundle_reason`
and `correlation_window_hours` to every case; fixes a genuine
determinism bug (`alert_id`/`case_id` were `uuid.uuid4()`-based, silently
non-deterministic across identical-input reruns — now SHA-256 content
hashes); adds 30 new tests (`test_case_bundling.py`,
`test_ground_truth_isolation.py`) — full suite now 47/47 passing.
Verified via two full pipeline runs against identical `mock_data/`:
`suspected_alerts.json`/`cases.json` are byte-identical (including IDs);
`evidence/*.json` differs only in `evidence_id`/`generated_at` (confirmed
across all 21 files), which is `evidence_model.py`/`network_layer.py`'s
pre-existing, documented, and deliberately-untouched non-determinism —
out of this checkpoint's scope. Ground-truth isolation reconfirmed
repo-wide (Python + JS/TS/JSON/YAML), not just re-asserted. Did not touch
`evidence_model.py`; did not restore `eval_pipeline.py`; did not start
investigator authority, action/escalation, or the ground-truth network
model. Alert/case counts on the checked-in dataset are unchanged from
pre-Checkpoint-3 (31 alerts → 21 cases) — the fix changes *why* cases are
bundled and makes it inspectable, not how many bundles occur on this
particular generated dataset.

**CHECKPOINT 4 (this session): Investigator Authority / Escalation Policy
Engine. [COMPLETE] [VERIFIED].** Adds `backend/authority_policy.py`: a
single deterministic decision function, `assess_authority()`, that reads
only already-computed upstream signals (Checkpoint 2's `evidence_items`/
`completeness`, `network_layer.py`'s `net`/pattern output, the case's own
already-computed alert severities) and returns a structured
`junior`/`senior` routing decision with machine-readable reason codes
(`critical_evidence_missing`, `high_risk_typology`, `high_value_transaction`,
`complex_network`, `unresolved_contradiction`, `junior_action_limit_exceeded`,
or the three positive reasons when junior-authorized) — never free text,
never random, never reads ground truth. Wired additively into both live
entry points (`run_pipeline.py` per-case loop and `network_layer.py`'s
standalone `__main__`) as `evidence["authority"]`, so every persisted
evidence JSON now carries the authority decision alongside its evidence
items — the representation the later SAR/reporting stage will consume.
`test_ground_truth_isolation.py`'s `LIVE_MODULES` list now includes
`authority_policy.py`.

Two genuine defects found and fixed while finishing the module a prior
session in this same checkpoint had left mid-flight:
1. **Structural false-positive on `critical_evidence_missing`.** The
   `source_of_funds` evidence type is permanently unavailable dataset-wide
   (`evidence_model.py`'s own honest, documented gap — its checker returns
   not-available unconditionally for every case/typology, not just some).
   Because its severity is "critical", the original `critical_missing`
   check fired on *every single case in the system*, making junior
   authorization structurally unreachable regardless of how clean or
   complete the rest of a case's evidence was — this is what the prior
   session's investigation into node/edge counts was chasing without
   finding the real cause. Fixed by adding
   `AUTHORITY_POLICY["structural_gap_reasons"]`, a config set of
   missing-evidence reason codes that represent a dataset-wide permanent
   limitation rather than a case-specific gap; those items are excluded
   from the `critical_evidence_missing` trigger (they still appear
   untouched in `missing_evidence` for transparency). This is a real
   implementation fix, not a threshold tuned to hit a distribution —
   confirmed by two hand-built fixture tests
   (`test_clean_low_risk_case_is_junior`,
   `test_moderate_only_missing_evidence_stays_junior_if_above_threshold`).
2. **Three static-guard test failures** (`test_never_uses_random_module`,
   `test_never_uses_uuid_or_wall_clock`,
   `test_source_never_mentions_ground_truth_outside_docstrings`) caused by
   the module's own docstrings literally containing the banned substrings
   they were describing (e.g. "no `import random`" inside a docstring
   trips a whole-file substring scan for `"import random"`). Fixed by
   rewording the documentation only — no behavior change.

All 77 backend tests pass (23 in `test_authority_policy.py` + 54 existing).
Verified via a fresh end-to-end pipeline run (220 accounts → 31 alerts →
21 cases → 21 evidence records, every one carrying a well-formed
`authority` block) and direct inspection of persisted evidence JSON.

**Known, documented limitation carried forward (not a defect):** on the
checked-in mock dataset, all 21 real cases currently route to `senior`
(via `high_value_transaction`/`complex_network`/`high_risk_typology`/
`junior_action_limit_exceeded` — never via the fixed structural-gap bug
above). This is a property of the mock data's fraud-detection thresholds
naturally producing higher-severity alerts, not a policy defect — the
policy's junior path is proven correct and reachable via hand-built
fixtures in `test_authority_policy.py` (`test_clean_low_risk_case_is_junior`
et al.), per this checkpoint's own instruction not to tune fraud-detection
thresholds or the policy just to manufacture a junior case on this
particular dataset. Confidence and contradiction-state signals are also
still approximated (`confidence_source: "derived_from_evidence_quality"`,
`contradiction_state: "not_evaluated"`) because the LLM contradiction/
hypothesis agents are not yet wired into `run_pipeline.py`'s live
per-case loop — documented in `authority_policy.py`'s own docstring, not
hidden.

**CHECKPOINT 5 (this session, across multiple sessions): Regulatory
Compliance Rule Engine + Regulatory RAG + Investigation Auditor + Case
Completeness Score + bounded targeted re-gather, made jurisdiction-aware.
[COMPLETE] [VERIFIED].**

**Note on numbering:** an earlier version of this document reserved
"CHECKPOINT 5" for the ground-truth network model + `eval_pipeline.py`
rebuild (see the now-superseded line previously here). That work was
never started; this project's actual Checkpoint 5, decided and built
across several sessions, is the regulatory/audit/completeness stage
described below instead. The ground-truth network model + `eval_pipeline.py`
rebuild remains unstarted and is renumbered into "Remaining work" below.

**What this checkpoint adds** (see `docs/ARCHITECTURE.md`'s new
"Checkpoint 5" section for the full design contract): six new modules —
`jurisdiction.py`, `regulatory_corpus.py`, `regulatory_rag.py`,
`regulatory_rules.py`, `investigation_auditor.py`, `case_completeness.py`,
`regather_loop.py` — wired additively into `run_pipeline.py` between
Evidence (Checkpoint 2) and Investigator Authority (Checkpoint 4), which
remains unchanged and fully intact.

- **India-primary jurisdiction.** The mock dataset's every account has
  `registered_country = "India"`. `jurisdiction.py` determines, from real
  fields only (registered_country, `is_international`, `currency`, geo
  country-mismatch — never a guess), a per-case `jurisdiction` label
  (`IN` / `US` / `cross_border` / `unknown`), `applicable_jurisdictions`,
  and a `confidence`. Real dataset run: 21/21 cases resolve
  `base_jurisdiction = IN` (`confidence`: 19 high, 2 medium); 14/21 are
  additionally tagged `cross_border` (real `is_international`/foreign-
  currency/geo signals present) — **0/21 ever retrieve a US-tagged
  regulatory citation.**
- **Regulatory corpus** (`regulatory_corpus.py`) is an explicitly-labeled
  **static bundled reference corpus** — documented as NOT a live
  regulatory feed and NOT legal advice. India entries (PMLA Rule 3 CTR
  threshold — INR 10,00,000; PMLA STR duty; RBI KYC/CDD Master Direction;
  FEMA/LRS cross-border) were checked this session against FIU-IND's own
  FAQ page, SEBI's cash-transaction-report guidance, and the RBI KYC
  Master Direction's own reference number. US entries (BSA/31 CFR
  1010.311 $10,000 CTR threshold, etc.) are retained, gated to `US`/
  `cross_border`-tagged cases only.
- **Regulatory RAG** (`regulatory_rag.py`) hard-gates retrieval by
  `applicable_jurisdictions` *before* any keyword scoring — an entry
  outside the case's jurisdiction is structurally unreachable regardless
  of keyword overlap. Deterministic, no network calls at runtime.
- **Rule engine** (`regulatory_rules.py`): `confirmed_concern` /
  `potentially_applicable` / `no_identified_breach` / `insufficient_evidence`
  — a rule never claims a breach from one bare anomaly (requires ≥2
  corroborating signals for `confirmed_concern`). The CTR rule is
  jurisdiction-*and*-currency-aware: compares INR amounts against India's
  threshold, USD amounts against the US threshold, and returns
  `insufficient_evidence` (never a silent conversion) when the only
  gathered amount is in a currency the case's jurisdiction's threshold
  isn't defined in, or when jurisdiction itself is unresolved.
- **Investigation auditor** (`investigation_auditor.py`) independently
  checks unsupported regulatory claims, contradictory evidence,
  unsupported authority conclusions, provenance gaps, **jurisdiction
  mismatch**, and **unresolved jurisdiction** — never repeats the rule
  engine's own conclusion. Real dataset run: only `missing_critical_evidence`
  fires (4 cases); zero jurisdiction-mismatch/unresolved-jurisdiction
  issues, consistent with all 21 accounts resolving cleanly to India.
- **Case completeness score** (`case_completeness.py`): one explainable
  0–100 score (evidence 60% / regulatory 25% / auditor 15%), with a
  dedicated `case_jurisdiction_not_resolved_with_high_confidence` reason
  surfaced when jurisdiction confidence is low/unknown (additive —
  doesn't change the fixed weights). The `source_of_funds`-style
  dataset-wide structural gap (Checkpoint 4's `structural_gap_reasons`)
  is excluded from the score exactly as it already was from
  `critical_evidence_missing`, so it cannot by itself cap every case
  below "complete".
- **Bounded re-gather loop** (`regather_loop.py`): max 2 iterations,
  targeted per missing evidence type, never invents evidence, stops early
  once nothing case-specific remains to request. Intentionally
  jurisdiction-blind (see ARCHITECTURE.md) — `run_pipeline.py` re-
  evaluates regulatory findings/auditor/completeness after regather using
  the *same*, already-determined `jurisdiction_context`.
- **Pipeline wiring fix found and fixed this session:** `run_pipeline.py`
  had NOT been computing or passing `jurisdiction_context` at all to
  `evaluate_compliance_rules()`/`audit_investigation()`/
  `compute_case_completeness()` — meaning the auditor's jurisdiction
  checks were silently inert (`if not jurisdiction_context: return []`)
  despite being fully implemented. Fixed: the pipeline now computes
  `jurisdiction_context` once per case (before the CTR/rule evaluation)
  and threads the same object through every downstream call, including
  the post-regather re-evaluation; it is also persisted on the case
  output as `evidence["jurisdiction"]`.
- **Test-fixture defect found and fixed this session:**
  `tests/test_checkpoint5.py`'s CTR tests were calibrated against the
  pre-jurisdiction, implicitly-US-shaped $10,000-equivalent threshold
  (a bare INR 25,000 test amount) and had no `registered_country` at
  all. Fixed by giving the `_account_swap_case_and_net` fixture explicit,
  overridable `registered_country`/`amount_high`/`amount_low` parameters;
  the pre-existing India-shaped test now uses an amount above the real
  INR 10,00,000 threshold, and four new tests were added: US-jurisdiction
  CTR (USD threshold, confirms `confirmed_concern` is reachable outside
  India), unknown-jurisdiction (`insufficient_evidence`, never a guess),
  and currency-mismatch (India case with only a USD amount gathered —
  `insufficient_evidence`, never a silent conversion).

**Tests: 114/114 passing** (31 in `test_checkpoint5.py`, up from the
prior session's unverified "111/111" claim — the true baseline including
this checkpoint's fixture fix and 4 new tests). Run via:
```
cd backend
PYTHONPATH="$(pwd)/venv/Lib/site-packages" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python3 -m pytest tests/ -q
```
(`PYTHONPATH`/`PYTEST_DISABLE_PLUGIN_AUTOLOAD` are needed in this
environment only because the checked-in `venv/` is Windows-targeted
— `fastapi`/`uvicorn`/`langgraph`'s `pydantic_core`/DLL-loading
dependencies do not import on Linux — but `pytest` itself and every
package the Checkpoint 5 modules and their tests actually need
(`networkx`, `pandas`, `pytest`) import fine; disabling pytest's
auto-loaded `langsmith` plugin avoids that one unrelated import path.)

**Real pipeline run** (`python3 run_pipeline.py`): 220 accounts scanned →
31 alerts → 21 cases → 21 evidence records (unchanged from Checkpoint 4's
baseline — this checkpoint adds fields to each case's evidence record, it
does not change detection/bundling). Case completeness: 17 complete / 4
incomplete, all 4 incomplete cases triggered the bounded re-gather loop
(unchanged from the prior session's reported baseline, now independently
re-verified rather than merely repeated). Jurisdiction distribution:
21/21 `IN` base jurisdiction, 14/21 additionally `cross_border`, 0/21 `US`
or `unknown`. No PII beyond pseudonymous account/case/transaction IDs is
present in persisted evidence JSON.

**Known limitations, honestly carried forward:**
- The bundled regulatory corpus is small and hand-curated, not a licensed
  compliance content provider or a live feed — documented as such in
  `regulatory_corpus.py`'s own docstring.
- Because every real account in this dataset resolves to `IN`, the
  jurisdiction-mismatch and unresolved-jurisdiction auditor checks are
  exercised end-to-end only by the unit tests (hand-built `US`/`unknown`
  fixtures), not by any real case in the current mock dataset — this is
  a property of the dataset being India-only, not a gap in the checks
  themselves.
- `contradiction_state` is still not wired into this live per-case loop
  (same pre-existing, documented Checkpoint 4 limitation) — the auditor's
  contradictory-evidence check degrades to "not evaluated" rather than
  guessing.
- Action/escalation (pipeline stage 9) — see CHECKPOINT 6 below; the
  recommendation/authorization/audit/review/memory portion is now
  implemented, real execution against an external banking system remains
  simulated only.

**CHECKPOINT 6 (this session): Next-Best-Action + Audit Trail + Human
Review + Investigator Action + Case Memory. [COMPLETE] [VERIFIED].**

**Note on numbering:** this document previously reserved "CHECKPOINT 6"
for the ground-truth network model + `eval_pipeline.py` rebuild (see the
now-superseded line previously here, itself already a renumbering from an
earlier "CHECKPOINT 5" reservation — see the Checkpoint 5 note above).
That work was never started and remains not started; this project's
actual Checkpoint 6, built and verified this session, is the downstream
action/authorization/audit/human-review/case-memory stage described
below instead — matching the pipeline stage list in
`docs/ARCHITECTURE.md`'s "Checkpoint 6" section and the task brief this
session was given. The ground-truth network model + `eval_pipeline.py`
rebuild remains unstarted and is renumbered again into "Remaining work"
below, alongside the SAR-report-generation work (which this session was
explicitly instructed not to start).

**What this checkpoint adds** (see `docs/ARCHITECTURE.md`'s new
"Checkpoint 6" section for the full design contract): six new modules —
`next_best_action.py`, `case_state.py`, `audit_trail.py`,
`investigator_action.py`, `case_memory.py`, `action_pipeline.py` — plus
`demo_checkpoint6.py` and `tests/test_checkpoint6.py`, wired additively
into `run_pipeline.py` immediately after Checkpoint 4's authority
decision. Every real case now gets, deterministically: a Next-Best-Action
recommendation, a seeded audit trail (the upstream Checkpoint 3–5
computations logged as `system` events), an initial lifecycle state
(`HUMAN_REVIEW` if `case_completeness.status == "complete"`, otherwise
held at `INVESTIGATING`), and a Case Memory record. Human review/
investigator-action/override/escalation are demonstrated via
`tests/test_checkpoint6.py` (38 tests) and `demo_checkpoint6.py`'s five
labeled scenarios, not fabricated for every real case (that would be
inventing investigator behavior the pipeline has no basis to assert).

**Verification performed this session (not merely repeated from the
prior session's handoff):**
- `tests/test_checkpoint6.py`: **38/38 passing.**
- Full regression suite: **153/153 passing, 0 failed** (up from the
  Checkpoint 5 baseline of 114; the +39 delta is the 38 new Checkpoint 6
  tests plus 1 pytest-collection accounting difference), run via the same
  `PYTHONPATH`/`PYTEST_DISABLE_PLUGIN_AUTOLOAD` invocation documented
  above for the Windows-targeted checked-in `venv/`.
- Fresh `python3 run_pipeline.py --demo_case`: 220 accounts → 31 alerts →
  21 cases → 21 evidence records (unchanged from Checkpoint 5's baseline —
  Checkpoint 6 adds fields to each case's evidence record, it does not
  change detection/bundling/regulatory output). Every one of the 21 real
  cases received a Checkpoint 6 result. Next-Best-Action distribution on
  this real data: `RESTRICT_ACCOUNT` (11), `BLOCK_TRANSACTION` (6),
  `REQUEST_MORE_INFORMATION` (4). Lifecycle state after Checkpoint 6
  seeding: `HUMAN_REVIEW` (17, the complete cases), `INVESTIGATING` (4,
  the incomplete cases correctly held back from review). No case reached
  `CLEAR`/`CLOSE_CASE`/`MONITOR` naturally on this dataset — see the
  known limitation in `docs/ARCHITECTURE.md`'s Checkpoint 6 section; this
  is a property of the mock dataset only containing alert-triggered
  cases, not a policy defect.
- `demo_checkpoint6.py` (five labeled, explicitly-not-claimed-as-natural
  scenarios): senior-executed `BLOCK_TRANSACTION` (case_state → `CLOSED`);
  the same case attempted by a junior → `authorized=False`,
  `actual_action=REJECTED_UNAUTHORIZED`, case_state stays at
  `HUMAN_REVIEW` (never silently executed); an escalation to senior
  (case_state → `ESCALATED`); a senior override of the system
  recommendation (`recommendation_followed=False`, non-empty
  `override_reason` required and present, case_state → `ESCALATED`); and
  a hand-built clean/low-risk case junior-authorized straight to `CLEAR`
  (case_state → `CLOSED`).
- Security/integrity spot-checks: no `ground_truth` import anywhere in
  the six new modules (`grep`-verified); no `random`/`uuid` use in any
  Checkpoint 6 module (all IDs are deterministic content hashes, same
  style as `detection_layer.py`'s Checkpoint 3 fix); `case_memory.py`'s
  output schema contains no evaluation-only/ground-truth field;
  `audit_trail.py` exposes no delete/overwrite method; a junior
  investigator's attempt at a senior-only action is rejected and audited,
  never silently executed.
- Checkpoint 4/5 regression: `test_authority_policy.py`,
  `test_checkpoint5.py`, `test_case_bundling.py`,
  `test_ground_truth_isolation.py`, `test_evidence_model.py` all still
  pass unmodified in content (only CRLF line-ending normalization
  differs in the working tree from the last commit — see the git-status
  note at the top of this session's changes; no logic in any Checkpoint
  4/5 file was altered).

**Known limitations, honestly carried forward:**
- Real execution against an external banking system is simulated only —
  `action_executed` audit events are recorded, but no real system call is
  made. This was explicitly out of scope for this checkpoint.
- SAR report generation is deferred to a later checkpoint, per this
  checkpoint's own instruction; `case_memory.py` persists every field a
  future SAR generator will need but does not produce SAR narrative text.
- `INVESTIGATOR_DIRECTORY` in `investigator_action.py` is a deterministic
  test identity/role table, not real authentication/SSO — documented in
  that module's own docstring as the one place that will need to change
  when real auth is built.
- `contradiction_state` is still not wired into the live per-case loop
  (same pre-existing Checkpoint 4/5 limitation); Next-Best-Action reads
  it as an optional parameter and defaults to policy-neutral behavior
  when absent, rather than guessing.

**CHECKPOINT 7 (this session): LLM PII Sanitization Boundary + SAR
Generation + Scoped Investigator Data Access + LangGraph Orchestration +
API Integration. [COMPLETE] [VERIFIED].**

**What this checkpoint adds** (built across this and a prior session;
this session finished, verified, and corrected it — see below): five new
modules — `agents/llm_pii_sanitizer.py`, `sar_report.py`,
`case_data_access.py`, `langgraph_orchestration.py`, plus `main.py`'s
investigation API layer — and five new/extended test files
(`tests/test_llm_pii_sanitizer.py`, `tests/test_checkpoint7.py`,
`tests/test_scoped_data_access.py`, `tests/test_langgraph_orchestration.py`,
plus `test_ground_truth_isolation.py`'s `LIVE_MODULES` list extended to
cover all five).

**LLM PII sanitization boundary — [IMPLEMENTED] [VERIFIED].**
`agents/llm_pii_sanitizer.py` sits immediately in front of the three (and
only three — confirmed via `grep -rn "generate_content" backend/`) live
`client.models.generate_content()` call sites:
`agents/scammer_hypothesis_agent.py`, `agents/legitimate_hypothesis_agent.py`
(both mask `evidence`+`derived_signals` via `sanitize_pair_for_llm()` before
building the prompt), and `agents/contradiction_agent.py` (takes no raw
evidence at all — only the two hypothesis agents' own JSON output, which by
construction can only reference already-masked pseudonyms). Direct
identifiers (`ACC*`/`BENE*`/`DEV*` prefixes, plus `beneficiary_name`) are
replaced with deterministic, sorted-order pseudonyms
(`ACCOUNT_001`, `BENEFICIARY_002`, ...); the SAME raw value always maps to
the SAME pseudonym within one call (evidence and derived_signals share one
pseudonym map), and different values never collide. Financial/behavioral
facts (amounts, ratios, timestamps, typology, pattern flags) pass through
unmasked — masking them would make the evidence useless to the model, and
that's explicitly out of scope. The masked payload is a separate, deep-copied
object; the original `evidence`/`derived_signals` objects are never mutated
and remain exactly what the rest of the pipeline (case storage, audit trail,
SAR generation) sees — this is a distinct trust boundary from
`case_data_access.mask_account()`'s investigator-UI PII masking, and the two
are not confused anywhere in the code. `resolve_pseudonym()` is the only
reverse-lookup path, and it is never called to send a real value back to the
LLM. **25/25 tests pass** in `tests/test_llm_pii_sanitizer.py`: unit tests
against the sanitizer directly (no raw PII in the masked payload, no
mutation, deterministic repeated execution, consistent same-value mapping,
no collisions, correct recursion into arbitrarily nested evidence, graceful
handling of missing/null fields, non-PII fields intact, beneficiary-name/
sibling-ID reuse, hash-fallback pseudonyms for names with no sibling ID,
`resolve_pseudonym` round-trip) plus integration tests that mock
`client.models.generate_content` and assert on the actual outbound
`contents` string sent to both hypothesis agents (no raw ID pattern, no raw
name, agent's return value untouched, the caller's own `evidence` object
still holds the real data after the call) and on `contradiction_agent.py`
(its function signature takes no `evidence` parameter at all; its outbound
payload never contains `"beneficiary"`/`"account_profile"`).

**Two genuine test-file defects found and fixed this session** (both in
test code, not production code):
1. `tests/test_llm_pii_sanitizer.py`'s hand-built `_evidence()` fixture's
   `geo_events` entry was missing `distance_from_last_location_km`, a field
   `agents/evidence_builder.py`'s real `gather_evidence()`/`_clean_geo()`
   always populates and `compute_derived_signals()` indexes directly
   (`g["distance_from_last_location_km"]`, no `.get()`) — the fixture
   didn't match the real evidence shape it was standing in for, causing a
   `KeyError` before sanitization even ran. Fixed by adding the missing
   field to the fixture, not by loosening the production code's indexing.
2. `test_sanitizer_module_has_no_ground_truth_dependency()` false-positived
   on the sanitizer's own docstring, which used the ordinary-English phrase
   "no ground-truth or external data is consulted" (about the hash-fallback
   pseudonym, nothing to do with `mock_data/ground_truth_*.csv`) — the
   test's substring scan for `"ground-truth"` doesn't distinguish that from
   an actual evaluation-data reference. Fixed by rewording the docstring to
   "no external reference data of any kind" — no behavior change, and
   `agents/llm_pii_sanitizer.py` still has zero actual ground-truth
   dependency (confirmed independently by
   `test_ground_truth_isolation.py`'s AST-based static scan, which this
   session added the module to).

**SAR generation — [IMPLEMENTED] [VERIFIED].** `sar_report.py` is
deterministic and template-based: no new evidence gathered, no LLM call, no
randomness, `sar_id` a SHA-256 content hash. Three preconditions
independently re-validated (never trusted from the caller): at least one
`confirmed_concern` regulatory finding, a resolved jurisdiction
(`IN`/`US`/`cross_border`, never `unknown`), and an actually-authorized
`FILE_SAR` investigator action — any one failing returns a structured
`BLOCKED_*` record, never a fabricated filing. A critical, unresolved
auditor issue at generation time downgrades a filing to
`DRAFT_REQUIRES_SECONDARY_REVIEW` rather than silently filing anyway.
Cross-border cases file to India (`FIU-IND`) with cross-border material as
supplementary-only citations, never substituted for the India filing
(Rule 4/5 — verified, never a silent US filing). **Explicitly NOT
implemented, and not claimed as implemented: PDF generation and
password-protection of the SAR document** — `sar_report.py` produces a
JSON-serializable structured record only; there is no `pdf`/`pdf_path`/
`password`/`encrypted` field anywhere in its output (asserted directly by
`test_no_pdf_or_password_protection_fields_present`). This remains
deferred, exactly as the prior session's own instructions deferred it — it
is not silently implied to exist anywhere in this checkpoint's code or
docs. **24/24 tests pass** in `tests/test_checkpoint7.py`: every
precondition/status branch, India/US/cross-border filing routing,
citation-jurisdiction filtering, empty-citation honesty (never
backfilled), critical-vs-moderate auditor-warning handling, determinism,
`sar_id` sensitivity to which findings are confirmed, evidence-availability
filtering, and integration through `action_pipeline.CaseActionLayer`
(junior cannot file, senior can, a non-SAR action never populates
`sar_report`, `case_memory.py`'s `sar_report`/`sar_report_history` fields
default/append correctly) plus a regression check against real, checked-in
Checkpoint 4–6 pipeline output (every real case gets a `sar_report`/
`sar_report_history` key; none reach a real `FILE_SAR` on this dataset —
a documented, known dataset property, not a defect, matching Checkpoint
6's own documented limitation that no real case naturally reaches
`CLEAR`/`MONITOR`/`FILE_SAR`).

**Scoped investigator data access — [IMPLEMENTED] [VERIFIED].**
`case_data_access.py`'s `ScopedDataAccess` is the one controlled interface
a case/agent/investigator goes through — `DataStore` itself remains
unscoped-but-trusted for the existing deterministic pipeline (Checkpoints
2–6), by design; this is an additive, narrower view layered in front of it
for the Checkpoint 7 API/agent surface only. Case scope is derived only
from data the deterministic pipeline already computed (the case's own
account, graph nodes for graph-based typologies, transaction counterparties)
— never a fresh, wider traversal. `junior` gets root-account + depth-1
counterparties only; `senior` gets the full case-derived scope; an unknown
role defaults to `junior` (least privilege). `mask_account()` redacts
`customer_name`/`occupation`/`annual_income`/`home_branch` for non-senior
roles via a deterministic content-hash redaction — never mutates its
input, never touches non-PII risk fields (`kyc_status`/`risk_rating`/
`account_type`) the investigation logic actually reasons over. This PII
masking is a distinct boundary from the LLM sanitization boundary above —
`ScopedDataAccess` governs what an investigator's UI/API view sees;
`llm_pii_sanitizer.py` governs what an external LLM call sees; neither
delegates to or is confused with the other anywhere in the code. A request
for data outside the computed/authorized scope raises `ScopeViolationError`
(never silently narrowed or ignored) — verified against both a hand-built
fixture graph (root → direct → indirect accounts, plus one genuinely
unrelated account) and the real, checked-in `pipeline_output/`
(junior scope is always a subset of senior scope; senior scope always
includes the case's own root account; junior view masks PII, senior view
doesn't, on every real persisted case). **29/29 tests pass** in
`tests/test_scoped_data_access.py`.

**LangGraph orchestration — [IMPLEMENTED] [VERIFIED — including real
build/compile/invoke, not just the manual-chain approximation].**
`langgraph_orchestration.py` wraps the existing Checkpoint 2–6
deterministic functions as one node per orchestration responsibility
(case intake, evidence, network, investigation/evidence-synthesis,
jurisdiction, regulatory, auditor, completeness, bounded re-gather,
authority/action) with an explicit `InvestigationGraphState` schema and
`langgraph.graph.StateGraph` as the execution engine. No node
recomputes, duplicates, or second-guesses its wrapped function's answer.
Makes **no LLM calls** — the "Investigation Agent" node maps to
`evidence_model.build_evidence_items()` (deterministic), not to the
`agents/` LLM modules, exactly as documented in the module's own
docstring, so nothing here can let an LLM decide jurisdiction, regulatory
applicability, authority, completeness, or evidence truth.
`HUMAN_REVIEW`/`INVESTIGATING` is a real terminal graph state — the
compiled graph's execution always ends there; `resume_after_human_review()`
is the *only* function that can advance a case past it, and it does so by
calling `action_pipeline.CaseActionLayer`'s existing, unmodified
`complete_human_review()`/`submit_action()` methods, adding no new
authorization logic of its own. `langgraph` (v1.2.11 in this environment)
is genuinely importable and usable here — unlike a documented prior
sandbox limitation (Windows-only checked-in `venv/` with compiled
`pydantic_core` `.pyd` files that don't load on Linux), this session's
execution environment has a working Linux-native install. This session
therefore added **real** `build_graph()`/`.compile()`/`.invoke()` coverage
(`test_real_graph_compiles_to_an_invocable_object`,
`test_real_graph_invocation_reaches_human_review_or_investigating`,
`test_real_graph_invocation_matches_manual_chain_approximation` — the real
`StateGraph` execution and the hand-chained node-by-node approximation
used elsewhere in the file are cross-checked and produce identical
`case_state`/`next_best_action`/`case_completeness` output for the same
case, `test_real_graph_bounded_regather_never_exceeds_max_hops_across_dataset`,
`test_real_graph_never_bypasses_human_review`) alongside the pre-existing
manual-chain-based tests, which remain valid and are kept as an
environment-independent fallback path (skipped, not failed, if a future
environment can't import `langgraph`). **22/22 tests pass** in
`tests/test_langgraph_orchestration.py` (17 pre-existing + 5 new
real-graph tests this session added).

**API integration — [IMPLEMENTED].** `main.py` adds a read-oriented
investigation API over already-persisted `pipeline_output/` (list/get
case, evidence, network — role-scoped via `ScopedDataAccess`, — regulatory,
audit, actions, SAR, timeline) plus two human-in-the-loop mutating
endpoints (`POST /cases/{case_id}/human-review`, `POST /cases/{case_id}/action`)
that rebuild that case's `CaseActionLayer` from persisted evidence and call
its existing Checkpoint 6 methods, adding no new authorization logic. An
in-process `_LIVE_LAYERS` cache is an explicit, documented limitation (does
not survive a process restart — a real deployment would persist the layer's
constituent state in a datastore instead).

**Deterministic-ID investigation — [VERIFIED, and a prior-session static
claim corrected].** A prior session's static trace suggested
`evidence_model.py`'s `EvidenceItem.evidence_id` might still use
`uuid.uuid4()`, based on stale, since-superseded evidence-stage
non-determinism documented back in Checkpoint 3 (see section 4, item 8,
now marked resolved above). That specific claim does **not** hold against
the current code: `grep -rn "uuid\.uuid4()"` across every live-pipeline
module returns zero real call sites — `uuid` is imported in
`evidence_model.py`/`network_layer.py` only for comments explaining why
content-hashing was chosen *instead*, and the one genuine `uuid.uuid4()`
call anywhere in the repository is in `generate_mock_data.py` (a
`device_fingerprint` field on synthetic input data, unrelated to pipeline
output determinism). `evidence_id` is `_content_hash_id("EVD", case_id,
evidence_type, source_record_ids)` (SHA-256); `event_id` in
`audit_trail.py` is `_content_hash_id("EVT", case_id, event_type,
actor_id, position_in_trail)` — deliberately *not* derived from the
wall-clock timestamp, precisely so it stays reproducible across runs.

The apparent non-determinism actually observed this session (diffing a
fresh `run_pipeline.py` run against the `pipeline_output/` snapshot
already checked into the working tree) was traced to its real cause:
**that checked-in snapshot predates the current content-hash-based ID
scheme** — recomputing `_content_hash_id("EVD", "CASE-37897EBB",
"amount_retention_ratio", [])` by hand against the *current* code
reproduces the fresh run's ID exactly, and does **not** reproduce the
stale snapshot's ID, confirming the snapshot itself (not the current
code) is the outdated artifact. It is not evidence of live nondeterminism.

**Real double-run verification performed this session:** `run_pipeline.py`
executed twice in immediate succession against the identical, unmodified
`mock_data/`. Console summary output byte-identical between the two runs.
All 21 `pipeline_output/evidence/*.json` files byte-identical between the
two runs after stripping only the two fields explicitly documented as
wall-clock/runtime-generated (`generated_at`, audit-event `timestamp`) —
**zero other diffs**, across every `evidence_id`, `event_id`, `sar_id`
(where applicable), `case_id`, `alert_id`, `next_best_action`, `case_state`,
`audit_trail`, and `case_memory` field. `cases.json`/`suspected_alerts.json`
case-id and alert-id sets identical between runs.

**Real pipeline numbers (unchanged from the Checkpoint 6 baseline —
re-verified, not re-tuned):**
```
Accounts scanned      : 220
Alerts generated       : 31   {money_mule: 11, smurfing: 10, account_swap: 5, reverse_smurfing: 5}
Cases generated        : 21   {money_mule: 5, smurfing: 6, account_swap: 5, reverse_smurfing: 5}
Evidence objects        : 21
Evidence completeness (weighted): avg=86.9 min=85.0 max=100.0 (21/21)
Case completeness (Ckpt 5): 17 complete / 4 incomplete (4/21 triggered re-gather)
Next-Best-Action (Ckpt 6): BLOCK_TRANSACTION=6, RESTRICT_ACCOUNT=11, REQUEST_MORE_INFORMATION=4
Case lifecycle after Ckpt 6 seeding: HUMAN_REVIEW=17, INVESTIGATING=4
SAR reports generated (Ckpt 7): 0/21 — expected; see sar_report.py's own
  known-dataset-property note; FILE_SAR is exercised via hand-built
  fixtures in tests/test_checkpoint7.py instead.
Jurisdiction distribution: IN(base)=21/21, cross_border=14/21, US=0/21, unknown=0/21
```
India-primary jurisdiction behavior is intact: 0/21 real cases ever resolve
to a bare `US` jurisdiction; the 14 cross-border cases still file to
`FIU-IND` per `sar_report.py`'s own jurisdiction routing (re-confirmed by
`test_cross_border_case_files_as_india_never_as_us`, unmodified and
passing). No international-transaction case silently triggers US
regulatory contamination — `RULE-CTR-001`'s USD/US-threshold path is
exercised only by explicit `registered_country="United States"` fixtures,
never by an `is_international` flag alone.

**Ground-truth isolation — [VERIFIED].** `test_ground_truth_isolation.py`'s
`LIVE_MODULES` list now covers all five Checkpoint 7 modules
(`sar_report.py`, `case_data_access.py`, `langgraph_orchestration.py`, plus
the three LLM agent modules already listed from Checkpoint 5). Both the
static AST scan (imports/string-literals/attributes/identifiers, excluding
docstrings) and the dynamic proof (real Detection→Case-Intake pipeline run
against a copy of `mock_data/` with every `ground_truth_*.csv` physically
deleted, output byte-identical to the untouched run) pass. **30/30 tests
pass** in `tests/test_ground_truth_isolation.py`.

**Checkpoint 4–6 regression — [VERIFIED].** `test_authority_policy.py`,
`test_checkpoint5.py`, `test_checkpoint6.py`, `test_case_bundling.py`,
`test_evidence_model.py` all pass unmodified in content. Real pipeline
numbers (above) are byte-for-byte the same funnel as every prior
checkpoint's documented baseline.

**Full backend test suite: 262/262 passing, 0 failed** (up from
Checkpoint 6's 153 — the +109 delta is Checkpoint 5's fixture/coverage work
already counted in that baseline being superseded by a larger, correctly-
accounted total, plus this session's 5 new real-langgraph tests; see each
file's individual count above for the itemized breakdown: 24
`test_checkpoint7.py` + 29 `test_scoped_data_access.py` + 22
`test_langgraph_orchestration.py` + 25 `test_llm_pii_sanitizer.py` + 30
`test_ground_truth_isolation.py` + 31 `test_checkpoint5.py` + 38
`test_checkpoint6.py` + the pre-Checkpoint-5 baseline). Run via:
```
cd backend
python3 -m pytest tests/ -q
```
No `PYTHONPATH`/`PYTEST_DISABLE_PLUGIN_AUTOLOAD` workaround was needed in
this session's execution environment — `fastapi`, `uvicorn`, `langgraph`,
and `google-genai` all import cleanly on this Linux Python 3.12.3
install (pip-installed from `requirements.txt`, not the checked-in
Windows-targeted `venv/`). A future session on a different sandbox may
still need that workaround; it is not required here and this document
does not claim it's required universally.

**Environment / dependencies used this session:**
- Python 3.12.3 (system `/usr/bin/python3`, not the checked-in `venv/`).
- Installed via `pip install -r requirements.txt --break-system-packages`:
  `fastapi` 0.141.1, `uvicorn` 0.52.4, `python-dotenv` 1.2.2, `langgraph`
  1.2.11, `google-genai` 2.19.0, `Faker` 40.37.0, `networkx` 3.6.1,
  `matplotlib` 3.10.8, `pytest` 9.1.1.
- Required environment variable: `GEMINI_API_KEY` (present in `backend/.env`,
  loaded via `python-dotenv`; value never printed/logged/committed by this
  session).
- Test command: `cd backend && python3 -m pytest tests/ -q`.
- Pipeline command: `cd backend && python3 run_pipeline.py`.
- Backend startup command (not exercised this session — no request was
  made to actually serve the API): `cd backend && uvicorn main:app --reload`.

**Known limitations, honestly carried forward:**
- Password-protected SAR PDF generation is **not implemented** — explicitly
  deferred, not silently claimed. `sar_report.py` produces a structured
  JSON record only.
- SAR generation is never naturally reached on the current mock dataset
  (documented dataset property, same class of limitation as Checkpoint 6's
  own "no case naturally reaches CLEAR" note) — exercised via hand-built
  fixtures in `tests/test_checkpoint7.py` instead.
- `main.py`'s `_LIVE_LAYERS` in-process cache does not survive a restart —
  documented in that module's own docstring as a explicit, intentional
  simplification for this checkpoint, not a production-ready session store.
- Real execution against an external banking system remains simulated only
  (unchanged, pre-existing Checkpoint 6 limitation).
- `INVESTIGATOR_DIRECTORY` remains a deterministic test identity table, not
  real auth/SSO (unchanged, pre-existing Checkpoint 6 limitation).
- The bundled regulatory corpus remains small and hand-curated, not a
  licensed/live feed (unchanged, pre-existing Checkpoint 5 limitation).

Remaining checkpoints, reserved for future sessions:
- (unscheduled) password-protected SAR PDF generation — explicitly
  deferred per this checkpoint's own instruction; see "Known limitations"
  above.
- Frontend integration, API productionization, deployment — explicitly out
  of scope for this session; backend Checkpoint 7 does not touch the
  frontend.
- (unscheduled) ground-truth network model + `eval_pipeline.py` rebuild
  (renumbered multiple times now — see the numbering notes above,
  unchanged this session).
- (unscheduled) real execution of an authorized action against an actual
  banking system — Checkpoint 6 computes, authorizes, and audits the
  decision; performing the real-world action itself is not yet built.