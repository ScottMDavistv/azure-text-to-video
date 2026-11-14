"""
Renew Azure Storage Queue message visibility during long-running work.

This module does not start a worker or consume messages by itself.
"""

import threading


class QueueVisibilityLease:
    def __init__(
        self,
        queue_client,
        message,
        visibility_timeout=120,
        renew_interval=60,
    ):
        if visibility_timeout < 1:
            raise ValueError("visibility_timeout must be positive")

        if renew_interval < 1:
            raise ValueError("renew_interval must be positive")

        if renew_interval >= visibility_timeout:
            raise ValueError(
                "renew_interval must be shorter than visibility_timeout"
            )

        self.queue_client = queue_client
        self.message_id = message.id
        self.pop_receipt = message.pop_receipt
        self.visibility_timeout = visibility_timeout
        self.renew_interval = renew_interval

        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self.error = None

    def renew(self):
        with self._lock:
            result = self.queue_client.update_message(
                self.message_id,
                self.pop_receipt,
                visibility_timeout=self.visibility_timeout,
            )

            # Azure returns a new pop receipt after each renewal.
            self.pop_receipt = result.pop_receipt
            return self.pop_receipt

    def _run(self):
        while not self._stop.wait(self.renew_interval):
            try:
                self.renew()
            except Exception as exc:
                self.error = exc
                self._stop.set()
                return

    def start(self):
        if self._thread is not None:
            raise RuntimeError("Visibility lease already started")

        self._thread = threading.Thread(
            target=self._run,
            name="queue-visibility-renewal",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

        if self._thread is not None:
            self._thread.join()
            self._thread = None

        return self

    def assert_healthy(self):
        if self.error is not None:
            raise RuntimeError(
                "Queue visibility renewal failed"
            ) from self.error

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
        return False
