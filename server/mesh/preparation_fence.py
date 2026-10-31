"""Shared ordering fence for immutable prepared-mesh publication."""

from collections import OrderedDict
from dataclasses import dataclass, field
import threading


class PreparationSuperseded(RuntimeError):
    """A newer revision of the same design was asked for; this build is not published."""


#: Build sessions remembered per process; the oldest is forgotten first.
_FENCE_SESSIONS = 1024


@dataclass
class PreparationFence:
    """Which compile of a design may still publish.

    A client numbers revisions from its own document load, so revision order
    is meaningful only within one ``(design, session)``: a reloaded page,
    another window or a reopened design starts a session of its own and is
    never refused by another's numbers. Across sessions the newest request
    wins: once a later request exists for the design, an older one still in
    flight -- from any session -- publishes nothing and moves no pointer.
    """

    _latest: OrderedDict[tuple[str, str], int] = field(default_factory=OrderedDict)
    _newest: OrderedDict[str, tuple[str, int]] = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def begin(self, design_uid: str, revision: int, session: str = "") -> tuple[str, str, int]:
        key = (design_uid, session)
        with self._lock:
            if revision < self._latest.get(key, revision):
                raise PreparationSuperseded("A newer revision of this design is already being prepared.")
            self._latest[key] = revision
            self._latest.move_to_end(key)
            self._newest[design_uid] = (session, revision)
            self._newest.move_to_end(design_uid)
            for remembered in (self._latest, self._newest):
                while len(remembered) > _FENCE_SESSIONS:
                    remembered.popitem(last=False)
        return design_uid, session, revision

    def current(self, token: tuple[str, str, int]) -> bool:
        with self._lock:
            return self._newest.get(token[0]) == (token[1], token[2])
