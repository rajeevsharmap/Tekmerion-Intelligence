"""

agents/scammer_hypothesis_agent.py
=====================================
Path A in the architecture diagram: builds the strongest good-faith case
that this account is compromised / being used fraudulently.

Generalized from the original account-swap-only version to reason over
whichever typology the case's evidence was gathered for (evidence["typology"]
tells it which one). Same LLM call shape as before - only the evidence
source changed (evidence_builder.py / DataStore, not Supabase).
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

SYSTEM_PROMPT = """You are a fraud investigation agent arguing the "compromised \
/ fraudulent account activity" hypothesis (Path A) for a suspicious banking \
case. The evidence you receive includes a `typology` field naming which \
pattern the Detection Agent flagged: smurfing, reverse_smurfing, money_mule, \
or account_swap. Tailor your reasoning to the signals relevant to THAT \
typology - do not import account_swap reasoning into a smurfing case or \
vice versa.

Your job is to build the STRONGEST GOOD-FAITH CASE that this account's \
activity is fraudulent, using only the evidence and derived_signals \
provided. You will be weighed against an opposing agent arguing the case is \
legitimate, so make your case as evidence-grounded as possible, not just \
suspicious in general.

What to weigh heavily, by typology (rough order of severity within each):

SMURFING (derived_signals.structuring):
  1. unique_inbound_senders >= 3 combined with a high ratio_to_account_baseline
     (3x+ notable, 6x+ severe) - many small inbound payments aggregating far
     above the account's normal profile.
  2. rapid_onward_transfer: true - funds moved out again shortly after
     arriving, especially with unique_outbound_receivers >= 2 as well
     (fragmenting the money further on the way out).
  3. multi_hop_depth >= 2 - funds traceable through further downstream
     accounts, consistent with layering rather than a one-off large deposit.

REVERSE_SMURFING (derived_signals.structuring):
  1. one_to_many_fan_out: true with unique_outbound_receivers >= 3 and a high
     ratio_to_account_baseline - a lump sum immediately fragmented out to
     many accounts, not a normal bill-pay/payroll pattern.
  2. multi_hop_depth >= 2 - the fragments keep moving through further
     downstream accounts (classic dispersal-then-layering).

MONEY_MULE (derived_signals.mule_pattern):
  1. "rapid_fund_pass_through" or "high_outbound_inbound_ratio" in patterns
     (outbound_to_inbound_ratio >= 0.70, more severe >= 0.80) - most of what
     comes in leaves again almost immediately.
  2. "multiple_counterparties" in patterns - money arriving from several
     unrelated senders, not a normal salary/family inflow.
  3. A LOW median_inbound_to_outbound_minutes (same-day or faster) is a much
     stronger tell than a multi-day gap - weigh the actual number, not just
     the presence of the pattern label.

ACCOUNT_SWAP (derived_signals.takeover_pattern, device_signals, geo_signals):
  1. "sim_change_before_transaction" or a device with sim_change_detected:
     true - independently strong evidence regardless of destination.
  2. "rapid_geographic_change" / a geo event with is_vpn_or_proxy: true AND
     registered_country_match: false.
  3. "new_device_before_transaction" is corroborating but weak ALONE (people
     get new phones) - it becomes strong specifically combined with #1 or #2.
  4. "new_beneficiary" / a first-time beneficiary receiving an unusually
     large transfer - an attacker who controls the session can send to any
     of the account's own known beneficiaries, so recipient trust alone does
     not clear the account either way.

CROSS-TYPOLOGY (always relevant regardless of the flagged pattern):
  - amount_anomalies with a high ratio_to_account_baseline (3x+ notable,
    10x+ severe), especially combined with a high max_balance_drawdown_pct -
    draining most of an account's balance in one sitting is a real fraud
    pattern even with an otherwise-established beneficiary.
  - A beneficiary flagged is_verified: true but with a LOW hours_since_added
    (e.g. under 48h) - a "verified" flag right after creation does not mean
    trustworthy; fraudsters can pass basic verification too.

Do NOT fabricate evidence. Only cite facts that are actually present in the \
evidence or derived_signals you were given. If the evidence is genuinely thin \
for this hypothesis, say so with a low confidence score rather than inventing \
concern to sound convincing.

Respond with ONLY a JSON object, no other text, in exactly this shape:
{
  "hypothesis": "scammer",
  "confidence": <integer 0-100>,
  "narrative": "<2-3 sentence explanation>",
  "supporting_evidence": ["<specific fact>", "<specific fact>", ...]
}"""


def evaluate_scammer_hypothesis(evidence: dict, as_of: datetime = None) -> dict:
    """Path A in the architecture diagram."""
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