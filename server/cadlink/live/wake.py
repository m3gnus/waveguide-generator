"""Wake long polls when a Fusion-bound request may have appeared.

A hint, never the truth: the store and the request files are authoritative, and
a long poll rescans them on its own, so a missed wake-up costs at most one
rescan interval (docs/reference/CADLINK-LIVE-PROTOCOL.md, section 9, "Missed
wake-up").

Every waiter makes its own :class:`asyncio.Event` in the event loop it runs on,
so nothing here is bound to a loop when the module is imported or a registry is
created. :func:`notify` may be called from any thread. Standard library only:
``server.cadlink.fusion_delivery`` imports it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import threading


_lock = threading.Lock()
#: resolved data directory -> the (loop, event) of every waiting long poll.
_waiters: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Event]]] = {}


def _key(data_dir: Path | str) -> str:
    return str(Path(data_dir).resolve())


def _set(loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
    try:
        loop.call_soon_threadsafe(event.set)
    except RuntimeError:
        # That loop has closed; its waiter is gone with it.
        pass


def notify(data_dir: Path | str) -> None:
    """Wake every long poll waiting on this data directory."""

    with _lock:
        waiters = list(_waiters.get(_key(data_dir), ()))
    for loop, event in waiters:
        _set(loop, event)


class Waiter:
    """One long poll's subscription, made inside the event loop it waits on.

    Subscribe before reading the store, so a request published between that
    read and the wait still wakes it.
    """

    def __init__(self, data_dir: Path | str) -> None:
        self._key = _key(data_dir)
        self._entry = (asyncio.get_running_loop(), asyncio.Event())
        with _lock:
            _waiters.setdefault(self._key, set()).add(self._entry)

    async def wait(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds; True when woken since the last wait."""

        event = self._entry[1]
        try:
            await asyncio.wait_for(event.wait(), timeout=max(0.0, timeout))
            return True
        except TimeoutError:
            return False
        finally:
            event.clear()

    def close(self) -> None:
        with _lock:
            waiters = _waiters.get(self._key)
            if waiters is not None:
                waiters.discard(self._entry)
                if not waiters:
                    del _waiters[self._key]


def waiting(data_dir: Path | str) -> int:
    """How many long polls wait on this data directory now (diagnostics, tests)."""

    with _lock:
        return len(_waiters.get(_key(data_dir), ()))


__all__ = ["Waiter", "notify", "waiting"]
