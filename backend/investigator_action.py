"""
investigator_action.py
=========================
CHECKPOINT 6 - Action Authorization Enforcement (Step 3), Human Review
object (Step 4), Investigator Action object (Step 5), and Recommendation
Override (Step 6).

    Next-Best-Action -> Audit Trail -> HUMAN REVIEW -> INVESTIGATOR ACTION
    -> Case Memory

This module CONSUMES the Checkpoint 4 authority decision (authority_
policy.py's `assess_authority()` output, already computed upstream and
passed in as part of `recommendation`/`case`) and next_best_action.py's
`ACTION_MINIMUM_AUTHORITY` table. It does NOT duplicate authority-policy
logic - it only compares an already-decided authority *requirement*
against the REAL, backend-resolved role of the acting investigator.

### Role separation (Step 11) - documented limitation ###
No real authentication/SSO exists yet in this project (see
docs/backend_implementation_status.md). Per this checkpoint's explicit
instruction ("If real authentication is not yet implemented, use an
explicit deterministic test identity/role abstraction and clearly
document the limitation"), `INVESTIGATOR_DIRECTORY` below is that
abstraction: a small, deterministic, backend-owned table mapping
`investigator_id -> role`. Every function in this module that needs an
investigator's role calls `resolve_investigator(investigator_id)` to look
it up here - a caller can pass any `investigator_id` string, but CANNOT
supply a role directly; a request body like `{"role": "senior"}` is never
read by this module. An unrecognized `investigator_id` resolves to `None`
(unauthenticated), never a default/permissive role. When real auth is
built, `resolve_investigator` is the one function that needs to change to
look up a real, authenticated session instead of this table - every
caller (authorize_action, create_human_review, record_investigator_action)
is already written against `resolve_investigator`'s interface, not this
table directly.
"""
import hashlib
import json
from datetime import datetime, timezone

from next_best_action import ACTION_MINIMUM_AUTHORITY

# ----------------------------------------------------------------------
# Deterministic TEST identity/role directory - see module docstring.
# ----------------------------------------------------------------------
INVESTIGATOR_DIRECTORY = {
    "INV-J001": {"investigator_id": "INV-J001", "name": "Junior Investigator 1", "role": "junior"},
    "INV-J002": {"investigator_id": "INV-J002", "name": "Junior Investigator 2", "role": "junior"},
    "INV-S001": {"investigator_id": "INV-S001", "name": "Senior Investigator 1", "role": "senior"},
    "INV-S002": {"investigator_id": "INV-S002", "name": "Senior Investigator 2", "role": "senior"},
}

_TIER_RANK = {"junior": 0, "senior": 1}


def resolve_investigator(investigator_id, directory=INVESTIGATOR_DIRECTORY):
    """The ONE place investigator role is resolved. Backend-authoritative:
    always looks the identity up server-side, never trusts a caller-
    supplied role. Returns None for an unknown investigator_id - callers
    must treat that as unauthenticated, never default to a permissive
    role."""
    return directory.get(investigator_id)


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _content_hash_id(prefix, *parts):
    raw = json.dumps(parts, sort_keys=True, default=str)
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:8].upper()}"


def _authority_met(investigator_authority, required_authority):
    return _TIER_RANK.get(investigator_authority, -1) >= _TIER_RANK.get(required_authority, 99)


# ----------------------------------------------------------------------
# Step 3 - Action Authorization Enforcement
# ----------------------------------------------------------------------
def authorize_action(action, required_authority, investigator_id, directory=INVESTIGATOR_DIRECTORY):
    """For every attempted investigator action: requested action, required
    authority, investigator authority, authorization result, authorization
    reason. A junior investigator can never execute an action requiring
    senior authority - `authorized` is computed here, never trusted from
    the caller. An unauthorized attempt is NOT silently rejected - the
    full record (including `authorized: False`) is returned so the caller
    can (and must) still write it to the audit trail."""
    investigator = resolve_investigator(investigator_id, directory)
    if investigator is None:
        return {
            "action": action,
            "required_authority": required_authority,
            "investigator_id": investigator_id,
            "investigator_authority": None,
            "authorized": False,
            "reason": "UNKNOWN_INVESTIGATOR_IDENTITY",
        }
    investigator_authority = investigator["role"]
    authorized = _authority_met(investigator_authority, required_authority)
    reason = "AUTHORIZED" if authorized else "ACTION_REQUIRES_SENIOR_AUTHORITY"
    return {
        "action": action,
        "required_authority": required_authority,
        "investigator_id": investigator_id,
        "investigator_authority": investigator_authority,
        "authorized": authorized,
        "reason": reason,
    }


# ----------------------------------------------------------------------
# Step 4 - Human Review object
# ----------------------------------------------------------------------
def create_human_review(case, recommendation, reviewer_id, investigator_decision,
                         decision_reason, evidence_reviewed=None,
                         regulatory_rules_reviewed=None, status="approved",
                         review_started_at=None, review_completed_at=None,
                         directory=INVESTIGATOR_DIRECTORY):
    """Structured human-review record. Stores only case/account/evidence
    identifiers already established elsewhere in the project - no
    customer PII. `reviewer_role`/`authority_at_review` are resolved
    server-side (Step 11) from `reviewer_id`, never accepted from a
    caller-supplied role."""
    reviewer = resolve_investigator(reviewer_id, directory)
    reviewer_role = reviewer["role"] if reviewer else None
    started = review_started_at or _utc_now_iso()
    completed = review_completed_at or _utc_now_iso()
    review_id = _content_hash_id(
        "REVIEW", case["case_id"], reviewer_id, recommendation["recommendation_id"], started
    )
    return {
        "review_id": review_id,
        "case_id": case["case_id"],
        "reviewer_id": reviewer_id,
        "reviewer_role": reviewer_role,
        "review_started_at": started,
        "review_completed_at": completed,
        "system_recommendation": recommendation["recommended_action"],
        "investigator_decision": investigator_decision,
        "decision_reason": decision_reason,
        "evidence_reviewed": evidence_reviewed or [],
        "regulatory_rules_reviewed": regulatory_rules_reviewed or [],
        "authority_at_review": reviewer_role,
        "status": status,
    }


# ----------------------------------------------------------------------
# Step 5 / 6 - Investigator Action + Recommendation Override
# ----------------------------------------------------------------------
class OverrideReasonRequiredError(ValueError):
    """Raised when `requested_action` differs from the system
    recommendation and no `override_reason` was supplied - the system
    must never silently change/accept a changed recommendation (Step 6)."""


def record_investigator_action(case, recommendation, investigator_id, requested_action,
                                reason, override_reason=None, directory=INVESTIGATOR_DIRECTORY,
                                timestamp=None):
    """The structured Investigator Action object. Always returns a record -
    including for an unauthorized attempt (`actual_action:
    "REJECTED_UNAUTHORIZED"`) - so the caller can append it to the audit
    trail regardless of outcome; nothing is silently dropped.

    A human investigator may disagree with `recommendation` as long as the
    action is authorized (Step 6): if `requested_action != recommended_
    action`, `override_reason` is REQUIRED (non-empty) or this raises
    OverrideReasonRequiredError - the recommendation itself is never
    silently changed to match what was actually done.
    """
    recommended_action = recommendation["recommended_action"]
    recommendation_followed = (requested_action == recommended_action)
    if not recommendation_followed and not (override_reason and override_reason.strip()):
        raise OverrideReasonRequiredError(
            "override_reason is required when requested_action differs from "
            "the system's recommended_action"
        )

    # The attempted action needs at least (a) its own minimum authority and
    # (b) the case's own Checkpoint-4/NBA-derived authority requirement -
    # an investigator cannot escape a senior-required CASE by requesting a
    # nominally junior-tier action instead of the recommended one.
    action_min_authority = ACTION_MINIMUM_AUTHORITY.get(requested_action, "senior")
    case_required_authority = recommendation.get("required_authority", "senior")
    required_authority = (
        case_required_authority
        if _TIER_RANK.get(case_required_authority, 1) > _TIER_RANK.get(action_min_authority, 1)
        else action_min_authority
    )

    authz = authorize_action(requested_action, required_authority, investigator_id, directory)

    ts = timestamp or _utc_now_iso()
    action_id = _content_hash_id("ACTION", case["case_id"], investigator_id, requested_action, ts)
    actual_action = requested_action if authz["authorized"] else "REJECTED_UNAUTHORIZED"

    return {
        "action_id": action_id,
        "case_id": case["case_id"],
        "investigator_id": investigator_id,
        "investigator_role": authz["investigator_authority"],
        "recommended_action": recommended_action,
        "requested_action": requested_action,
        "required_authority": required_authority,
        "authorized": authz["authorized"],
        "authorization_reason": authz["reason"],
        "actual_action": actual_action,
        "recommendation_followed": recommendation_followed,
        "override_reason": override_reason,
        "reason": reason,
        "supporting_evidence": recommendation.get("supporting_evidence_ids", []),
        "timestamp": ts,
    }