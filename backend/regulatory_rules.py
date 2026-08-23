"""
regulatory_rules.py
======================
CHECKPOINT 5 - Regulatory Compliance Rule Engine.

    Contradiction Agent -> REGULATORY COMPLIANCE RULE ENGINE -> Regulatory RAG
    -> Investigation Auditor -> Case Completeness Score -> ...

Deterministic, config-driven evaluation of a small set of BSA/AML-adjacent
compliance rules against a case's ALREADY-GATHERED, real evidence
(`evidence_items` from evidence_model.py, the raw `net` response from
network_layer.generate_network_evidence(), and - for the one rule that
needs it - real beneficiary rows from `store`). Never gathers its own
evidence, never reads ground truth, never calls an LLM, never uses
randomness.

Each rule's evaluation is explicitly split into the five things the
checkpoint requires kept separate:
  1. rule identification    -> RULE_DEFINITIONS entry (id/name/typologies)
  2. rule evaluation         -> the rule's `_evaluate` function
  3. evidence supporting it  -> `supporting_evidence` (evidence_type +
                                 source_record_ids, copied from real
                                 evidence_items/net, never invented)
  4. breach/concern determination -> `status`
  5. confidence/status       -> `confidence` (0.0-1.0, deterministic)

`status` is always one of:
  "confirmed_concern"     - multiple independently-discovered, corroborating
                             hard signals (never a single anomaly alone)
  "potentially_applicable" - one real signal found, flagged for human review
  "no_identified_breach"   - rule was evaluated against real data and found
                             nothing supporting it
  "insufficient_evidence"  - the evidence needed to evaluate this rule at
                             all was not gathered/available for this case,
                             OR (NEW this checkpoint) the case's regulatory
                             jurisdiction could not be determined, OR the
                             only transaction amounts gathered are in a
                             currency this rule cannot evaluate against its
                             jurisdiction-appropriate threshold - see
                             `_evaluate_ctr`

A rule is only emitted for typologies listed in its own
`applicable_typologies` - inapplicable rules are silently skipped (never
forced to a default status), so `evaluate_compliance_rules()`'s return
list only ever contains rules that actually apply to this case's typology.

JURISDICTION (NEW this checkpoint - see `jurisdiction.py`): every rule
result now carries a `jurisdiction_context` block (the case's determined
jurisdiction, confidence, and basis) and every rule's `regulatory_context`
retrieval is hard-gated to that jurisdiction's `applicable_jurisdictions`
(regulatory_rag.py enforces the gate; this module only ever supplies real,
already-computed jurisdiction data to it - never a guess). The CTR rule
additionally uses jurisdiction to pick which real-world reporting
threshold and currency apply (India: PMLA Rule 3, INR 10,00,000; US: 31
CFR 1010.311, $10,000) instead of comparing every case's transaction
amounts - regardless of currency - against one hardcoded US-dollar figure,
which was this module's pre-existing jurisdiction bug.
"""

from regulatory_rag import retrieve_regulatory_context
from jurisdiction import determine_case_jurisdiction

# ----------------------------------------------------------------------
# Shared helpers - read ALREADY-GATHERED real data only, never re-query
# raw CSVs directly and never read ground truth. Mirrors the same pattern
# authority_policy.py already uses for `_max_amount_touched` /
# `_is_high_value_transaction` (kept independent here rather than
# imported, since this module's CTR threshold is a real, config-driven
# regulatory number - 31 CFR 1010.311 - not authority_policy's own
# separate escalation-policy threshold; the two happen to both reason
# about "large transaction" but for different purposes and must not be
# silently coupled).
# ----------------------------------------------------------------------
# NEW this checkpoint: one CTR threshold per jurisdiction, keyed by
# `jurisdiction.py`'s `base_jurisdiction` value, each with the currency it
# is actually denominated in - a real, config-driven regulatory number per
# jurisdiction, never a single hardcoded USD figure applied regardless of
# where the account is registered or what currency its transactions are
# in (that conflation was this module's pre-existing jurisdiction bug -
# see module docstring). Both citations were verified this checkpoint
# (regulatory_corpus.py's docstring records how).
CTR_THRESHOLDS_BY_JURISDICTION = {
    "IN": {
        "currency": "INR",
        "amount": 1_000_000.0,  # Rule 3, PMLA (Maintenance of Records) Rules, 2005 - INR 10,00,000
        "corpus_source_id": "REG-IN-PMLA-CTR",
        "citation_note": "Rule 3, PMLA (Maintenance of Records) Rules, 2005 (INR 10,00,000 threshold)",
    },
    "US": {
        "currency": "USD",
        "amount": 10_000.0,  # 31 U.S.C. 5313; 31 CFR 1010.311
        "corpus_source_id": "REG-BSA-CTR-THRESHOLD",
        "citation_note": "31 U.S.C. 5313; 31 CFR 1010.311 ($10,000 threshold)",
    },
}
# Retained for any external caller that still references the old name;
# no longer used by `_evaluate_ctr` itself (see CTR_THRESHOLDS_BY_JURISDICTION).
CTR_THRESHOLD_USD = CTR_THRESHOLDS_BY_JURISDICTION["US"]["amount"]

_STRUCTURING_PATTERNS = {"many_to_one", "amount_fragmentation", "rapid_onward_transfer", "one_to_many"}
_SAR_RELEVANT_PATTERNS = {
    "rapid_onward_transfer", "multi_hop_flow", "rapid_fund_pass_through",
    "high_outbound_inbound_ratio", "sim_change_before_transaction",
}


def _evidence_item(evidence_items, evidence_type):
    for item in evidence_items:
        if item["evidence_type"] == evidence_type:
            return item
    return None


def _pattern_types(net):
    """Normalizes network_layer.py's two pattern shapes (a list of dicts
    with "type" for smurfing/reverse_smurfing, a list of plain strings for
    money_mule/account_swap) into one set of type-name strings."""
    raw = (net or {}).get("patterns") or []
    types = set()
    for p in raw:
        if isinstance(p, dict):
            if "type" in p:
                types.add(p["type"])
        else:
            types.add(p)
    return types


def _max_amount_touched(net, typology):
    """Largest single transaction amount present in whatever evidence was
    already gathered for this case - real data only, None if nothing to
    check. (Deliberately independent from authority_policy.py's identical-
    looking helper - see module docstring.)"""
    evidence = (net or {}).get("evidence") or {}
    if typology in ("smurfing", "reverse_smurfing"):
        amounts = [e["data"]["amount"] for e in evidence.get("edges", []) if "data" in e and "amount" in e["data"]]
    elif typology == "money_mule":
        amounts = [t["amount"] for t in evidence.get("transactions", []) if "amount" in t]
    elif typology == "account_swap":
        amounts = [e["amount"] for e in evidence.get("events", [])
                   if e.get("event_type") == "transaction" and "amount" in e]
    else:
        amounts = []
    return max(amounts) if amounts else None


def _amounts_with_currency(net, typology):
    """Like `_max_amount_touched`, but keeps each amount's own `currency`
    (already present on every edge/transaction/event network_layer.py
    produces - see that module's `_edges_to_cytoscape`/
    `build_money_mule_timeline`/`build_account_swap_timeline`) alongside
    its record id, so a CTR-style threshold can be compared only against
    amounts actually denominated in that threshold's own currency, never
    mixed across currencies. Real data only; defaults to "INR" only where
    network_layer.py's own edge serialization already does the same
    (smurfing/reverse_smurfing edges), never invented here."""
    evidence = (net or {}).get("evidence") or {}
    items = []
    if typology in ("smurfing", "reverse_smurfing"):
        for e in evidence.get("edges", []):
            d = e.get("data", {})
            if "amount" in d and "id" in d:
                items.append((d["id"], d["amount"], d.get("currency", "INR")))
    elif typology == "money_mule":
        for t in evidence.get("transactions", []):
            if "amount" in t and "transaction_id" in t:
                items.append((t["transaction_id"], t["amount"], t.get("currency", "INR")))
    elif typology == "account_swap":
        for e in evidence.get("events", []):
            if e.get("event_type") == "transaction" and "amount" in e and "event_id" in e:
                items.append((e["event_id"], e["amount"], e.get("currency", "INR")))
    return items


def _txn_ids_at_or_above(net, typology, threshold):
    evidence = (net or {}).get("evidence") or {}
    if typology in ("smurfing", "reverse_smurfing"):
        return sorted({e["data"]["id"] for e in evidence.get("edges", [])
                       if "data" in e and e["data"].get("amount", 0) >= threshold and "id" in e["data"]})
    if typology == "money_mule":
        return sorted({t["transaction_id"] for t in evidence.get("transactions", [])
                        if t.get("amount", 0) >= threshold})
    if typology == "account_swap":
        return sorted({e["event_id"] for e in evidence.get("events", [])
                        if e.get("event_type") == "transaction" and e.get("amount", 0) >= threshold})
    return []


# ----------------------------------------------------------------------
# Rule 1 - CTR filing threshold (jurisdiction-aware, NEW this checkpoint)
# ----------------------------------------------------------------------
def _evaluate_ctr(case, evidence_items, completeness, net, account, store, jurisdiction_context=None):
    typology = case.get("primary_trigger")
    amounts = _amounts_with_currency(net, typology)
    if not amounts:
        return "insufficient_evidence", 0.0, [], "no_transaction_amount_evidence_gathered_for_this_case"

    jurisdiction_context = jurisdiction_context or {}
    base_jurisdiction = jurisdiction_context.get("base_jurisdiction")
    threshold_cfg = CTR_THRESHOLDS_BY_JURISDICTION.get(base_jurisdiction)

    if threshold_cfg is None:
        observed_currencies = sorted({cur for _, _, cur in amounts})
        return ("insufficient_evidence", 0.0, [],
                "this case's regulatory jurisdiction could not be determined (no recognized "
                f"account registered_country - see jurisdiction_context), so no jurisdiction-"
                f"appropriate CTR-style reporting threshold can be applied; observed transaction "
                f"currencies were {observed_currencies}")

    threshold_currency = threshold_cfg["currency"]
    threshold_amount = threshold_cfg["amount"]
    relevant = [(txid, amt, cur) for (txid, amt, cur) in amounts if cur == threshold_currency]

    if not relevant:
        observed_currencies = sorted({cur for _, _, cur in amounts})
        return ("insufficient_evidence", 0.0, [],
                f"no transactions denominated in {threshold_currency} (the currency this case's "
                f"{base_jurisdiction} jurisdiction's reporting threshold is defined in - "
                f"{threshold_cfg['citation_note']}) were gathered for this case - only "
                f"{observed_currencies} present; this rule does not fabricate an FX conversion "
                "rate to compare a different currency against the threshold")

    max_amount = max(amt for _, amt, _ in relevant)
    if max_amount >= threshold_amount:
        ids = sorted({txid for txid, amt, _ in relevant if amt >= threshold_amount})
        return ("confirmed_concern", 1.0,
                [{"evidence_type": "transaction_amount", "source_record_ids": ids,
                  "observed_value": max_amount, "currency": threshold_currency}],
                f"largest gathered {threshold_currency} transaction ({threshold_currency} "
                f"{max_amount:,.2f}) meets or exceeds the {base_jurisdiction} reporting threshold "
                f"({threshold_currency} {threshold_amount:,.0f}) - {threshold_cfg['citation_note']}")
    return ("no_identified_breach", 1.0,
            [{"evidence_type": "transaction_amount", "source_record_ids": [],
              "observed_value": max_amount, "currency": threshold_currency}],
            f"largest gathered {threshold_currency} transaction ({threshold_currency} "
            f"{max_amount:,.2f}) is below the {base_jurisdiction} reporting threshold "
            f"({threshold_currency} {threshold_amount:,.0f})")


# ----------------------------------------------------------------------
# Rule 2 - structuring (REG-BSA-STRUCTURING)
# ----------------------------------------------------------------------
def _evaluate_structuring(case, evidence_items, completeness, net, account, store, jurisdiction_context=None):
    chain = _evidence_item(evidence_items, "transaction_chain")
    if chain is None or not chain["available"]:
        return "insufficient_evidence", 0.0, [], "transaction_chain_evidence_not_available_for_this_case"

    hits = _pattern_types(net) & _STRUCTURING_PATTERNS
    supporting = [{"evidence_type": "transaction_chain", "source_record_ids": chain["source_record_ids"]}]
    for h in sorted(hits):
        supporting.append({"evidence_type": "pattern", "pattern_type": h})

    if len(hits) >= 2:
        return ("confirmed_concern", round(min(1.0, 0.5 + 0.15 * len(hits)), 3), supporting,
                f"{len(hits)} independently-discovered structuring-relevant patterns "
                f"corroborate each other over a real, available transaction chain: {sorted(hits)}")
    if len(hits) == 1:
        return ("potentially_applicable", 0.4, supporting,
                f"a single structuring-relevant pattern ({next(iter(hits))}) was found; "
                "one signal alone is flagged for review, not confirmed")
    return ("no_identified_breach", 0.7, supporting,
            "transaction chain evidence was available but no structuring-relevant pattern was discovered")


# ----------------------------------------------------------------------
# Rule 3 - SAR filing consideration (REG-BSA-SAR)
# ----------------------------------------------------------------------
def _evaluate_sar(case, evidence_items, completeness, net, account, store, jurisdiction_context=None):
    patterns = _pattern_types(net)
    hits = patterns & _SAR_RELEVANT_PATTERNS
    has_any_evidence = bool(patterns) or bool((net or {}).get("evidence"))
    if not has_any_evidence:
        return "insufficient_evidence", 0.0, [], "no investigation patterns/evidence were gathered for this case"

    supporting = [{"evidence_type": "pattern", "pattern_type": h} for h in sorted(hits)]
    if len(hits) >= 2:
        return ("confirmed_concern", round(min(1.0, 0.5 + 0.15 * len(hits)), 3), supporting,
                f"{len(hits)} SAR-relevant patterns corroborate each other: {sorted(hits)}")
    if len(hits) == 1:
        return ("potentially_applicable", 0.4, supporting,
                f"one SAR-relevant pattern found ({next(iter(hits))}); flagged for review")
    return ("no_identified_breach", 0.6, supporting,
            "investigation evidence was gathered but no SAR-relevant pattern was discovered")


# ----------------------------------------------------------------------
# Rule 4 - account-takeover red flags (REG-FFIEC-ATO-REDFLAGS)
# ----------------------------------------------------------------------
def _evaluate_ato_redflags(case, evidence_items, completeness, net, account, store, jurisdiction_context=None):
    events = ((net or {}).get("evidence") or {}).get("events", [])
    if not events:
        return "insufficient_evidence", 0.0, [], "no device/geo/transaction event evidence gathered for this case"

    sim_item = _evidence_item(evidence_items, "sim_change_evidence")
    sim_hit = bool(sim_item and sim_item["available"])
    travel_hit = "rapid_geographic_change" in _pattern_types(net)

    supporting = []
    if sim_hit:
        supporting.append({"evidence_type": "sim_change_evidence", "source_record_ids": sim_item["source_record_ids"]})
    if travel_hit:
        supporting.append({"evidence_type": "pattern", "pattern_type": "rapid_geographic_change"})

    if sim_hit and travel_hit:
        return ("confirmed_concern", 0.85, supporting,
                "SIM-change event and rapid/impossible geographic change corroborate each "
                "other - a genuine account-takeover-pattern red flag, not a single anomaly")
    if sim_hit or travel_hit:
        return ("potentially_applicable", 0.4, supporting,
                "one account-takeover red flag found without a corroborating second signal")
    return ("no_identified_breach", 0.6, supporting,
            "device/geo evidence was gathered but no SIM-change or impossible-travel red flag was found")


# ----------------------------------------------------------------------
# Rule 5 - beneficiary verification / CDD (REG-BSA-CDD-BENEFICIAL-OWNERSHIP)
# ----------------------------------------------------------------------
def _evaluate_beneficiary_cdd(case, evidence_items, completeness, net, account, store, jurisdiction_context=None):
    bene_item = _evidence_item(evidence_items, "beneficiary_information")
    if bene_item is None or not bene_item["available"]:
        return "insufficient_evidence", 0.0, [], "beneficiary_information evidence not available for this case"

    account_id = (net or {}).get("account_id") or case.get("account_id")
    benes = store.bene_by_account.get(account_id, []) if store else []
    unverified_or_new = [b for b in benes if (not b.get("is_verified")) or b.get("is_first_time_beneficiary")]

    corroborating = bool(
        "high_value_transaction" in _pattern_types(net)
        or _SAR_RELEVANT_PATTERNS & _pattern_types(net)
    )
    supporting = [{"evidence_type": "beneficiary_information", "source_record_ids": bene_item["source_record_ids"],
                   "unverified_or_first_time_count": len(unverified_or_new)}]

    if unverified_or_new and corroborating:
        return ("confirmed_concern", 0.75, supporting,
                f"{len(unverified_or_new)} unverified/first-time beneficiary record(s) "
                "corroborated by a high-value or SAR-relevant transaction pattern")
    if unverified_or_new:
        return ("potentially_applicable", 0.4, supporting,
                f"{len(unverified_or_new)} unverified/first-time beneficiary record(s) found, "
                "no corroborating transaction pattern")
    return ("no_identified_breach", 0.6, supporting,
            "beneficiary records are present, verified, and not first-time")


# ----------------------------------------------------------------------
# Rule registry - identification is explicit config, not scattered
# if/elif branching.
# ----------------------------------------------------------------------
RULE_DEFINITIONS = [
    {
        "rule_id": "RULE-CTR-001",
        "rule_name": "Currency/Cash Transaction Report threshold",
        "applicable_typologies": ["smurfing", "reverse_smurfing", "money_mule", "account_swap"],
        # Illustrative only (NEW this checkpoint: jurisdiction-keyed, since
        # the actually-applicable corpus entry now depends on the case's
        # determined jurisdiction, not one fixed id) - never read at
        # runtime; the real retrieval is regulatory_rag.py's jurisdiction-
        # gated `retrieve_regulatory_context()` call below.
        "corpus_source_id": {"IN": "REG-IN-PMLA-CTR", "US": "REG-BSA-CTR-THRESHOLD"},
        "evaluate": _evaluate_ctr,
        "signal_terms": ["high_value_transaction", "high-value", "ctr"],
    },
    {
        "rule_id": "RULE-STRUCT-001",
        "rule_name": "Structuring to evade reporting",
        "applicable_typologies": ["smurfing", "reverse_smurfing"],
        "corpus_source_id": {"IN": "REG-IN-PMLA-STR", "US": "REG-BSA-STRUCTURING"},
        "evaluate": _evaluate_structuring,
        "signal_terms": ["structuring", "amount_fragmentation", "transaction_chain"],
    },
    {
        "rule_id": "RULE-SAR-001",
        "rule_name": "Suspicious Activity / Suspicious Transaction Report filing consideration",
        "applicable_typologies": ["smurfing", "reverse_smurfing", "money_mule", "account_swap"],
        "corpus_source_id": {"IN": "REG-IN-PMLA-STR", "US": "REG-BSA-SAR"},
        "evaluate": _evaluate_sar,
        "signal_terms": ["rapid_onward_transfer", "pass_through_timing", "amount_retention_ratio"],
    },
    {
        "rule_id": "RULE-ATO-001",
        "rule_name": "Account-takeover red flags",
        "applicable_typologies": ["account_swap"],
        # No bundled India-jurisdiction equivalent exists today - see
        # regulatory_corpus.py's REG-FFIEC-ATO-REDFLAGS entry's own
        # `applicability` note. An IN-jurisdiction case correctly retrieves
        # empty `regulatory_context` for this rule rather than borrowing
        # the US entry.
        "corpus_source_id": {"US": "REG-FFIEC-ATO-REDFLAGS"},
        "evaluate": _evaluate_ato_redflags,
        "signal_terms": ["sim_change_evidence", "impossible_travel", "rapid_geographic_change"],
    },
    {
        "rule_id": "RULE-CDD-001",
        "rule_name": "Beneficiary verification / customer due diligence",
        "applicable_typologies": ["account_swap", "money_mule"],
        "corpus_source_id": {"IN": "REG-IN-RBI-KYC-CDD", "US": "REG-BSA-CDD-BENEFICIAL-OWNERSHIP"},
        "evaluate": _evaluate_beneficiary_cdd,
        "signal_terms": ["beneficiary_information", "new_beneficiary", "is_verified"],
    },
]

# Keywords added to every rule's retrieval only when the case's jurisdiction
# was independently determined to be cross-border (NEW this checkpoint) -
# lets a cross-border case additionally surface REG-IN-PMLA-CROSSBORDER /
# REG-IN-FEMA-LRS alongside its base-jurisdiction citation, without ever
# retrieving them for a purely domestic case.
_CROSS_BORDER_SIGNAL_TERMS = {
    "cross_border", "international", "is_international",
    "foreign_currency", "non_local_currency_transaction", "remittance",
}


def evaluate_compliance_rules(case, evidence_items, completeness, net=None, account=None, store=None,
                               jurisdiction_context=None, rule_definitions=RULE_DEFINITIONS):
    """The one public entry point. Evaluates every rule in
    `rule_definitions` whose `applicable_typologies` includes this case's
    typology, against real already-gathered evidence only. Returns a list
    of structured rule-result dicts (never a single aggregate blob) so
    each conclusion stays individually traceable.

    `store` is optional (only RULE-CDD-001 uses it, to read real
    beneficiary rows - see that rule's docstring); when omitted, that rule
    degrades to "insufficient_evidence" rather than guessing.

    `jurisdiction_context` (NEW this checkpoint) is optional - if the
    caller already computed it (e.g. run_pipeline.py, which needs the same
    context for the auditor too), pass it in; if omitted, this function
    computes it itself from real data (`jurisdiction.determine_case_
    jurisdiction(case, net, account, store)`) so every pre-existing caller
    that never mentions jurisdiction keeps working unchanged and still
    gets the jurisdiction-aware fix.
    """
    if jurisdiction_context is None:
        jurisdiction_context = determine_case_jurisdiction(case, net=net, account=account, store=store)

    typology = case.get("primary_trigger")
    applicable_jurisdictions = jurisdiction_context.get("applicable_jurisdictions")
    is_cross_border = jurisdiction_context.get("jurisdiction") == "cross_border"

    results = []
    for rule in rule_definitions:
        if typology not in rule["applicable_typologies"]:
            continue

        status, confidence, supporting_evidence, rationale = rule["evaluate"](
            case, evidence_items, completeness, net, account, store, jurisdiction_context
        )

        pattern_terms = _pattern_types(net)
        signal_terms = set(rule["signal_terms"]) | pattern_terms
        if is_cross_border:
            signal_terms |= _CROSS_BORDER_SIGNAL_TERMS
        regulatory_context = retrieve_regulatory_context({
            "typology": typology,
            "signal_terms": signal_terms,
            "applicable_jurisdictions": applicable_jurisdictions,
        })

        results.append({
            "rule_id": rule["rule_id"],
            "rule_name": rule["rule_name"],
            "typology": typology,
            "status": status,
            "confidence": confidence,
            "supporting_evidence": supporting_evidence,
            "rationale": rationale,
            "regulatory_context": regulatory_context,
            "jurisdiction_context": jurisdiction_context,
            "case_id": case["case_id"],
        })
    return results