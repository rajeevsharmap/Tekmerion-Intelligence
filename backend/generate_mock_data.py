"""
Autonomous Financial Crime Investigation Agent - Mock Data Generator
======================================================================
Generates interlinked CSV datasets (accounts, transactions, devices, geo_events,
beneficiaries, cases, case_escalations, suspected_alerts) with embedded fraud
typologies: smurfing, reverse smurfing, money mule, and account takeover /
device-SIM-swap patterns, plus a majority of clean/legitimate activity.

Run:  python3 generate_mock_data.py --outdir ./mock_data --num_cases 38
"""

import argparse
import csv
import os
import random
import uuid
from datetime import datetime, timedelta

from faker import Faker

fake = Faker("en_IN")
Faker.seed(42)
random.seed(42)

# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------
INDIAN_CITIES = [
    ("Bengaluru", "India", 12.9716, 77.5946),
    ("Mumbai", "India", 19.0760, 72.8777),
    ("Delhi", "India", 28.7041, 77.1025),
    ("Chennai", "India", 13.0827, 80.2707),
    ("Hyderabad", "India", 17.3850, 78.4867),
    ("Pune", "India", 18.5204, 73.8567),
    ("Kolkata", "India", 22.5726, 88.3639),
    ("Ahmedabad", "India", 23.0225, 72.5714),
    ("Jaipur", "India", 26.9124, 75.7873),
    ("Lucknow", "India", 26.8467, 80.9462),
]
FOREIGN_CITIES = [
    ("Dubai", "UAE", 25.2048, 55.2708),
    ("Singapore", "Singapore", 1.3521, 103.8198),
    ("London", "UK", 51.5072, -0.1276),
    ("Hong Kong", "Hong Kong", 22.3193, 114.1694),
    ("Lagos", "Nigeria", 6.5244, 3.3792),
]
BANKS = ["HDFC Bank", "ICICI Bank", "State Bank of India", "Axis Bank", "Kotak Mahindra Bank",
         "Yes Bank", "IndusInd Bank", "Punjab National Bank", "IDFC First Bank", "Union Bank of India"]
BRANCHES = [f"{c[0]} - {b} Branch" for c in INDIAN_CITIES for b in ["MG Road", "Central", "Sector 5", "Main"]]
OCCUPATIONS = ["Salaried - IT", "Salaried - Govt", "Self Employed - Trader", "Business Owner",
               "Student", "Freelancer", "Retired", "Homemaker", "Salaried - Finance", "Unemployed"]
DEVICE_TYPES = ["Android Phone", "iPhone", "Windows Laptop", "MacBook", "Tablet"]
OS_LIST = {"Android Phone": ["Android 13", "Android 14", "Android 15"],
           "iPhone": ["iOS 17", "iOS 18"],
           "Windows Laptop": ["Windows 10", "Windows 11"],
           "MacBook": ["macOS Sonoma", "macOS Sequoia"],
           "Tablet": ["Android 13", "iPadOS 17"]}
CHANNELS = ["mobile_app", "net_banking", "UPI", "ATM", "branch", "IMPS", "NEFT", "RTGS"]
TXN_TYPES = ["transfer", "bill_payment", "cash_withdrawal", "cash_deposit", "merchant_payment", "upi_p2p"]
RELATIONSHIPS = ["self", "family", "friend", "vendor", "employer", "unknown/first-time", "business_partner"]

INVESTIGATORS_JUNIOR = ["A. Sharma", "R. Verma", "S. Iyer", "P. Nair", "K. Das", "M. Reddy"]
INVESTIGATORS_SENIOR = ["N. Krishnan (Sr)", "T. Bose (Sr)", "V. Menon (Sr)", "D'Souza (Sr)"]

START_DATE = datetime(2025, 1, 1)
END_DATE = datetime(2025, 12, 31)


def rand_date(start=START_DATE, end=END_DATE):
    delta = end - start
    return start + timedelta(seconds=random.randint(0, int(delta.total_seconds())))


def new_id(prefix, n):
    return f"{prefix}{n:06d}"


# --------------------------------------------------------------------------
# Global registries (populated as we go, referenced across tables)
# --------------------------------------------------------------------------
class Registry:
    def __init__(self):
        self.accounts = []          # list of dict
        self.devices = []
        self.geo_events = []
        self.beneficiaries = []
        self.transactions = []
        self.suspected_alerts = []
        self.cases = []
        self.case_escalations = []
        self._counters = {}

    def next_id(self, prefix):
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return new_id(prefix, self._counters[prefix])


REG = Registry()


# --------------------------------------------------------------------------
# 1. ACCOUNTS
# --------------------------------------------------------------------------
def generate_accounts(n):
    accounts = []
    for i in range(n):
        acc_id = REG.next_id("ACC")
        open_date = rand_date(datetime(2015, 1, 1), datetime(2024, 12, 1))
        risk = random.choices(["low", "medium", "high"], weights=[0.65, 0.25, 0.10])[0]
        kyc = random.choices(["verified", "pending", "rejected"], weights=[0.85, 0.10, 0.05])[0]
        acc = {
            "account_id": acc_id,
            "customer_id": f"CUST{100000+i}",
            "customer_name": fake.name(),
            "account_type": random.choice(["savings", "current", "business", "salary"]),
            "account_open_date": open_date.strftime("%Y-%m-%d"),
            "kyc_status": kyc,
            "risk_rating": risk,
            "registered_country": "India",
            "avg_monthly_txn_count": random.randint(4, 60),
            "avg_monthly_txn_amount": round(random.uniform(5000, 250000), 2),
            "home_branch": random.choice(BRANCHES),
            "occupation": random.choice(OCCUPATIONS),
            "annual_income": round(random.uniform(180000, 4500000), 2),
        }
        accounts.append(acc)
    REG.accounts = accounts
    return accounts


# --------------------------------------------------------------------------
# 2. DEVICES
# --------------------------------------------------------------------------
def generate_devices(accounts, fraud_account_ids):
    devices = []
    device_map = {}  # account_id -> list of device_ids
    for acc in accounts:
        acc_id = acc["account_id"]
        num_devices = random.randint(1, 2)
        is_takeover_acc = acc_id in fraud_account_ids.get("account_swap", set())
        if is_takeover_acc:
            num_devices += 1  # extra device = the attacker's new device
        dev_ids = []
        for d in range(num_devices):
            dev_id = REG.next_id("DEV")
            dtype = random.choice(DEVICE_TYPES)
            first_seen = rand_date(datetime.strptime(acc["account_open_date"], "%Y-%m-%d"), END_DATE)
            # last device added for a takeover account is recent + untrusted
            is_new_attacker_device = is_takeover_acc and d == num_devices - 1
            if is_new_attacker_device:
                first_seen = rand_date(datetime(2025, 10, 1), END_DATE)
                trusted = False
                sim_change = True
                jailbroken = random.choice([True, True, False])
            else:
                trusted = True
                sim_change = False
                jailbroken = False
            last_seen = rand_date(first_seen, END_DATE) if first_seen < END_DATE else first_seen
            dev = {
                "device_id": dev_id,
                "account_id": acc_id,
                "device_type": dtype,
                "os": random.choice(OS_LIST[dtype]),
                "device_fingerprint": uuid.uuid4().hex[:16],
                "first_seen_date": first_seen.strftime("%Y-%m-%d"),
                "last_seen_date": last_seen.strftime("%Y-%m-%d"),
                "is_trusted_device": trusted,
                "sim_change_detected": sim_change,
                "jailbroken_rooted": jailbroken,
            }
            devices.append(dev)
            dev_ids.append(dev_id)
        device_map[acc_id] = dev_ids
    REG.devices = devices
    return devices, device_map


# --------------------------------------------------------------------------
# 3. GEO EVENTS
# --------------------------------------------------------------------------
def generate_geo_events(accounts, fraud_account_ids):
    geo_events = []
    geo_map = {}  # account_id -> list of geo_event_ids (chronological)
    for acc in accounts:
        acc_id = acc["account_id"]
        num_events = random.randint(2, 5)
        is_takeover = acc_id in fraud_account_ids.get("account_swap", set())
        events = []
        last_time = rand_date(datetime(2025, 1, 1), datetime(2025, 9, 1))
        home_city = random.choice(INDIAN_CITIES)
        for e in range(num_events):
            geid = REG.next_id("GEO")
            if is_takeover and e == num_events - 1:
                # sudden distant location jump within a few hours of previous event
                city, country, lat, lon = random.choice(FOREIGN_CITIES)
                ts = last_time + timedelta(hours=random.uniform(1, 5))
                is_vpn = random.choice([True, True, False])
                distance = round(random.uniform(2500, 9000), 1)
                match = False
            else:
                city, country, lat, lon = home_city
                ts = last_time + timedelta(days=random.uniform(1, 20))
                is_vpn = random.choice([False, False, False, True])
                distance = round(random.uniform(0, 25), 1)
                match = True
            geo = {
                "geo_event_id": geid,
                "account_id": acc_id,
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "ip_address": fake.ipv4_public(),
                "city": city,
                "country": country,
                "latitude": lat,
                "longitude": lon,
                "is_vpn_or_proxy": is_vpn,
                "distance_from_last_location_km": distance,
                "registered_country_match": match,
            }
            geo_events.append(geo)
            events.append((geid, ts))
            last_time = ts
        geo_map[acc_id] = events
    REG.geo_events = geo_events
    return geo_events, geo_map


# --------------------------------------------------------------------------
# 4. BENEFICIARIES
# --------------------------------------------------------------------------
def generate_beneficiaries(accounts):
    beneficiaries = []
    bene_map = {}  # account_id -> list of beneficiary_id
    for acc in accounts:
        acc_id = acc["account_id"]
        num_bene = random.randint(1, 4)
        ids = []
        for b in range(num_bene):
            bid = REG.next_id("BENE")
            first_time = random.choice([True, False, False])
            bene = {
                "beneficiary_id": bid,
                "account_id": acc_id,
                "beneficiary_name": fake.name(),
                "beneficiary_account_number": fake.bban(),
                "beneficiary_bank": random.choice(BANKS),
                "beneficiary_ifsc_swift": fake.swift(length=8),
                "relationship_to_account_holder": random.choice(RELATIONSHIPS),
                "date_added": rand_date(datetime.strptime(acc["account_open_date"], "%Y-%m-%d"), END_DATE).strftime("%Y-%m-%d"),
                "is_first_time_beneficiary": first_time,
                "is_verified": random.choice([True, True, True, False]),
                "beneficiary_risk_flag": random.choices(["none", "watchlist", "high_risk"], weights=[0.85, 0.10, 0.05])[0],
                "total_transfers_to_date": random.randint(0, 40),
            }
            beneficiaries.append(bene)
            ids.append(bid)
        bene_map[acc_id] = ids
    REG.beneficiaries = beneficiaries
    return beneficiaries, bene_map


# --------------------------------------------------------------------------
# Helper: create a single transaction record
# --------------------------------------------------------------------------
def make_txn(sender_id, receiver_id, ts, amount, ttype, channel, bene_id, device_id, geo_event_id,
             is_intl, currency="INR"):
    tid = REG.next_id("TXN")
    txn = {
        "transaction_id": tid,
        "sender_account_id": sender_id,
        "receiver_account_id": receiver_id,
        "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
        "amount": round(amount, 2),
        "currency": currency,
        "transaction_type": ttype,
        "channel": channel,
        "beneficiary_id": bene_id or "",
        "device_id": device_id or "",
        "geo_event_id": geo_event_id or "",
        "is_international": is_intl,
        "balance_after": round(random.uniform(1000, 500000), 2),
    }
    REG.transactions.append(txn)
    return txn


# --------------------------------------------------------------------------
# 5. NORMAL / LEGITIMATE TRANSACTIONS
# --------------------------------------------------------------------------
def generate_legit_transactions(accounts, device_map, geo_map, bene_map, n_per_account_range=(3, 10)):
    acc_ids = [a["account_id"] for a in accounts]
    for acc in accounts:
        acc_id = acc["account_id"]
        n = random.randint(*n_per_account_range)
        for _ in range(n):
            counterpart = random.choice(acc_ids)
            if counterpart == acc_id:
                continue
            ts = rand_date()
            amount = round(random.uniform(200, min(acc["avg_monthly_txn_amount"] * 0.6, 60000)), 2)
            ttype = random.choice(TXN_TYPES)
            channel = random.choice(CHANNELS)
            dev = random.choice(device_map[acc_id]) if device_map.get(acc_id) else None
            geo = random.choice(geo_map[acc_id])[0] if geo_map.get(acc_id) else None
            bene = random.choice(bene_map[acc_id]) if bene_map.get(acc_id) and random.random() < 0.5 else None
            make_txn(acc_id, counterpart, ts, amount, ttype, channel, bene, dev, geo, is_intl=False)


# --------------------------------------------------------------------------
# 6. FRAUD TYPOLOGY GENERATORS
# --------------------------------------------------------------------------
def pick_fraud_accounts(accounts, k):
    """Pick k accounts, biased toward higher-risk-rating ones, without replacement."""
    pool = accounts[:]
    random.shuffle(pool)
    return pool[:k]


def get_device_geo_bene(acc_id, device_map, geo_map, bene_map):
    dev = random.choice(device_map[acc_id]) if device_map.get(acc_id) else None
    geo = random.choice(geo_map[acc_id])[0] if geo_map.get(acc_id) else None
    bene = random.choice(bene_map[acc_id]) if bene_map.get(acc_id) else None
    return dev, geo, bene


def generate_smurfing_network(accounts, device_map, geo_map, bene_map, num_networks=5):
    """Fan-in structuring: many small senders -> collector -> hop2 -> hop3 -> final destination.
    Depth 3 chain after the aggregation point, as required for graph visualization."""
    networks = []
    acc_pool = accounts[:]
    random.shuffle(acc_pool)
    idx = 0
    for _ in range(num_networks):
        collector = acc_pool[idx]; idx += 1
        hop2 = acc_pool[idx]; idx += 1
        hop3 = acc_pool[idx]; idx += 1
        final_dest = acc_pool[idx]; idx += 1
        senders = acc_pool[idx: idx + random.randint(6, 10)]
        idx += len(senders)

        base_time = rand_date(datetime(2025, 2, 1), datetime(2025, 11, 1))
        involved_txns = []
        # many small senders -> collector, each below reporting threshold (structuring)
        for s in senders:
            ts = base_time + timedelta(minutes=random.randint(0, 600))
            amount = round(random.uniform(38000, 49500), 2)  # just under 50k threshold
            dev, geo, bene = get_device_geo_bene(s["account_id"], device_map, geo_map, bene_map)
            t = make_txn(s["account_id"], collector["account_id"], ts, amount, "cash_deposit",
                         random.choice(["branch", "UPI", "IMPS"]), bene, dev, geo, is_intl=False)
            involved_txns.append(t["transaction_id"])

        # collector -> hop2 -> hop3 -> final_dest (level 3 chain), consolidated then moved on quickly
        chain_accounts = [collector, hop2, hop3, final_dest]
        chain_time = base_time + timedelta(hours=2)
        total_collected = sum(random.uniform(38000, 49500) for _ in senders)
        running_amount = total_collected
        for i in range(len(chain_accounts) - 1):
            src, dst = chain_accounts[i], chain_accounts[i + 1]
            chain_time += timedelta(hours=random.uniform(1, 6))
            leg_amount = running_amount * random.uniform(0.85, 0.97)
            dev, geo, bene = get_device_geo_bene(src["account_id"], device_map, geo_map, bene_map)
            t = make_txn(src["account_id"], dst["account_id"], chain_time, leg_amount, "transfer",
                         random.choice(["net_banking", "IMPS", "NEFT"]), bene, dev, geo, is_intl=False)
            involved_txns.append(t["transaction_id"])
            running_amount = leg_amount

        networks.append({
            "typology": "smurfing",
            "primary_account": collector["account_id"],
            "network_accounts": [a["account_id"] for a in chain_accounts] + [s["account_id"] for s in senders],
            "chain": [a["account_id"] for a in chain_accounts],
            "transactions": involved_txns,
        })
    return networks


def generate_reverse_smurfing_network(accounts, device_map, geo_map, bene_map, num_networks=4):
    """Fan-out structuring: one account receives a lump sum then disperses it downward
    through a level-3 chain of accounts, each fanning out to further sub-accounts."""
    networks = []
    acc_pool = accounts[:]
    random.shuffle(acc_pool)
    idx = 0
    for _ in range(num_networks):
        source = acc_pool[idx]; idx += 1
        # level1 needs >= 3 direct fan-out targets so the source account itself
        # trips the "unique_outbound_receivers >= 3 within 24h" structuring signal
        level1 = acc_pool[idx: idx + 3]; idx += 3
        level2 = acc_pool[idx: idx + 6]; idx += 6
        level3 = acc_pool[idx: idx + 6]; idx += 6

        base_time = rand_date(datetime(2025, 2, 1), datetime(2025, 11, 1))
        involved_txns = []
        lump_sum = round(random.uniform(400000, 900000), 2)

        # lump sum arrives at source (e.g. from an overseas scam proceeds account)
        dev, geo, bene = get_device_geo_bene(source["account_id"], device_map, geo_map, bene_map)
        t = make_txn("EXT_UNKNOWN", source["account_id"], base_time, lump_sum, "transfer",
                     "net_banking", bene, dev, geo, is_intl=True, currency="USD")
        involved_txns.append(t["transaction_id"])

        # source -> level1 (fan out, level 1)
        remaining = lump_sum
        t_time = base_time
        for l1 in level1:
            t_time += timedelta(hours=random.uniform(0.5, 3))
            amt = round(remaining / len(level1) * random.uniform(0.9, 1.0), 2)
            dev, geo, bene = get_device_geo_bene(source["account_id"], device_map, geo_map, bene_map)
            t = make_txn(source["account_id"], l1["account_id"], t_time, amt, "transfer",
                         random.choice(["IMPS", "net_banking"]), bene, dev, geo, is_intl=False)
            involved_txns.append(t["transaction_id"])

        # level1 -> level2 (fan out further, level 2), amounts under reporting threshold
        for i, l1 in enumerate(level1):
            targets = level2[i * 2:(i + 1) * 2]
            for l2 in targets:
                t_time += timedelta(hours=random.uniform(0.5, 4))
                amt = round(random.uniform(38000, 49500), 2)
                dev, geo, bene = get_device_geo_bene(l1["account_id"], device_map, geo_map, bene_map)
                t = make_txn(l1["account_id"], l2["account_id"], t_time, amt, "transfer",
                             random.choice(["UPI", "IMPS"]), bene, dev, geo, is_intl=False)
                involved_txns.append(t["transaction_id"])

        # level2 -> level3 (final dispersal, level 3), often cashed out
        for i, l2 in enumerate(level2):
            targets = level3[i * 1:(i + 1) * 1] if i < len(level3) else []
            for l3 in targets:
                t_time += timedelta(hours=random.uniform(0.5, 5))
                amt = round(random.uniform(15000, 42000), 2)
                dev, geo, bene = get_device_geo_bene(l2["account_id"], device_map, geo_map, bene_map)
                t = make_txn(l2["account_id"], l3["account_id"], t_time, amt, "cash_withdrawal",
                             random.choice(["ATM", "branch"]), bene, dev, geo, is_intl=False)
                involved_txns.append(t["transaction_id"])

        networks.append({
            "typology": "reverse_smurfing",
            "primary_account": source["account_id"],
            "network_accounts": [source["account_id"]] + [a["account_id"] for a in level1 + level2 + level3],
            "chain": [source["account_id"]] + [a["account_id"] for a in level1],
            "transactions": involved_txns,
        })
    return networks


def generate_mule_pattern(accounts, device_map, geo_map, bene_map, num_mules=5):
    """Money mule: account receives funds from several unrelated senders, then rapidly
    (within hours) forwards the bulk of it onward - high velocity pass-through."""
    networks = []
    acc_pool = accounts[:]
    random.shuffle(acc_pool)
    idx = 0
    for _ in range(num_mules):
        mule = acc_pool[idx]; idx += 1
        senders = acc_pool[idx: idx + random.randint(4, 7)]; idx += len(senders)
        forward_dest = acc_pool[idx]; idx += 1

        base_time = rand_date(datetime(2025, 2, 1), datetime(2025, 11, 1))
        involved_txns = []
        total_in = 0
        for s in senders:
            ts = base_time + timedelta(minutes=random.randint(0, 180))
            amount = round(random.uniform(20000, 150000), 2)
            total_in += amount
            dev, geo, bene = get_device_geo_bene(s["account_id"], device_map, geo_map, bene_map)
            t = make_txn(s["account_id"], mule["account_id"], ts, amount, "transfer",
                         random.choice(["UPI", "IMPS", "net_banking"]), bene, dev, geo, is_intl=False)
            involved_txns.append(t["transaction_id"])

        # rapid pass-through, within a few hours of first inbound funds, ~90% forwarded
        out_time = base_time + timedelta(hours=random.uniform(2, 8))
        dev, geo, bene = get_device_geo_bene(mule["account_id"], device_map, geo_map, bene_map)
        t = make_txn(mule["account_id"], forward_dest["account_id"], out_time, total_in * 0.9,
                     "transfer", "net_banking", bene, dev, geo, is_intl=random.choice([False, True]))
        involved_txns.append(t["transaction_id"])

        networks.append({
            "typology": "money_mule",
            "primary_account": mule["account_id"],
            "network_accounts": [mule["account_id"]] + [s["account_id"] for s in senders] + [forward_dest["account_id"]],
            "chain": [mule["account_id"], forward_dest["account_id"]],
            "transactions": involved_txns,
        })
    return networks


def generate_account_swap(accounts, device_map, geo_map, bene_map, takeover_account_ids):
    """Account swap / takeover: SIM change + new untrusted device + large distant
    location jump within a few hours + unusually high-value transfer to a new/first-time
    beneficiary shortly after.

    IMPORTANT: this must operate on the exact same accounts whose devices/geo_events
    were pre-seeded with the attacker artefacts in generate_devices/generate_geo_events
    (takeover_account_ids) - picking a fresh random subset here would decouple the
    device/geo mutation from the account this function drains, which is exactly the
    bug that silently produced zero detectable account_swap cases the first time round.

    Timing is deliberately tightened here (not left to the independent random draws
    used elsewhere) so the device / geo / transaction events actually cluster within
    the few-hour compound window a real account-takeover detector looks for."""
    device_by_id = {d["device_id"]: d for d in REG.devices}
    geo_by_id = {g["geo_event_id"]: g for g in REG.geo_events}
    accounts_by_id = {a["account_id"]: a for a in accounts}

    networks = []
    acc_pool = accounts[:]
    random.shuffle(acc_pool)
    for acc_id in takeover_account_ids:
        victim = accounts_by_id[acc_id]
        # use the newest (attacker) device and the last (foreign) geo event that were
        # pre-seeded for this account in generate_devices/generate_geo_events
        attacker_device_id = device_map[acc_id][-1]
        attacker_geo_id, _ = geo_map[acc_id][-1]
        device_rec = device_by_id[attacker_device_id]
        geo_rec = geo_by_id[attacker_geo_id]
        bene_ids = bene_map.get(acc_id, [])
        # force a first-time / unverified beneficiary to receive the drained funds
        drain_bene = bene_ids[-1] if bene_ids else None

        attack_time = rand_date(datetime(2025, 3, 1), datetime(2025, 11, 1))
        # new device first seen shortly before the attack; sim change rides on it
        device_first_seen = attack_time - timedelta(hours=random.uniform(0.5, 3))
        device_rec["first_seen_date"] = device_first_seen.strftime("%Y-%m-%d %H:%M:%S")
        device_rec["last_seen_date"] = attack_time.strftime("%Y-%m-%d %H:%M:%S")
        # the impossible-travel geo event lands shortly before the drain transaction
        geo_rec["timestamp"] = (attack_time - timedelta(minutes=random.randint(15, 90))).strftime("%Y-%m-%d %H:%M:%S")

        amount = round(random.uniform(150000, 900000), 2)
        drain_target = random.choice(acc_pool)["account_id"]
        t = make_txn(acc_id, drain_target, attack_time, amount, "transfer", "mobile_app",
                     drain_bene, attacker_device_id, attacker_geo_id, is_intl=random.choice([False, True]))
        networks.append({
            "typology": "account_swap",
            "primary_account": acc_id,
            "network_accounts": [acc_id, drain_target],
            "chain": [acc_id, drain_target],
            "transactions": [t["transaction_id"]],
        })
    return networks


# --------------------------------------------------------------------------
# 7. SUSPECTED ALERTS  (detection layer output)
# --------------------------------------------------------------------------
ALERT_RULES = {
    "smurfing": [("structuring_below_threshold", 55, 80), ("high_velocity_fan_in", 60, 85)],
    "reverse_smurfing": [("structuring_fan_out", 55, 80), ("rapid_dispersal", 60, 88)],
    "money_mule": [("pass_through_velocity", 65, 90), ("unrelated_sender_concentration", 55, 80)],
    "account_swap": [("sim_swap_detected", 75, 97), ("geo_impossible_travel", 70, 95),
                          ("untrusted_device_high_value_txn", 70, 95)],
}


def generate_suspected_alerts_for_network(net):
    alerts = []
    rules = ALERT_RULES[net["typology"]]
    acc_id = net["primary_account"]
    for txn_id in net["transactions"][:6]:  # cap alerts per network to keep volume sane
        rule, lo, hi = random.choice(rules)
        aid = REG.next_id("ALRT")
        alert = {
            "alert_id": aid,
            "account_id": acc_id,
            "transaction_id": txn_id,
            "alert_type": net["typology"],
            "triggering_rule": rule,
            "alert_score": random.randint(lo, hi),
            "created_at": rand_date(datetime(2025, 2, 1), END_DATE).strftime("%Y-%m-%d %H:%M:%S"),
            "linked_case_id": "",  # filled in once cases are built
        }
        alerts.append(alert)
        REG.suspected_alerts.append(alert)
    return alerts


def generate_background_alerts(accounts, n=15):
    """A handful of low-score alerts on otherwise-legit accounts that resolve to 'clear'."""
    alerts = []
    legit_txns = [t for t in REG.transactions if t["transaction_type"] in TXN_TYPES]
    for _ in range(n):
        if not legit_txns:
            break
        t = random.choice(legit_txns)
        aid = REG.next_id("ALRT")
        alert = {
            "alert_id": aid,
            "account_id": t["sender_account_id"] if t["sender_account_id"] in [a["account_id"] for a in accounts] else t["receiver_account_id"],
            "transaction_id": t["transaction_id"],
            "alert_type": "behavioral_deviation",
            "triggering_rule": random.choice(["amount_above_avg", "new_channel_used", "off_hours_txn"]),
            "alert_score": random.randint(20, 45),
            "created_at": rand_date(datetime(2025, 2, 1), END_DATE).strftime("%Y-%m-%d %H:%M:%S"),
            "linked_case_id": "",
        }
        alerts.append(alert)
        REG.suspected_alerts.append(alert)
    return alerts


# --------------------------------------------------------------------------
# 8. CASES + CASE ESCALATIONS
# --------------------------------------------------------------------------
def build_case_from_network(net, alerts):
    case_id = REG.next_id("CASE")
    acc_id = net["primary_account"]
    typology = net["typology"]
    evidence_signals = sorted(set(a["triggering_rule"] for a in alerts))
    primary_trigger = typology  # matches the spec's Case JSON (primary_trigger="smurfing", not a rule id)

    # completeness score: takeover/high-severity networks tend to gather fuller evidence faster
    base_score = {"account_swap": 78, "money_mule": 70, "smurfing": 65, "reverse_smurfing": 62}[typology]
    completeness = min(99, max(35, int(random.gauss(base_score, 12))))

    # ground truth: majority of flagged networks are fraud, but include some legitimate
    # look-alikes (e.g. legitimate high-value family transfer, legitimate business bulk payroll)
    is_actually_fraud = random.random() < 0.78
    ground_truth_label = "fraud" if is_actually_fraud else "legitimate"

    if not is_actually_fraud:
        ground_truth_required_tier = random.choice(["junior", "junior", "senior"])
        ground_truth_recommended_action = random.choice(["clear", "monitor"])
    else:
        if typology == "account_swap" or completeness < 55:
            ground_truth_required_tier = "senior"
        else:
            ground_truth_required_tier = random.choice(["junior", "senior"])
        ground_truth_recommended_action = "block" if typology in ("account_swap", "money_mule") or completeness > 80 else \
            random.choice(["block", "escalate", "monitor"])

    escalated = ground_truth_required_tier == "senior" or completeness < 55
    assigned_tier = "junior"  # junior always sees it first per workflow
    status = "open"
    if escalated:
        status = "escalated"
    if ground_truth_recommended_action == "monitor" and not escalated:
        status = "monitor"
    if ground_truth_recommended_action == "block" and escalated:
        status = "block" if random.random() < 0.6 else "escalated"

    case = {
        "case_id": case_id,
        "account_id": acc_id,
        "created_at": rand_date(datetime(2025, 2, 1), END_DATE).strftime("%Y-%m-%d %H:%M:%S"),
        "primary_trigger": primary_trigger,
        "evidence_signals": "|".join(evidence_signals),
        "completeness_score": completeness,
        "assigned_investigator_tier": "senior" if escalated else "junior",
        "escalated": escalated,
        "status": status,
        "ground_truth_label": ground_truth_label,
        "ground_truth_required_tier": ground_truth_required_tier,
        "ground_truth_recommended_action": ground_truth_recommended_action,
    }
    REG.cases.append(case)
    for a in alerts:
        a["linked_case_id"] = case_id

    if escalated:
        esc_id = REG.next_id("ESC")
        gap_options = {
            "account_swap": "pending confirmation from telecom SIM-swap registry",
            "money_mule": "beneficiary network ownership unverified beyond hop 2",
            "smurfing": "source-of-funds documentation incomplete for structuring senders",
            "reverse_smurfing": "final-tier cash-out accounts not yet KYC-linked",
        }
        escalation = {
            "escalation_id": esc_id,
            "case_id": case_id,
            "evidence_gap": gap_options[typology],
            "completeness_score_at_escalation": completeness,
            "primary_trigger": primary_trigger,
            "evidence_signals": "|".join(evidence_signals),
            "escalated_at": (datetime.strptime(case["created_at"], "%Y-%m-%d %H:%M:%S") + timedelta(hours=random.randint(2, 48))).strftime("%Y-%m-%d %H:%M:%S"),
            "escalated_by": random.choice(INVESTIGATORS_JUNIOR),
            "ground_truth_label": ground_truth_label,
            "ground_truth_recommended_action": ground_truth_recommended_action,
            "status": status,
        }
        REG.case_escalations.append(escalation)
    return case


def build_case_from_background_alert(alert, accounts_by_id):
    """Simple single-alert legit-leaning case handled entirely by a junior investigator."""
    case_id = REG.next_id("CASE")
    completeness = random.randint(60, 95)
    ground_truth_label = random.choices(["legitimate", "fraud"], weights=[0.85, 0.15])[0]
    action = "clear" if ground_truth_label == "legitimate" else random.choice(["monitor", "escalate"])
    escalated = action == "escalate" or completeness < 55
    status = "escalated" if escalated else ("monitor" if action == "monitor" else "open")
    case = {
        "case_id": case_id,
        "account_id": alert["account_id"],
        "created_at": alert["created_at"],
        "primary_trigger": "behavioral_deviation",
        "evidence_signals": alert["triggering_rule"],
        "completeness_score": completeness,
        "assigned_investigator_tier": "senior" if escalated else "junior",
        "escalated": escalated,
        "status": status,
        "ground_truth_label": ground_truth_label,
        "ground_truth_required_tier": "senior" if escalated else "junior",
        "ground_truth_recommended_action": action,
    }
    REG.cases.append(case)
    alert["linked_case_id"] = case_id
    if escalated:
        esc_id = REG.next_id("ESC")
        REG.case_escalations.append({
            "escalation_id": esc_id,
            "case_id": case_id,
            "evidence_gap": "additional transaction history required for full pattern confirmation",
            "completeness_score_at_escalation": completeness,
            "primary_trigger": "behavioral_deviation",
            "evidence_signals": alert["triggering_rule"],
            "escalated_at": (datetime.strptime(alert["created_at"], "%Y-%m-%d %H:%M:%S") + timedelta(hours=random.randint(2, 48))).strftime("%Y-%m-%d %H:%M:%S"),
            "escalated_by": random.choice(INVESTIGATORS_JUNIOR),
            "ground_truth_label": ground_truth_label,
            "ground_truth_recommended_action": action,
            "status": status,
        })
    return case


# --------------------------------------------------------------------------
# CSV WRITERS
# --------------------------------------------------------------------------
FIELDNAMES = {
    "accounts": ["account_id", "customer_id", "customer_name", "account_type", "account_open_date",
                 "kyc_status", "risk_rating", "registered_country", "avg_monthly_txn_count",
                 "avg_monthly_txn_amount", "home_branch", "occupation", "annual_income"],
    "transactions": ["transaction_id", "sender_account_id", "receiver_account_id", "timestamp", "amount",
                      "currency", "transaction_type", "channel", "beneficiary_id", "device_id",
                      "geo_event_id", "is_international", "balance_after"],
    "devices": ["device_id", "account_id", "device_type", "os", "device_fingerprint", "first_seen_date",
                "last_seen_date", "is_trusted_device", "sim_change_detected", "jailbroken_rooted"],
    "geo_events": ["geo_event_id", "account_id", "timestamp", "ip_address", "city", "country", "latitude",
                   "longitude", "is_vpn_or_proxy", "distance_from_last_location_km", "registered_country_match"],
    "beneficiaries": ["beneficiary_id", "account_id", "beneficiary_name", "beneficiary_account_number",
                       "beneficiary_bank", "beneficiary_ifsc_swift", "relationship_to_account_holder",
                       "date_added", "is_first_time_beneficiary", "is_verified", "beneficiary_risk_flag",
                       "total_transfers_to_date"],
    "cases": ["case_id", "account_id", "created_at", "primary_trigger", "evidence_signals",
              "completeness_score", "assigned_investigator_tier", "escalated", "status",
              "ground_truth_label", "ground_truth_required_tier", "ground_truth_recommended_action"],
    "case_escalations": ["escalation_id", "case_id", "evidence_gap", "completeness_score_at_escalation",
                          "primary_trigger", "evidence_signals", "escalated_at", "escalated_by",
                          "ground_truth_label", "ground_truth_recommended_action", "status"],
    "suspected_alerts": ["alert_id", "account_id", "transaction_id", "alert_type", "triggering_rule",
                          "alert_score", "created_at", "linked_case_id"],
}


def write_csv(rows, table_name, outdir):
    path = os.path.join(outdir, f"{table_name}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES[table_name])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    return path


# --------------------------------------------------------------------------
# MAIN ORCHESTRATION
# --------------------------------------------------------------------------
def generate_all(outdir="./mock_data", num_accounts=220, num_cases_target=38):
    os.makedirs(outdir, exist_ok=True)

    # 1. accounts
    accounts = generate_accounts(num_accounts)

    # decide which accounts will be victims of account-takeover ahead of time so
    # devices/geo_events can be pre-seeded with the attacker artefacts
    shuffled = accounts[:]
    random.shuffle(shuffled)
    takeover_accounts = {a["account_id"] for a in shuffled[:8]}
    fraud_account_ids = {"account_swap": takeover_accounts}

    # 2-4. devices, geo_events, beneficiaries (depend on accounts)
    devices, device_map = generate_devices(accounts, fraud_account_ids)
    geo_events, geo_map = generate_geo_events(accounts, fraud_account_ids)
    beneficiaries, bene_map = generate_beneficiaries(accounts)

    # 5. legit background transaction traffic
    generate_legit_transactions(accounts, device_map, geo_map, bene_map)

    # 6. fraud typology networks
    networks = []
    networks += generate_smurfing_network(accounts, device_map, geo_map, bene_map, num_networks=6)
    networks += generate_reverse_smurfing_network(accounts, device_map, geo_map, bene_map, num_networks=5)
    networks += generate_mule_pattern(accounts, device_map, geo_map, bene_map, num_mules=6)
    networks += generate_account_swap(accounts, device_map, geo_map, bene_map, takeover_account_ids=sorted(takeover_accounts))
    random.shuffle(networks)

    # 7. suspected alerts (detection layer) + 8. cases per network
    accounts_by_id = {a["account_id"]: a for a in accounts}
    for net in networks:
        alerts = generate_suspected_alerts_for_network(net)
        build_case_from_network(net, alerts)

    # top up with simple background/legit-leaning cases until we hit the target case count
    bg_alerts = generate_background_alerts(accounts, n=max(0, (num_cases_target - len(REG.cases)) + 5))
    for alert in bg_alerts:
        if len(REG.cases) >= num_cases_target:
            break
        build_case_from_background_alert(alert, accounts_by_id)

    # 9. write everything out
    paths = {}
    paths["accounts"] = write_csv(accounts, "accounts", outdir)
    paths["transactions"] = write_csv(REG.transactions, "transactions", outdir)
    paths["devices"] = write_csv(devices, "devices", outdir)
    paths["geo_events"] = write_csv(REG.geo_events, "geo_events", outdir)
    paths["beneficiaries"] = write_csv(beneficiaries, "beneficiaries", outdir)
    paths["cases"] = write_csv(REG.cases, "cases", outdir)
    paths["case_escalations"] = write_csv(REG.case_escalations, "case_escalations", outdir)
    paths["suspected_alerts"] = write_csv(REG.suspected_alerts, "suspected_alerts", outdir)

    print(f"Accounts:          {len(accounts)}")
    print(f"Devices:           {len(devices)}")
    print(f"Geo events:        {len(REG.geo_events)}")
    print(f"Beneficiaries:     {len(beneficiaries)}")
    print(f"Transactions:      {len(REG.transactions)}")
    print(f"Suspected alerts:  {len(REG.suspected_alerts)}")
    print(f"Cases:             {len(REG.cases)}")
    print(f"Case escalations:  {len(REG.case_escalations)}")
    print(f"Fraud networks:    {len(networks)}  "
          f"(smurfing / reverse_smurfing / money_mule / account_takeover)")
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate mock CSV data for the financial crime investigation hackathon.")
    parser.add_argument("--outdir", default="./mock_data")
    parser.add_argument("--num_accounts", type=int, default=220)
    parser.add_argument("--num_cases", type=int, default=38)
    args = parser.parse_args()
    generate_all(outdir=args.outdir, num_accounts=args.num_accounts, num_cases_target=args.num_cases)