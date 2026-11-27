"""A read that failed is not retried for a while.

The overlay readers run on hot paths (``load_segment_maps`` on every delivery, the
text readers on every file read). If their table is missing — code deployed before
migration 234 — or the database hiccups, each call would add a failing request and
a warning. After one failure the reader skips its read for ``seconds`` and returns
"nothing to overlay" (the stored values), which is always a safe answer.
"""
from __future__ import annotations

import threading
import time


class Backoff:
    def __init__(self, seconds: float) -> None:
        self.seconds = float(seconds)
        self._lock = threading.Lock()
        self._until = 0.0

    def down(self) -> bool:
        """Inside the pause after a failure."""
        with self._lock:
            return time.monotonic() < self._until

    def failed(self) -> None:
        with self._lock:
            self._until = time.monotonic() + self.seconds

    def reset(self) -> None:
        with self._lock:
            self._until = 0.0
