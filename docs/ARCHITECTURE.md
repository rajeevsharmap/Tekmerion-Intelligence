# ARCHITECTURE.md — Target Architecture (Source of Truth)

This is the authoritative description of what the backend **should** do.
`docs/backend_implementation_status.md` tracks what it **actually** does
right now against this document, phase by phase. When they disagree, this
file wins — fix the code, don't rewrite this file to match the code,
unless a genuine design decision changed (in which case update this file
explicitly and say why, per the "Decisions like X instead of Y" pattern
used throughout this project's docs).

## Non-negotiable principle

> Do not optimize for "make the number of detected cases equal the number
> generated." Optimize for "make the pipeline independently discover,
> investigate, explain and evaluate the synthetic scenarios."

A fraud network being *planted* in mock data does not mean it *will* or
*should* produce exactly one alert and exactly one case. The pipeline's
job is to behave the way a real detection system would against real data
— sometimes missing things (false negatives), sometimes over-flagging
(false positives), sometimes merging related signals, sometimes not. The
mock generator's job is to plant realistic scenarios; the *evaluation
layer's* job is to measure how well the pipeline found them — not to force
agreement.

## The three-way data separation

Three concepts must never be treated as the same thing, anywhere in the
codebase:

| | What it is | Who may read it |
|---|---|---|
| **Raw observable data** | accounts, transactions, devices, geo_events, beneficiaries — what a real bank system would expose | Everything |
| **Ground truth** | which accounts/transactions belong to an injected scenario, expected typology, expected signals, expected evidence, expected outcome | **Evaluation code only** |
| **Live pipeline output** | what the actual rules/agents independently produced from raw data alone | Everything downstream of the pipeline (frontend, evaluation-for-scoring-only) |

**Ground truth must never be read by:** `detection_layer.py`, case
bundling, investigation agents, `network_layer.py`, any live API
endpoint, or any LLM prompt. The only legitimate reader is evaluation code
that compares ground truth against live output *after the fact*, purely
to produce metrics.

**[VERIFIED] (Checkpoint 3):** confirmed by an AST-based static scan of
every live module (imports, non-docstring string literals, attribute
access, identifiers — comments/docstrings excluded, since those may
legitimately describe the separation in prose) plus a dynamic proof
(running the real Detection → Case Bundling pipeline against a copy of
`mock_data/` with every `ground_truth_*.csv` deleted and confirming
byte-identical output) — see `tests/test_ground_truth_isolation.py`. A
repo-wide grep across all `.py`/`.json`/`.js`/`.jsx`/`.ts`/`.tsx`/`.yaml`
files (excluding `venv/`, `node_modules/`, `.git/`) found ground-truth
references only in `generate_mock_data.py` (the generator, permitted),
`run_pipeline.py`/`detection_layer.py` (comments only), and the test
files themselves.

## Target directory layout

```
backend/
  mock_data/
    accounts.csv / transactions.csv / devices.csv / geo_events.csv / beneficiaries.csv
                                        ← RAW DATA (source of truth for the pipeline)
    ground_truth/
      fraud_networks.json              ← stable network_id per injected scenario (see below)
      ground_truth_alerts.csv
      ground_truth_cases.csv
      ground_truth_case_escalations.csv
                                        ← GROUND TRUTH (evaluation-only, never read live)
  pipeline_output/
    suspected_alerts.csv / cases.csv
    evidence/{case_id}.json
                                        ← LIVE PIPELINE OUTPUT (independently derived)
  eval_results.csv                     ← ground truth vs. live output comparison
```

Current implementation status of this layout: see
`docs/backend_implementation_status.md`. As of this writing, the
`ground_truth/` subdirectory and `fraud_networks.json` do not exist yet —
the ground-truth files currently sit flat in `mock_data/` with a
`ground_truth_` filename prefix, which satisfies the *naming*
non-collision requirement but not the subdirectory structure recommended
here. Moving them into a subdirectory is optional polish, not urgent;
what matters is that live code never reads them, which is already true.

## The 9-stage pipeline

```
RAW DATA → DETECTION → ALERTS → CASE BUNDLING → INVESTIGATION → EVIDENCE
  → EVIDENCE COMPLETENESS → INVESTIGATOR AUTHORITY → ACTION / ESCALATION
```

Each arrow is a real data transformation with a real function boundary —
no stage may skip ahead by reading a later stage's expected output, and no
stage may read ground truth to shortcut its own computation.

1. **Raw data** — `data_store.py`'s `DataStore` class, loaded from the 5
   bank source CSVs.
2. **Detection** — `detection_layer.py`'s 4 typology detectors
   (`detect_smurfing`, `detect_reverse_smurfing`, `detect_money_mule`,
   `detect_account_swap`), each a pure function of `DataStore` state. No
   randomness, no ground truth.
3. **Alerts** — the output of stage 2: `{alert_id, account_id,
   transaction_id, relevant_transaction_ids, typology, triggering_rules,
   alert_score, ...}`. One alert is not one case and is not one fraud
   network. `alert_id` is a deterministic hash of the alert's own content
   (typology, account_id, transaction_id, triggering_rules, created_at) —
   see "Determinism & reproducibility" below.
4. **Case bundling** — `bundle_alerts_into_cases()`. Groups alerts that
   plausibly describe the same underlying incident and records *why* via a
   structured `bundle_reason` field. [IMPLEMENTED] [VERIFIED] as of
   Checkpoint 3 — see "Bundle reason" below.
5. **Investigation** — gathering typology-specific evidence for a
   bundled case: transaction chain, beneficiary relationships,
   device/geo history, network traversal. Currently done by
   `agents/evidence_builder.py` (for the LLM-agent evaluation path) and
   `network_layer.py` (for the case-scoped graph/timeline evidence path)
   — these are two separate, not-yet-unified investigation entry points;
   see status doc.
6. **Evidence** — a structured, typed evidence object per case (see
   "Evidence object model" below). Currently only partially
   structured — see status doc for the gap between what exists and this
   target schema.
7. **Evidence completeness** — required vs. available vs. missing,
   computed from the actual evidence gathered, never a random number (see
   "Evidence completeness model" below). [IMPLEMENTED] as of Checkpoint 2;
   see "Checkpoint 5" below for the case-level completeness score that
   builds on top of this per-evidence-item score.
8. **Investigator authority** — a policy decision (junior/senior),
   computed from evidence completeness + typology risk + confidence +
   contradiction state — never hardcoded, never randomly assigned, never
   decided by Detection/Case-Intake (see "Investigator authority model"
   below). [IMPLEMENTED] as of Checkpoint 4 (`authority_policy.py`).
9. **Action / escalation** — clear / monitor / block / escalate, driven by
   authority + policy. [IMPLEMENTED] as of Checkpoint 6
   (`next_best_action.py` / `audit_trail.py` / `investigator_action.py` /
   `case_state.py` / `case_memory.py` / `action_pipeline.py`) for the
   deterministic recommendation, authorization enforcement, audit
   trail, human review, and case memory portions — see the "Checkpoint
   6" section below. **Still not implemented:** actually executing an
   action against a real banking system (this remains simulated —
   `action_executed` events are recorded but never call an external
   system) and SAR report generation, which is explicitly deferred to a
   later checkpoint (see `case_memory.py`'s docstring). See the
   Checkpoint 5 section below for the regulatory compliance, audit,
   completeness, and re-gather stages that run just ahead of this one.

## Checkpoint 5: Regulatory compliance, audit, completeness, re-gather

**This section supersedes the abbreviated stage list above for what
happens between stage 6 (Evidence) and stage 8 (Investigator authority).**
The 9-stage list above still describes the pipeline correctly at a high
level, but understates what now runs between "Evidence" and
"Investigator authority": five additional, real (non-LLM, deterministic)
stages, all implemented and wired into `run_pipeline.py` as of Checkpoint
5.

```
... → EVIDENCE (stage 6)
    → JURISDICTION DETERMINATION            (jurisdiction.py)
    → REGULATORY COMPLIANCE RULE ENGINE     (regulatory_rules.py)
        ↳ REGULATORY RAG                    (regulatory_rag.py + regulatory_corpus.py)
    → INVESTIGATION AUDITOR                 (investigation_auditor.py)
    → CASE COMPLETENESS SCORE               (case_completeness.py)
    → [incomplete] → BOUNDED TARGETED RE-GATHER (regather_loop.py, max 2 iterations)
                       ↳ re-evaluates regulatory findings/auditor/completeness
                         against the same case's jurisdiction (unchanged)
    → INVESTIGATOR AUTHORITY (stage 8, Checkpoint 4 — unchanged)
    → ACTION / ESCALATION (stage 9 — not yet implemented)
```

**Jurisdiction determination** (`jurisdiction.py`) — the mock dataset is
India-primary (every account's `registered_country` is "India"), with a
minority of genuinely international/cross-border transactions and geo
events. This module determines, per case, from real already-available
fields only (`registered_country`, `is_international`, `currency`,
`geo_events.registered_country_match` — never a guess, never an LLM call):
a `jurisdiction` label (`IN` | `US` | `cross_border` | `unknown`), a
`base_jurisdiction`, the `applicable_jurisdictions` list that gates
downstream retrieval, a `confidence`, and a structured `basis` an auditor
can read. A foreign counterpart or foreign-currency transaction on an
India-registered account does **not** make US law apply to that account —
it makes the case `cross_border`, which has its own tagged reference
material (India-side FEMA/LRS), never a US citation.

**Regulatory compliance rule engine** (`regulatory_rules.py`) — a small,
config-driven set of deterministic BSA/AML-adjacent rules
(`RULE_DEFINITIONS`), each evaluated only against already-gathered real
evidence. Every rule result is one of `confirmed_concern` (multiple
independently-discovered corroborating signals — never a single anomaly),
`potentially_applicable` (one real signal, flagged for human review),
`no_identified_breach`, or `insufficient_evidence` (evidence not gathered,
OR jurisdiction could not be determined, OR the only amounts gathered are
in a currency this rule cannot compare against its jurisdiction-
appropriate threshold). The CTR rule is jurisdiction-and-currency-aware:
India uses PMLA Rule 3 (INR 10,00,000); the US uses 31 CFR 1010.311
($10,000) — never one hardcoded figure applied regardless of currency.

**Regulatory RAG** (`regulatory_rag.py` + `regulatory_corpus.py`) — pure,
deterministic keyword-overlap retrieval over a small **bundled, static
reference corpus** (explicitly documented as such — not a live regulatory
feed, not legal advice). Jurisdiction is a hard gate checked *before* any
keyword scoring: an entry whose own `jurisdiction` tag is outside the
case's `applicable_jurisdictions` is structurally unreachable, regardless
of keyword overlap. Every returned entry carries its own provenance
(`source_id`, `citation`, `authority`, `jurisdiction`) copied verbatim
from the corpus — nothing is paraphrased or invented here. India entries
were checked this session against FIU-IND's own FAQ page, SEBI's cash-
transaction-report guidance, and the RBI KYC Master Direction's own
reference number; where a specific numeric threshold could not be
corroborated (e.g. cross-border wire-transfer reporting specifically),
the corpus states the qualitative obligation only rather than guessing.

**Investigation auditor** (`investigation_auditor.py`) — an independent
structural re-check over already-computed upstream output; it never
re-gathers evidence, never calls an LLM, and never simply repeats the
rule engine's own conclusion. Checks include unsupported regulatory
claims, contradictory evidence (degrades to "not evaluated" when the LLM
contradiction agent hasn't run for this case, rather than guessing),
provenance gaps, unsupported authority conclusions, **jurisdiction
mismatch** (a regulatory citation whose own jurisdiction isn't in the
case's `applicable_jurisdictions`), and **unresolved jurisdiction** (a
`confirmed_concern` regulatory conclusion resting on a case whose own
jurisdiction is `unknown`).

**Case completeness score** (`case_completeness.py`) — one explainable
0–100 score combining three real, weighted components: evidence
completeness (recomputed over only *reachable* evidence types, so a
dataset-wide structural gap like `source_of_funds` — permanently absent
from every case in this mock dataset — cannot by itself cap every case's
score below "complete"), regulatory rule resolution, and auditor
cleanliness (penalized per critical issue). Jurisdiction uncertainty is
surfaced as an explicit `reasons` entry
(`case_jurisdiction_not_resolved_with_high_confidence`) rather than a
separate weighted component, since it already reaches the score through
the regulatory/auditor components above.

**Bounded targeted re-gather loop** (`regather_loop.py`) — when
completeness is below threshold, converts the *specific* missing evidence
into targeted requests against the existing typology-specific network/
timeline builders, re-run with a wider time window (never a new,
fabricated evidence source). Hard-capped at 2 iterations; stops earlier
the moment nothing case-specific remains to request. Intentionally
jurisdiction-*blind* by design: jurisdiction is account-level metadata
(`registered_country`) that a wider transaction/network traversal window
cannot change, so there is no jurisdiction-specific evidence for this loop
to request. `run_pipeline.py` re-evaluates regulatory findings, the
auditor, and completeness against the re-gathered evidence using the
*same*, already-determined `jurisdiction_context` (not recomputed).

## Checkpoint 6: Next-Best-Action, Audit Trail, Human Review, Investigator Action, Case Memory

**This section supersedes the abbreviated stage list above for what
happens between stage 8 (Investigator authority, Checkpoint 4) and stage
9 (Action / escalation).** Five additional, real (non-LLM, deterministic)
components, all implemented and wired into `run_pipeline.py` as of
Checkpoint 6:

```
... → INVESTIGATOR AUTHORITY (stage 8, Checkpoint 4 — unchanged)
    → NEXT-BEST-ACTION                      (next_best_action.py)
    → AUDIT TRAIL                           (audit_trail.py)
    → HUMAN REVIEW                          (investigator_action.py — create_human_review)
    → INVESTIGATOR ACTION                   (investigator_action.py — authorize_action / record_investigator_action)
        ↳ RECOMMENDATION OVERRIDE           (requires an explicit, non-empty override_reason)
    → CASE MEMORY                           (case_memory.py)
```

All five are sequenced by `action_pipeline.py`'s `CaseActionLayer`, one
instance per case, which also threads the case's lifecycle state
(`case_state.py`) through the flow above. `CaseActionLayer` does not
duplicate any of the five modules' own logic — it only calls them in
order and records every step to the case's `AuditTrail`.

**Next-Best-Action** (`next_best_action.py`) — a deterministic,
config-driven decision cascade (never an LLM call, never `random`/
`uuid4`) over already-computed upstream signals only: Checkpoint 5's
`case_completeness`/`regulatory_findings`/`auditor_result` and
Checkpoint 4's `authority_decision`. Produces exactly one action from a
fixed vocabulary (`CLEAR`, `MONITOR`, `REQUEST_MORE_INFORMATION`,
`ESCALATE_TO_SENIOR`, `RESTRICT_ACCOUNT`, `BLOCK_TRANSACTION`,
`FILE_SAR`, `CLOSE_CASE`) with machine-readable `reason_codes`,
`regulatory_basis`, `supporting_evidence_ids`, a `required_authority`
(the greater of the case's own Checkpoint-4 tier and the action's own
minimum — irreversible actions like `BLOCK_TRANSACTION`/`RESTRICT_
ACCOUNT`/`FILE_SAR` always require `senior`, regardless of how the case
itself routed), and `requires_human_review: True` — this engine only
recommends; it never executes.

**Case lifecycle state machine** (`case_state.py`) — an explicit state
enum (`SUSPECTED → INVESTIGATING → AUDIT_READY → HUMAN_REVIEW →
ACTION_PENDING → ACTION_EXECUTED → CLOSED`, plus `ESCALATED`) with a
fixed adjacency list of allowed transitions. `CLOSED` is terminal (no
outgoing edges). Any transition not explicitly listed — including from
an unrecognized state — raises `InvalidTransitionError` rather than
silently occurring; an unauthorized/rejected investigator attempt
returns the case to `HUMAN_REVIEW`, it never advances to
`ACTION_EXECUTED`.

**Audit trail** (`audit_trail.py`) — one `AuditTrail` instance per case.
Append-only from the application's perspective: the class exposes only
`append()` and read-only accessors that return defensive copies; there is
no delete/overwrite code path anywhere in the module. Every lifecycle
event (`case_created` through `case_closed`, 18 event types) records
actor, actor type (`system` vs `investigator` — so a system recommendation
is never conflated with a human decision or a human action), before/after
state, reason, related evidence, and a deterministic content-hash
`event_id`.

**Investigator action / authorization** (`investigator_action.py`) —
enforces Checkpoint 4/Checkpoint-6 authority requirements against a
backend-resolved investigator role (`resolve_investigator()` — a
deterministic test identity/role table, `INVESTIGATOR_DIRECTORY`, since
no real authentication exists yet; a caller can supply an
`investigator_id` but can never supply a role directly, and an unknown ID
resolves to unauthenticated, never a permissive default). `authorize_
action()` computes `authorized` server-side and returns a full record
even when unauthorized, so the attempt is still audited rather than
silently dropped. `create_human_review()` builds the structured Human
Review record (system recommendation vs. investigator decision, kept
distinct). `record_investigator_action()` builds the Investigator Action
record; if the requested action differs from the system recommendation,
a non-empty `override_reason` is mandatory or `OverrideReasonRequiredError`
is raised — the recommendation itself is never silently changed to match
what was actually done.

**Case memory** (`case_memory.py`) — the durable, historical investigation
record. References evidence/regulatory/audit-trail entries by ID (never
duplicates full evidence blobs). `build_case_memory()` creates the record
once per case; `update_case_memory()` only ever appends to `*_history`
lists (`lifecycle_history`, `human_review_history`, `investigator_action_
history`, `case_completeness_history`) and returns a new dict — it never
mutates or drops a prior entry. Carries every field a later SAR-generation
checkpoint will need (typology, jurisdiction, regulatory findings with
citations, evidence references, auditor findings, the system
recommendation, and the investigator's actual action/identity/role/
authorization result/timestamp) without generating SAR narrative text
itself. Ground-truth labels are never read or stored here — verified by
`tests/test_checkpoint6.py` and by inspection (no `ground_truth` import
anywhere in the Checkpoint 6 modules).

**Known limitation, honestly carried forward:** on the checked-in mock
dataset, every real case's typology/evidence profile routes to
`RESTRICT_ACCOUNT`, `BLOCK_TRANSACTION`, or `REQUEST_MORE_INFORMATION` —
no real case naturally reaches `CLEAR`/`CLOSE_CASE`/`MONITOR`, because
Checkpoint 3's detection/bundling only creates a case from a fired alert
in the first place (there is no "nothing happened" case in this
dataset). The `CLEAR` and junior-authorized paths, along with a senior
override and an escalation, are proven correct via `tests/
test_checkpoint6.py`'s hand-built fixtures and `demo_checkpoint6.py`'s
five labeled scenarios instead — not fabricated as if they occurred
naturally in the mock pipeline run.

## Ground-truth network model

Every injected fraud scenario gets a **stable, typology-prefixed network
ID** independent of any account/transaction/alert/case ID:

```
GT-SMURF-001, GT-SMURF-002, ...
GT-REVSMURF-001, ...
GT-MULE-001, ...
GT-ATO-001, ...   (account takeover / account_swap)
```

Each ground-truth network record:

```json
{
  "network_id": "GT-SMURF-001",
  "typology": "smurfing",
  "primary_account": "ACC000123",
  "accounts": ["ACC000123", "ACC000045", "..."],
  "transactions": ["TXN000012", "TXN000013", "..."],
  "expected_signals": ["structuring_below_threshold", "high_velocity_fan_in"],
  "required_evidence": ["source_accounts", "transaction_chain",
                         "temporal_concentration", "amount_distribution",
                         "downstream_destination", "source_of_funds"],
  "expected_outcome": "fraud"
}
```

`expected_signals` and `required_evidence` describe what a *correct*
detector/investigator *should* find — the detector must discover these
independently from the transaction data; it must never read this record.
This is evaluation-only, same as everything else under `ground_truth/`.

**Current status:** not implemented. The mock generator currently tracks
fraud networks as plain in-memory dicts during generation
(`{"typology": ..., "primary_account": ..., "transactions": [...]}`, see
`generate_smurfing_network()` etc. in `generate_mock_data.py`) but never
assigns a stable `network_id` and never persists this structure to disk —
so today there is no way to ask "which ground-truth network, if any, does
this live-detected case correspond to?" except by cross-referencing
`account_id` by hand. This is required for the evaluation metrics in
"Ground-truth evaluation methodology" below (especially recall/precision
at the network level, not just the alert/case level).

## Typology definitions & evidence requirements

### Smurfing — NETWORK GRAPH

```
many senders → collector → hop2 → hop3 → final destination
```

Detection should independently discover: structuring below threshold,
fan-in, velocity, consolidation, downstream movement.

| Evidence type | Weight |
|---|---|
| transaction_chain | 0.20 |
| temporal_pattern | 0.15 |
| counterparty_relationship | 0.15 |
| beneficiary_information | 0.10 |
| device_information | 0.15 |
| geo_information | 0.10 |
| source_of_funds | 0.15 |

(Weights above are the Phase 7 example weighting and apply generically;
each typology may reasonably use a different weighting scheme — the point
is that weights must be **documented and configurable**, not implicit in
code. Current status: no weighting scheme is implemented at all — see
status doc.)

Additional smurfing-specific required evidence beyond the generic table:
source accounts, inbound transaction cluster, amount distribution,
threshold proximity, collector account, downstream chain.

### Reverse smurfing — NETWORK GRAPH

```
source → multiple level-1 accounts → multiple level-2 accounts → cash-out/final accounts
```

Detection should independently discover: fan-out, structuring, rapid
dispersal, downstream cash-out.

Required evidence: source account, inbound source transaction, fan-out
recipients, dispersal timing, amounts, downstream recipients, cash-out
behavior, beneficiary information.

### Money mule — TRANSACTION TIMELINE (not a graph)

```
multiple unrelated inbound transactions → mule account → rapid forwarding
```

Detection should independently discover: unrelated sender concentration,
pass-through velocity, inbound/outbound ratio, temporal proximity, unusual
counterparties, rapid onward movement.

Required evidence: inbound senders, sender relationship diversity,
inbound timestamps, outbound transaction, pass-through interval, amount
retention/forwarding ratio, beneficiary, destination account, account
behavior baseline.

### Account swap / account takeover — BEHAVIORAL TRANSACTION-VS-TIME TIMELINE (not a graph)

Canonical machine-readable typology: `account_swap`. Human-readable
label: "Account Takeover / SIM-Swap".

```
trusted device → SIM change/new device → unusual geo/IP → possible VPN
  → impossible travel → new/untrusted device → high-value transaction
  → new/first-time beneficiary
```

The same account_id must connect device, SIM-change event, geo event,
transaction, and beneficiary — independently randomized records must
never accidentally break this causal chain. **This is already correctly
implemented** (`generate_account_swap()` deliberately mutates the
pre-seeded attacker device/geo timestamps to cluster around a single
`attack_time` anchor — see status doc, this is one of the few Phase 4
items already done correctly and must be preserved).

Required evidence: trusted device history, new device, device
fingerprint, SIM change, geo history, impossible travel, IP/VPN
information, high-value transaction, new beneficiary, beneficiary
verification status.

## Alert ≠ case ≠ fraud network

One fraud network may produce zero, one, or multiple alerts. Multiple
alerts may bundle into one case or stay as multiple cases, depending on
bundling policy. **Never assume `1 fraud network = 1 alert = 1 case`.**
Evaluation must measure the mapping between them, not assert equality.

## Bundle reason (case bundling policy)

The bundler must be able to answer "why do these alerts belong to the same
case?" and record the answer as structured data, not bundle purely because
alerts happen to come from the same mock network (the live system has no
knowledge of mock network IDs).

```json
{
  "bundle_reason": [
    "same_primary_account",
    "same_typology",
    "within_case_window",
    "shared_transaction_chain"
  ]
}
```

Correlation attributes to consider: account_id, typology, temporal
proximity, transaction relationships, shared evidence entities, network
connectivity.

**Current status [IMPLEMENTED] [VERIFIED] (Checkpoint 3):**
`bundle_alerts_into_cases()` now bundles in two steps: (1) a temporal
cluster per `account_id` within a configurable `window_hours` (default
24h, recorded on the case as `correlation_window_hours`), then (2) within
that cluster, alerts are only merged if they are actually correlated —
same `typology`, or a shared entry in `relevant_transaction_ids` (see
`_pairwise_correlation()` / `_split_cluster_by_correlation()`). Same
account + same window alone no longer merges two alerts of different,
uncorrelated typologies — each becomes its own single-alert case with
`bundle_reason: ["single_alert_case"]`. A merged case's `bundle_reason` is
`sorted({"same_primary_account", "within_case_window"} | correlation
reasons)`, e.g. `["same_primary_account", "same_typology",
"within_case_window"]` or (when the correlation is cross-typology, via a
shared transaction anchor) `["same_primary_account",
"shared_transaction_chain", "within_case_window"]`. Verified against the
real generated dataset: every existing cross-typology merge (money_mule +
smurfing sharing an anchor transaction) turned out to already satisfy
`shared_transaction_chain`, so case/alert counts are unchanged from before
this checkpoint (31 alerts → 21 cases) — the correction changes *why* a
merge happens and makes it inspectable, not *how many* merges happen on
this particular dataset. Network connectivity, shared beneficiaries, and
shared devices are not yet usable correlation signals because the `Alert`
object itself doesn't carry beneficiary/device IDs — only
`relevant_transaction_ids` — see "Known limitations" in
`backend_implementation_status.md`.

## Evidence object model

Every evidence item, at minimum:

```json
{
  "evidence_id": "EVD-001",
  "case_id": "CASE-001",
  "evidence_type": "transaction_chain",
  "source": "transactions.csv",
  "source_record_ids": ["TXN001", "TXN002", "TXN003"],
  "required": true,
  "available": true,
  "quality": "high",
  "confidence": 0.94,
  "supports": ["smurfing"],
  "contradicts": []
}
```

For evidence that is required but not available:

```json
{
  "evidence_type": "source_of_funds",
  "required": true,
  "available": false,
  "missing_reason": "documentation_not_available"
}
```

**Missing evidence is a first-class object/state, never a free-text
string.** A structured `missing_reason` with a `severity` field is what
lets an escalation policy reason over evidence gaps programmatically (see
"Missing-evidence-driven escalation" below) instead of pattern-matching
prose.

**Current status:** `wrap_as_evidence()` in `network_layer.py` produces
`{evidence_id, case_id, evidence_type, typology, source, confidence,
data, generated_at}` — evidence_type is always the hardcoded string
`"network_analysis"`, there is no per-evidence-item breakdown, no
required/available/missing tracking, no `source_record_ids`, no
`supports`/`contradicts`. This is a single evidence *blob* per case, not
a collection of typed evidence *items*. Building the item-level model
above is required before evidence completeness (stage 7) can be computed
from real data rather than a random number.

## Evidence completeness model

```
REQUIRED EVIDENCE → AVAILABLE EVIDENCE → MISSING EVIDENCE
  → EVIDENCE QUALITY → COMPLETENESS
```

Simple version: `completeness = available_count / required_count`.
Preferred version: weighted by evidence-item importance (see the smurfing
weight table above as an example scheme) so that, e.g., missing
`source_of_funds` (weight 0.15) hurts completeness more than missing
`geo_information` (weight 0.10) for a typology where the former matters
more.

**Completeness must be computed from actual gathered evidence, every
time. `random.gauss(base_score, 12)` (or any other random draw) may exist
ONLY inside the ground-truth generator, as a stand-in for "what
completeness the eventual investigation would plausibly reach" for
evaluation purposes — it must never be read by, or presented as, live
system output.**

**Current status:** not implemented in live code at all.
`generate_mock_data.py`'s `build_case_from_network()` computes
`completeness = min(99, max(35, int(random.gauss(base_score, 12))))` —
this is explicitly ground-truth-only (correctly, it only ever lands in
`ground_truth_cases.csv`) but there is no live equivalent anywhere. This
is the top-priority implementation gap.

## Network layer traversal rules

Case-scoped transaction IDs are **investigation anchors, not the complete
network boundary**. If a case contains `TXN-A` and `TXN-A` leads to
account Y, the network layer must be able to retrieve Y's other
transactions (`TXN-B`, `TXN-C`, ...) from the underlying dataset even
though they were never part of the original alert — those become
*contextual* network evidence, never retroactively merged into the
case's own alert/transaction list, never auto-promoted into new
alerts/cases (if Y independently satisfies detection rules, that's
Detection Layer's job to raise separately).

Configurable, and currently implemented:
- `MAX_DEPTH = 3` — maximum graph traversal depth.
- Time-windowed traversal (`anchor_time ± window_hours`, default 72h for
  smurfing/reverse_smurfing graphs, 48h for money_mule timelines, 7 days
  for account_swap timelines) — added specifically because unrestricted
  traversal on a small dense demo graph touches almost the entire dataset
  (small-world effect).

Not yet configurable: transaction direction as an explicit parameter
(inbound/outbound/both exists as a code-level `direction` argument but
isn't exposed as case-level policy), minimum relevance threshold (no
concept of "this discovered edge is too weak/old to include" exists).

**Verified this session:** the persisted evidence for a real case shows
`case['alert_ids']` (2 alerts) vs. a *separately computed*
`source_transactions` list (15 globally-traversed transactions) — but see
the regression noted in the status doc: `wrap_as_evidence()` currently
drops `source_transactions` before persisting, even though
`generate_network_evidence()` computes it correctly. The distinction is
correct in-memory but not currently provable from the persisted JSON file
on disk.

## Network output shape by typology

| Typology | visualization_type | Contains |
|---|---|---|
| smurfing | `network` | nodes, edges, node roles (source/collector/intermediary/final_destination/cash_out), transaction IDs, amounts, timestamps, traversal depth |
| reverse_smurfing | `network` | same shape as smurfing |
| money_mule | `transaction_timeline` | chronological in/out events, summary (total in/out, ratio, median gap) |
| account_swap | `behavioral_transaction_timeline` | SIM/device/geo/transaction events + a `behavioral_summary` comparing baseline vs. recent activity |

Never force all four typologies into the same visualization model — this
was a real bug fixed in a prior session (an implicit fallback used to be
able to route an unrecognized typology into money-mule timeline handling)
and must not regress.

## Contradiction agent constraints

The contradiction agent evaluates competing hypotheses (e.g. "smurfing"
vs. "legitimate business bulk payments") using evidence **already
gathered** by the investigation agents. It must never independently search
the entire typology universe, and it must never fabricate evidence. Its
job: identify supporting evidence, contradictory evidence, unresolved
evidence, and a hypothesis confidence — nothing more.

**Current status:** `agents/contradiction_agent.py` exists and follows
this contract (it receives two already-computed hypothesis results and
resolves between them, never calls tools or fetches its own evidence).
This part of the architecture is already correct.

## Investigator authority model

**Evidence completeness and investigator authority are separate
decisions.** A case can have high completeness and still require senior
review (e.g. a high-risk typology or high-value transaction). A case can
have moderate completeness and still be safely junior-resolvable,
depending on policy.

```
Detection → Investigation → Evidence assessment → Authority assessment
```

Junior may resolve when: evidence sufficient, confidence sufficient, no
critical evidence gap, action falls within junior authority.

Senior required when: critical evidence missing, high-risk typology,
high-value transaction, complex network, contradictory evidence,
policy-defined high-risk action, junior authority exceeded.

**`ground_truth_required_tier` may exist for evaluation, but live routing
must be computed by an actual policy engine — never randomly assigned,
never hardcoded, never decided by Detection Layer or Case Intake.** This
was explicitly corrected in a prior session (removed
`assigned_investigator_tier`/`escalated` from `bundle_alerts_into_cases()`)
— that correction remains valid and must not be reverted. What's still
missing is the *positive* half: an actual authority-assessment component
that computes a real tier from real policy, which does not exist yet
anywhere in live code.

## Missing-evidence-driven escalation

Replace hardcoded strings like `"source-of-funds documentation
incomplete"` (currently only present in the ground-truth generator, as
`gap_options` dict values) with structured evidence gaps the escalation
policy can reason over:

```json
{"missing_evidence": [{"type": "source_of_funds", "severity": "critical", "reason": "not_available"}]}
```

Policy sketch: critical required evidence missing → escalate. Evidence
sufficient + action within junior authority → junior may resolve.
Evidence conflicts → escalate.

**Current status:** not implemented in live code (ground-truth-only, as
free text, not structured).

## Ground-truth evaluation methodology

The evaluation layer (separate from the live pipeline) must compute, at
minimum: detection precision, detection recall, false-positive rate,
false-negative rate, typology classification accuracy, alert-to-case
bundling accuracy, case-level precision/recall, network reconstruction
accuracy, evidence completeness accuracy, missing-evidence identification
accuracy, junior/senior routing accuracy, recommended-action accuracy.

**Do not expect `25 injected fraud networks == 25 alerts == 25 cases`** —
evaluate the actual mapping between ground truth and live output instead.

**Current status:** `eval_pipeline.py` currently measures exactly one of
these twelve metrics — whether the LLM contradiction agent's
`favored_hypothesis` matches `ground_truth_label` (fraud/legitimate),
broken down by typology. It does not measure detection precision/recall
at the alert or network level, bundling accuracy, evidence-completeness
accuracy, or routing accuracy, because none of those upstream
computations (real completeness, real authority) exist yet to measure.
Building the ground-truth network model (stable `network_id`s) above is a
prerequisite for the network-level metrics specifically.

## API / LLM safety

- Quota/rate-limit handling: **implemented**, in `eval_pipeline.py`
  (`CALL_SPACING_SECONDS`, retry-with-backoff on 429/503, an explicit
  `print_daily_quota_alert()` warning when the daily quota is hit). This
  is real, working infrastructure — preserve it, don't rebuild it.
- API key handling: **implemented correctly** — `GEMINI_API_KEY` is read
  from the environment in all three agent files; no key appears in any
  CSV, mock data file, frontend code, or committed file, confirmed by
  inspection this session.
- LLM privacy masking (real customer identifiers → tokenization → LLM →
  structured response → controlled demasking): **not implemented.**
  Currently the hypothesis agents receive raw `account_id`,
  `customer_name` (via account profile — actually check: `evidence_builder.gather_evidence()`'s
  `account_profile` field currently does NOT include `customer_name`, only
  `risk_rating/kyc_status/account_type/account_open_date/avg_monthly_txn_count/avg_monthly_txn_amount`
  — so customer names are not currently sent to the LLM, which is
  good, but this is incidental, not a designed masking architecture, and
  beneficiary names ARE sent (`beneficiary_name` is included in the
  cleaned beneficiary evidence)). If a masking architecture is required,
  it needs to be designed from scratch, not "restored" — there is no
  prior version of it in this codebase to preserve.

## Determinism & reproducibility

`generate_mock_data.py` seeds `Faker`/`random` with a fixed value at
import time (`seed=42`, not currently exposed as a `--seed` CLI flag).
Running it twice with the same `--num_accounts`/`--num_cases` produces
byte-identical output.

**[VERIFIED] (Checkpoint 3):** Live detection/case-bundling logic contains
zero calls to the `random` module, and — as of this checkpoint — zero
calls to `uuid.uuid4()` either. Prior to Checkpoint 3, `detection_layer.py`
generated `alert_id` and `case_id` via `uuid.uuid4()`, which draws from
`os.urandom` and is **not** affected by `random.seed(42)`; this silently
contradicted this section's own claim, since re-running the pipeline
against identical `mock_data/` produced different alert/case IDs every
time (confirmed empirically this checkpoint). Fixed: `alert_id` is now
`sha256(typology, account_id, transaction_id, triggering_rules,
created_at)[:8]`, and `case_id` is `sha256(account_id, sorted
alert_ids)[:8]` — both pure functions of the alert's/case's own already-
deterministic content. Verified: two full pipeline runs against the same
`mock_data/` now produce byte-identical `suspected_alerts.json` and
`cases.json` (including IDs), confirmed via `json.dumps(..., sort_keys=True)`
equality, not just matching counts.

**Known, intentionally out-of-scope non-determinism:** `evidence/*.json`
output is **not** byte-identical across repeated runs — `network_layer.py`
(`generated_at: datetime.now().isoformat()`, `evidence_id:
uuid.uuid4()`) and `evidence_model.py` (each evidence item's `evidence_id:
uuid.uuid4()`) both still use wall-clock time / non-seeded UUIDs. Verified
this checkpoint: stripping only the `evidence_id`/`generated_at` keys from
every evidence file produces byte-identical output across two runs (i.e.
this is the *only* source of divergence — no completeness score, evidence
type, or content field differs). Per Checkpoint 3's explicit scope
(Detection → Alerts → Case Bundling only; evidence generation is
downstream, see "Evidence object model" above and
`docs/backend_implementation_status.md`), this was **not** fixed here —
doing so would mean editing `network_layer.py`/`evidence_model.py` outside
this checkpoint's stated boundary. Recommended for a future checkpoint
that explicitly owns the Evidence stage.

Recommended improvement (not yet done): expose `--seed` as a CLI argument
instead of a hardcoded module-level call, so different seeds can be tested
without editing source.

## Frontend contract (current)

The React frontend (`frontend/src/Page/Dashboard/`) has four tabs:
**Suspected**, **Audit-Ready**, **Reference**, **Escalated**. As of this
writing the frontend makes zero API calls (no `fetch`/`axios` found
anywhere in `frontend/src/`) — it is a static UI shell, not yet wired to
`pipeline_output/`. `Suspected.jsx` (295 lines) is the only fully-built
tab; `AuditReady.jsx`, `Reference.jsx`, `Escalated.jsx` are stubs (34
lines each). **There is currently no live API contract to break** — this
significantly de-risks backend schema changes right now, but means the
eventual API design should be settled *before* the other three tabs are
built out, not after, to avoid rework.

## Testing requirements

17 tests are specified below (see `docs/backend_implementation_status.md`
for current pass/fail status). **[VERIFIED] (Checkpoint 3):** 47 automated
tests exist under `tests/` and pass under `pytest` — `test_evidence_model.py`
(17, from Checkpoint 2), `test_case_bundling.py` (17, new this checkpoint —
covers items 2, 3, 4, and 13 below), `test_ground_truth_isolation.py` (13,
new this checkpoint — covers item 17 below). Items 5, 6, 7, 9 (partially),
10, 11, 12, 14, 15, 16 remain unimplemented/unverified — they require
ground-truth-vs-live comparison (5, 6), network-layer traversal correctness
(7), or authority/escalation engines explicitly out of scope for this
checkpoint (10, 11) — see `backend_implementation_status.md` "Known
limitations". Minimum required coverage, verbatim from the corrected
architecture spec:

1. Mock data generates with all references resolving.
2. Live detector never consumes ground truth. **[VERIFIED]** —
   `test_ground_truth_isolation.py`.
3. Case bundling can merge multiple alerts into one case. **[VERIFIED]** —
   `test_case_bundling.py`.
4. One network can produce multiple alerts. **[VERIFIED, in live terms]**
   — `test_case_bundling.py` confirms one account can produce multiple live
   alerts; mapping this to ground-truth network IDs is evaluation's job,
   out of scope here (see item 5/6).
5. False positives are possible (a legitimate-labeled ground-truth case
   can still trigger a live alert).
6. False negatives are measurable (a fraud-labeled ground-truth network
   can produce zero live alerts).
7. Network traversal discovers transactions NOT present in the original
   case bundle.
8. Required/available/missing evidence calculation is correct.
9. Completeness is derived from actual evidence, never a random number.
10. Missing critical evidence can trigger escalation.
11. Junior/senior routing behaves per policy.
12. Contradiction evidence is based only on gathered evidence (no
    fabrication).
13. Account-swap device/geo/transaction linkage holds (same account_id
    connects all four). **[VERIFIED, causal-chain regression guard]** —
    `test_case_bundling.py::test_account_swap_causal_linkage_intact_on_real_data`.
14. Smurfing graph reconstruction is correct.
15. Money-mule transaction timeline is correct.
16. Reverse-smurfing graph reconstruction is correct.
17. Ground truth is never imported by any live pipeline module (should be
    an automated `grep`/`ast`-based test, not just a manual check).

**[VERIFIED] (Checkpoint 6, this session):** `tests/test_checkpoint6.py`
adds 38 tests covering the Checkpoint 6 acceptance criteria — NBA
determinism, case-state transition validity (including that invalid
transitions raise `InvalidTransitionError` and that `CLOSED` is
terminal), audit-trail append-only behavior, junior/senior action
authorization (including that a junior cannot execute a senior-only
action), mandatory `override_reason` on a changed recommendation, and
case-memory history preservation across updates. Full regression: **153
tests pass, 0 failed** (`python -m pytest tests/ -q`, run this session).