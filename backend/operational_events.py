"""
Structured, allowlisted job-event logging.

Never logs prompts, access tokens, provider API keys,
download URLs, video bytes or arbitrary error messages.
"""

import json
import logging
from datetime import datetime, timezone
from uuid import UUID


LOGGER = logging.getLogger("textvideo.operations")

EVENT_TYPES = frozenset({
    "job_created",
    "job_queued",
    "job_claimed",
    "job_completed",
    "job_failed",
    "job_cancelled",
    "job_skipped",
    "worker_started",
    "worker_stopped",
})


def safe_job_id(job_id):
    if job_id is None:
        return None

    try:
        return str(UUID(str(job_id)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError(
            "Operational event job ID must be a UUID"
        ) from None


def build_event(
    event_type,
    *,
    job_id=None,
    status=None,
    now=None,
):
    if event_type not in EVENT_TYPES:
        raise ValueError(
            "Unsupported operational event type"
        )

    event = {
        "event": event_type,
        "timestamp": (
            now or datetime.now(timezone.utc)
        ).isoformat(),
    }

    if job_id is not None:
        event["job_id"] = safe_job_id(job_id)

    if status is not None:
        if status not in {
            "created",
            "queued",
            "processing",
            "completed",
            "failed",
            "cancelled",
            "skipped",
        }:
            raise ValueError(
                "Unsupported operational status"
            )

        event["status"] = status

    return event


def log_event(event_type, *, job_id=None, status=None):
    event = build_event(
        event_type,
        job_id=job_id,
        status=status,
    )

    LOGGER.info(
        "%s",
        json.dumps(
            event,
            separators=(",", ":"),
        ),
    )

    return event
