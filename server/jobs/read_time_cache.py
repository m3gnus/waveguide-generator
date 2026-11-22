"""Bounded memo of read-time presentations of stored results, keyed by digest.

A finished sweep's results run to megabytes. Presenting an archived record
(read-time flags or identities added in memory, the stored row untouched)
parses it, so the few most recently opened records keep their verdict here:
the presented text and digest, or that the stored bytes are served as they
are. Reopening or polling a run then costs a dictionary lookup.
"""

from __future__ import annotations

from hashlib import sha256
import threading
from typing import Callable


class ReadTimeCache:
    """Thread-safe, least-recently-used verdicts for a handful of records.

    The key must name everything the presentation depends on, the stored
    digest first; the caller supplies the presentation, which returns the
    presented text or None when the stored bytes need nothing.
    """

    def __init__(self, size: int) -> None:
        self._size = size
        self._verdicts: dict[str, tuple[str, str] | None] = {}
        self._lock = threading.Lock()

    def serve(
        self,
        key: str,
        text: str,
        digest: str,
        present: Callable[[], str | None],
    ) -> tuple[str, str]:
        with self._lock:
            hit = key in self._verdicts
            if hit:
                verdict = self._verdicts.pop(key)
                self._verdicts[key] = verdict
        if not hit:
            presented = present()
            verdict = (
                None
                if presented is None
                else (presented, sha256(presented.encode("utf-8")).hexdigest())
            )
            with self._lock:
                self._verdicts.pop(key, None)
                self._verdicts[key] = verdict
                while len(self._verdicts) > self._size:
                    self._verdicts.pop(next(iter(self._verdicts)))
        return (text, digest) if verdict is None else verdict

    def clear(self) -> None:
        with self._lock:
            self._verdicts.clear()
