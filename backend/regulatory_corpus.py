"""
regulatory_corpus.py
=======================
CHECKPOINT 5 - Regulatory RAG's underlying source material.

This is a small, STATIC, explicitly-labeled BUNDLED REFERENCE CORPUS of
well-known, public regulatory concepts relevant to the four typologies
this project detects (structuring, mule/pass-through activity, account-
takeover red flags, CTR/SAR/STR filing duties, beneficiary/CDD checks).
This module is NOT a live regulatory feed, NOT legal advice, and NOT a
substitute for a licensed regulatory content provider (Thomson Reuters,
LexisNexis, a bank's own compliance corpus, etc.) in a real deployment.

JURISDICTION (updated this checkpoint): the mock dataset is India-based
(every account's `registered_country` is "India"), with a small number of
genuinely international/cross-border transactions. This corpus therefore
covers THREE jurisdiction tags, never conflated with one another:

  "IN"           - India: Prevention of Money Laundering Act, 2002 (PMLA)
                   and its Rules, RBI Master Directions. Primary
                   jurisdiction for this dataset.
  "US"           - United States: Bank Secrecy Act / FFIEC. Retained from
                   the prior session's work; still real, still correct,
                   just no longer assumed to apply to an India-registered
                   account merely because a transaction happened to be
                   flagged international.
  "cross_border" - Reference material that specifically concerns funds
                   leaving/entering a jurisdiction (e.g. India's FEMA/
                   Liberalised Remittance Scheme) - only retrieved when
                   `jurisdiction.py` has independently found real cross-
                   border evidence (an is_international transaction, a
                   non-local-currency transaction, or a geo event whose
                   country doesn't match the account's registered
                   country), never merely because the entry exists.

`regulatory_rag.py` hard-filters retrieval to a case's
`applicable_jurisdictions` (from `jurisdiction.py`) BEFORE any keyword
scoring happens - an entry tagged "US" is structurally unreachable for an
India-jurisdiction case, and vice versa, regardless of keyword overlap.

CITATION VERIFICATION (this checkpoint): the India entries below were
checked this session against multiple independent sources reachable via
web search, including two primarily government/quasi-government sources
for the PMLA Rule 3 CTR threshold and reporting-entity obligations
(FIU-IND's own FAQ page, fiuindia.gov.in, and SEBI's cash-transaction-
report guidance page) and the RBI's own KYC Master Direction document
(rbi.org.in) for its exact reference number. Every `citation` here is a
real, independently-checkable statute/rule/circular reference - nothing
here is invented. Where a specific numeric threshold could not be
corroborated across independent sources (e.g. an exact INR figure for
cross-border wire-transfer reporting specifically, as opposed to the
well-corroborated general CTR/STR/LRS figures), this module does NOT
assert one - see REG-IN-PMLA-CROSSBORDER's `summary`/`text` field, which
states the qualitative obligation only and flags the omission explicitly
rather than guessing a number.

KNOWN LIMITATION (unchanged, restated): a real, continuously-updated
regulatory corpus/vector index is not available in this repository. This
module is the smallest architecture-consistent stand-in - a fixed list of
dicts - so `regulatory_rag.py`'s retrieval interface can be swapped later
to hit a real corpus without changing anything downstream of it
(`regulatory_rules.py`/`investigation_auditor.py` never import this
module directly - they only ever see whatever
`regulatory_rag.retrieve_regulatory_context()` returns).

Each entry (fields marked NEW are additive on top of the prior session's
schema - nothing existing was renamed or removed):
{
    "source_id":              stable id, referenced in rule/auditor output
    "title":                  short human-readable name
    "citation":                real statute/regulation/circular citation
    "jurisdiction":            "IN" | "US" | "cross_border"
    "authority":         NEW - issuing body (e.g. "FIU-IND", "RBI", "FinCEN")
    "topic":             NEW - short topic label (e.g. "CTR", "STR", "CDD")
    "effective_context":  NEW - when/how this applies (plain language)
    "summary":                 1-3 sentence plain-language summary
    "text":               NEW - alias of `summary`, kept for schema
                                 parity with the checkpoint's suggested
                                 corpus shape (jurisdiction/authority/
                                 rule_id/title/topic/citation/
                                 effective_context/source_type/text/
                                 applicability) - never a second,
                                 independently-editable copy; always set
                                 equal to `summary` at definition time.
    "applicability":      NEW - plain-language statement of when this
                                 entry applies / does not apply, so an
                                 auditor can see the applicability
                                 decision's basis without re-deriving it
    "applicable_typologies":   which of the 4 known typologies this is
                                relevant background for (used by the RAG
                                retrieval scorer, not a hard filter)
    "keywords":                 retrieval keywords (signals/evidence_types/
                                pattern names this entry is relevant to)
    "source_type":              "bundled_reference_corpus" (verified
                                citation, cross-checked this checkpoint
                                against multiple independent sources) or
                                "bundled_reference_corpus_unverified_figure"
                                (citation is real; a specific number
                                within it could not be independently
                                corroborated this session and is therefore
                                omitted rather than guessed - see
                                REG-IN-PMLA-CROSSBORDER)
}
"""

REGULATORY_CORPUS = [
    # ------------------------------------------------------------------
    # UNITED STATES (unchanged from the prior session's work - real,
    # correct, simply no longer assumed applicable to India-jurisdiction
    # cases merely because a transaction touched a foreign country)
    # ------------------------------------------------------------------
    {
        "source_id": "REG-BSA-CTR-THRESHOLD",
        "title": "Currency Transaction Report (CTR) filing threshold",
        "citation": "31 U.S.C. 5313; 31 CFR 1010.311",
        "jurisdiction": "US",
        "authority": "FinCEN",
        "topic": "CTR",
        "effective_context": "Applies to a US-jurisdiction account/transaction only.",
        "summary": (
            "A financial institution must file a Currency Transaction "
            "Report for any transaction in currency exceeding $10,000 "
            "conducted by, or on behalf of, one person in a single "
            "business day."
        ),
        "applicability": (
            "Applies only when the case's determined jurisdiction is US "
            "(the account's registered_country maps to the United "
            "States) and the transaction amount being evaluated is "
            "denominated in USD."
        ),
        "applicable_typologies": ["smurfing", "reverse_smurfing", "money_mule", "account_swap"],
        "keywords": [
            "high_value_transaction", "high-value", "amount_fragmentation",
            "large_transaction", "ctr",
        ],
        "source_type": "bundled_reference_corpus",
    },
    {
        "source_id": "REG-BSA-STRUCTURING",
        "title": "Prohibition on structuring transactions to evade reporting",
        "citation": "31 U.S.C. 5324",
        "jurisdiction": "US",
        "authority": "FinCEN",
        "topic": "structuring",
        "effective_context": "Applies to a US-jurisdiction account/transaction only.",
        "summary": (
            "It is unlawful to break a transaction into smaller amounts, "
            "or otherwise structure/assist in structuring transactions, "
            "for the purpose of evading a financial institution's "
            "currency-transaction-reporting requirements."
        ),
        "applicability": "Applies only when the case's determined jurisdiction is US.",
        "applicable_typologies": ["smurfing", "reverse_smurfing"],
        "keywords": [
            "structuring", "amount_fragmentation", "many_to_one", "one_to_many",
            "transaction_chain", "temporal_pattern", "fan_in", "fan_out",
        ],
        "source_type": "bundled_reference_corpus",
    },
    {
        "source_id": "REG-BSA-SAR",
        "title": "Suspicious Activity Report (SAR) filing duty",
        "citation": "31 CFR 1020.320",
        "jurisdiction": "US",
        "authority": "FinCEN",
        "topic": "SAR",
        "effective_context": "Applies to a US-jurisdiction account/transaction only.",
        "summary": (
            "A bank must file a Suspicious Activity Report for a "
            "transaction (or pattern of transactions) it knows, suspects, "
            "or has reason to suspect involves funds derived from illegal "
            "activity, is designed to evade BSA requirements, or has no "
            "apparent lawful purpose, once the applicable dollar threshold "
            "and knowledge standard are met."
        ),
        "applicability": "Applies only when the case's determined jurisdiction is US.",
        "applicable_typologies": ["smurfing", "reverse_smurfing", "money_mule", "account_swap"],
        "keywords": [
            "rapid_onward_transfer", "multi_hop_flow", "pass_through_timing",
            "amount_retention_ratio", "suspicious", "no_apparent_lawful_purpose",
        ],
        "source_type": "bundled_reference_corpus",
    },
    {
        "source_id": "REG-BSA-TRAVEL-RULE",
        "title": "Recordkeeping (\"Travel Rule\") for fund transfers",
        "citation": "31 CFR 1010.410(f)",
        "jurisdiction": "US",
        "authority": "FinCEN",
        "topic": "recordkeeping",
        "effective_context": "Applies to a US-jurisdiction account/transaction only.",
        "summary": (
            "Banks must retain and pass along specified originator/"
            "beneficiary information for funds transfers of $3,000 or "
            "more, so the transfer can be traced through the payment "
            "chain."
        ),
        "applicability": "Applies only when the case's determined jurisdiction is US.",
        "applicable_typologies": ["money_mule", "smurfing", "reverse_smurfing"],
        "keywords": [
            "beneficiary_information", "counterparty_relationship",
            "inbound_transaction_chain", "outbound_transaction_chain",
        ],
        "source_type": "bundled_reference_corpus",
    },
    {
        "source_id": "REG-BSA-CDD-BENEFICIAL-OWNERSHIP",
        "title": "Customer Due Diligence / beneficiary verification expectations",
        "citation": "31 CFR 1010.230",
        "jurisdiction": "US",
        "authority": "FinCEN",
        "topic": "CDD",
        "effective_context": "Applies to a US-jurisdiction account/transaction only.",
        "summary": (
            "Covered institutions are expected to identify and verify the "
            "identity of customers and, where relevant, beneficial owners/"
            "beneficiaries of an account relationship, as part of ongoing "
            "customer due diligence."
        ),
        "applicability": "Applies only when the case's determined jurisdiction is US.",
        "applicable_typologies": ["account_swap", "money_mule"],
        "keywords": [
            "beneficiary_information", "new_beneficiary", "is_first_time_beneficiary",
            "is_verified", "unverified",
        ],
        "source_type": "bundled_reference_corpus",
    },
    {
        "source_id": "REG-FFIEC-ATO-REDFLAGS",
        "title": "Account-takeover / unauthorized-access red flags (supervisory guidance)",
        "citation": "FFIEC IT Examination Handbook - Authentication and Access Management guidance",
        "jurisdiction": "US",
        "authority": "FFIEC",
        "topic": "account_takeover",
        "effective_context": "Applies to a US-jurisdiction account/transaction only.",
        "summary": (
            "Federal banking-agency supervisory guidance identifies "
            "device change, SIM/authentication-factor change, geographically "
            "improbable access, and an immediate high-value transaction to a "
            "new payee shortly after such a change as red flags warranting "
            "enhanced review for possible account takeover."
        ),
        "applicability": (
            "Applies only when the case's determined jurisdiction is US. "
            "No India-specific bundled equivalent exists in this corpus "
            "today - see module docstring's KNOWN LIMITATION; an "
            "India-jurisdiction account_swap case will correctly retrieve "
            "no regulatory_context for this rule rather than borrowing "
            "this US guidance."
        ),
        "applicable_typologies": ["account_swap"],
        "keywords": [
            "sim_change_evidence", "sim_change_before_transaction", "device_information",
            "new_device_before_transaction", "impossible_travel",
            "rapid_geographic_change", "high_value_transaction",
        ],
        "source_type": "bundled_reference_corpus",
    },

    # ------------------------------------------------------------------
    # INDIA (NEW this checkpoint - the dataset's primary jurisdiction)
    # ------------------------------------------------------------------
    {
        "source_id": "REG-IN-PMLA-CTR",
        "title": "Cash Transaction Report (CTR) filing threshold",
        "citation": "Rule 3, Prevention of Money-Laundering (Maintenance of Records) Rules, 2005 (framed under the Prevention of Money Laundering Act, 2002)",
        "jurisdiction": "IN",
        "authority": "FIU-IND",
        "topic": "CTR",
        "effective_context": "Applies to an India-jurisdiction account/transaction only.",
        "summary": (
            "Every reporting entity (banking company, financial "
            "institution, intermediary) must maintain a record of, and "
            "report to FIU-IND, all cash transactions of a value of more "
            "than INR 10,00,000 (ten lakh rupees), or a series of "
            "integrally connected cash transactions that cumulatively "
            "exceed that amount within a month, or their equivalent in "
            "foreign currency."
        ),
        "applicability": (
            "Applies only when the case's determined jurisdiction is IN "
            "(the account's registered_country maps to India) and the "
            "transaction amount being evaluated is denominated in INR "
            "(this bundled corpus does not fabricate an FX conversion "
            "rate to compare a non-INR amount against this threshold)."
        ),
        "applicable_typologies": ["smurfing", "reverse_smurfing", "money_mule", "account_swap"],
        "keywords": [
            "high_value_transaction", "high-value", "amount_fragmentation",
            "large_transaction", "ctr", "structuring",
        ],
        "source_type": "bundled_reference_corpus",
    },
    {
        "source_id": "REG-IN-PMLA-STR",
        "title": "Suspicious Transaction Report (STR) filing duty",
        "citation": (
            "Section 12(1) and Section 12A, Prevention of Money "
            "Laundering Act, 2002; Rule 3(1)(D), Prevention of "
            "Money-Laundering (Maintenance of Records) Rules, 2005"
        ),
        "jurisdiction": "IN",
        "authority": "FIU-IND",
        "topic": "STR",
        "effective_context": "Applies to an India-jurisdiction account/transaction only.",
        "summary": (
            "A reporting entity must furnish a Suspicious Transaction "
            "Report to FIU-IND for any transaction or attempted "
            "transaction - cash or non-cash, regardless of value - that "
            "gives rise to reasonable grounds to suspect it involves "
            "proceeds of crime, has no apparent economic rationale, is "
            "structured to fall just below a reporting threshold, or is "
            "otherwise unusual for the client's profile; unlike the CTR, "
            "there is no monetary floor."
        ),
        "applicability": (
            "Applies only when the case's determined jurisdiction is IN. "
            "Never conditioned on a specific transaction amount, unlike "
            "REG-IN-PMLA-CTR."
        ),
        "applicable_typologies": ["smurfing", "reverse_smurfing", "money_mule", "account_swap"],
        "keywords": [
            "rapid_onward_transfer", "multi_hop_flow", "pass_through_timing",
            "amount_retention_ratio", "suspicious", "no_apparent_lawful_purpose",
            "structuring", "amount_fragmentation", "many_to_one", "one_to_many",
            "transaction_chain", "fan_in", "fan_out",
        ],
        "source_type": "bundled_reference_corpus",
    },
    {
        "source_id": "REG-IN-PMLA-CROSSBORDER",
        "title": "Cross-border transaction record-keeping (general obligation)",
        "citation": "Rule 3, Prevention of Money-Laundering (Maintenance of Records) Rules, 2005",
        "jurisdiction": "cross_border",
        "authority": "FIU-IND",
        "topic": "cross_border_reporting",
        "effective_context": (
            "Applies to an India-registered account when this case's "
            "jurisdiction has been independently determined to be "
            "cross-border (a real is_international transaction, "
            "non-INR-currency transaction, or foreign geo event was "
            "found for this account) - never merely because a "
            "transaction happens to touch a foreign counterparty type "
            "or currency in isolation."
        ),
        "summary": (
            "Rule 3 of the PMLA Maintenance of Records Rules obligates "
            "reporting entities to maintain records of certain "
            "cross-border wire transfers alongside cash and suspicious "
            "transactions. Independent sources consistently confirm the "
            "existence of this cross-border record-keeping/reporting "
            "obligation; this bundled corpus was NOT able to "
            "independently corroborate one specific INR reporting "
            "threshold figure for cross-border wire transfers "
            "specifically (as distinct from the well-corroborated CTR "
            "figure above) across more than one source this session, so "
            "no such figure is asserted here - see `source_type`."
        ),
        "applicability": (
            "Applies only when jurisdiction.py has found real "
            "cross-border evidence for this India-registered account. "
            "Qualitative obligation only - no numeric threshold is "
            "asserted by this entry (see summary)."
        ),
        "applicable_typologies": ["smurfing", "reverse_smurfing", "money_mule", "account_swap"],
        "keywords": [
            "cross_border", "international", "is_international",
            "foreign_currency", "non_local_currency_transaction",
            "geo_country_mismatch",
        ],
        "source_type": "bundled_reference_corpus_unverified_figure",
    },
    {
        "source_id": "REG-IN-FEMA-LRS",
        "title": "Liberalised Remittance Scheme (LRS) outward-remittance limit",
        "citation": (
            "Foreign Exchange Management Act, 1999 (FEMA); RBI A.P. "
            "(DIR Series) Circular No. 64 dated 4 February 2004; RBI "
            "Master Direction - Liberalised Remittance Scheme (LRS)"
        ),
        "jurisdiction": "cross_border",
        "authority": "RBI",
        "topic": "outward_remittance",
        "effective_context": (
            "Applies to an India-registered resident individual account "
            "when this case's jurisdiction has been independently "
            "determined to be cross-border (see REG-IN-PMLA-CROSSBORDER)."
        ),
        "summary": (
            "Under the Liberalised Remittance Scheme, a resident "
            "individual may remit up to USD 250,000 (or equivalent) "
            "abroad per financial year for permitted current- or "
            "capital-account purposes without prior RBI approval; "
            "remittances exceeding this cumulative annual limit, or for "
            "a prohibited purpose, require specific RBI approval and are "
            "themselves a FEMA-compliance concern independent of any "
            "AML/CTR/STR analysis."
        ),
        "applicability": (
            "Applies only when jurisdiction.py has found real "
            "cross-border evidence for this India-registered account. "
            "This is an outward-remittance LIMIT, not a suspicious-"
            "activity threshold - a case can be within the LRS limit and "
            "still separately warrant an STR, or vice versa; the two are "
            "independent findings, never merged into one."
        ),
        "applicable_typologies": ["smurfing", "reverse_smurfing", "money_mule", "account_swap"],
        "keywords": [
            "cross_border", "international", "is_international",
            "foreign_currency", "non_local_currency_transaction", "remittance",
        ],
        "source_type": "bundled_reference_corpus",
    },
    {
        "source_id": "REG-IN-RBI-KYC-CDD",
        "title": "Customer Due Diligence / beneficiary verification expectations",
        "citation": (
            "Master Direction - Know Your Customer (KYC) Direction, "
            "2016 (RBI/DBR/2015-16/18, Master Direction DBR.AML.BC.No."
            "81/14.01.001/2015-16, dated 25 February 2016, as amended)"
        ),
        "jurisdiction": "IN",
        "authority": "RBI",
        "topic": "CDD",
        "effective_context": "Applies to an India-jurisdiction account/transaction only.",
        "summary": (
            "RBI-regulated entities must undertake Customer Due "
            "Diligence, including identification and verification of "
            "customers, beneficial owners, and beneficiaries, at "
            "onboarding and on an ongoing basis, with enhanced due "
            "diligence for higher-risk relationships."
        ),
        "applicability": (
            "Applies only when the case's determined jurisdiction is IN."
        ),
        "applicable_typologies": ["account_swap", "money_mule"],
        "keywords": [
            "beneficiary_information", "new_beneficiary", "is_first_time_beneficiary",
            "is_verified", "unverified",
        ],
        "source_type": "bundled_reference_corpus",
    },
]

# Additive alias so `text` (the checkpoint's suggested field name) and
# `summary` (the prior session's field name) are never two independently-
# editable copies of the same fact - `text` is always set equal to
# `summary` at import time, for every entry, in every jurisdiction.
for _entry in REGULATORY_CORPUS:
    _entry["text"] = _entry["summary"]
del _entry