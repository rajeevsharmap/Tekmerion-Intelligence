"""
jurisdiction.py
==================
CHECKPOINT 5 - Jurisdiction determination.

    Case + Evidence -> JURISDICTION DETERMINATION -> Regulatory RAG /
    Regulatory Compliance Rule Engine -> Investigation Auditor -> ...

The mock dataset is India-based (every account's `registered_country` is
"India") with a small number of genuinely international/cross-border
transactions and geo events. `regulatory_corpus.py` bundles reference
material for more than one jurisdiction (US and India today). Nothing
downstream of this module is allowed to attach a jurisdiction's citation
to a case unless THIS module said that jurisdiction applies - a foreign
counterpart, foreign geo event, or foreign currency transaction touching
an India-registered account does NOT, by itself, make US law apply to
that account; it makes the case CROSS-BORDER, which is its own tagged
state with its own (India-side, FEMA/LRS) reference material - see
`regulatory_corpus.py`.

Determinism: reads only already-available real fields already present in
the dataset (`accounts.registered_country`, `transactions.is_international`
/`transactions.currency`, `geo_events.registered_country_match`) - never
guesses, never calls an LLM, never uses randomness. If the account's
registered_country is missing or unrecognized, jurisdiction is honestly
reported as "unknown" rather than defaulted to any particular country.

Output contract (`determine_case_jurisdiction`):
{
    "jurisdiction":            "IN" | "US" | "cross_border" | "unknown"
                                 (the single label callers display)
    "base_jurisdiction":       "IN" | "US" | None
                                 (from registered_country alone, before
                                 considering cross-border indicators)
    "applicable_jurisdictions": list of corpus `jurisdiction` values that
                                 may legitimately be retrieved for this
                                 case - e.g. ["IN", "cross_border"] - the
                                 hard filter regulatory_rag.py enforces
    "confidence":               "high" | "medium" | "low"
    "registered_country":       raw value from accounts.csv, or None
    "cross_border_indicators":  [...structured, real evidence...]
    "basis":                    [...structured explanation of how each
                                 signal was used...] - so an auditor can
                                 see WHY this jurisdiction was selected
}
"""

_COUNTRY_TO_BASE_JURISDICTION = {
    "india": "IN",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "us": "US",
}

_EXPECTED_CURRENCY_FOR_JURISDICTION = {"IN": "INR", "US": "USD"}


def _map_registered_country(registered_country):
    if not registered_country:
        return None
    return _COUNTRY_TO_BASE_JURISDICTION.get(registered_country.strip().lower())


def _account_transactions(store, account_id):
    if not store or not account_id:
        return []
    outbound = getattr(store, "outbound_by_account", {}).get(account_id, [])
    inbound = getattr(store, "inbound_by_account", {}).get(account_id, [])
    return list(outbound) + list(inbound)


def determine_case_jurisdiction(case, net=None, account=None, store=None):
    """The one public entry point. `account` is preferred when the caller
    already has it (avoids a redundant lookup); if omitted, resolved from
    `store.accounts_by_id` using the case's/net's account_id - real data
    only, same pattern regulatory_rules.py already uses for beneficiaries.
    """
    account_id = (net or {}).get("account_id") or case.get("account_id")
    if account is None and store is not None and account_id:
        account = getattr(store, "accounts_by_id", {}).get(account_id)

    registered_country = (account or {}).get("registered_country")
    base_jurisdiction = _map_registered_country(registered_country)

    basis = [{
        "signal": "account_registered_country",
        "value": registered_country,
        "mapped_to": base_jurisdiction,
    }]

    cross_border_indicators = []
    txns = _account_transactions(store, account_id)

    intl_txn_ids = sorted({
        t["transaction_id"] for t in txns
        if t.get("is_international") and t.get("transaction_id")
    })
    if intl_txn_ids:
        cross_border_indicators.append({
            "signal": "is_international_transaction",
            "transaction_ids": intl_txn_ids,
            "count": len(intl_txn_ids),
        })

    expected_currency = _EXPECTED_CURRENCY_FOR_JURISDICTION.get(base_jurisdiction)
    foreign_currency_txn_ids = []
    if expected_currency:
        foreign_currency_txn_ids = sorted({
            t["transaction_id"] for t in txns
            if t.get("currency") and t["currency"] != expected_currency and t.get("transaction_id")
        })
        if foreign_currency_txn_ids:
            cross_border_indicators.append({
                "signal": "non_local_currency_transaction",
                "transaction_ids": foreign_currency_txn_ids,
                "count": len(foreign_currency_txn_ids),
                "expected_currency": expected_currency,
            })

    geo_events = getattr(store, "geo_by_account", {}).get(account_id, []) if store else []
    foreign_geo = [g for g in geo_events if g.get("registered_country_match") is False]
    if foreign_geo:
        cross_border_indicators.append({
            "signal": "geo_country_mismatch",
            "geo_event_ids": sorted({g["geo_event_id"] for g in foreign_geo if g.get("geo_event_id")}),
            "count": len(foreign_geo),
        })

    is_cross_border = bool(cross_border_indicators)

    if base_jurisdiction is None:
        jurisdiction = "unknown"
        confidence = "low"
        applicable_jurisdictions = ["unknown"]
    elif is_cross_border:
        jurisdiction = "cross_border"
        confidence = "high" if (intl_txn_ids or foreign_currency_txn_ids) else "medium"
        applicable_jurisdictions = [base_jurisdiction, "cross_border"]
    else:
        jurisdiction = base_jurisdiction
        confidence = "high"
        applicable_jurisdictions = [base_jurisdiction]

    return {
        "case_id": case.get("case_id"),
        "jurisdiction": jurisdiction,
        "base_jurisdiction": base_jurisdiction,
        "applicable_jurisdictions": applicable_jurisdictions,
        "confidence": confidence,
        "registered_country": registered_country,
        "cross_border_indicators": cross_border_indicators,
        "basis": basis,
    }