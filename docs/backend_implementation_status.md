# backend_implementation_status.md

Session-continuity checkpoint. Read this + `docs/ARCHITECTURE.md` before
touching any Python code. `ARCHITECTURE.md` is what the system *should*
do; this file is what it *actually* does right now, verified by
inspection and by running real code this session — not assumed from
prior session notes. **A prior session's claim in this exact file (a
"regression" in `wrap_as_evidence()`) turned out to be stale/incorrect —
see section 9 below.** Treat every status in this file as re-verifiable:
run the commands in section 6 yourself if in doubt.

**This session implemented CHECKPOINT 2 (Evidence Architecture)**: a
canonical, typed `EvidenceItem` model, per-typology-configurable required
evidence, deterministic weighted completeness, structured missing
evidence, and a 17-test suite — all new code in `backend/evidence_model.py`,
wired additively into `run_pipeline.py`. Investigator authority, action
routing, and large-scale evaluation were explicitly NOT started (per the
governing instruction) and remain `[NOT IMPLEMENTED]`.

## 1. Architecture

See `docs/ARCHITECTURE.md` for the full target architecture. Summary of
the 9-stage pipeline and current status:

| Stage | Status |
|---|---|
| Raw data (`DataStore`) | ✅ [IMPLEMENTED] [VERIFIED] |
| Detection (4 typology detectors) | ✅ [IMPLEMENTED] [VERIFIED] |
| Alerts | ✅ [IMPLEMENTED] [VERIFIED] |
| Case bundling | 🟡 [PARTIALLY IMPLEMENTED] (account_id + time window only; no `bundle_reason`, no typology/shared-transaction correlation — unchanged this session, Checkpoint 3 scope) |
| Investigation (evidence gathering) | 🟡 [PARTIALLY IMPLEMENTED] (two separate, unmerged entry points — see section 3; unchanged this session) |
| Evidence (structured object model) | ✅ [IMPLEMENTED] [VERIFIED] — typed `EvidenceItem`s (`backend/evidence_model.py`), additive to the existing `data` blob. **NEW this session.** |
| Evidence completeness | ✅ [IMPLEMENTED] [VERIFIED] — deterministic, weighted, computed from real evidence every run; zero randomness in live code. **NEW this session.** |
| Investigator authority | ❌ [NOT IMPLEMENTED] in live code (ground-truth-only field; live code correctly asserts nothing — out of scope this session by explicit instruction) |
| Action / escalation | ❌ [NOT IMPLEMENTED] (out of scope this session) |

## 2. Completed changes (CHECKPOINT 2, this session)

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

In priority order, matching `ARCHITECTURE.md`'s stage list. Items 1–3
from the prior version of this document are DONE (this checkpoint);
renumbered from the original item 4 onward:

1. **Investigator authority policy engine** — a real function computing
   junior/senior from completeness (now real, computed by this
   checkpoint's `evidence_model.py`) + typology risk + confidence +
   contradiction state. Must not live in `detection_layer.py` (confirmed
   correct exclusion, preserve it). **Explicitly out of scope for this
   session — do not start until instructed.**
2. **Bundle reason** on `bundle_alerts_into_cases()` output — additive
   `bundle_reason: [...]` field; see `ARCHITECTURE.md`'s "Bundle reason"
   section for the open question about false-merging unrelated same-
   account alerts within the same 24h window. Unchanged this session.
3. **Ground-truth network model with stable IDs** (`GT-SMURF-001` etc.,
   `fraud_networks.json`) — needed before network-level evaluation
   metrics can exist. Touches only `generate_mock_data.py` and
   evaluation code, never live detection/investigation code.
4. **Action/escalation stage** — depends on 1 above existing first.
5. **Missing-evidence-driven escalation policy** — the structured
   `missing_reason`/`severity` this checkpoint added is the direct input
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
8. **Remaining test coverage** — 17/17 of this checkpoint's own tests pass
   (evidence model + completeness). `ARCHITECTURE.md`'s full test list
   (section "Testing requirements") covers stages beyond evidence
   (detection, bundling, authority, evaluation) that this checkpoint
   didn't touch — those tests remain to be written when those stages are
   built.

## 5. Tests passed / failed

**17/17 passing** (`backend/tests/test_evidence_model.py`, run via
`cd backend && python3 -m pytest tests/ -v`):

| Test | Result |
|---|---|
| `test_weights_sum_to_one` | PASSED |
| `test_every_required_evidence_type_has_a_checker` | PASSED |
| `test_known_typologies_covered` | PASSED |
| `test_unclassified_typology_returns_no_items` | PASSED |
| `test_money_mule_full_evidence_available` | PASSED |
| `test_money_mule_all_evidence_missing` | PASSED |
| `test_missing_evidence_is_structured_not_free_text` | PASSED |
| `test_severity_is_deterministic_function_of_weight` | PASSED |
| `test_completeness_is_deterministic_across_repeated_calls` | PASSED |
| `test_completeness_never_uses_random_module` | PASSED |
| `test_wrap_as_evidence_preserves_scope_fields` | PASSED (regression guard — see section 9) |
| `test_build_evidence_items_on_real_case[smurfing]` | PASSED |
| `test_build_evidence_items_on_real_case[reverse_smurfing]` | PASSED |
| `test_build_evidence_items_on_real_case[money_mule]` | PASSED |
| `test_build_evidence_items_on_real_case[account_swap]` | PASSED |
| `test_source_of_funds_always_missing_on_real_data` | PASSED |
| `test_persisted_evidence_items_round_trip` | PASSED |

This is 17/17 of the tests this checkpoint added, not 17/17 of
`ARCHITECTURE.md`'s full test list (that list spans stages this
checkpoint didn't touch — see section 4, item 8).

## 6. Current pipeline command

```bash
cd backend
python3 generate_mock_data.py --outdir mock_data --num_accounts 220 --num_cases 38
python3 run_pipeline.py --demo_case
python3 -m pytest tests/ -v
```

`run_pipeline.py` is committed to the repo (added in commit `88068ce`,
alongside this checkpoint's predecessor doc commit). This session's
changes to it are additive (see section 2, item 3) — no existing
behavior removed.

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

**CHECKPOINT 2 (this session): Evidence Architecture.** Adds
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
NOT started this session.

Remaining checkpoints, reserved for future sessions:
- CHECKPOINT 3: detection + case-bundling correction (`bundle_reason`)
- CHECKPOINT 4: investigator authority / escalation policy engine
- CHECKPOINT 5: ground-truth network model + `eval_pipeline.py` rebuild
- CHECKPOINT 6: tests and final verification across all stages