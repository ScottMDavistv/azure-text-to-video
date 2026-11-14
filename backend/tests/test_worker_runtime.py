from threading import Event
from unittest.mock import MagicMock, patch

import pytest

from worker_readiness import WorkerNotReadyError
from worker_runtime import (
    build_parser,
    main,
    run_worker,
)


def test_check_mode_does_not_start_worker():
    with (
        patch("worker_runtime.worker_readiness") as readiness,
        patch("worker_runtime.run_worker") as worker,
    ):
        readiness.return_value = {
            "ready_for_live_execution": False,
        }

        result = main(["--check"])

    assert result == 0
    worker.assert_not_called()


def test_parser_requires_operation():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_runtime_fails_before_pipeline_construction():
    pipeline = MagicMock()
    polling = MagicMock()

    with patch(
        "worker_runtime.require_worker_readiness",
        side_effect=WorkerNotReadyError(
            "Not configured"
        ),
    ):
        with pytest.raises(WorkerNotReadyError):
            run_worker(
                pipeline_factory=pipeline,
                polling_function=polling,
            )

    pipeline.assert_not_called()
    polling.assert_not_called()


def test_runtime_calls_polling_function():
    renderer = MagicMock()
    pipeline = MagicMock(return_value=renderer)

    polling = MagicMock(
        return_value={
            "iterations": 2,
            "processed": 1,
        }
    )

    stop = Event()

    with patch(
        "worker_runtime.require_worker_readiness",
        return_value={"ready_for_live_execution": True},
    ):
        result = run_worker(
            poll_interval=5,
            max_iterations=2,
            stop_event=stop,
            pipeline_factory=pipeline,
            polling_function=polling,
        )

    assert result["processed"] == 1

    pipeline.assert_called_once_with()

    polling.assert_called_once_with(
        renderer,
        poll_interval=5,
        max_iterations=2,
        stop_event=stop,
    )


def test_continuous_mode_supported():
    polling = MagicMock(
        return_value={
            "iterations": 0,
            "stopped": True,
        }
    )

    with patch(
        "worker_runtime.require_worker_readiness",
        return_value={},
    ):
        result = run_worker(
            max_iterations=None,
            pipeline_factory=MagicMock(),
            polling_function=polling,
        )

    assert result["stopped"] is True
    assert (
        polling.call_args.kwargs["max_iterations"]
        is None
    )


def test_invalid_poll_interval():
    with pytest.raises(ValueError):
        run_worker(poll_interval=-1)


def test_invalid_iterations():
    with pytest.raises(ValueError):
        run_worker(max_iterations=0)


def test_boolean_iterations_rejected():
    with pytest.raises(ValueError):
        run_worker(max_iterations=True)


def test_worker_readiness_error_propagates():
    with patch(
        "worker_runtime.require_worker_readiness",
        side_effect=WorkerNotReadyError(
            "Disabled"
        ),
    ):
        with pytest.raises(WorkerNotReadyError):
            run_worker()
