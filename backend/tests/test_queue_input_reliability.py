"""Queue reliability and API boundary regression tests (offline only)."""
import json
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from consumer import consume_one
from job_queue import parse_job_message


@pytest.mark.parametrize("payload", [
    "[]", "null", "true", '"text"',
    json.dumps({"job_id": 123}),
    json.dumps({"job_id": str(uuid4()).upper()}),
    json.dumps({"job_id": str(uuid4()).replace("-", "")}),
    " " * 4097,
])
def test_reject_noncanonical_and_invalid_queue_messages(payload):
    with pytest.raises((ValueError, TypeError, KeyError)):
        parse_job_message(payload)


def test_accept_canonical_queue_message():
    job_id = str(uuid4())
    assert parse_job_message(json.dumps({"job_id": job_id})) == job_id


@pytest.mark.parametrize("options", [
    {"max_attempts": True},
    {"max_attempts": 0},
    {"max_attempts": 101},
    {"max_attempts": 1.5},
    {"visibility_timeout": True},
    {"visibility_timeout": 0},
    {"visibility_timeout": 604801},
    {"visibility_timeout": float("nan")},
    {"renew_interval": True},
    {"renew_interval": 0},
    {"renew_interval": float("inf")},
    {"renew_interval": float("nan")},
    {"renew_interval": 120},
])
def test_invalid_configuration_never_receives_message(options):
    queue = MagicMock()
    with pytest.raises(ValueError):
        consume_one(processor=MagicMock(), queue_client=queue, **options)
    queue.receive_messages.assert_not_called()
    queue.delete_message.assert_not_called()


@pytest.mark.parametrize("bad_count", [None, True, False, -1, 0, "7"])
def test_untrusted_dequeue_count_defaults_to_one(bad_count):
    message = MagicMock()
    message.id = "queue-msg"
    message.pop_receipt = "receipt"
    message.dequeue_count = bad_count
    job_id = str(uuid4())
    message.content = json.dumps({"job_id": job_id})
    queue = MagicMock()
    queue.receive_messages.return_value = [message]
    processor = MagicMock(return_value={"status": "skipped"})
    result = consume_one(processor, queue_client=queue)
    assert result["attempts"] == 1
    assert result["status"] == "acknowledged"
    processor.assert_called_once_with(job_id)


def test_noncanonical_message_is_retained_for_poison_handling():
    message = MagicMock()
    message.id = "queue-msg"
    message.pop_receipt = "receipt"
    message.dequeue_count = 2
    message.content = json.dumps({"job_id": str(uuid4()).upper()})
    queue = MagicMock()
    queue.receive_messages.return_value = [message]
    with pytest.raises(ValueError):
        consume_one(MagicMock(), queue_client=queue)
    queue.delete_message.assert_not_called()
