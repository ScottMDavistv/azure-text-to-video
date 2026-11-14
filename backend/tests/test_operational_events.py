import json
from unittest.mock import patch
from uuid import uuid4

import pytest

from operational_events import (
    build_event,
    log_event,
)


def test_build_job_event():
    job_id = str(uuid4())

    event = build_event(
        "job_completed",
        job_id=job_id,
        status="completed",
    )

    assert event["job_id"] == job_id
    assert event["status"] == "completed"
    assert "timestamp" in event


def test_worker_event_without_job_id():
    event = build_event("worker_started")
    assert "job_id" not in event


def test_unknown_event_rejected():
    with pytest.raises(ValueError):
        build_event("secret_exfiltrated")


def test_invalid_job_id_rejected():
    with pytest.raises(ValueError):
        build_event(
            "job_created",
            job_id="../../bad",
        )


def test_invalid_status_rejected():
    with pytest.raises(ValueError):
        build_event(
            "job_completed",
            status="something_else",
        )


def test_logging_contains_only_allowlisted_fields():
    job_id = str(uuid4())

    with patch(
        "operational_events.LOGGER.info"
    ) as logger:
        result = log_event(
            "job_cancelled",
            job_id=job_id,
            status="cancelled",
        )

    assert set(result) == {
        "event",
        "timestamp",
        "job_id",
        "status",
    }

    payload = json.loads(
        logger.call_args.args[1]
    )

    assert payload["event"] == "job_cancelled"
    assert "prompt" not in payload
    assert "api_key" not in payload
