# backend_implementation_status.md

Session-continuity checkpoint. Read this + `docs/ARCHITECTURE.md` before
touching any Python code. `ARCHITECTURE.md` is what the system *should*
do; this file is what it *actually* does right now, verified by
inspection this session (not assumed from prior session notes — several
prior-session claims turned out to be stale; see "Regression found this
session" below).

**This is a documentation-only session.** No Python files were modified.
Per the governing instruction for this session: inspect first, document
second, implement only in a following session.

## 1. Architecture

See `docs/ARCHITECTURE.md` for the full target architecture. Summary of
the 9-stage pipeline and current status:

| Stage | Status |
|---|---|
| Raw data (`DataStore`) | ✅ IMPLEMENTED, VERIFIED |
| Detection (4 typology detectors) | ✅ IMPLEMENTED, VERIFIED |
| Alerts | ✅ IMPLEMENTED, VERIFIED |
| Case bundling | 🟡 PARTIALLY IMPLEMENTED (account_id + time window only; no `bundle_reason`, no typology/shared-transaction correlation) |
| Investigation (evidence gathering) | 🟡 PARTIALLY IMPLEMENTED (two separate, unmerged entry points — see section 3) |
| Evidence (structured object model) | 🟡 PARTIALLY IMPLEMENTED (one blob per case, not typed evidence items) |
| Evidence completeness | ❌ NOT IMPLEMENTED in live code (ground-truth-only, random) |
| Investigator authority | ❌ NOT IMPLEMENTED in live code (ground-truth-only field; live code correctly asserts nothing, but nothing positive replaces it either) |
| Action / escalation | ❌ NOT IMPLEMENTED |

## 2. Completed changes (this session)

1. Inspected the full repository, including a remote update (`be195e7
   "Updating Frontend and working on pipeline"`) that had landed since the
   last session — the frontend now has real `Page/Dashboard/` tabs
   (Suspected, Audit-Ready, Reference, Escalated) and `Page/Login.jsx`,
   and `eval_pipeline.py` now has a materially better rate-limit/quota UX
   than what was documented previously (boxed terminal output, an
   explicit `print_daily_quota_alert()`). Both are real improvements made
   outside this session and are preserved untouched.
2. Reconciled local sandbox state against the remote repo (a stale local
   stash from a prior session was discarded in favor of remote HEAD for
   all `backend/*.py` files, after confirming they matched what was
   already on remote — no work was lost, nothing was blindly overwritten).
3. Created `docs/ARCHITECTURE.md` (target architecture / source of truth).
4. Created this file.
5. Found and recorded one real regression (section 9) that must be fixed
   in the next implementation session before anything else in Phase 8
   (evidence object model) is built on top of it.

## 3. Remaining work

In priority order, matching `ARCHITECTURE.md`'s stage list:

1. **Fix the `wrap_as_evidence()` regression** (section 9) — quick, should
   be first, everything else touching evidence builds on this.
2. **Evidence completeness model** (`ARCHITECTURE.md` → "Evidence
   completeness model") — the single largest gap. Requires the evidence
   object model (next item) to exist first, since completeness is
   computed *from* typed evidence items, not from the current
   evidence-blob shape.
3. **Evidence object model** (`ARCHITECTURE.md` → "Evidence object
   model") — replace the current single `data` blob per case with typed
   evidence items (`evidence_type`, `required`, `available`,
   `source_record_ids`, `supports`/`contradicts`, structured
   `missing_reason`). This is a schema change to `pipeline_output/evidence/{case_id}.json`
   — no frontend consumes this yet (confirmed, see section 6), so this is
   currently a **zero-risk** schema change. Do it before the frontend
   starts consuming it, not after.
4. **Investigator authority policy engine** — a real function computing
   junior/senior from completeness + typology risk + confidence +
   contradiction state, replacing the current "nothing computes this"
   state. Must not live in `detection_layer.py` (confirmed correct
   exclusion, preserve it).
5. **Bundle reason** on `bundle_alerts_into_cases()` output — smaller,
   additive change to the existing function; add a `bundle_reason: [...]`
   field without changing the bundling logic itself yet (or, if the
   logic itself needs to consider typology-correlation more precisely,
   that's a slightly larger change — see `ARCHITECTURE.md`'s "Bundle
   reason" section for the open question about false-merging two
   unrelated same-account alerts within the same 24h window).
6. **Ground-truth network model with stable IDs** (`GT-SMURF-001` etc.,
   `fraud_networks.json`) — needed before network-level evaluation
   metrics can exist. Touches only `generate_mock_data.py` and
   evaluation code, never live detection/investigation code.
7. **Action/escalation stage** — depends on 2 and 4 both existing first.
8. **Missing-evidence-driven escalation policy** — depends on 3 (typed
   evidence items with structured `missing_reason`) existing first.
9. **Test suite** (section 5) — should be built incrementally alongside
   1-8, not as a separate final phase; at minimum, test 17 (ground truth
   never imported by live modules) should exist now, today, since it's
   cheap and would have caught nothing wrong currently but guards against
   future regression.
10. **LLM privacy/masking architecture** — lower priority than the above;
    no prior version exists to restore (see `ARCHITECTURE.md`'s "API/LLM
    safety" section), would be new design work.

## 4. Known limitations

- The mock data generator still produces structurally identical evidence
  for fraud-labeled and legitimate-labeled cases within the smurfing/
  reverse_smurfing/money_mule typologies (ground truth label assigned
  independently of the generated pattern's severity) — diagnosed in an
  earlier session, unrelated to this session's findings, still unfixed.
  This caps achievable LLM-hypothesis accuracy on those specific
  legitimate-labeled cases regardless of prompt or completeness-model
  quality, and should be fixed as part of item 6 above (a proper
  ground-truth network model is a natural place to also encode
  legitimate-vs-fraud pattern differentiation, since both require
  touching the same generator functions).
- No automated tests exist at all (0/17 from `ARCHITECTURE.md`'s test
  list).
- `eval_pipeline.py` has never been run against the real Gemini API in
  any Claude session so far (no network access to `googleapis.com`, no
  API key, in the sandbox these sessions run in) — only structural
  validation via mocked calls has happened on the Claude side. The
  terminal output pasted into an earlier turn suggests the user HAS run
  it successfully locally with real results (23/38 correct at that
  point) — that real run predates this session's mock-data timestamp
  fixes and the current repo state, so those specific numbers should be
  treated as stale, not as the current baseline.

## 5. Tests passed / failed

None exist to run. 0/17 required tests from `ARCHITECTURE.md` are
implemented. No `tests/` directory exists anywhere in the repo (confirmed
via `find . -iname "test*"` returning nothing outside `node_modules`).

## 6. Current pipeline command

```bash
cd backend
python3 generate_mock_data.py --outdir mock_data --num_accounts 220 --num_cases 38
python3 run_pipeline.py --demo_case
```

`run_pipeline.py` exists locally in this session's working tree but **is
not yet committed to the remote repository** — `git log` / `git show
HEAD:backend/run_pipeline.py` confirms it is absent from `be195e7` and
all prior commits, even though `pipeline_output/` (its output) IS
committed. This means whoever generated the committed `pipeline_output/`
content ran a local copy of `run_pipeline.py` that was never `git add`-ed.
**This needs to be committed** — see checkpoint list below.

Evaluation (separate, not part of the live pipeline):
```bash
export GEMINI_API_KEY=...
python3 eval_pipeline.py --limit 10   # smoke test, costs real API calls
```

## 7. Current output locations

- `mock_data/accounts.csv`, `transactions.csv`, `devices.csv`,
  `geo_events.csv`, `beneficiaries.csv` — raw data.
- `mock_data/ground_truth_alerts.csv`, `ground_truth_cases.csv`,
  `ground_truth_case_escalations.csv` — ground truth (flat, prefixed;
  not yet in a `ground_truth/` subdirectory per `ARCHITECTURE.md`'s
  recommended layout — low priority to move).
- `pipeline_output/suspected_alerts.csv`/`.json`,
  `pipeline_output/cases.csv`/`.json`,
  `pipeline_output/evidence/{case_id}.json` — live pipeline output.
  **Confirmed by inspection this session: `run_pipeline.py` never reads
  any `ground_truth_*` file — verified via `grep -n "ground_truth"
  detection_layer.py run_pipeline.py`, both matches are explanatory
  comments, not reads.**
- `eval_results.csv` (gitignored) — evaluation output, not committed.

## 8. Important schema decisions

- `primary_trigger` on a case is the **typology string** (`"smurfing"`),
  never a rule ID — this was a deliberate fix in a prior session, matches
  the spec's own Case JSON convention, and several modules
  (`evidence_builder.py`, `network_layer.py`'s dispatcher) depend on
  exact-string-match against it. Do not regress this.
- `ground_truth_*` filename prefix is load-bearing — `eval_pipeline.py`
  is the only script permitted to read files with this prefix.
- Case objects (both live and ground-truth) carry **no**
  `assigned_investigator_tier`/`escalated` field — deliberate, confirmed
  still true this session (`grep -n "assigned_investigator_tier"
  backend/*.py backend/agents/*.py` → zero matches). This is correct per
  `ARCHITECTURE.md`'s authority model and must stay this way until the
  actual policy engine (remaining-work item 4) replaces it with a real
  computed value — not a placeholder field.
- `wrap_as_evidence()`'s current output schema
  (`{evidence_id, case_id, evidence_type, typology, source, confidence,
  data, generated_at}`) is what's actually persisted today — see section
  9, this is the regression that needs fixing first.

## 9. Regression found this session

`network_layer.py`'s `generate_network_evidence()` correctly computes and
returns `account_id`, `source_transactions`, and `network_scope` in its
in-memory return value (verified by reading the function body directly).
But `wrap_as_evidence()`, which builds the object that actually gets
persisted to `pipeline_output/evidence/{case_id}.json`, drops all three
fields before persisting — confirmed by inspecting a real committed
evidence file (`pipeline_output/evidence/CASE-02B2367B.json`), whose
top-level keys are only `['evidence_id', 'case_id', 'evidence_type',
'typology', 'source', 'confidence', 'data', 'generated_at']`.

This means the distinction between "this case's originating alert
transactions" and "contextual transactions discovered via global
traversal" (`ARCHITECTURE.md`'s "Network layer traversal rules" section)
is currently **true in memory but not provable from any file on disk** —
exactly the failure mode a prior session's fix was supposed to close. The
fix was made and verified working in that session, but the version of
`network_layer.py` currently on the remote `main` branch does not contain
it — most likely because the fix was made in a Claude sandbox that was
never `git commit`/`git push`-ed, and a different (earlier) version of the
file is what actually got committed later.

**Fix for next session:** in `network_layer.py`'s `wrap_as_evidence()`,
add `account_id`, `source_transactions`, and `network_scope` as top-level
fields on the returned dict, copied from `network_response`. This is a
small, low-risk, well-understood fix (it's been done and verified once
already) — safe to do first, before anything else in the remaining-work
list.

## 10. Next recommended task

Start remaining-work item 1 (section 3): fix the `wrap_as_evidence()`
regression. It's small, well-understood, and unblocks correct
verification of everything built on top of it afterward. Then proceed to
item 2/3 (evidence completeness + evidence object model) as the next
substantial piece of work, since that's the largest gap between this
document and `ARCHITECTURE.md`, and is a prerequisite for authority
routing, action/escalation, and most of the missing evaluation metrics.

Before starting implementation: re-read `ARCHITECTURE.md`'s "Evidence
object model" and "Evidence completeness model" sections in full, and
check whether the frontend's `AuditReady.jsx`/`Escalated.jsx` stubs
(currently 34 lines each, effectively empty) have been fleshed out by the
user in the meantime — if so, their expected data shape becomes a real
constraint on the evidence schema redesign that doesn't exist today.

## Checkpoints

**CHECKPOINT 1 (this session): MDS architecture update.** Adds
`docs/ARCHITECTURE.md` and `docs/backend_implementation_status.md`. No
Python files touched. `run_pipeline.py` (present locally, absent from
git) is committed alongside this checkpoint purely so the repository
matches what's described in section 6 above — it is unchanged
implementation code from a prior session, not new work product of this
session, and is called out separately in the commit message for that
reason.

Remaining checkpoints (2-6, per the governing instruction) are reserved
for the next implementation session:
- CHECKPOINT 2: mock-data / ground-truth separation (network_id model)
- CHECKPOINT 3: detection + case-bundling correction (bundle_reason)
- CHECKPOINT 4: evidence model + completeness
- CHECKPOINT 5: investigator authority/escalation
- CHECKPOINT 6: tests and final verification