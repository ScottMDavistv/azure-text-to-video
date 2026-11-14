import json
from unittest.mock import patch
from uuid import uuid4

import pytest

from job_queue import enqueue_job, parse_job_message


def test_enqueue_job():
    job_id = str(uuid4())

    with patch("job_queue.get_queue_client") as mock_client:
        result = enqueue_job(job_id)

        assert result["job_id"] == job_id
        assert result["queued"] is True

        call = mock_client.return_value.send_message.call_args
        message = json.loads(call.args[0])

        assert message["job_id"] == job_id


def test_parse_job_message():
    job_id = str(uuid4())
    message = json.dumps({"job_id": job_id})

    assert parse_job_message(message) == job_id


def test_reject_invalid_job_id():
    with pytest.raises(ValueError):
        enqueue_job("invalid-job-id")
