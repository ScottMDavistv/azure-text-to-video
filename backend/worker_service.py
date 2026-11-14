"""
Controlled Azure video rendering worker.

The polling service is not started automatically.

A real rendering provider must be supplied before
processing live Azure Queue messages.
"""

import threading
import time

from consumer import consume_one
from worker import process_job


DEFAULT_POLL_INTERVAL = 10


def run_once(renderer, queue_client=None):
    """
    Process at most one Azure Queue message.

    An actual renderer must be explicitly provided.
    """
    if not callable(renderer):
        raise ValueError(
            "A callable video renderer is required"
        )

    def processor(job_id):
        return process_job(job_id, renderer)

    return consume_one(
        processor=processor,
        queue_client=queue_client,
    )


def run_polling(
    renderer,
    *,
    queue_client=None,
    poll_interval=DEFAULT_POLL_INTERVAL,
    max_iterations=None,
    stop_event=None,
    sleep_func=time.sleep,
):
    """
    Run a controlled polling loop.

    This function is not invoked automatically.
    """

    if not callable(renderer):
        raise ValueError(
            "A callable video renderer is required"
        )

    if poll_interval < 0:
        raise ValueError(
            "poll_interval cannot be negative"
        )

    if max_iterations is not None and max_iterations < 1:
        raise ValueError(
            "max_iterations must be positive"
        )

    stop_event = (
        stop_event
        if stop_event is not None
        else threading.Event()
    )

    iterations = 0
    processed = 0
    empty = 0
    deadlettered = 0

    while not stop_event.is_set():

        if (
            max_iterations is not None
            and iterations >= max_iterations
        ):
            break

        result = run_once(
            renderer=renderer,
            queue_client=queue_client,
        )

        iterations += 1

        if result["status"] == "acknowledged":
            processed += 1

        elif result["status"] == "deadlettered":
            deadlettered += 1

        elif result["status"] == "empty":
            empty += 1

        if stop_event.is_set():
            break

        if (
            max_iterations is None
            or iterations < max_iterations
        ):
            if poll_interval:
                if stop_event.wait(poll_interval):
                    break

    return {
        "iterations": iterations,
        "processed": processed,
        "empty": empty,
        "deadlettered": deadlettered,
        "stopped": stop_event.is_set(),
    }
