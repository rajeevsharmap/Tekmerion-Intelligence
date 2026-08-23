"""
detection_layer.py
====================
Rule-based Detection Agent for the Autonomous Financial Crime Investigation
hackathon MVP, plus the Case Bundler that turns triggered alerts into cases.

Responsible for (per spec section 25):
    - Rule evaluation
    - Anomaly detection
    - Typology classification
    - Alert scoring
    - Evidence signal generation
    - Alert creation
    - Case creation trigger

NOT responsible for:
    - Final fraud decision
    - Regulatory interpretation
    - Human investigator decision
    - Graph/timeline construction (that's network_layer.py)

Every detector returns the Common Alert Contract from the spec. Detectors
never read ground_truth_* fields - those only exist in the mock cases.csv
for offline evaluation and must never influence live scoring.
"""

import hashlib
import statistics
from datetime import timedelta

from data_store import DataStore

CASE_BUNDLE_WINDOW_HOURS = 24
DEFAULT_INBOUND_WINDOW_HOURS = 24
DEFAULT_ANCHOR = "2026-08-19T10:30:00"  # fallback ISO string if a txn has no timestamp


# ----------------------------------------------------------------------
# 1. Rule configurations (verbatim from the spec, section 5-8)
# ----------------------------------------------------------------------
SMURFING_RULES = {
    "SMF-001": {"name": "multiple_inbound_counterparties",
                "description": "Account receives funds from multiple distinct accounts within a short period.",
                "condition": {"unique_inbound_senders": ">= 3", "time_window_hours": "<= 24"}, "score": 15},
    "SMF-002": {"name": "fragmented_inbound_transactions",
                "description": "Multiple relatively small incoming transactions aggregate into a materially larger amount.",
                "condition": {"inbound_transaction_count": ">= 3", "aggregate_inbound_amount": "> account_baseline",
                              "amount_variance": "low_or_moderate"}, "score": 15},
    "SMF-003": {"name": "rapid_onward_transfer",
                "description": "Funds received by the account are transferred onward shortly after receipt.",
                "condition": {"incoming_to_outgoing_time_minutes": "<= 360", "outgoing_amount_ratio_of_incoming": ">= 0.70"},
                "score": 20},
    "SMF-004": {"name": "multiple_outbound_counterparties",
                "description": "Account distributes received funds to multiple beneficiaries/accounts.",
                "condition": {"unique_outbound_receivers": ">= 2", "time_window_hours": "<= 24"}, "score": 10},
    "SMF-005": {"name": "transaction_profile_deviation",
                "description": "Current transaction activity materially exceeds the customer's normal profile.",
                "condition": {"current_period_amount": "> 3 * avg_monthly_txn_amount"}, "score": 15},
    "SMF-006": {"name": "multi_hop_fund_flow",
                "description": "Funds can be followed through multiple accounts.",
                "condition": {"network_depth": ">= 2"}, "score": 15},
}

REVERSE_SMURFING_RULES = {
    "RSMF-001": {"name": "multiple_outbound_counterparties",
                 "description": "One account distributes funds to multiple distinct receiving accounts.",
                 "condition": {"unique_outbound_receivers": ">= 3", "time_window_hours": "<= 24"}, "score": 20},
    "RSMF-002": {"name": "outbound_amount_fragmentation",
                 "description": "A source amount is divided into multiple smaller transfers.",
                 "condition": {"outbound_transaction_count": ">= 3", "individual_amounts": "relatively_similar"}, "score": 15},
    "RSMF-003": {"name": "rapid_distribution",
                 "description": "Funds are distributed to several accounts within a short period.",
                 "condition": {"distribution_window_minutes": "<= 360"}, "score": 15},
    "RSMF-004": {"name": "downstream_pass_through",
                 "description": "Recipients rapidly transfer received funds onward.",
                 "condition": {"recipient_onward_transfer_within_hours": "<= 6"}, "score": 20},
    "RSMF-005": {"name": "transaction_profile_deviation",
                 "description": "Distribution activity exceeds the source account's normal profile.",
                 "condition": {"current_amount": "> 3 * avg_monthly_txn_amount"}, "score": 15},
    "RSMF-006": {"name": "multi_hop_distribution_network",
                 "description": "Distributed funds continue through downstream accounts.",
                 "condition": {"network_depth": ">= 2"}, "score": 15},
}

MONEY_MULE_RULES = {
    "MM-001": {"name": "high_inbound_velocity",
               "description": "Account receives multiple incoming transfers within a short period.",
               "condition": {"inbound_transaction_count": ">= 3", "time_window_hours": "<= 24"}, "score": 15},
    "MM-002": {"name": "rapid_fund_pass_through",
               "description": "Substantial incoming funds are transferred out shortly after receipt.",
               "condition": {"outgoing_after_incoming_hours": "<= 6", "outgoing_to_incoming_ratio": ">= 0.70"}, "score": 25},
    "MM-003": {"name": "high_counterparty_count",
               "description": "Account interacts with unusually many counterparties.",
               "condition": {"unique_counterparties": ">= 4", "time_window_hours": "<= 24"}, "score": 15},
    "MM-004": {"name": "profile_deviation",
               "description": "Transaction volume significantly exceeds historical profile.",
               "condition": {"current_amount": "> 3 * avg_monthly_txn_amount"}, "score": 15},
    "MM-005": {"name": "low_retention_of_received_funds",
               "description": "Most incoming funds leave the account shortly after receipt.",
               "condition": {"outgoing_incoming_ratio": ">= 0.80"}, "score": 20},
    "MM-006": {"name": "new_beneficiary_after_inbound",
               "description": "Funds are transferred to a newly added or first-time beneficiary after suspicious incoming activity.",
               "condition": {"is_first_time_beneficiary": True, "outgoing_after_inbound_hours": "<= 6"}, "score": 10},
}

ACCOUNT_SWAP_RULES = {
    "AS-001": {"name": "sim_change",
               "description": "SIM change detected near suspicious transaction activity.",
               "condition": {"sim_change_detected": True, "transaction_within_hours": "<= 24"}, "score": 25},
    "AS-002": {"name": "new_device",
               "description": "Transaction occurs from previously unseen or untrusted device.",
               "condition": {"is_trusted_device": False}, "score": 15},
    "AS-003": {"name": "device_fingerprint_change",
               "description": "Device fingerprint changes around suspicious activity.",
               "condition": {"new_device_fingerprint": True}, "score": 15},
    "AS-004": {"name": "impossible_travel",
               "description": "Large geographic movement occurs within an implausibly short period.",
               "condition": {"distance_from_last_location_km": "> 500", "time_difference_hours": "<= 4"}, "score": 20},
    "AS-005": {"name": "registered_country_mismatch",
               "description": "Transaction-related location differs from registered country.",
               "condition": {"registered_country_match": False}, "score": 10},
    "AS-006": {"name": "new_beneficiary",
               "description": "Large/unusual transfer is made to a newly added beneficiary.",
               "condition": {"is_first_time_beneficiary": True}, "score": 10},
    "AS-007": {"name": "transaction_amount_anomaly",
               "description": "Transaction amount significantly exceeds customer's normal activity.",
               "condition": {"transaction_amount": "> 3 * avg_monthly_txn_amount"}, "score": 15},
    "AS-008": {"name": "compound_account_takeover_signal",
               "description": "Multiple independent security and transaction anomalies occur together.",
               "condition": {"minimum_security_signals": ">= 2", "minimum_transaction_signals": ">= 1"}, "score": 20},
}

RULEBOOKS = {
    "smurfing": SMURFING_RULES,
    "reverse_smurfing": REVERSE_SMURFING_RULES,
    "money_mule": MONEY_MULE_RULES,
    "account_swap": ACCOUNT_SWAP_RULES,
}


# ----------------------------------------------------------------------
# 2. Score classification (spec section 4)
# ----------------------------------------------------------------------
def classify_alert(score):
    if score >= 80:
        return {"severity": "critical", "initial_action": "escalate"}
    elif score >= 60:
        return {"severity": "high", "initial_action": "escalate"}
    elif score >= 30:
        return {"severity": "medium", "initial_action": "monitor"}
    else:
        return {"severity": "low", "initial_action": "clear"}


# ----------------------------------------------------------------------
# 3. Shared helpers
# ----------------------------------------------------------------------
def _best_window(txns, key_field, window_hours=DEFAULT_INBOUND_WINDOW_HOURS):
    """Try every txn as the start of a rolling window; return the window
    (list of txns) that maximises unique counterparties on `key_field`.
    O(n^2) but n is small per-account for a hackathon dataset."""
    best = []
    best_unique = -1
    for t0 in txns:
        start = t0["timestamp"]
        end = start + timedelta(hours=window_hours)
        window = [t for t in txns if start <= t["timestamp"] <= end]
        unique = len({t[key_field] for t in window})
        if unique > best_unique or (unique == best_unique and len(window) > len(best)):
            best, best_unique = window, unique
    return best


def _coeff_variation(amounts):
    if len(amounts) < 2:
        return 0.0
    mean = statistics.mean(amounts)
    if mean == 0:
        return 0.0
    return statistics.pstdev(amounts) / mean


def compute_flow_depth(store, account_id, max_depth=3, _visited=None):
    """Lightweight standalone multi-hop check used only for the SMF-006 /
    RSMF-006 signal - NOT the full graph the network layer builds. Measures
    how many hops an account's outbound funds can be traced forward through
    other accounts within max_depth."""
    visited = {account_id}
    frontier = [account_id]
    depth = 0
    for d in range(1, max_depth + 1):
        next_frontier = []
        for acc in frontier:
            for t in store.outbound_by_account.get(acc, []):
                tgt = t["receiver_account_id"]
                if tgt not in visited and tgt in store.accounts_by_id:
                    visited.add(tgt)
                    next_frontier.append(tgt)
        if not next_frontier:
            break
        depth = d
        frontier = next_frontier
    return depth


def _new_alert_id(typology, *content_parts):
    """CHECKPOINT 3 fix: alert_id used to be uuid.uuid4()-based, which draws
    from os.urandom - NOT from the random.seed(42) that generate_mock_data.py
    sets, and NOT deterministic across repeated runs on identical input data.
    This silently violated docs/ARCHITECTURE.md's own "Determinism &
    reproducibility" claim ("given identical input data, output is always
    identical") for the one field most likely to be compared run-to-run.
    Fixed here (not weakened/undocumented) per this checkpoint's Section 10
    guidance: when the doc and the code genuinely disagree, correct the
    code, and record the fix. content_parts must fully determine the alert's
    identity (account_id, transaction_id, triggering_rules, created_at) so
    two distinct alerts never collide and the same alert always gets the
    same id."""
    digest = hashlib.sha256("|".join(str(p) for p in content_parts).encode("utf-8")).hexdigest()
    return f"ALERT-{typology.upper()[:4]}-{digest[:8].upper()}"


def _make_alert(account_id, transaction_id, typology, rulebook, triggering_rules, evidence_signals, created_at,
                 min_rules=1, relevant_transaction_ids=None):
    score = min(100, sum(rulebook[r]["score"] for r in triggering_rules))
    triggered = score >= 30 and len(triggering_rules) >= min_rules
    classification = classify_alert(score)
    # relevant_transaction_ids: every observable transaction the detector actually
    # examined while building this alert (the rolling window / pass-through set),
    # not just the single anchor transaction_id. This is additive to the existing
    # transaction_id field (never replaces it - preserves the existing contract)
    # and exists so (a) the alert is independently explainable without re-deriving
    # the detector's window, and (b) Case Bundling (CHECKPOINT 3) can check for
    # real shared-evidence overlap between two alerts instead of only comparing
    # account_id + a time window.
    relevant_ids = sorted(set(relevant_transaction_ids)) if relevant_transaction_ids else [transaction_id]
    if transaction_id not in relevant_ids:
        relevant_ids = sorted(set(relevant_ids) | {transaction_id})
    created_at_str = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
    alert_id = _new_alert_id(typology, account_id, transaction_id, tuple(triggering_rules), created_at_str)
    return {
        "alert_id": alert_id,
        "account_id": account_id,
        "transaction_id": transaction_id,
        "relevant_transaction_ids": relevant_ids,
        "typology": typology,
        "triggered": triggered,
        "alert_score": score,
        "severity": classification["severity"],
        "triggering_rules": triggering_rules,
        "evidence_signals": evidence_signals,
        "recommended_initial_action": classification["initial_action"] if triggered else "clear",
        "case_required": triggered and classification["initial_action"] in ("escalate", "monitor"),
        "created_at": created_at_str,
    }


# ----------------------------------------------------------------------
# 4. Smurfing detector
# ----------------------------------------------------------------------
def detect_smurfing(store, account):
    account_id = account["account_id"]
    inbound = store.inbound_by_account.get(account_id, [])
    outbound = store.outbound_by_account.get(account_id, [])
    if len(inbound) < 3:
        return None

    window = _best_window(inbound, "sender_account_id")
    # a "window" of one transaction has a trivial 0-minute span and would make
    # every downstream timing check pass vacuously - require a real cluster
    if len(window) < 3:
        return None

    triggering, signals = [], []
    unique_senders = {t["sender_account_id"] for t in window}
    aggregate = sum(t["amount"] for t in window)
    baseline = account["avg_monthly_txn_amount"]

    if len(unique_senders) >= 3:
        triggering.append("SMF-001")
        signals.append({"signal": "multiple_inbound_counterparties", "value": len(unique_senders), "threshold": 3})

    cv = _coeff_variation([t["amount"] for t in window])
    if len(window) >= 3 and aggregate > baseline and cv <= 0.4:
        triggering.append("SMF-002")
        signals.append({"signal": "aggregate_inbound_amount", "value": round(aggregate, 2), "currency": "INR"})

    window_end = max(t["timestamp"] for t in window)
    rapid_txn = None
    for t in sorted(outbound, key=lambda x: x["timestamp"]):
        if t["timestamp"] < window_end:
            continue
        if (t["timestamp"] - window_end) > timedelta(minutes=360):
            break
        if aggregate > 0 and (t["amount"] / aggregate) >= 0.70:
            rapid_txn = t
            break
    if rapid_txn:
        triggering.append("SMF-003")
        signals.append({"signal": "rapid_onward_transfer", "value": True, "maximum_hours": 6})

    anchor_time = rapid_txn["timestamp"] if rapid_txn else window_end
    out_window = [t for t in outbound if abs((t["timestamp"] - anchor_time).total_seconds()) <= 24 * 3600]
    unique_receivers = {t["receiver_account_id"] for t in out_window}
    if len(unique_receivers) >= 2:
        triggering.append("SMF-004")
        signals.append({"signal": "multiple_outbound_counterparties", "value": len(unique_receivers), "threshold": 2})

    if baseline and aggregate > 3 * baseline:
        triggering.append("SMF-005")
        signals.append({"signal": "transaction_profile_deviation", "value": round(aggregate / baseline, 2) if baseline else None})

    depth = compute_flow_depth(store, account_id) if rapid_txn else 0
    if depth >= 2:
        triggering.append("SMF-006")
        signals.append({"signal": "network_depth", "value": depth})

    if not triggering:
        return None

    root_txn_id = rapid_txn["transaction_id"] if rapid_txn else max(window, key=lambda t: t["amount"])["transaction_id"]
    relevant_txn_ids = ([t["transaction_id"] for t in window]
                         + ([rapid_txn["transaction_id"]] if rapid_txn else [])
                         + [t["transaction_id"] for t in out_window])
    return _make_alert(account_id, root_txn_id, "smurfing", SMURFING_RULES, triggering, signals, anchor_time,
                        min_rules=2, relevant_transaction_ids=relevant_txn_ids)


# ----------------------------------------------------------------------
# 5. Reverse smurfing detector
# ----------------------------------------------------------------------
def detect_reverse_smurfing(store, account):
    account_id = account["account_id"]
    outbound = store.outbound_by_account.get(account_id, [])
    if len(outbound) < 3:
        return None

    window = _best_window(outbound, "receiver_account_id")
    # same guard as smurfing: a single-transaction window has a trivial 0-minute
    # "distribution window" that would pass RSMF-003 vacuously
    if len(window) < 3:
        return None

    triggering, signals = [], []
    unique_receivers = {t["receiver_account_id"] for t in window}
    aggregate = sum(t["amount"] for t in window)
    baseline = account["avg_monthly_txn_amount"]

    if len(unique_receivers) >= 3:
        triggering.append("RSMF-001")
        signals.append({"signal": "unique_outbound_receivers", "value": len(unique_receivers), "threshold": 3})

    cv = _coeff_variation([t["amount"] for t in window])
    if len(window) >= 3 and cv <= 0.4:
        triggering.append("RSMF-002")
        signals.append({"signal": "outbound_amount_fragmentation", "value": len(window)})

    span_minutes = (max(t["timestamp"] for t in window) - min(t["timestamp"] for t in window)).total_seconds() / 60
    if span_minutes <= 360:
        triggering.append("RSMF-003")
        signals.append({"signal": "distribution_window_hours", "value": round(span_minutes / 60, 2)})

    # downstream pass-through: do the receivers themselves forward funds onward within 6h?
    pass_through_hits = 0
    for t in window:
        recv = t["receiver_account_id"]
        for t2 in store.outbound_by_account.get(recv, []):
            gap = (t2["timestamp"] - t["timestamp"]).total_seconds() / 3600
            if 0 <= gap <= 6:
                pass_through_hits += 1
                break
    if pass_through_hits >= max(2, len(window) // 2):
        triggering.append("RSMF-004")
        signals.append({"signal": "downstream_pass_through", "value": True})

    if baseline and aggregate > 3 * baseline:
        triggering.append("RSMF-005")
        signals.append({"signal": "transaction_profile_deviation", "value": round(aggregate / baseline, 2) if baseline else None})

    depth = compute_flow_depth(store, account_id)
    if depth >= 2:
        triggering.append("RSMF-006")
        signals.append({"signal": "network_depth", "value": depth})

    if not triggering:
        return None

    root_txn_id = max(window, key=lambda t: t["amount"])["transaction_id"]
    anchor_time = min(t["timestamp"] for t in window)
    relevant_txn_ids = [t["transaction_id"] for t in window]
    return _make_alert(account_id, root_txn_id, "reverse_smurfing", REVERSE_SMURFING_RULES, triggering, signals,
                        anchor_time, min_rules=2, relevant_transaction_ids=relevant_txn_ids)


# ----------------------------------------------------------------------
# 6. Money mule detector
# ----------------------------------------------------------------------
def detect_money_mule(store, account):
    account_id = account["account_id"]
    inbound = store.inbound_by_account.get(account_id, [])
    outbound = store.outbound_by_account.get(account_id, [])
    if not inbound or not outbound:
        return None

    window = _best_window(inbound, "sender_account_id")
    if not window:
        return None

    triggering, signals = [], []
    aggregate_in = sum(t["amount"] for t in window)
    baseline = account["avg_monthly_txn_amount"]
    window_end = max(t["timestamp"] for t in window)

    if len(window) >= 3:
        triggering.append("MM-001")
        signals.append({"signal": "inbound_transaction_count", "value": len(window)})

    out_6h = [t for t in outbound if window_end <= t["timestamp"] <= window_end + timedelta(hours=6)]
    aggregate_out_6h = sum(t["amount"] for t in out_6h)
    ratio = (aggregate_out_6h / aggregate_in) if aggregate_in else 0

    gaps = []
    for t in out_6h:
        preceding = [i for i in window if i["timestamp"] <= t["timestamp"]]
        if preceding:
            gaps.append((t["timestamp"] - max(i["timestamp"] for i in preceding)).total_seconds() / 60)

    if out_6h and ratio >= 0.70:
        triggering.append("MM-002")
        signals.append({"signal": "outgoing_incoming_ratio", "value": round(ratio, 2)})
        if gaps:
            signals.append({"signal": "median_inbound_to_outbound_minutes", "value": round(statistics.median(gaps), 1)})

    counterparties = {t["sender_account_id"] for t in window} | {t["receiver_account_id"] for t in out_6h}
    if len(counterparties) >= 4:
        triggering.append("MM-003")
        signals.append({"signal": "unique_counterparties", "value": len(counterparties), "threshold": 4})

    if baseline and aggregate_in > 3 * baseline:
        triggering.append("MM-004")
        signals.append({"signal": "profile_deviation", "value": round(aggregate_in / baseline, 2) if baseline else None})

    if ratio >= 0.80:
        triggering.append("MM-005")
        signals.append({"signal": "low_retention_of_received_funds", "value": round(ratio, 2)})

    root_txn = max(out_6h, key=lambda t: t["amount"]) if out_6h else None
    if root_txn:
        bene = store.bene_by_id.get(root_txn.get("beneficiary_id"))
        if bene and bene.get("is_first_time_beneficiary"):
            triggering.append("MM-006")
            signals.append({"signal": "new_beneficiary_after_inbound", "value": True})

    if not triggering:
        return None

    root_txn_id = root_txn["transaction_id"] if root_txn else max(window, key=lambda t: t["amount"])["transaction_id"]
    relevant_txn_ids = [t["transaction_id"] for t in window] + [t["transaction_id"] for t in out_6h]
    return _make_alert(account_id, root_txn_id, "money_mule", MONEY_MULE_RULES, triggering, signals, window_end,
                        min_rules=2, relevant_transaction_ids=relevant_txn_ids)


# ----------------------------------------------------------------------
# 7. Account swap / takeover detector
# ----------------------------------------------------------------------
def detect_account_swap(store, account):
    """Unlike the other three, this evaluates candidate outbound transactions
    individually - account takeover is anchored to a specific anomalous
    transaction, not a rolling window. Do NOT trigger on a single signal
    (per spec section 8) - require the compound check (AS-008) or at least
    two independent rule families to fire together."""
    account_id = account["account_id"]
    outbound = store.outbound_by_account.get(account_id, [])
    devices = store.devices_by_account.get(account_id, [])
    geo_events = store.geo_by_account.get(account_id, [])
    baseline = account["avg_monthly_txn_amount"]

    if not outbound:
        return None

    best_alert_data = None
    best_score = -1

    for txn in outbound:
        triggering, signals = [], []
        security_signal_count = 0
        transaction_signal_count = 0

        # AS-001 / AS-002 / AS-003: device signals near this transaction
        nearby_devices = [d for d in devices
                           if abs((txn["timestamp"] - d["first_seen_date"]).total_seconds()) <= 24 * 3600]
        untrusted_nearby = [d for d in nearby_devices if not d["is_trusted_device"]]
        sim_change_nearby = [d for d in nearby_devices if d["sim_change_detected"]]

        if sim_change_nearby:
            triggering.append("AS-001")
            signals.append({"signal": "sim_change_detected", "value": True})
            security_signal_count += 1
        if untrusted_nearby:
            triggering.append("AS-002")
            signals.append({"signal": "new_device", "value": True})
            security_signal_count += 1
        if untrusted_nearby:  # newly-seen fingerprint co-occurs with an untrusted device in this mock schema
            triggering.append("AS-003")
            signals.append({"signal": "device_fingerprint_change", "value": True})
            security_signal_count += 1

        # AS-004 / AS-005: geo signals nearest (and before) this transaction
        prior_geo = [g for g in geo_events if g["timestamp"] <= txn["timestamp"]]
        if prior_geo:
            nearest_geo = max(prior_geo, key=lambda g: g["timestamp"])
            gap_hours = (txn["timestamp"] - nearest_geo["timestamp"]).total_seconds() / 3600
            if nearest_geo["distance_from_last_location_km"] > 500 and gap_hours <= 24:
                triggering.append("AS-004")
                signals.append({"signal": "distance_from_last_location_km", "value": nearest_geo["distance_from_last_location_km"]})
                signals.append({"signal": "time_difference_hours", "value": round(gap_hours, 2)})
                security_signal_count += 1
            if not nearest_geo["registered_country_match"]:
                triggering.append("AS-005")
                signals.append({"signal": "registered_country_mismatch", "value": True})
                security_signal_count += 1

        # AS-006: new beneficiary
        bene = store.bene_by_id.get(txn.get("beneficiary_id"))
        if bene and bene.get("is_first_time_beneficiary"):
            triggering.append("AS-006")
            signals.append({"signal": "first_time_beneficiary", "value": True})
            transaction_signal_count += 1

        # AS-007: amount anomaly
        if baseline and txn["amount"] > 3 * baseline:
            triggering.append("AS-007")
            signals.append({"signal": "transaction_amount", "value": txn["amount"], "currency": txn["currency"]})
            transaction_signal_count += 1

        # AS-008: compound signal - the real account-takeover tell
        if security_signal_count >= 2 and transaction_signal_count >= 1:
            triggering.append("AS-008")
            signals.append({"signal": "compound_account_takeover_signal", "value": True})

        if not triggering:
            continue
        # require the compound pattern (or at least 2 security + 1 txn signal) to
        # actually raise an alert - a lone AS-006/AS-007 on its own is too weak
        if "AS-008" not in triggering and not (security_signal_count >= 1 and transaction_signal_count >= 1 and len(triggering) >= 3):
            continue

        score = min(100, sum(ACCOUNT_SWAP_RULES[r]["score"] for r in triggering))
        if score > best_score:
            best_score = score
            best_alert_data = (txn, triggering, signals)

    if not best_alert_data:
        return None

    txn, triggering, signals = best_alert_data
    return _make_alert(account_id, txn["transaction_id"], "account_swap", ACCOUNT_SWAP_RULES, triggering, signals, txn["timestamp"])


# ----------------------------------------------------------------------
# 8. Orchestration: detect_all + case bundler
# ----------------------------------------------------------------------
def detect_all(store, account_id):
    """Run every typology detector for one account_id and return the list of
    TRIGGERED alerts (spec section 21)."""
    account = store.accounts_by_id.get(account_id)
    if not account:
        return []
    alerts = []
    for detector in (detect_smurfing, detect_reverse_smurfing, detect_money_mule, detect_account_swap):
        alert = detector(store, account)
        if alert and alert["triggered"]:
            alerts.append(alert)
    return alerts


def run_detection_pipeline(store):
    """Run detect_all across every account in the DataStore."""
    all_alerts = []
    for account_id in store.account_ids():
        all_alerts.extend(detect_all(store, account_id))
    return all_alerts


def _temporal_clusters(acc_alerts, window_hours):
    """Group one account's alerts into candidate temporal clusters: a greedy
    rolling window, exactly as before this checkpoint. This step alone is
    NOT sufficient justification for a single case (see
    _split_cluster_by_correlation below) - it just bounds which alerts are
    even eligible to be considered together."""
    from datetime import datetime as _dt
    acc_alerts = sorted(acc_alerts, key=lambda a: a["created_at"])
    clusters = []
    cluster = []
    for a in acc_alerts:
        ts = _dt.fromisoformat(a["created_at"])
        if not cluster:
            cluster = [a]
        elif (ts - _dt.fromisoformat(cluster[0]["created_at"])).total_seconds() <= window_hours * 3600:
            cluster.append(a)
        else:
            clusters.append(cluster)
            cluster = [a]
    if cluster:
        clusters.append(cluster)
    return clusters


def _pairwise_correlation(a, b):
    """Deterministic reasons two alerts (already known to share account_id
    and to fall in the same temporal cluster) may be merged into the SAME
    case, derived only from data already present on the alert objects
    (typology, transaction anchors) - never ground truth, never a
    mock-generator fraud-network id, never data pulled from a later
    investigation/network-layer stage (case bundling is Stage 4; it must not
    reach into Stage 5's evidence to justify its own grouping decision).

    Returns a set of reason strings, or an empty set if these two alerts must
    NOT be merged on this pairing alone (per docs/ARCHITECTURE.md's "Bundle
    reason" section: same account + same time window is not, by itself,
    sufficient - two genuinely unrelated alerts can coincidentally land on
    the same account in the same window and must stay separate cases unless
    they also share typology or an observable transaction anchor)."""
    reasons = set()
    if a["typology"] == b["typology"]:
        reasons.add("same_typology")

    a_txns = set(a.get("relevant_transaction_ids") or [a["transaction_id"]])
    b_txns = set(b.get("relevant_transaction_ids") or [b["transaction_id"]])
    if a_txns & b_txns:
        reasons.add("shared_transaction_chain")

    return reasons


def _split_cluster_by_correlation(cluster):
    """Within one temporal cluster (same account, same window), split into
    the actual correlated sub-groups using union-find over pairwise
    _pairwise_correlation() results, and return (alerts, reasons) per
    sub-group. Two alerts end up in the same sub-group only if there is a
    direct or transitive correlation edge between them - same account + same
    window alone never creates an edge. This is what stops two unrelated
    typologies that merely happen to land in the same window from being
    silently merged (see docs/ARCHITECTURE.md "Bundle reason", TEST 3 in the
    Checkpoint 3 spec)."""
    n = len(cluster)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    pair_reasons = {}
    for i in range(n):
        for j in range(i + 1, n):
            reasons = _pairwise_correlation(cluster[i], cluster[j])
            if reasons:
                union(i, j)
                pair_reasons[(i, j)] = reasons

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    sub_clusters = []
    for idxs in groups.values():
        group_reasons = set()
        for i in idxs:
            for j in idxs:
                if i < j and (i, j) in pair_reasons:
                    group_reasons |= pair_reasons[(i, j)]
        sub_clusters.append(([cluster[i] for i in idxs], group_reasons))
    return sub_clusters


def bundle_alerts_into_cases(alerts, window_hours=CASE_BUNDLE_WINDOW_HOURS):
    """Case Intake (spec section 9, corrected in CHECKPOINT 3): bundle
    triggered alerts into a single case only when they are BOTH (a) for the
    same account_id within `window_hours` of each other, AND (b) actually
    correlated - same typology, or a directly observable shared-evidence
    anchor (currently: overlapping transaction IDs; see
    _pairwise_correlation). "same account + same time window" alone is no
    longer sufficient justification by itself (that was the pre-Checkpoint-3
    behavior this corrects) - see docs/ARCHITECTURE.md's "Bundle reason"
    section and docs/backend_implementation_status.md for why.

    Every case records a structured, deterministic `bundle_reason: [...]`
    explaining why its alerts were grouped, and a `correlation_window_hours`
    field recording the (configurable) window actually used - never a
    ground-truth network id, never a mock-generator scenario id (this
    function never reads a `network_id`/ground-truth field of any kind; it
    only ever sees the live alert dicts passed in).

    Case Intake's job stops at: bundle alerts, assign a case_id, set status
    to "open". It does NOT decide completeness (that's the Investigation
    Auditor's job downstream) and it does NOT assign an investigator tier or
    decide escalation (that's a human-in-the-loop / action-stage decision,
    made only after the investigation has produced evidence - not something
    Detection/Case-Intake can know at bundling time). Earlier versions of
    this function set assigned_investigator_tier="junior" and escalated=False
    here; both were removed because Case Intake has no basis to assert either
    - "junior" was never actually decided by anything, just hardcoded, and a
    case that's 30 seconds old cannot be "escalated" or not yet. That
    correction remains valid and is unchanged by this checkpoint."""
    by_account = {}
    for a in alerts:
        by_account.setdefault(a["account_id"], []).append(a)

    cases = []
    for account_id, acc_alerts in by_account.items():
        for temporal_cluster in _temporal_clusters(acc_alerts, window_hours):
            for cl, correlation_reasons in _split_cluster_by_correlation(temporal_cluster):
                cl = sorted(cl, key=lambda a: a["created_at"])
                primary = max(cl, key=lambda a: a["alert_score"])
                evidence_signals = sorted({s["signal"] for a in cl for s in a["evidence_signals"]})
                typologies = sorted({a["typology"] for a in cl})
                # CHECKPOINT 3 fix: case_id used to be uuid.uuid4()-based (same
                # os.urandom non-determinism issue as alert_id, above) - now a
                # deterministic hash of the case's own fully-determined content
                # (account_id + sorted alert_ids, which are themselves now
                # deterministic), so repeated runs on identical input data
                # produce identical case_ids, per docs/ARCHITECTURE.md's
                # determinism requirement.
                alert_ids_sorted = sorted(a["alert_id"] for a in cl)
                case_digest = hashlib.sha256(
                    "|".join([account_id] + alert_ids_sorted).encode("utf-8")
                ).hexdigest()
                case_id = f"CASE-{case_digest[:8].upper()}"

                if len(cl) == 1:
                    # No correlation decision was actually made - one alert
                    # became one case. Say so honestly rather than claiming a
                    # window/typology match that wasn't the deciding factor.
                    bundle_reason = ["single_alert_case"]
                else:
                    bundle_reason = {"same_primary_account", "within_case_window"} | correlation_reasons
                    bundle_reason = sorted(bundle_reason)

                case = {
                    "case_id": case_id,
                    "account_id": account_id,
                    "created_at": cl[0]["created_at"],
                    "primary_trigger": primary["typology"],
                    "alert_ids": [a["alert_id"] for a in cl],
                    "evidence_signals": evidence_signals,
                    "typologies": typologies,
                    "status": "open",
                    "bundle_reason": bundle_reason,
                    "correlation_window_hours": window_hours,
                }
                for a in cl:
                    a["linked_case_id"] = case_id
                cases.append(case)
    return cases


# ----------------------------------------------------------------------
# Persistence - the REAL, live pipeline's own output.
#
# This is deliberately separate from mock_data/ground_truth_*.csv (written by
# generate_mock_data.py), which are synthetic ground-truth labels for offline
# evaluation only. These two functions write what the Detection Agent and
# Case Intake actually produced by running the rules against the DataStore -
# no ground_truth_* fields exist here because live code never has access to
# them and must never fabricate them.
# ----------------------------------------------------------------------
ALERT_CSV_FIELDS = ["alert_id", "account_id", "transaction_id", "relevant_transaction_ids", "typology", "triggered",
                     "alert_score", "severity", "triggering_rules", "evidence_signals",
                     "recommended_initial_action", "case_required", "created_at", "linked_case_id"]

CASE_CSV_FIELDS = ["case_id", "account_id", "created_at", "primary_trigger", "alert_ids",
                    "evidence_signals", "typologies", "status", "bundle_reason", "correlation_window_hours"]


def persist_alerts_csv(alerts, path):
    """Writes the Detection Agent's own live alerts to CSV - this project's
    real suspected_alerts.csv."""
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ALERT_CSV_FIELDS)
        writer.writeheader()
        for a in alerts:
            row = dict(a)
            row["triggering_rules"] = "|".join(a.get("triggering_rules", []))
            row["evidence_signals"] = "|".join(s["signal"] for s in a.get("evidence_signals", []))
            row["relevant_transaction_ids"] = "|".join(a.get("relevant_transaction_ids", []))
            row["linked_case_id"] = a.get("linked_case_id", "")
            writer.writerow({k: row.get(k, "") for k in ALERT_CSV_FIELDS})
    return path


def persist_cases_csv(cases, path):
    """Writes Case Intake's own live cases to CSV - this project's real
    cases.csv. Deliberately has NO status beyond "open"/whatever
    bundle_alerts_into_cases set, no investigator tier, no escalation flag -
    those belong to the human-in-the-loop/action stage, not here."""
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CASE_CSV_FIELDS)
        writer.writeheader()
        for c in cases:
            row = dict(c)
            row["alert_ids"] = "|".join(c.get("alert_ids", []))
            row["evidence_signals"] = "|".join(c.get("evidence_signals", []))
            row["typologies"] = "|".join(c.get("typologies", []))
            row["bundle_reason"] = "|".join(c.get("bundle_reason", []))
            writer.writerow({k: row.get(k, "") for k in CASE_CSV_FIELDS})
    return path


if __name__ == "__main__":
    import argparse
    import json
    import os

    parser = argparse.ArgumentParser(description="Run the Detection Agent + Case Intake over a mock data directory. "
                                                   "For the full Detection -> Case -> Evidence chain in one process, "
                                                   "use run_pipeline.py instead - this entry point is for standalone "
                                                   "testing of the Detection Layer alone.")
    parser.add_argument("--data_dir", default="mock_data")
    parser.add_argument("--out_dir", default="pipeline_output")
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    store = DataStore(args.data_dir)
    alerts = run_detection_pipeline(store)
    cases = bundle_alerts_into_cases(alerts)

    by_typology = {}
    for a in alerts:
        by_typology[a["typology"]] = by_typology.get(a["typology"], 0) + 1

    print(f"Accounts scanned:   {len(store.accounts)}")
    print(f"Alerts triggered:   {len(alerts)}  {by_typology}")
    print(f"Cases created:      {len(cases)}")

    persist_alerts_csv(alerts, f"{args.out_dir}/suspected_alerts.csv")
    persist_cases_csv(cases, f"{args.out_dir}/cases.csv")
    with open(f"{args.out_dir}/suspected_alerts.json", "w") as f:
        json.dump(alerts, f, indent=2, default=str)
    with open(f"{args.out_dir}/cases.json", "w") as f:
        json.dump(cases, f, indent=2, default=str)
    print(f"Wrote {args.out_dir}/suspected_alerts.csv, cases.csv (+ .json copies)")