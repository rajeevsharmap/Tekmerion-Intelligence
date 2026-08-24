"""
agents/llm_pii_sanitizer.py
==============================
CHECKPOINT 7 (LLM security boundary) - trusted-application-side masking
layer sitting immediately in front of every external LLM call made by
the hypothesis/contradiction agents.

### The gap this closes ###
`agents/evidence_builder.gather_evidence()` assembles real internal
evidence (account/beneficiary/device/transaction records straight from
`DataStore`). Before this module existed, `agents/scammer_hypothesis_agent.py`,
`agents/legitimate_hypothesis_agent.py`, and `agents/contradiction_agent.py`
each did `json.dumps(evidence, ...)` directly into an external Gemini
prompt - i.e. raw account IDs, beneficiary IDs/names, and device IDs would
have been sent to a third-party LLM API the moment anything actually
called those functions.

This is a SEPARATE boundary from `case_data_access.mask_account()`, which
governs what an investigator/UI sees. That boundary already existed and
is untouched by this module. This module governs what an EXTERNAL LLM
sees, which is a stricter, different trust boundary - a masked value here
must never be reversible by the LLM itself, only by the trusted
application after the response comes back (see `resolve_pseudonym`).

### What is masked ###
Only direct identifiers - see `ID_PREFIX_MAP` and `NAME_FIELDS` below.
Financial facts (amounts, directions, timestamps, channels, ratios,
pattern flags, typology, risk/kyc fields, graph structure) are left
exactly as computed - masking them would make the evidence useless to
the model and is explicitly out of scope.

### Determinism ###
Pseudonyms are derived from the SORTED set of raw identifiers actually
present in a given evidence payload (`ACCOUNT_001`, `ACCOUNT_002`, ...),
not from call order or randomness. The same evidence therefore always
produces the same masked payload, run over run, process over process,
and two different identifiers always receive two different pseudonyms.
Names (e.g. `beneficiary_name`) reuse their sibling identifier's
pseudonym when one is present on the same record, and otherwise fall
back to a content-hash-derived pseudonym (`PERSON_<hash>`) - never a
random one.

### Provenance ###
This module never mutates its input and never touches the trusted
internal evidence object used everywhere else in the pipeline (case
storage, investigator-facing views, audit trail, SAR generation). It
only ever produces a new, derived, masked COPY for the LLM call site.
The pseudonym map returned alongside the masked payload is how the
trusted application boundary can resolve a pseudonym mentioned in an LLM
response back to the real internal entity, without ever sending the real
identity back to the LLM.
"""
import copy
import hashlib
import re

# Raw internal ID prefixes -> LLM-safe pseudonym prefixes. Only these are
# tokenized; transaction_id/case_id/geo_event_id are internal correlation
# keys, not customer-identifying values, and are left as-is so the model
# can still reason about "this transaction", "this case" etc.
ID_PREFIX_MAP = {
    "ACC": "ACCOUNT",
    "BENE": "BENEFICIARY",
    "DEV": "DEVICE",
}

# Used both to recognize a string that IS entirely an id (structured
# evidence fields) and to find an id mentioned INSIDE a longer string
# (defense-in-depth for free-text LLM output, e.g. a hypothesis agent's
# narrative/supporting_evidence quoting an id verbatim before it reaches
# the contradiction agent's own downstream prompt).
_ID_PATTERN = re.compile(r"\b(" + "|".join(ID_PREFIX_MAP) + r")\d+\b")

# dict keys whose string VALUE is a human name, not a coded identifier,
# and therefore can't be caught by _ID_PATTERN. Extend here if the
# evidence schema ever grows a customer_name/account_holder_name field -
# do not silently trust that "not ID-shaped" means "not PII".
NAME_FIELDS = {"beneficiary_name"}

# When a name field's record also carries one of these ID fields, reuse
# that ID's pseudonym for the name instead of minting a separate one -
# keeps "BENEFICIARY_003" and its name consistent within one payload.
NAME_FIELD_TO_ID_FIELD = {"beneficiary_name": "beneficiary_id"}


def _collect_raw_ids(node, found):
    if isinstance(node, dict):
        for v in node.values():
            if isinstance(v, str):
                for prefix, whole in _iter_id_matches(v):
                    found.add(whole)
            elif isinstance(v, (dict, list)):
                _collect_raw_ids(v, found)
    elif isinstance(node, list):
        for item in node:
            _collect_raw_ids(item, found)


def _iter_id_matches(text):
    """Yield (prefix, whole_match) for every raw id mentioned anywhere in
    `text`, whether `text` IS the id or merely CONTAINS it."""
    for m in _ID_PATTERN.finditer(text):
        yield m.group(1), m.group(0)


def build_pseudonym_map(evidence: dict) -> dict:
    """Deterministic raw_id -> pseudonym map for every direct identifier
    found anywhere in `evidence` (any nesting depth). Sorted-order
    assignment, not first-seen-order, so the map never depends on
    traversal order or dict insertion order - only on the SET of raw
    values actually present."""
    found = set()
    _collect_raw_ids(evidence, found)

    by_prefix = {}
    for raw in found:
        m = _ID_PATTERN.match(raw)
        prefix = m.group(1)
        by_prefix.setdefault(prefix, []).append(raw)

    mapping = {}
    for prefix, raws in by_prefix.items():
        pseudo_prefix = ID_PREFIX_MAP[prefix]
        for i, raw in enumerate(sorted(raws), start=1):
            mapping[raw] = f"{pseudo_prefix}_{i:03d}"
    return mapping


def _pseudonym_for_name(value: str) -> str:
    """Fallback for a name with no sibling identifier to key off of.
    Content-hash-derived, never random - the same raw name always maps
    to the same pseudonym, and no external reference data of any kind is
    consulted."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8].upper()
    return f"PERSON_{digest}"


def _mask_string(value: str, mapping: dict) -> str:
    """Mask a string value against `mapping`. Handles both cases: the
    string IS entirely a raw id (the common, structured-evidence case -
    fast-pathed via a direct dict lookup), and the string merely CONTAINS
    one or more raw ids somewhere inside a longer sentence (the free-text
    defense-in-depth case - e.g. a hypothesis agent narrative that quotes
    an id verbatim). Every id substring found by `_ID_PATTERN` is replaced
    via `_ID_PATTERN.sub`; a match with no entry in `mapping` (should not
    happen, since `build_pseudonym_map` scans with the same pattern over
    the same nesting) is left as-is rather than silently dropped, so a
    genuine gap stays visible instead of being masked over."""
    exact = mapping.get(value)
    if exact is not None:
        return exact
    if _ID_PATTERN.search(value):
        return _ID_PATTERN.sub(lambda m: mapping.get(m.group(0), m.group(0)), value)
    return value


def _mask_node(node, mapping):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in NAME_FIELDS and isinstance(v, str) and v:
                id_field = NAME_FIELD_TO_ID_FIELD.get(k)
                sibling_id = node.get(id_field) if id_field else None
                if sibling_id in mapping:
                    out[k] = mapping[sibling_id]
                else:
                    out[k] = _pseudonym_for_name(v)
            elif isinstance(v, str):
                out[k] = _mask_string(v, mapping)
            elif isinstance(v, (dict, list)):
                out[k] = _mask_node(v, mapping)
            else:
                out[k] = v
        return out
    elif isinstance(node, list):
        return [_mask_node(item, mapping) for item in node]
    elif isinstance(node, str):
        return _mask_string(node, mapping)
    else:
        return node


def sanitize_evidence_for_llm(evidence: dict) -> tuple:
    """The trusted boundary. Returns (masked_evidence, pseudonym_map).

    - `evidence` is never mutated (deep-copied before masking).
    - `masked_evidence` is a full structural copy of `evidence` with every
      direct identifier and every configured name field replaced by a
      deterministic pseudonym; every other field (amounts, timestamps,
      typology, ratios, pattern flags, risk/kyc fields, graph structure)
      is passed through unchanged.
    - `pseudonym_map` (raw_id -> pseudonym) is NOT sent to the LLM. It is
      for the trusted application boundary only, e.g. to resolve a
      pseudonym mentioned in an LLM response back to the real entity via
      `resolve_pseudonym`.
    - Nothing is fabricated: if a field is missing, it stays missing; if
      a value isn't a recognized identifier or configured name field, it
      passes through as-is.
    """
    if evidence is None:
        return None, {}
    mapping = build_pseudonym_map(evidence)
    masked = _mask_node(copy.deepcopy(evidence), mapping)
    return masked, mapping


def sanitize_pair_for_llm(evidence: dict, derived_signals: dict) -> tuple:
    """Like `sanitize_evidence_for_llm`, but masks `evidence` and
    `derived_signals` against ONE shared pseudonym map built from the
    union of identifiers found in both. Hypothesis agents send both
    objects in the same prompt, so an identifier must resolve to the
    SAME pseudonym in both halves - masking them independently could
    assign ACCOUNT_001 to different real accounts in each half if the
    two objects don't reference identifiers in the same set/order.
    Returns (masked_evidence, masked_derived_signals, pseudonym_map).
    """
    combined = {"evidence": evidence, "derived_signals": derived_signals}
    mapping = build_pseudonym_map(combined)
    masked_evidence = _mask_node(copy.deepcopy(evidence), mapping) if evidence is not None else None
    masked_signals = _mask_node(copy.deepcopy(derived_signals), mapping) if derived_signals is not None else None
    return masked_evidence, masked_signals, mapping


def resolve_pseudonym(pseudonym: str, pseudonym_map: dict):
    """Trusted-boundary-only reverse lookup: pseudonym -> real internal
    identifier. Never call this to send the real value back to the LLM;
    it exists solely for the trusted application to interpret an LLM
    response that mentions a pseudonym (e.g. in `deciding_factor`)."""
    for raw, pseudo in pseudonym_map.items():
        if pseudo == pseudonym:
            return raw
    return None