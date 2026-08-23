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
   transaction_id, typology, triggering_rules, alert_score, ...}`. One
   alert is not one case and is not one fraud network.
4. **Case bundling** — `bundle_alerts_into_cases()`. Groups alerts that
   plausibly describe the same underlying incident. Must record *why*
   (see "Bundle reason" below) — not yet implemented, see status doc.
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
   "Evidence completeness model" below). **Not implemented at all today**
   — this is the single largest gap between this document and the code.
8. **Investigator authority** — a policy decision (junior/senior),
   computed from evidence completeness + typology risk + confidence +
   contradiction state — never hardcoded, never randomly assigned, never
   decided by Detection/Case-Intake (see "Investigator authority model"
   below). **Not implemented at all today.**
9. **Action / escalation** — clear / monitor / block / escalate, driven by
   authority + policy. **Not implemented at all today.**

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

**Current status:** `bundle_alerts_into_cases()` bundles purely on
`account_id` + a fixed 24h window. This happens to produce
`same_primary_account` + `within_case_window` reasoning implicitly, but
never records it as structured output, and never considers
typology-correlation or shared-transaction-chain as independent
correlation signals (an account could plausibly have two *unrelated*
alerts land in the same 24h window that shouldn't be merged — the current
bundler cannot express that distinction).

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
byte-identical output. Live detection/case-bundling logic contains zero
calls to `random` — given identical input data, output is always
identical. Recommended improvement (not yet done): expose `--seed` as a
CLI argument instead of a hardcoded module-level call, so different seeds
can be tested without editing source.

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

17 tests are specified (see `docs/backend_implementation_status.md` for
current pass/fail status — as of this writing, **zero automated tests
exist in this repository**, no `tests/` directory, nothing runs under
pytest). Minimum required coverage, verbatim from the corrected
architecture spec:

1. Mock data generates with all references resolving.
2. Live detector never consumes ground truth.
3. Case bundling can merge multiple alerts into one case.
4. One network can produce multiple alerts.
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
    connects all four).
14. Smurfing graph reconstruction is correct.
15. Money-mule transaction timeline is correct.
16. Reverse-smurfing graph reconstruction is correct.
17. Ground truth is never imported by any live pipeline module (should be
    an automated `grep`/`ast`-based test, not just a manual check).