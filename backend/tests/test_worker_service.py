from unittest.mock import MagicMock, patch
from threading import Event

import pytest

from worker_service import run_once, run_polling


def test_run_once_requires_renderer():
    with pytest.raises(ValueError):
        run_once(None)


def test_run_once_processes_job():
    queue = MagicMock()
    renderer = MagicMock(return_value=b"mp4-data")

    with (
        patch("worker_service.consume_one") as consume,
        patch("worker_service.process_job") as process,
    ):
        consume.side_effect = (
            lambda processor, queue_client: {
                "status": "acknowledged",
                "result": processor("job-123"),
            }
        )

        process.return_value = {
            "status": "completed"
        }

        result = run_once(
            renderer,
            queue_client=queue,
        )

    assert result["status"] == "acknowledged"

    process.assert_called_once_with(
        "job-123",
        renderer,
    )


def test_polling_stops_after_limit():
    with patch(
        "worker_service.run_once",
        return_value={"status": "empty"},
    ) as run:
        result = run_polling(
            renderer=MagicMock(),
            poll_interval=0,
            max_iterations=3,
        )

    assert result["iterations"] == 3
    assert result["empty"] == 3
    assert run.call_count == 3


def test_polling_counts_completed_jobs():
    results = [
        {"status": "acknowledged"},
        {"status": "deadlettered"},
        {"status": "empty"},
    ]

    with patch(
        "worker_service.run_once",
        side_effect=results,
    ):
        result = run_polling(
            renderer=MagicMock(),
            poll_interval=0,
            max_iterations=3,
        )

    assert result["processed"] == 1
    assert result["deadlettered"] == 1
    assert result["empty"] == 1


def test_polling_respects_stop_event():
    stop = Event()
    stop.set()

    with patch("worker_service.run_once") as run:
        result = run_polling(
            renderer=MagicMock(),
            stop_event=stop,
        )

    assert result["iterations"] == 0
    assert result["stopped"] is True
    run.assert_not_called()


def test_polling_rejects_invalid_interval():
    with pytest.raises(ValueError):
        run_polling(
            renderer=MagicMock(),
            poll_interval=-1,
        )


def test_polling_rejects_invalid_iterations():
    with pytest.raises(ValueError):
        run_polling(
            renderer=MagicMock(),
            max_iterations=0,
        )


def test_polling_requires_renderer():
    with pytest.raises(ValueError):
        run_polling(renderer=None)


def test_worker_failure_propagates():
    with patch(
        "worker_service.run_once",
        side_effect=RuntimeError("Rendering failed"),
    ):
        with pytest.raises(RuntimeError):
            run_polling(
                renderer=MagicMock(),
                poll_interval=0,
                max_iterations=1,
            )
