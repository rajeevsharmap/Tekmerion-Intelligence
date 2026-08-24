"""
sar_report.py
================
CHECKPOINT 7 - Suspicious Activity Report (SAR) generation.

    ... -> Investigator Action (FILE_SAR, authorized) -> Case Memory
    -> SAR REPORT

Deterministic, template-based builder for a structured SAR record from
already-computed upstream output only: `case`, `jurisdiction_context`
(jurisdiction.py, Checkpoint 5), `regulatory_findings` (regulatory_
rules.py, Checkpoint 5, including each finding's own `regulatory_context`
citations from regulatory_rag.py), `evidence_items` (evidence_model.py,
Checkpoint 2), `auditor_result` (investigation_auditor.py, Checkpoint 5),
and `investigator_action` (investigator_action.py, Checkpoint 6 - the
authorized FILE_SAR record). This module GATHERS NOTHING NEW: no new
evidence, no LLM call, no randomness. IDs are deterministic content
hashes, same style as every other Checkpoint 4-6 module.

### Why this module exists now (not invented) ###
`case_memory.py`'s own docstring (Checkpoint 6) already commits to
carrying "every field a future SAR generator will need" and explicitly
defers building the generator itself. `next_best_action.py` already
emits `FILE_SAR` as a real action in its vocabulary, gated to `senior`
authority in `ACTION_MINIMUM_AUTHORITY`. This module is the natural next
stage the existing architecture already pointed at - not a new product
feature.

### Scope (explicitly bounded, same deferral discipline as Checkpoint 6) ###
Produces a STRUCTURED SAR RECORD (a JSON-serializable dict) only.
`suspicious_activity_summary` is template-assembled strictly from real,
already-established fields (each confirmed finding's `rule_name` +
`rationale`) - never freely generated, never paraphrased by an LLM, so it
can never say something the upstream evidence/regulatory layer didn't
already establish. This module does NOT produce a PDF and does NOT
password-protect anything - both remain explicitly out of scope, exactly
as the prior checkpoint's own instructions deferred them ("do NOT begin
... password-protected SAR PDFs").

### Preconditions - independently re-validated, never trusted from the caller ###
`build_sar_report()` is meant to be called only after Checkpoint 6 has
already authorized and executed a `FILE_SAR` investigator action, but it
never assumes the caller got that right - it re-checks every
precondition itself (defense in depth, same posture as
`investigator_action.authorize_action` never trusting a caller-supplied
role):

  1. At least one regulatory finding at status `"confirmed_concern"`
     exists. If none, returns a BLOCKED record
     (`status = "BLOCKED_INSUFFICIENT_REGULATORY_BASIS"`) - a SAR is
     never filed on a fabricated or absent regulatory basis (mirrors
     `regulatory_rules.py`'s own "never a single anomaly alone" rule:
     this module doesn't relax that, it inherits it).
  2. `jurisdiction_context["jurisdiction"]` is one of `"IN"`, `"US"`,
     `"cross_border"` - never `"unknown"`. If unresolved, returns a
     BLOCKED record (`status = "BLOCKED_JURISDICTION_UNRESOLVED"`) - a
     SAR cannot be correctly routed to a filing regulator without a
     resolved jurisdiction (Rule 4/5: never guess a US filing merely
     because a case is cross-border, and never guess at all when
     jurisdiction is unknown).
  3. `investigator_action["actual_action"] == "FILE_SAR"` and
     `investigator_action["authorized"] is True`. If not, returns a
     BLOCKED record (`status = "BLOCKED_ACTION_NOT_AUTHORIZED"`) - this
     module never files a report for an action that was rejected,
     merely recommended, or different from FILE_SAR (Human review must
     not be bypassed; this is the same authorization result Checkpoint 6
     already computed, consumed here, never re-decided).

Even once all three preconditions pass, if the Investigation Auditor
still shows an unresolved CRITICAL issue at generation time, the record
is produced but flagged (`status = "DRAFT_REQUIRES_SECONDARY_REVIEW"`,
`auditor_warnings` populated) instead of `"FILED"` - a critical audit
issue is never silently dropped just because Checkpoint 6 already
authorized the underlying action (in practice `next_best_action.py`'s own
Step 2 already routes a confirmed-concern-plus-critical-issue case to
`ESCALATE_TO_SENIOR` instead of `FILE_SAR`, so this should rarely fire on
live data - it exists as a defensive check, not dead code, since a senior
override could still reach FILE_SAR on such a case per Checkpoint 6's
override mechanism).

### Filing jurisdiction / regulator (POLICY ASSUMPTION, documented) ###
Reuses jurisdiction.py's own determination, never re-derives it:
  IN / cross_border -> regulator "FIU-IND" (this dataset's base
                        jurisdiction is always India - see
                        jurisdiction.py - so a cross-border case still
                        files as IN; cross-border citations are included
                        as SUPPLEMENTARY basis only, never substituted
                        for the India filing - Rule 5).
  US                -> regulator "FinCEN".
  (unknown already handled as a precondition failure above.)

`legal_basis_citations` are pulled only from the confirmed-concern
findings' own `regulatory_context` entries whose `jurisdiction` tag
matches the filing jurisdiction (or, for cross_border, also includes a
separately-labeled `supplementary_cross_border_citations` list) - copied
verbatim (`source_id`/`citation`/`authority`), never invented. An empty
citation list is reported as a fact (`legal_basis_citations: []`), not
silently backfilled with something plausible-looking.
"""
import hashlib
import json
from datetime import datetime, timezone

REGULATOR_BY_FILING_JURISDICTION = {
    "IN": "FIU-IND",
    "US": "FinCEN",
}


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _content_hash_id(prefix, *parts):
    raw = json.dumps(parts, sort_keys=True, default=str)
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:8].upper()}"


def _blocked(case, reason_code, jurisdiction_context=None, filed_at=None):
    """A BLOCKED record still names the case, the reason, and whatever
    jurisdiction context is available - so a blocked attempt is never
    silently invisible, it is a structured, inspectable, non-fabricated
    negative result."""
    ts = filed_at or utc_now_iso()
    return {
        "sar_id": _content_hash_id("SAR", case["case_id"], reason_code, ts),
        "case_id": case["case_id"],
        "status": reason_code,
        "filing_jurisdiction": None,
        "regulator": None,
        "legal_basis_citations": [],
        "supplementary_cross_border_citations": [],
        "subject_accounts": [case.get("account_id")],
        "typology": case.get("primary_trigger"),
        "suspicious_activity_summary": None,
        "supporting_evidence_ids": [],
        "auditor_warnings": [],
        "filed_by": None,
        "filed_by_role": None,
        "filed_at": ts,
        "generated_at": ts,
    }


def build_sar_report(case, jurisdiction_context, regulatory_findings, evidence_items,
                      auditor_result, investigator_action, filed_at=None):
    """The one public entry point. Deterministic given identical inputs
    (including `filed_at`, since a SAR's own filing timestamp is real
    wall-clock data like every other Checkpoint 6 timestamped record -
    see `audit_trail.py`/`investigator_action.py` for the same pattern);
    never gathers new evidence, never calls an LLM, never uses
    randomness.

    Returns a structured SAR record (see module docstring for the
    BLOCKED/DRAFT_REQUIRES_SECONDARY_REVIEW/FILED status values).
    """
    regulatory_findings = regulatory_findings or []
    auditor_result = auditor_result or {"issues": [], "critical_issue_count": 0}
    investigator_action = investigator_action or {}
    ts = filed_at or utc_now_iso()

    # ---- Precondition 3: the action actually authorized-and-executed
    # must be FILE_SAR - this module never files for anything else. ----
    if (investigator_action.get("actual_action") != "FILE_SAR"
            or investigator_action.get("authorized") is not True):
        return _blocked(case, "BLOCKED_ACTION_NOT_AUTHORIZED", jurisdiction_context, ts)

    # ---- Precondition 1: real, confirmed regulatory basis. -------------
    confirmed = sorted(
        (f for f in regulatory_findings if f.get("status") == "confirmed_concern"),
        key=lambda f: f["rule_id"],
    )
    if not confirmed:
        return _blocked(case, "BLOCKED_INSUFFICIENT_REGULATORY_BASIS", jurisdiction_context, ts)

    # ---- Precondition 2: resolved jurisdiction. -------------------------
    jurisdiction = (jurisdiction_context or {}).get("jurisdiction")
    if jurisdiction not in ("IN", "US", "cross_border"):
        return _blocked(case, "BLOCKED_JURISDICTION_UNRESOLVED", jurisdiction_context, ts)

    filing_jurisdiction = "IN" if jurisdiction in ("IN", "cross_border") else "US"
    regulator = REGULATOR_BY_FILING_JURISDICTION[filing_jurisdiction]

    # ---- Legal basis citations: copied verbatim from real regulatory_
    # context entries already attached to each confirmed finding - never
    # invented, never paraphrased. Deduplicated by source_id, sorted for
    # determinism. ----
    def _citations(jurisdiction_tag):
        seen = {}
        for finding in confirmed:
            for entry in finding.get("regulatory_context", []) or []:
                if entry.get("jurisdiction") != jurisdiction_tag:
                    continue
                seen[entry.get("source_id")] = {
                    "source_id": entry.get("source_id"),
                    "citation": entry.get("citation"),
                    "authority": entry.get("authority"),
                    "jurisdiction": entry.get("jurisdiction"),
                    "supporting_rule_id": finding["rule_id"],
                }
        return [seen[k] for k in sorted(seen)]

    legal_basis_citations = _citations(filing_jurisdiction)
    supplementary_cross_border_citations = (
        _citations("cross_border") if jurisdiction == "cross_border" else []
    )

    # ---- Suspicious activity summary: template-assembled strictly from
    # each confirmed finding's own rule_name + rationale - never
    # freely generated. ----
    summary_lines = [
        f"{f['rule_id']} ({f['rule_name']}): {f.get('rationale', '')}".strip()
        for f in confirmed
    ]
    suspicious_activity_summary = " | ".join(summary_lines)

    # ---- Supporting evidence: real evidence_ids already gathered. ------
    supporting_evidence_ids = sorted({
        i["evidence_id"] for i in (evidence_items or []) if i.get("available")
    })

    # ---- Auditor warnings: unresolved CRITICAL issues at generation
    # time are never silently dropped, even though Checkpoint 6 already
    # authorized this action (see module docstring). ----
    auditor_warnings = [
        i for i in auditor_result.get("issues", []) if i.get("severity") == "critical"
    ]
    status = "DRAFT_REQUIRES_SECONDARY_REVIEW" if auditor_warnings else "FILED"

    sar_id = _content_hash_id(
        "SAR", case["case_id"], filing_jurisdiction,
        tuple(f["rule_id"] for f in confirmed),
        investigator_action.get("investigator_id"), ts,
    )

    return {
        "sar_id": sar_id,
        "case_id": case["case_id"],
        "status": status,
        "filing_jurisdiction": filing_jurisdiction,
        "regulator": regulator,
        "legal_basis_citations": legal_basis_citations,
        "supplementary_cross_border_citations": supplementary_cross_border_citations,
        "subject_accounts": [case.get("account_id")],
        "typology": case.get("primary_trigger"),
        "suspicious_activity_summary": suspicious_activity_summary,
        "supporting_evidence_ids": supporting_evidence_ids,
        "auditor_warnings": auditor_warnings,
        "filed_by": investigator_action.get("investigator_id"),
        "filed_by_role": investigator_action.get("investigator_role"),
        "filed_at": ts,
        "generated_at": ts,
    }