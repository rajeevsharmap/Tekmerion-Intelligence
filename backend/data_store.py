"""
data_store.py
==============
Lightweight, dependency-free CSV loader + in-memory indices shared by the
Detection Layer and the Network Evidence Layer. Neither layer talks to the
bank database directly in this MVP - both read through this one DataStore,
which mirrors the "Bank System -> Detection Agent" data feed in the
architecture diagram.

Not a layer in its own right - just the common plumbing so
detection_layer.py and network_layer.py stay focused on rules and
graph/timeline logic respectively.
"""

import csv
import os
from collections import defaultdict
from datetime import datetime


def _parse_bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes")


def _parse_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_dt(v):
    if isinstance(v, datetime):
        return v
    v = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized timestamp format: {v!r}")


def _read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class DataStore:
    """Loads accounts/transactions/devices/geo_events/beneficiaries from a
    directory of CSVs (matching the schema in the hackathon spec) and builds
    the indices both layers need. All timestamps are parsed to datetime,
    numeric/bool fields coerced, so callers never touch raw strings.
    """

    def __init__(self, data_dir):
        self.data_dir = data_dir

        self.accounts = self._load_accounts()
        self.transactions = self._load_transactions()
        self.devices = self._load_devices()
        self.geo_events = self._load_geo_events()
        self.beneficiaries = self._load_beneficiaries()

        self._build_indices()

    # -- loaders ------------------------------------------------------
    def _load_accounts(self):
        rows = _read_csv(os.path.join(self.data_dir, "accounts.csv"))
        for r in rows:
            r["avg_monthly_txn_count"] = int(_parse_float(r.get("avg_monthly_txn_count")))
            r["avg_monthly_txn_amount"] = _parse_float(r.get("avg_monthly_txn_amount"))
            r["annual_income"] = _parse_float(r.get("annual_income"))
        return rows

    def _load_transactions(self):
        rows = _read_csv(os.path.join(self.data_dir, "transactions.csv"))
        for r in rows:
            r["timestamp"] = _parse_dt(r["timestamp"])
            r["amount"] = _parse_float(r.get("amount"))
            r["balance_after"] = _parse_float(r.get("balance_after"))
            r["is_international"] = _parse_bool(r.get("is_international"))
        return rows

    def _load_devices(self):
        rows = _read_csv(os.path.join(self.data_dir, "devices.csv"))
        for r in rows:
            r["first_seen_date"] = _parse_dt(r["first_seen_date"])
            r["last_seen_date"] = _parse_dt(r["last_seen_date"])
            r["is_trusted_device"] = _parse_bool(r.get("is_trusted_device"))
            r["sim_change_detected"] = _parse_bool(r.get("sim_change_detected"))
            r["jailbroken_rooted"] = _parse_bool(r.get("jailbroken_rooted"))
        return rows

    def _load_geo_events(self):
        rows = _read_csv(os.path.join(self.data_dir, "geo_events.csv"))
        for r in rows:
            r["timestamp"] = _parse_dt(r["timestamp"])
            r["latitude"] = _parse_float(r.get("latitude"))
            r["longitude"] = _parse_float(r.get("longitude"))
            r["is_vpn_or_proxy"] = _parse_bool(r.get("is_vpn_or_proxy"))
            r["distance_from_last_location_km"] = _parse_float(r.get("distance_from_last_location_km"))
            r["registered_country_match"] = _parse_bool(r.get("registered_country_match"))
        return rows

    def _load_beneficiaries(self):
        rows = _read_csv(os.path.join(self.data_dir, "beneficiaries.csv"))
        for r in rows:
            r["is_first_time_beneficiary"] = _parse_bool(r.get("is_first_time_beneficiary"))
            r["is_verified"] = _parse_bool(r.get("is_verified"))
            r["total_transfers_to_date"] = int(_parse_float(r.get("total_transfers_to_date")))
        return rows

    # -- indices --------------------------------------------------------
    def _build_indices(self):
        self.accounts_by_id = {a["account_id"]: a for a in self.accounts}

        self.inbound_by_account = defaultdict(list)   # account_id -> txns received
        self.outbound_by_account = defaultdict(list)  # account_id -> txns sent
        self.txns_by_account = defaultdict(list)       # account_id -> all txns touching it
        self.txn_by_id = {}
        for t in self.transactions:
            self.txn_by_id[t["transaction_id"]] = t
            self.outbound_by_account[t["sender_account_id"]].append(t)
            self.inbound_by_account[t["receiver_account_id"]].append(t)
            self.txns_by_account[t["sender_account_id"]].append(t)
            self.txns_by_account[t["receiver_account_id"]].append(t)
        for idx in (self.inbound_by_account, self.outbound_by_account, self.txns_by_account):
            for k in idx:
                idx[k].sort(key=lambda x: x["timestamp"])

        self.devices_by_account = defaultdict(list)
        for d in self.devices:
            self.devices_by_account[d["account_id"]].append(d)
        for k in self.devices_by_account:
            self.devices_by_account[k].sort(key=lambda x: x["first_seen_date"])

        self.geo_by_account = defaultdict(list)
        for g in self.geo_events:
            self.geo_by_account[g["account_id"]].append(g)
        for k in self.geo_by_account:
            self.geo_by_account[k].sort(key=lambda x: x["timestamp"])

        self.bene_by_account = defaultdict(list)
        self.bene_by_id = {}
        for b in self.beneficiaries:
            self.bene_by_account[b["account_id"]].append(b)
            self.bene_by_id[b["beneficiary_id"]] = b

    # -- convenience ------------------------------------------------------
    def account_ids(self):
        return [a["account_id"] for a in self.accounts]