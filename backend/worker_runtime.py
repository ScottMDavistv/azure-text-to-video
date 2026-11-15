"""
Standalone video worker runtime.

Default command is --check, which performs no network I/O.

--run requires explicit worker enablement, provider
configuration, billing-risk acknowledgment, and budget
preflight checks.

The worker is not deployed or started by this module's
import, nor by a Docker image build.
"""

import argparse
import json
import signal
import threading

from worker_readiness import (
    require_worker_readiness,
    worker_readiness,
)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Video Render queue worker"
    )

    operation = parser.add_mutually_exclusive_group(
        required=True
    )

    operation.add_argument(
        "--check",
        action="store_true",
        help="Display safe readiness status",
    )

    operation.add_argument(
        "--verify-fal",
        action="store_true",
        help="Perform one read-only Fal queue status request",
    )

    operation.add_argument(
        "--run",
        action="store_true",
        help="Start explicitly authorized queue consumption",
    )

    parser.add_argument(
        "--poll-interval",
        type=float,
        default=10,
    )

    parser.add_argument(
        "--max-iterations",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Run until a shutdown signal",
    )

    return parser


def run_worker(
    *,
    poll_interval=10,
    max_iterations=1,
    stop_event=None,
    pipeline_factory=None,
    polling_function=None,
):
    """
    Run guarded worker polling.

    All readiness checks occur before constructing a
    live provider client or contacting Azure.

    Dependency injection supports fully offline testing.
    """
    if (
        isinstance(poll_interval, bool)
        or not isinstance(poll_interval, (int, float))
        or not 0 < poll_interval <= 3600
    ):
        raise ValueError(
            "poll_interval must be between 0 and 3600 seconds"
        )

    if max_iterations is not None:
        if (
            isinstance(max_iterations, bool)
            or not isinstance(max_iterations, int)
            or max_iterations < 1
        ):
            raise ValueError(
                "max_iterations must be a positive integer"
            )

    require_worker_readiness()

    if pipeline_factory is None:
        from render_pipeline import create_render_pipeline
        pipeline_factory = create_render_pipeline

    if polling_function is None:
        from worker_service import run_polling
        polling_function = run_polling

    renderer = pipeline_factory()

    event = (
        stop_event
        if stop_event is not None
        else threading.Event()
    )

    return polling_function(
        renderer,
        poll_interval=poll_interval,
        max_iterations=max_iterations,
        stop_event=event,
    )


def run_with_signals(
    *,
    poll_interval=10,
    max_iterations=1,
):
    """
    Request graceful shutdown on SIGTERM or SIGINT.

    A synchronous rendering operation may still take
    time to finish. This does not forcibly interrupt it.
    """
    stop_event = threading.Event()

    def request_shutdown(signum, frame):
        stop_event.set()

    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)

    signal.signal(
        signal.SIGTERM,
        request_shutdown,
    )
    signal.signal(
        signal.SIGINT,
        request_shutdown,
    )

    try:
        return run_worker(
            poll_interval=poll_interval,
            max_iterations=max_iterations,
            stop_event=stop_event,
        )
    finally:
        signal.signal(
            signal.SIGTERM,
            previous_term,
        )
        signal.signal(
            signal.SIGINT,
            previous_int,
        )


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.verify_fal:
        from fal_auth_diagnostic import main as verify_fal
        return verify_fal()

    if args.check:
        print(
            json.dumps(
                worker_readiness(),
                indent=2,
            )
        )
        return 0

    if (
        args.poll_interval <= 0
        or args.poll_interval > 3600
    ):
        parser.error(
            "poll interval must be between 0 and 3600"
        )

    if (
        not args.continuous
        and args.max_iterations < 1
    ):
        parser.error(
            "max-iterations must be positive"
        )

    from worker_readiness import WorkerNotReadyError

    try:
        result = run_with_signals(
            poll_interval=args.poll_interval,
            max_iterations=(
                None
                if args.continuous
                else args.max_iterations
            ),
        )
    except WorkerNotReadyError as exc:
        parser.exit(
            2,
            f"Worker startup blocked: {exc}\n",
        )

    print(
        json.dumps(
            result,
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
