"""
agents/contradiction_agent.py
================================
Resolves the two opposing hypotheses. Structurally unchanged from the
original - it never touches typology-specific evidence itself, it just
weighs the specificity of what each side already cited - but the examples
in the prompt are widened so it can recognize which side has the more
structurally significant evidence across all four typologies, not just
account_swap.
"""
import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

SYSTEM_PROMPT = """You are the Contradiction Agent in a fraud investigation \
system. You receive two opposing hypotheses about the same case -- one \
arguing scammer/compromised, one arguing legitimate -- each already scored \
with its own confidence and supporting evidence, plus which typology
(smurfing, reverse_smurfing, money_mule, or account_swap) was under
investigation. Your job is to resolve them.

You are not just picking the higher confidence score. Actually weigh the \
SPECIFIC supporting_evidence each side cited:
- If one side's evidence is a surface-level flag (e.g. "beneficiary is \
  verified", "relationship is family") and the other side's evidence is a \
  more specific, harder-to-fake structural signal (e.g. "aggregate inbound \
  amount is 6.6x baseline from 16 unique senders with rapid onward transfer \
  to 10 receivers", "sim_change_detected: true 40 minutes before a
  transaction to a first-time beneficiary", "outbound_to_inbound_ratio 0.85
  with a 12-minute median gap"), the more specific and structurally
  significant evidence should usually win, even if the surface-level side
  reported higher confidence.
- Treat structuring signals (unique_inbound/outbound_senders/receivers,
  ratio_to_account_baseline, multi_hop_depth), mule pass-through signals
  (outbound_to_inbound_ratio, median_inbound_to_outbound_minutes), and
  account-swap compromise signals (sim_change_detected, VPN+country
  mismatch) as equally hard, equally specific evidence as
  amount_anomalies/max_balance_drawdown_pct -- do not let an
  established-relationship argument automatically outweigh a severe
  structuring or drawdown signal just because "who received it" sounds more
  reassuring than "what the money actually did."
- A single CORROBORATED hard signal for the flagged typology (e.g. for
  account_swap: sim_change_detected true, or VPN combined with country
  mismatch; for smurfing/reverse_smurfing: a high ratio_to_account_baseline
  AND rapid onward transfer/downstream multi-hop together; for money_mule: a
  high pass-through ratio with a short median gap) should generally outweigh
  "established beneficiary" or "verified relationship" evidence alone.
- Conversely, a balance-drawdown or amount-anomaly signal, or a bare
  structuring pattern (e.g. several inbound senders but a LOW
  ratio_to_account_baseline and no rapid onward transfer), with NO
  accompanying corroborating red flag, should not automatically read as
  fraud -- check whether anything else is actually wrong before treating a
  large-but-explainable pattern as compromise.
- A "new device" flag with NEITHER sim_change_detected NOR jailbroken_rooted
  set, or a fan-in/fan-out pattern with a LOW ratio_to_account_baseline, is
  weak evidence on its own -- do not let it outweigh a genuinely established,
  verified beneficiary relationship by itself.
- Explicitly name which piece of evidence was decisive.

Respond with ONLY a JSON object, no other text, in exactly this shape:
{
  "favored_hypothesis": "scammer" | "legitimate",
  "confidence": <integer 0-100>,
  "reasoning": "<2-3 sentences>",
  "deciding_factor": "<the single piece of evidence that tipped it>"
}"""


def resolve_contradiction(scammer_result: dict, legitimate_result: dict, typology: str = None) -> dict:
    user_message = f"""Typology under investigation: {typology}

Path A (scammer hypothesis):
{json.dumps(scammer_result, indent=2)}

Path B (legitimate hypothesis):
{json.dumps(legitimate_result, indent=2)}"""

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
        ),
    )
    return json.loads(response.text)