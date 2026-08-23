# PROJECT_STATE.md - What should I remember about this project?

> **Architecture correction in progress.** `docs/ARCHITECTURE.md` is now
> the authoritative target architecture (raw data / ground truth / live
> output separation, evidence completeness model, investigator authority
> model, etc.) and `docs/backend_implementation_status.md` is the current
> checkpoint against it - read both before this file. This file remains
> useful for narrative history (why things are the way they are, past
> bugs and fixes) but where it conflicts with `docs/ARCHITECTURE.md` on
> what the target design should be, `docs/ARCHITECTURE.md` wins.

## Current architecture

```
generate_mock_data.py
        │  writes bank source data (accounts/transactions/devices/geo_events/
        │  beneficiaries) + GROUND TRUTH (ground_truth_cases.csv,
        │  ground_truth_alerts.csv, ground_truth_case_escalations.csv)
        ▼
mock_data/*.csv ──────────────► data_store.py (DataStore)
                                        │
                    ┌───────────────────┴────────────────────┐
                    ▼                                         ▼
       run_pipeline.py  ─────────────────────────►  eval_pipeline.py
       (THE live orchestrator)                      (reads ground_truth_*
              │                                       ONLY, for scoring)
              │ 1. run_detection_pipeline(store)            │
              │ 2. bundle_alerts_into_cases(alerts)          │ calls
              │ 3. persist alerts/cases -> pipeline_output/   │ agents.evidence_builder
              │ 4. for case in cases:                         │  -> scammer/legitimate
              │      generate_network_evidence(store, case)   │     _hypothesis_agent
              │      wrap_as_evidence(...)                    │  -> contradiction_agent
              │      persist -> pipeline_output/evidence/      │
              │      {case_id}.json                            ▼
              ▼                                        eval_results.csv
     pipeline_output/
     ├── suspected_alerts.csv/.json   (LIVE Detection Agent output)
     ├── cases.csv/.json               (LIVE Case Intake output)
     └── evidence/{case_id}.json       (one per case, never batched)
```

`detection_layer.py` and `network_layer.py` each still have their own
`__main__` for standalone layer-level testing, but **`run_pipeline.py` is
the only script that runs the complete, correctly-wired chain** in one
process. Before this session, no such single entry point existed - you had
to run `detection_layer.py` then separately run `network_layer.py` pointed
at its JSON output, and even then `network_layer.py`'s own `__main__`
batched every case's evidence into one shared file rather than persisting
per-case.

`visualize_network.py` sits off to the side - reads `pipeline_output/cases.json`
and calls the same `network_layer.py` builder functions, rendering with
matplotlib instead of Cytoscape.js, purely for eyeballing.

**Everything downstream of Evidence in the original target architecture
(Investigation/Audit Result, Human-in-the-loop Action, Monitor/Block/
Escalate) does not exist as code yet.**

## Important design decisions and why they were made

- **Ground truth and live pipeline output are two different file sets with
  deliberately different names, on purpose.** This was a real bug fixed
  this session, not a stylistic choice from the start. Before: the mock
  generator wrote `mock_data/cases.csv`, `suspected_alerts.csv`,
  `case_escalations.csv` (synthetic ground truth for evaluation), while the
  live `detection_layer.py`/`network_layer.py` pipeline wrote *different*,
  differently-named JSON files (`detected_alerts.json`, `detected_cases.json`,
  `network_evidence.json`) and **never produced any CSV at all** - so
  anyone looking at `mock_data/` for "the pipeline's output" would find
  files with the exact right-sounding names (`cases.csv`) that were
  actually the ground truth, not live output, with no live CSV equivalent
  existing anywhere. Fixed by renaming the ground-truth files to
  `ground_truth_*.csv` and having `run_pipeline.py` write the real live
  `suspected_alerts.csv`/`cases.csv` to a separate `pipeline_output/`
  directory. Neither script may read the other's files as input -
  `eval_pipeline.py` reads only `ground_truth_cases.csv`, and
  `run_pipeline.py`/`detection_layer.py`/`network_layer.py` never read any
  `ground_truth_*` file.
- **Detection Layer / Case Intake carry no investigator tier or escalation
  field.** This was also a real, fixed bug: `bundle_alerts_into_cases()`
  used to hardcode `assigned_investigator_tier: "junior"` and
  `escalated: False` on every case it created. Neither value was ever
  actually *decided* by anything - "junior" was a constant, and "escalated"
  was always `False` at creation time because nothing downstream had run
  yet to justify escalating. Per the target architecture, investigator
  tier assignment and escalation are a human-in-the-loop / action-stage
  decision made *after* the investigation has produced evidence -
  Detection/Case-Intake cannot know this at bundling time and should not
  assert a value it has no basis for. `ground_truth_case_escalations.csv`
  still exists as a mock/evaluation artifact representing what that
  not-yet-built stage's output would eventually look like - it is never
  read by live code.
- **`run_pipeline.py` is a thin orchestrator, not a rewrite of the layers
  it calls.** It imports `run_detection_pipeline`, `bundle_alerts_into_cases`,
  `persist_alerts_csv`, `persist_cases_csv` from `detection_layer.py` and
  `generate_network_evidence`, `wrap_as_evidence` from `network_layer.py`
  unchanged - the fix was connective tissue, not new detection/evidence
  logic. This mirrors the user's own explicit push-back against "isolated
  agent demos": every function it calls operates on the real `DataStore`
  and the real output of the previous step, never a hand-constructed
  demonstration case.
- **CSV files instead of a database**, and **one `DataStore` class instead
  of four separate per-table Supabase-era agents** - both carried over
  unchanged from earlier sessions; see below under "Past problems" only for
  what changed this session.
- **One typology dispatcher, explicit branches, no shared fallback.** The
  dispatcher in `generate_network_evidence()` used to have a
  double-negative boolean condition
  (`typology == "money_mule" or typology not in ("smurfing",
  "reverse_smurfing", "account_swap")`) that, worked through by hand,
  actually routed all 4 known typologies correctly - but silently treated
  any *unrecognized* typology (e.g. the mock data's own
  `behavioral_deviation` label, used for non-typology "background" cases)
  as money_mule, with no error, no warning, and no way to tell from the
  output that a fallback had fired. Rewritten as an explicit if/elif chain,
  one branch per typology, with a clearly-labeled `network_type:
  "unclassified"` fallback that still returns a usable transaction
  timeline but never pretends to be money_mule.
- **Account swap is a `behavioral_transaction_timeline`, computed, not just
  listed.** Previously `build_account_swap_timeline()` returned a
  chronological list of security/transaction events and some boolean
  pattern flags (`sim_change_before_transaction`, etc.) but never actually
  computed "is this a sudden increase in value/frequency" as a number -
  the qualitative pattern label existed, the quantitative evidence behind
  it didn't. Added `_compute_behavioral_summary()`: splits the account's
  transactions into a "baseline" window (everything outside ±48h of the
  case's anchor event) and a "recent" window (inside it), computes average
  amount and daily frequency for each, and returns the ratio - e.g. a real
  verified case showed `amount_deviation_ratio: 12.25` (baseline avg
  ₹39,926/txn vs. recent avg ₹489,260/txn). Renamed `visualization_type`
  from `security_transaction_timeline` to `behavioral_transaction_timeline`
  specifically so it stops reading as a variant of a graph-based view -
  it never was one and must never become one.
- **`wrap_as_evidence()` was fixed once to keep `source_transactions`/
  `network_scope` as top-level fields, not folded into `data` - but this
  fix REGRESSED and is not present in the current repository state.**
  Found while verifying that contextual (globally-discovered) transactions
  stay visibly distinct from a case's originating alert transactions -
  `wrap_as_evidence()` was silently dropping both fields when building the
  persisted Evidence Store record, which meant the distinction was true in
  the *function* but invisible in the *output file*. This was fixed and
  verified working in the session that made it. **A subsequent inspection
  (the session that produced `docs/backend_implementation_status.md`)
  confirmed the fix is NOT present in the version of `network_layer.py`
  currently on the remote `main` branch** - most likely because that fix
  was made in a sandbox session whose changes were never `git commit`/
  `git push`-ed, and an earlier version of the file is what actually got
  committed later. **Do not assume this fix is live - verify against
  `docs/backend_implementation_status.md` section 9 and the actual
  committed file before relying on it.** The intended, not-yet-durably-
  applied behavior: the persisted JSON should prove the separation via
  `case['alert_ids']` (from `cases.csv`) vs.
  `evidence['source_transactions']` (from the evidence file) as
  independently inspectable, genuinely different lists.

## Database structure

Still no database - flat files, now split into two clearly-separated
directories:

**`mock_data/` (input + ground truth):**
- `accounts.csv`, `transactions.csv`, `devices.csv`, `geo_events.csv`,
  `beneficiaries.csv` - bank source-of-truth data (schema unchanged from
  before this session).
- `ground_truth_cases.csv` (renamed from `cases.csv`) - same columns as
  before **minus** `assigned_investigator_tier` and `escalated` (removed
  for the same reason they were removed from the live pipeline - even the
  ground-truth generator shouldn't assert a "live" investigator-tier fact
  that the target architecture says doesn't exist at this stage;
  `ground_truth_required_tier` alone, explicitly prefixed as an evaluation
  label, remains). Current columns: `case_id, account_id, created_at,
  primary_trigger, evidence_signals, completeness_score, status,
  ground_truth_label, ground_truth_required_tier,
  ground_truth_recommended_action`.
- `ground_truth_alerts.csv` (renamed from `suspected_alerts.csv`) -
  columns unchanged: `alert_id, account_id, transaction_id, alert_type,
  triggering_rule, alert_score, created_at, linked_case_id`.
- `ground_truth_case_escalations.csv` (renamed from
  `case_escalations.csv`) - columns unchanged.

**`pipeline_output/` (live pipeline output, new this session):**
- `suspected_alerts.csv`/`.json` - columns: `alert_id, account_id,
  transaction_id, typology, triggered, alert_score, severity,
  triggering_rules, evidence_signals, recommended_initial_action,
  case_required, created_at, linked_case_id`.
- `cases.csv`/`.json` - columns: `case_id, account_id, created_at,
  primary_trigger, alert_ids, evidence_signals, typologies, status`. No
  investigator tier, no escalation flag, no completeness score (nothing
  computes one yet).
- `evidence/{case_id}.json` - one Evidence Store record per case:
  `evidence_id, case_id, account_id, evidence_type, typology, source,
  confidence, data (typology-specific), source_transactions, network_scope,
  generated_at`.

**Golden rule, unchanged and re-confirmed this session:**
`ground_truth_*` fields/files exist purely for offline evaluation. Live
code (`run_pipeline.py`, `detection_layer.py`, `network_layer.py`,
`agents/evidence_builder.py`) must never read them. Only
`eval_pipeline.py` reads `ground_truth_cases.csv`, and only to score
predictions after the fact.

## API structure

Still effectively none. `main.py` is a bare FastAPI app with a single `/`
health-check route (`{"message": "Backend is alive", "status": "ok"}`) -
CORS is configured for `localhost:5173`/`localhost:3000` (a frontend dev
server) but no actual endpoint exists yet. `run_pipeline.py` fulfills the
*logic* the spec's `POST /api/cases/{case_id}/network-evidence` describes
(via `generate_network_evidence(store, case)`), but nothing wraps it in an
HTTP route yet - if/when that happens, `run_pipeline.py`'s per-case loop
body is exactly what a route handler for "regenerate evidence for one
case_id" should call, and `pipeline_output/evidence/{case_id}.json` is
exactly what "GET evidence for one case_id" should read.

## Authentication approach

None exists. Planned (per the user, still not designed or built): CSV-based
junior/senior investigator login (name + password + role). Re-confirmed
this session: **investigator tier/escalation must not live inside
Detection Layer or Case Intake** - if/when auth and escalation are built,
they belong in a separate, later human-in-the-loop/action-stage module
that consumes `pipeline_output/cases.csv` + evidence as input.

## Important dependencies

Unchanged from before this session: `networkx`, `matplotlib`, `faker`,
`google-genai` (requires `GEMINI_API_KEY`, client created at *import time*
in each agent file - importing any agent module without the env var set
fails immediately), plus Python standard library. `main.py` additionally
needs `fastapi`, `uvicorn` (both in `requirements.txt`, unused beyond the
health-check route so far).

## Data flow

1. `generate_mock_data.py` → writes bank source CSVs + ground-truth CSVs.
2. `run_pipeline.py`:
   a. `DataStore(data_dir)` → loads and indexes the 5 bank source CSVs.
   b. `run_detection_pipeline(store)` → runs the 4 rule detectors over
      every account, returns triggered alerts.
   c. `bundle_alerts_into_cases(alerts)` → groups same-account alerts
      within 24h into cases (`status: "open"`, nothing else asserted about
      lifecycle/ownership).
   d. `persist_alerts_csv`/`persist_cases_csv` → writes
      `pipeline_output/suspected_alerts.csv`/`cases.csv` (+ JSON copies).
   e. For each case (and ONLY the cases just produced in step c - never a
      hardcoded case, never a case pulled from ground truth): calls
      `generate_network_evidence(store, case)` → typology dispatch to the
      right graph/timeline builder → `wrap_as_evidence(...)` → writes
      `pipeline_output/evidence/{case_id}.json`.
3. `eval_pipeline.py` (separate, unrelated run) reads
   `mock_data/ground_truth_cases.csv`, and for each ground-truth case row:
   calls `agents.evidence_builder.gather_evidence(store, case)` (which
   itself re-runs `detect_all()` for that account and calls
   `generate_network_evidence(store, case)` again, live, against the same
   `DataStore` - it does NOT read `pipeline_output/` at all, it rebuilds
   evidence fresh from the ground-truth case's `account_id`/
   `primary_trigger`), then runs the two hypothesis agents + contradiction
   agent, and scores the result against `ground_truth_label`.

**Note the asymmetry:** `run_pipeline.py` and `eval_pipeline.py` are two
independent consumers of the same `DataStore` and the same
`generate_network_evidence()` function, but operate over two different
case sets (live-detected cases vs. ground-truth cases) for two different
purposes (produce investigation-ready output vs. score the LLM agents).
They are not sequential stages of one pipeline - don't assume
`eval_pipeline.py` consumes `run_pipeline.py`'s output, and don't wire them
together without a specific reason to.

## Known limitations

- **Mock fraud/legitimate cases share identical underlying data** for the
  structuring typologies - diagnosed in a prior session, unrelated to and
  unfixed by this session's orchestration/escalation work. Caps
  `eval_pipeline.py`'s achievable accuracy on legitimate-labeled
  smurfing/reverse_smurfing/money_mule cases regardless of prompt quality.
- **No real completeness scoring anywhere.** `ground_truth_cases.csv` has
  a `completeness_score` column (a mock/pre-decided number for evaluation
  purposes), but the live pipeline's own `pipeline_output/cases.csv` has no
  such column at all - nothing computes one. This is intentional (Case
  Intake should not fabricate a completeness score any more than it should
  fabricate an investigator tier) but means an Investigation Auditor
  component is a hard prerequisite before completeness-based routing can
  exist.
- **`eval_pipeline.py` has never been run against the real Gemini API** in
  any session so far - no network access to `googleapis.com` in this
  sandbox, no API key available. Only structural/plumbing validation via
  mocked LLM calls has ever happened.
- **No persisted Evidence Store beyond flat files** - `pipeline_output/evidence/{case_id}.json`
  is genuinely case-keyed and one-record-per-case, but there's no query
  layer, no versioning, no concurrent-write handling.
- **`main.py` has no real routes.**
- **No automated test suite.** All verification this session (and prior
  sessions) was ad-hoc: run the script, inspect the actual output with a
  short Python snippet, confirm the numbers/shapes are what's expected.
  Worth formalizing into pytest before the codebase grows further.

## Things that look unusual but are intentional

- **`detection_layer.py` and `network_layer.py` each still have their own
  `__main__`, separate from `run_pipeline.py`.** This is not leftover
  cruft - both remain useful for testing one layer in isolation (e.g.
  `python3 network_layer.py --cases_file pipeline_output/cases.json` to
  regenerate evidence for an already-detected case set without re-running
  detection). `run_pipeline.py` is additive, not a replacement for either.
- **`ALERT_RULES` in `generate_mock_data.py` uses different rule names
  than `detection_layer.py`'s real `RULEBOOKS`** (e.g. mock's
  `"structuring_below_threshold"` vs. real `SMF-001`
  `"multiple_inbound_counterparties"`) - intentional, unchanged from a
  prior session. The mock generator's alert labels only populate
  `ground_truth_alerts.csv` (illustrative, evaluation-only) and are never
  read by live code, which re-derives its own alerts from `detect_all()`.
- **`classify_alert()`'s `"initial_action": "escalate"` is not the same
  thing as the investigator-escalation logic that was removed this
  session.** It's a per-alert severity classification (spec section 4,
  score-band-driven: 80+ → critical/escalate, 60-79 → high/escalate,
  30-59 → medium/monitor, <30 → low/clear) describing what the Detection
  Agent recommends happen to the *alert*, not a decision about which human
  investigator handles the resulting *case*. Both use the word "escalate";
  they are not the same concept, and only the latter was ever
  architecturally wrong to have in Detection Layer.
- **`gemini-3.6-flash` as the model name** - carried over unchanged from
  the user's pre-existing working code; don't "correct" it.

## Past problems and their solutions

_Carried over from prior sessions (mock-data timing bugs, over-triggering
detectors, evidence-leakage bugs) - see git history / prior HANDOFF
versions for those. This session's problems and solutions:_

| Problem | Root cause | Fix |
|---|---|---|
| No single command ran the complete pipeline; `detection_layer.py` and `network_layer.py` had to be run manually, in order, with the second reading a file the first happened to write | No orchestrator existed - each layer's `__main__` was written for standalone testing, not composition | Added `run_pipeline.py`: one process, one `DataStore`, calls both layers' real functions in the correct order, persists real output |
| Live pipeline never produced `suspected_alerts.csv`/`cases.csv` at all, only JSON | `detection_layer.py`'s `__main__` only ever wrote `.json`; no CSV persistence function existed for live output | Added `persist_alerts_csv()`/`persist_cases_csv()` to `detection_layer.py`, called from both its own `__main__` and `run_pipeline.py` |
| Files that looked like live pipeline output (`mock_data/cases.csv` etc.) were actually the mock generator's synthetic ground truth, with no naming distinction | `generate_mock_data.py` and `detection_layer.py`'s output happened to want the same filenames for conceptually different artifacts | Renamed the ground-truth files to `ground_truth_*.csv`; live output now goes to a separate `pipeline_output/` directory with the "real" names |
| `bundle_alerts_into_cases()` asserted `assigned_investigator_tier`/`escalated` on every case | Carried over from an earlier design that hadn't yet separated Case Intake from the (not-yet-built) human-in-the-loop stage | Removed both fields; case now only has `status: "open"` at creation |
| `network_layer.py`'s `__main__` wrote one shared `network_evidence.json` array for all cases | Written for quick manual inspection, never revisited once real per-case persistence was needed | Rewrote to loop over cases and write `evidence/{case_id}.json` per case; `run_pipeline.py` does the same for its own run |
| Typology dispatcher could silently misroute an unrecognized typology into money-mule handling | A double-negative boolean fallback condition (`typology == "money_mule" or typology not in (...)`) that was correct for the 4 known typologies but had no explicit "unknown typology" branch | Rewrote as an explicit if/elif chain with a clearly-labeled `unclassified` fallback |
| Account-swap evidence had pattern *labels* (`high_value_transaction` etc.) but no actual baseline-vs-recent *numbers* behind them | `build_account_swap_timeline()` only ever listed events and ran boolean pattern checks, never computed a quantitative deviation | Added `_compute_behavioral_summary()`, wired into both the timeline builder and the dispatcher's evidence payload |
| `source_transactions`/`network_scope` (the proof that contextual global-traversal evidence stays separate from a case's own originating alerts) were computed but then dropped before persistence | `wrap_as_evidence()` only copied `visualization_type` + typology-specific `evidence` + `patterns` into the persisted record, never the two fields that actually demonstrate case-scope vs. global-traversal separation | Added both as top-level fields on the wrapped Evidence Store record |

## Important assumptions

Unchanged from prior sessions: single fictional India-based bank, mostly
INR with occasional USD/international legs, deterministic mock data
(`seed=42`), current scale 220 accounts / ~1600 transactions / 38
ground-truth cases / ~25 planted fraud networks, valid `GEMINI_API_KEY`
assumed available when running the 3 agent files or `eval_pipeline.py`.

**New this session:** `run_pipeline.py` assumes `mock_data/` has already
been generated (it does not call `generate_mock_data.py` itself) and
creates `pipeline_output/` (and `pipeline_output/evidence/`) if they don't
exist. Re-running `run_pipeline.py` overwrites all files in
`pipeline_output/` from scratch each time (no incremental/append mode,
unlike `eval_pipeline.py`'s resume-skip logic).

## Things that must not be broken

Everything previously listed (CSV column names, `primary_trigger`
semantics, rule dicts/scores, `min_rules=2` gating, the no-ground-truth-
in-live-evidence rule) still applies, **plus, new this session:**

- **`run_pipeline.py`'s call order** - detection → bundle → persist →
  per-case evidence loop. Don't generate evidence before cases exist.
- **No `assigned_investigator_tier`/`escalated` field on any case object
  anywhere in `detection_layer.py` or `run_pipeline.py`'s output.** This
  was explicitly, repeatedly required by the user across two consecutive
  messages this session - treat as a hard architectural constraint.
  `ground_truth_cases.csv` also no longer has these two columns, for
  consistency.
- **`generate_network_evidence()`'s explicit if/elif dispatch structure**
  - the fix specifically was replacing implicit/fallback logic with
    explicit branches; don't reintroduce a catch-all.
- **The ground-truth vs. live-output file separation** -
  `ground_truth_*.csv` (mock_data/) vs. the unprefixed real names
  (pipeline_output/). Don't let any live-code path read a `ground_truth_*`
  file, and don't let `eval_pipeline.py` start reading `pipeline_output/`
  instead of ground truth without a deliberate reason (it would change
  what's being measured - "does the LLM pipeline agree with known-good
  labels" vs. "does the LLM pipeline agree with what Detection happened to
  flag", which are different questions).

## Technical debt

Unchanged from prior sessions (no test suite, no structured logging, no
persistence beyond flat files, `eval_pipeline.py` re-runs
`gather_evidence()`/`detect_all()` from scratch per case with no caching),
**plus:** `run_pipeline.py` has the same no-caching property - re-running
it re-executes detection and evidence generation for the entire dataset
every time, fine at 220 accounts, would need rework at real scale.

## Decisions like "we use X instead of Y because…"

- **We separated `mock_data/ground_truth_*.csv` from
  `pipeline_output/*.csv` instead of keeping one shared `cases.csv`
  because** two fundamentally different things - "what a hypothetical
  fully-built system would have concluded, for scoring" and "what the
  actual rules-as-written currently produce" - were sharing one filename,
  making it impossible to tell, just from the directory listing, which one
  you were looking at.
- **We built `run_pipeline.py` as a thin script that imports and calls the
  existing layer functions, instead of rewriting detection/evidence logic
  inline, because** the user was explicit that isolated fixes to
  individual functions without fixing orchestration was the exact failure
  mode to avoid - the fix needed to be connective tissue, and duplicating
  logic that already existed and was already correct would have
  reintroduced the same kind of drift risk this session was trying to
  close.
- **We kept `detection_layer.py`/`network_layer.py`'s own `__main__`
  blocks instead of deleting them once `run_pipeline.py` existed,
  because** standalone layer testing (e.g. "does detection alone still
  work after I changed a rule") is still a real, useful workflow distinct
  from "does the whole thing work end to end."

## External services and how they interact

Unchanged from prior sessions: Google Gemini API only
(`google-genai`/`gemini-3.6-flash`, `GEMINI_API_KEY`), used exclusively by
the 3 agent files. `run_pipeline.py` and everything it calls makes zero
external calls - it's pure local computation over the `DataStore`.

## Current overall progress

Against the target architecture:

```
Bank/DataStore → Detection Layer → Suspected Alerts → Case Bundling/Intake
    → Case-specific Investigation → Case-specific Network/Timeline Evidence
    → Evidence Store → Investigation/Audit Result → Human-in-the-loop Action
    → Monitor/Block/Escalate/Other final action
```

- ✅ Bank/DataStore
- ✅ Detection Layer (rules, scoring, alert generation - verified: 220
  accounts scanned, 31 alerts across all 4 typologies)
- ✅ Suspected Alerts (now genuinely persisted as live CSV/JSON, not just
  ground truth)
- ✅ Case Bundling/Intake (21 cases from 31 alerts, `case_id` as
  correlation key, no escalation logic)
- ✅ Case-specific Investigation / Case-specific Network/Timeline Evidence
  (verified: 21 evidence objects for 21 cases, 1:1, correct visualization
  type per typology - `network`/`network`/`transaction_timeline`/
  `behavioral_transaction_timeline`)
- 🟡 Evidence Store (flat per-case JSON files exist and are genuinely
  case-keyed; no query layer, no HTTP surface)
- 🟡 Hypothesis Agents + Contradiction Agent exist and are structurally
  wired to the real `DataStore` via `agents/evidence_builder.py`, but
  operate on **ground truth cases** (for evaluation), not on
  `run_pipeline.py`'s live-detected cases - these two are parallel
  consumers, not sequential stages, and nothing currently runs the
  hypothesis agents against `pipeline_output/cases.csv`
- ❌ Investigation/Audit Result (real completeness scoring)
- ❌ Human-in-the-loop Action (including investigator escalation - by
  design, not yet built anywhere)
- ❌ Monitor/Block/Escalate/final action
- ❌ Frontend, real API surface, authentication

The concrete, verified state as of this session's final run: **220
accounts → 31 alerts → 21 cases → 21 case-scoped evidence records**, every
one traceable end to end (demonstrated: `ACC000001 → ALERT-MONE-8EEE00D4 →
CASE-5BA32240 → EVID-081D9EAF`), with money_mule/account_swap correctly
rendered as timelines and smurfing/reverse_smurfing correctly rendered as
graphs, and zero investigator-tier/escalation logic anywhere in Detection
Layer or Case Intake.

### Progress against the fuller reference diagram (LangGraph multi-agent workflow)

The checklist above uses a coarser pipeline description from earlier in
the project's life and bundles several distinct components into single
buckets ("Investigation/Audit Result", "Human-in-the-loop Action"). A
later, more detailed architecture diagram (three-lane: "Intake &
Detection" / "LangGraph Multi-Agent State" / "Decision, Audit & Human
Review", plus a UI Layer and an Access Control Tiers note) makes several
components explicit that the coarse checklist above was silently standing
in for. Tracked separately here so nothing gets lost again:

**Lane 1 - Intake & Detection** (maps 1:1 to the checklist above):
- ✅ Bank System / Detection Agent / Anomaly? / Case Intake - built,
  verified this session (`run_pipeline.py`).
- ✅ No Action/Case Closed - implicit (an account with no triggered alert
  simply produces no case; no separate "closed" state is written anywhere,
  which is a gap if the diagram intends an explicit closed-case record).

**Lane 2 - LangGraph Multi-Agent State:**
- ✅ Evidence Gathering Agents (Beneficiary/Transaction History/Device &
  Geo) - exist, but as three data-pulling functions inside one
  `agents/evidence_builder.py`, not three separate LangGraph agent nodes.
- 🟡 Evidence Store - flat per-case JSON files (`pipeline_output/evidence/{case_id}.json`),
  not a queryable store a graph node could read/write incrementally.
- ✅ Path A - Scammer Hypothesis Agent / Path B - Legitimate Hypothesis
  Agent - built (`agents/scammer_hypothesis_agent.py`,
  `agents/legitimate_hypothesis_agent.py`), Gemini-backed.
- ✅ Contradiction Agent - built (`agents/contradiction_agent.py`).
- ❌ **Investigation Auditor Agent** - not built. Distinct from the
  Contradiction Agent; nothing computes evidence completeness or
  regulatory completeness anywhere.
- ❌ **Case Completeness Score** (as an explicit decision gate routing
  high/low) - not built. `ground_truth_cases.csv` has a
  `completeness_score` *column* (a pre-decided mock number for
  evaluation), but nothing in live code computes one, and nothing branches
  on it.
- ❌ **Request More Evidence / "re-gather" loop** - not built at all. The
  diagram shows low completeness routing back to the Evidence Gathering
  Agents; there is currently no control flow anywhere that re-invokes
  evidence gathering based on a completeness judgment - `eval_pipeline.py`
  calls Path A → Path B → Contradiction exactly once per case, always,
  with no loop.
- ❌ **Regulatory Compliance Rule Engine** - not built. Not the same thing
  as `detection_layer.py`'s fraud-typology rules (SMF/RSMF/MM/AS) - this
  would be a separate deterministic regulatory-compliance check, per spec
  section on the Regulatory Rule Engine's responsibilities.
- ❌ **Regulatory RAG** - not built. No retrieval-augmented generation
  component, no regulatory source corpus, nothing wired to any agent.

**Lane 3 - Decision, Audit & Human Review:**
- ❌ **Next-Best-Action Agent** - not built. `classify_alert()` in
  `detection_layer.py` produces a `recommended_initial_action` per
  *alert* (clear/monitor/escalate) as a severity classification, which is
  NOT the same thing as this diagram's Next-Best-Action Agent (a
  post-investigation, post-completeness-check recommendation at the case
  level) - don't conflate the two if building this.
- ❌ **Audit Trail / Replay Log** - not built. No record of what the
  agents did, when, or why is persisted anywhere beyond the evidence JSON
  itself (which records `generated_at` but not a full decision trail).
- ❌ **Human-in-the-Loop Review** - not built (no UI, no workflow, no
  reviewer role model).
- ❌ **Investigator Action** (Approve/Override/Escalate/Request More
  Evidence) - not built. This is also explicitly where investigator
  tier/escalation belongs, per repeated instruction across prior
  sessions - it is not Detection Layer's job, and this diagram confirms
  where it actually goes.
- ❌ **Case Memory** - not built. No persisted record of finalized
  investigations or historical patterns for future reference.

**Below the three lanes:**
- ❌ **UI Layer** (case dashboard, evidence viewer, completeness meter,
  action buttons) - not built. `main.py` is a bare FastAPI health-check
  stub; no frontend consumes any of this project's output yet.
- ❌ **Access Control Tiers / role-based evidence visibility** - not
  designed, not built. The diagram's own text is worth recording verbatim
  since it directly resolves an open question from an earlier session
  about where junior/senior distinctions belong: *"Junior Investigator:
  New Device Alerts and Geo-Location changes only. Senior Investigator:
  additionally KYC records and Financial records. If a junior
  investigator's evidence view requires KYC or funds case, the system
  routes the case to a Senior investigator rather than exposing restricted
  evidence."* This describes access-control-driven **routing**, not a
  field Case Intake writes onto a case (consistent with, and more precise
  than, this project's existing "escalation belongs in the human-in-the-
  loop stage, not Detection Layer" rule).

**LangGraph - declared but unused.** `backend/requirements.txt` lists
`langgraph` as a dependency, and the diagram's middle lane is explicitly
titled "LangGraph Multi-Agent State," implying the Evidence Gathering
Agents + Path A/B + Contradiction + Auditor + re-gather loop should be
wired together as a LangGraph `StateGraph` with conditional edges (the
"re-gather" and "low completeness" branches). **Nothing in the current
codebase imports or uses `langgraph`** - confirmed via `grep -rn
"langgraph\|StateGraph" backend/` returning zero matches. The current
agent orchestration (`eval_pipeline.py`) is plain sequential Python
function calls with a fixed call order and no conditional branching -
functionally equivalent to a single straight-line path through the graph
your diagram describes, with the auditor/completeness/re-gather loop
entirely absent. If LangGraph is intended to actually be used, this is a
structural rewrite of the agent orchestration layer, not an incremental
addition - flag before starting it, since it changes how
`evidence_builder.py`/the three agent files are called, not just what
calls them.