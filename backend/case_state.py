"""
case_state.py
================
CHECKPOINT 6 - Case lifecycle state machine.

Explicit states (per the checkpoint spec; no existing frontend/status
model was found to conflict with - detection_layer.py's case objects only
ever carry `status: "open"`, a single flat marker, never a lifecycle
enum, so this module is additive and does not repurpose that field):

    SUSPECTED -> INVESTIGATING -> AUDIT_READY -> HUMAN_REVIEW
        -> ACTION_PENDING -> ACTION_EXECUTED -> CLOSED

Plus ESCALATED, reachable from HUMAN_REVIEW or ACTION_EXECUTED, and able
to return to HUMAN_REVIEW/ACTION_PENDING (a senior investigator resumes
handling an escalated case) or close directly.

Invalid transitions (e.g. CLOSED -> INVESTIGATING) are rejected with
InvalidTransitionError, never silently allowed - CLOSED and REJECTED are
terminal: no transition leaves them.
"""

SUSPECTED = "SUSPECTED"
INVESTIGATING = "INVESTIGATING"
AUDIT_READY = "AUDIT_READY"
HUMAN_REVIEW = "HUMAN_REVIEW"
ACTION_PENDING = "ACTION_PENDING"
ACTION_EXECUTED = "ACTION_EXECUTED"
ESCALATED = "ESCALATED"
CLOSED = "CLOSED"

CASE_STATES = (
    SUSPECTED, INVESTIGATING, AUDIT_READY, HUMAN_REVIEW,
    ACTION_PENDING, ACTION_EXECUTED, ESCALATED, CLOSED,
)

# Adjacency list of allowed forward/backward transitions. Backward edges
# are deliberate, real workflow needs, not omissions:
#   HUMAN_REVIEW -> INVESTIGATING   : investigator requests more evidence
#                                      (case_completeness "re_gather" ->
#                                      regather_loop.py runs again)
#   ACTION_PENDING -> HUMAN_REVIEW  : an attempted action was rejected as
#                                      unauthorized (Step 3) and returns
#                                      for a different investigator/decision
#   ESCALATED -> HUMAN_REVIEW /
#   ESCALATED -> ACTION_PENDING     : a senior investigator picks the
#                                      escalated case back up
# CLOSED has no outgoing edges - terminal, per the spec's explicit example
# ("CLOSED -> INVESTIGATING should not silently occur").
VALID_TRANSITIONS = {
    SUSPECTED: {INVESTIGATING},
    INVESTIGATING: {AUDIT_READY},
    AUDIT_READY: {HUMAN_REVIEW},
    HUMAN_REVIEW: {ACTION_PENDING, ESCALATED, INVESTIGATING, CLOSED},
    ACTION_PENDING: {ACTION_EXECUTED, HUMAN_REVIEW},
    ACTION_EXECUTED: {CLOSED, ESCALATED},
    ESCALATED: {HUMAN_REVIEW, ACTION_PENDING, CLOSED},
    CLOSED: set(),
}


class InvalidTransitionError(Exception):
    """Raised when a requested case-state transition is not in
    VALID_TRANSITIONS - never allowed to occur silently."""


def transition(current_state, target_state, valid_transitions=VALID_TRANSITIONS):
    """Validate and return `target_state`. Raises InvalidTransitionError on
    any transition not explicitly listed - including from/to an unknown
    state - so an invalid transition can never silently occur."""
    if current_state not in valid_transitions:
        raise InvalidTransitionError(f"unknown current_state: {current_state!r}")
    if target_state not in CASE_STATES:
        raise InvalidTransitionError(f"unknown target_state: {target_state!r}")
    if target_state not in valid_transitions[current_state]:
        raise InvalidTransitionError(
            f"invalid transition: {current_state} -> {target_state}"
        )
    return target_state