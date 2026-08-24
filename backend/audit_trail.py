"""
audit_trail.py
=================
CHECKPOINT 6 - Append-only Audit Trail / Replay Log.

One AuditTrail instance per case. Enforces append-only FROM THE
APPLICATION'S PERSPECTIVE: the class exposes `append()` and read-only
accessors (`events`, `to_list()`) and deliberately has no `remove`/`pop`/
`clear`/`__setitem__` method anywhere - there is no code path in this
module that can delete or overwrite a previously-appended event.
`events`/`to_list()` return a *copy* of the internal list, so a caller
mutating the returned list can never affect the trail's real internal
state either.

Event schema (Step 7 of the checkpoint spec):
    {
        "event_id": deterministic content-hash ID,
        "case_id": ...,
        "event_type": one of EVENT_TYPES,
        "actor_type": "system" | "investigator",
        "actor_id": ...,
        "timestamp": ISO-8601 UTC,
        "before_state": ...,
        "after_state": ...,
        "reason": ...,
        "related_evidence_ids": [...],
        "metadata": {...},
    }
"""
import hashlib
import json
from datetime import datetime, timezone

EVENT_TYPES = (
    "case_created",
    "alert_created",
    "evidence_gathered",
    "evidence_regathered",
    "regulatory_evaluation",
    "auditor_evaluation",
    "completeness_evaluation",
    "authority_evaluation",
    "next_best_action_generated",
    "human_review_started",
    "human_review_completed",
    "action_requested",
    "action_authorized",
    "action_rejected",
    "action_executed",
    "recommendation_overridden",
    "case_escalated",
    "case_closed",
    "sar_report_generated",  # CHECKPOINT 7 - additive, see sar_report.py
)


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _content_hash_id(prefix, *parts):
    raw = json.dumps(parts, sort_keys=True, default=str)
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:8].upper()}"


class AuditTrail:
    """Append-only, per-case audit trail."""

    def __init__(self, case_id, events=None):
        self.case_id = case_id
        # Defensive copy on construction too, so a caller cannot hand in a
        # live list and later mutate it out from under this trail.
        self._events = list(events) if events else []

    @property
    def events(self):
        """Read-only copy - mutating the returned list never affects the
        trail's real internal state."""
        return list(self._events)

    def append(self, event_type, actor_type, actor_id, before_state=None,
               after_state=None, reason="", related_evidence_ids=None,
               metadata=None, timestamp=None):
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event_type: {event_type!r}")
        if actor_type not in ("system", "investigator"):
            raise ValueError(f"actor_type must be 'system' or 'investigator', got {actor_type!r}")
        ts = timestamp or utc_now_iso()
        event = {
            # event_id is deliberately NOT derived from `ts`: the wall-clock
            # timestamp varies run-to-run even for identical business logic,
            # which would make this "deterministic content-hash ID" (see
            # module docstring) non-deterministic in practice. Identity is
            # case_id + event_type + actor_id + position-in-trail instead,
            # which is exactly reproducible across repeated invocations of
            # the same case with the same inputs.
            "event_id": _content_hash_id(
                "EVT", self.case_id, event_type, actor_id, len(self._events)
            ),
            "case_id": self.case_id,
            "event_type": event_type,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "timestamp": ts,
            "before_state": before_state,
            "after_state": after_state,
            "reason": reason,
            "related_evidence_ids": related_evidence_ids or [],
            "metadata": metadata or {},
        }
        self._events.append(event)
        return event

    def to_list(self):
        return self.events

    def __len__(self):
        return len(self._events)


def is_append_only_extension(previous_events, current_events):
    """Verification helper (used by tests): True iff `current_events` is
    exactly `previous_events` plus zero or more new events appended at the
    end - i.e. every previously-recorded event is still present, in the
    same order, unmodified."""
    if len(current_events) < len(previous_events):
        return False
    return current_events[: len(previous_events)] == previous_events