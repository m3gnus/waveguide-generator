"""WG's CAD coordination gate: whether the frontend polls CAD state on a clock.

This is WG's half of the activation boundary (PLAN AR step 2; the M1 transfer
contract, C7 and C8). With the gate **on** -- the default, and today's
behaviour -- the frontend keeps its CAD returns listing and its Fusion-status
read on their adaptive clocks (2.5 s, widening to 30 s when idle). With it
**off**, neither runs unconditionally: they run while CAD work is in flight (an
unfinished CAD operation, or a Send or pull the user started) and on explicit
events, and operation changes arrive as ``cadOperation`` messages on the jobs
socket, which is a push, not a poll.

The gate never touches the transfer path. The backend's solve-command
consumer (``WG2_CAD_DELIVERY``, ``start_cad_delivery``) and the request inbox
it reads are behind neither this gate nor any other activation switch: they
are geometry exchange, not coordination.

Read once, at application start-up, and reported on the startup line so a
comparison run records its configuration from WG's own log.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

#: Set to ``off`` (or ``0``, ``false``, ``no``) to stop the clock-driven polls.
CAD_COORDINATION_ENV = "WG2_CAD_COORDINATION"
COORDINATION_ON = "on"
COORDINATION_OFF = "off"

_OFF_VALUES = frozenset({"0", "off", "false", "no"})


def read_cad_coordination(environ: Mapping[str, str] | None = None) -> str:
    """``off`` only when the variable says so; anything else is today's behaviour."""

    source = os.environ if environ is None else environ
    value = source.get(CAD_COORDINATION_ENV, "").strip().lower()
    return COORDINATION_OFF if value in _OFF_VALUES else COORDINATION_ON


__all__ = [
    "CAD_COORDINATION_ENV",
    "COORDINATION_OFF",
    "COORDINATION_ON",
    "read_cad_coordination",
]
