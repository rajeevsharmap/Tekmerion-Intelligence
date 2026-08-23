"""
regulatory_rag.py
====================
CHECKPOINT 5 - Regulatory RAG.

    Contradiction Agent -> REGULATORY COMPLIANCE RULE ENGINE -> REGULATORY RAG
    -> Investigation Auditor -> Case Completeness Score -> ...

Retrieves regulatory reference material relevant to a rule-evaluation
context (typology + the real signals/evidence types already gathered for
this case) from `regulatory_corpus.py`'s bundled corpus (see that module's
docstring for the "not a live feed" caveat).

Design contract (per the checkpoint's "Regulatory RAG" requirements):
  1. Retrieval is a pure function of (rule_context, corpus) - deterministic,
     no randomness, no network calls, nothing "invented": every returned
     entry is copied verbatim from the corpus, never paraphrased or
     synthesized here.
  2. Every returned entry carries its own source metadata/provenance
     (source_id, citation, jurisdiction, source_type) unchanged.
  3. Retrieved regulatory TEXT (title/summary/citation) is returned as its
     own clearly-separated block; this module never adds interpretation,
     confidence, or a rule verdict - that is regulatory_rules.py's job, one
     layer up. Keeping "what the source says" and "what the rule engine
     concluded" in two different objects is what lets a human auditor tell
     them apart later (per ARCHITECTURE.md's evidence-provenance principle,
     applied here to regulatory sources).
  4. Never returns an entry that isn't in `REGULATORY_CORPUS` - if nothing
     in the bundled corpus matches, returns an empty list (honest "no
     relevant source in this bundled corpus", never a fabricated citation).
  5. JURISDICTION IS A HARD GATE, checked BEFORE any keyword scoring
     (updated this checkpoint - see `jurisdiction.py`). `rule_context` may
     carry `applicable_jurisdictions` - a list of jurisdiction tags
     (`jurisdiction.determine_case_jurisdiction()`'s output) that are
     legitimately in scope for THIS case. An entry whose own `jurisdiction`
     is not in that list is never returned, no matter how well its
     keywords match - a foreign-currency transaction on an India-
     registered account must never cause a US citation to be retrieved
     just because the entry's keywords happened to overlap. If
     `applicable_jurisdictions` is omitted entirely (not merely empty),
     retrieval falls back to the prior, jurisdiction-blind behavior - this
     keeps every pre-existing caller/test that never mentions jurisdiction
     working exactly as before; passing an explicit empty list, by
     contrast, means "no jurisdiction is in scope" and correctly excludes
     every entry.

Retrieval scoring is simple deterministic keyword overlap (a real corpus
swap-in could later replace `_score_entry` with embedding similarity
without changing this function's public contract - `retrieve_regulatory_
context(rule_context, corpus=..., top_k=...)`.
"""

from regulatory_corpus import REGULATORY_CORPUS

DEFAULT_TOP_K = 3


def _score_entry(entry, typology, signal_terms):
    """Deterministic overlap score: +2 if the corpus entry lists this
    typology as applicable, +1 per keyword that also appears in
    `signal_terms` (the real signals/evidence types/pattern names already
    produced for this case - never a guess about what the case might
    contain)."""
    score = 0
    if typology and typology in entry.get("applicable_typologies", []):
        score += 2
    entry_keywords = set(entry.get("keywords", []))
    score += len(entry_keywords & signal_terms)
    return score


def retrieve_regulatory_context(rule_context, corpus=REGULATORY_CORPUS, top_k=DEFAULT_TOP_K):
    """rule_context: {"typology": str|None, "signal_terms": iterable[str],
    "applicable_jurisdictions": iterable[str]|None} where `signal_terms` is
    whatever real, already-computed signal names are relevant (pattern
    types from network_layer.py, evidence_type names from evidence_model.py,
    etc.) - built by the CALLER from real data, never invented here.
    `applicable_jurisdictions` is likewise built by the CALLER from real
    data (`jurisdiction.determine_case_jurisdiction()`'s output) - see the
    module docstring's point 5 for the hard-gate contract, including the
    "omitted vs. explicit empty list" distinction.

    Returns a list (possibly empty) of up to `top_k` corpus entries, each
    tagged with its own `retrieval_score` and `matched_keywords` (added
    fields, additive, so provenance stays traceable to why THIS entry was
    retrieved for THIS case) - sorted by score descending, ties broken by
    `source_id` for determinism. Entries with score 0 are never returned
    (an entry must match on typology and/or at least one real keyword to
    be considered relevant, never returned "just because it exists").
    """
    typology = rule_context.get("typology")
    signal_terms = set(rule_context.get("signal_terms") or [])
    applicable_jurisdictions = rule_context.get("applicable_jurisdictions")
    jurisdiction_gate = set(applicable_jurisdictions) if applicable_jurisdictions is not None else None

    scored = []
    for entry in corpus:
        if jurisdiction_gate is not None and entry.get("jurisdiction") not in jurisdiction_gate:
            continue
        score = _score_entry(entry, typology, signal_terms)
        if score <= 0:
            continue
        matched_keywords = sorted(set(entry.get("keywords", [])) & signal_terms)
        scored.append({
            **entry,
            "retrieval_score": score,
            "matched_keywords": matched_keywords,
        })

    scored.sort(key=lambda e: (-e["retrieval_score"], e["source_id"]))
    return scored[:top_k]