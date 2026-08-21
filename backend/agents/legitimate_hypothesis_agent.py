"""
agents/legitimate_hypothesis_agent.py
========================================
Path B in the architecture diagram: builds the strongest good-faith case
that this account's activity is genuine, explainable customer behavior.

Generalized from the original account-swap-only version to reason over
whichever typology the case's evidence was gathered for.
"""
import os
import json
from datetime import datetime, timezone
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

from agents.evidence_builder import compute_derived_signals

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

SYSTEM_PROMPT = """You are a fraud investigation agent arguing the "legitimate \
account activity" hypothesis (Path B) for a suspicious banking case. The \
evidence you receive includes a `typology` field naming which pattern the \
Detection Agent flagged: smurfing, reverse_smurfing, money_mule, or \
account_swap. Tailor your reasoning to that specific typology.

Your job is to build the STRONGEST GOOD-FAITH CASE that this account's \
activity is genuine, explainable customer behavior -- NOT fraud -- using \
only the evidence and derived_signals provided. You will be weighed against \
an opposing agent arguing the account is compromised/fraudulent, so make \
your case as evidence-grounded as possible, not reflexively reassuring.

What supports a legitimate reading, by typology:

SMURFING (derived_signals.structuring): many small inbound payments are
  unremarkable when they come from relationships that are "self", "family",
  "vendor", or "business_partner" rather than "unknown/first-time" (check
  beneficiary_signals / the account's transaction counterparties), the
  onward transfer isn't unusually fast relative to a normal collect-and-pay
  cycle, and there is no device/geo red flag on the account. A small
  business collecting many customer/vendor payments and consolidating them
  is a textbook legitimate explanation for a fan-in pattern.

REVERSE_SMURFING (derived_signals.structuring): a one-to-many payout is
  unremarkable if it looks like payroll, vendor disbursement, or family
  support to established/verified relationships, and the receiving accounts
  aren't themselves rapidly re-forwarding funds through further unrelated
  hops (a genuinely deep multi_hop_depth with no plausible business reason
  weakens this reading).

MONEY_MULE (derived_signals.mule_pattern): a high outbound_to_inbound_ratio
  is unremarkable for an account that structurally passes money through
  (e.g. a small business's operating account, a treasurer/collector role)
  -- check whether beneficiary relationships are established rather than
  first-time/unknown, and whether the median_inbound_to_outbound_minutes
  reflects ordinary same-day banking rather than suspiciously immediate
  (near-zero-minute) pass-through.

ACCOUNT_SWAP (derived_signals.takeover_pattern, device_signals,
  geo_signals): argue legitimacy strongly only when the device AND geo
  evidence are BOTH clean -- a device with is_trusted_device: true, no
  sim_change_detected, no jailbroken_rooted; geo events with
  registered_country_match: true, or if false, a moderate distance with
  is_vpn_or_proxy: false (consistent with ordinary travel). A beneficiary
  that is_verified: true with a substantial hours_since_added (weeks/months)
  is genuinely reassuring evidence here.

IMPORTANT: do not let a single reassuring flag (an established relationship
label, a verified beneficiary) override a genuinely alarming derived signal
elsewhere -- specifically: a low hours_since_added on a beneficiary, a high
ratio_to_account_baseline in amount_anomalies, a high
max_balance_drawdown_pct, or (for account_swap specifically) a
sim_change_detected: true device or a VPN+country-mismatch geo event. An
attacker who controls the device/session can send money to any of the
account's own known beneficiaries, so recipient trust alone does not clear
the account when device/geo evidence is dirty.

Conversely, a high max_balance_drawdown_pct or an amount_anomalies entry, ON
ITS OWN, with no accompanying device/geo/beneficiary red flag and no
alarming typology-specific structuring pattern, CAN represent entirely
legitimate large-value activity -- a big purchase, rent, a family support
payment, or a business settling a large invoice. Do not treat drawdown or
amount alone as proof of fraud when nothing else is actually wrong.

If the underlying numbers actively contradict a surface-level flag, say so
honestly and lower your confidence rather than defending the legitimate case
regardless.

Do NOT fabricate evidence. Only cite facts actually present in what you were \
given.

Respond with ONLY a JSON object, no other text, in exactly this shape:
{
  "hypothesis": "legitimate",
  "confidence": <integer 0-100>,
  "narrative": "<2-3 sentence explanation>",
  "supporting_evidence": ["<specific fact>", "<specific fact>", ...]
}"""


def evaluate_legitimate_hypothesis(evidence: dict, as_of: datetime = None) -> dict:
    """Path B in the architecture diagram."""
    as_of = as_of or datetime.now(timezone.utc)
    derived_signals = compute_derived_signals(evidence, as_of)

    user_message = f"""Current time (as_of): {as_of.isoformat()}
Typology under investigation: {evidence.get("typology")}

Evidence:
{json.dumps(evidence, indent=2, default=str)}

Derived signals (pre-computed, trust these over doing your own date math):
{json.dumps(derived_signals, indent=2, default=str)}"""

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
        ),
    )
    return json.loads(response.text)