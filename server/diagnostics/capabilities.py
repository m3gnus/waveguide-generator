"""What this machine can solve with, and whether it is the stack it claims.

Lifted out of ``create_app``'s ``/api/capabilities`` handler so a problem
report and the interface answer the same question with the same code. Two
copies of this would drift, and the copy that drifted would be the one in the
bug report -- the one nobody re-reads until it is already wrong.
"""

from __future__ import annotations

import asyncio
import platform
from dataclasses import asdict
from typing import Any, Protocol

from server.cadlink.build_flags import onshape_enabled
from server.engines.registry import full3d_engine_order
from server.integration.installed import measure_installed_stack
from server.integration.provenance import pinned_dependency_shas
from server.platform.sqlite import journal_mode_statuses


#: How long a report waits for the solver probe.
#:
#: The probe is not free -- a cold BEMPP worker takes tens of seconds -- and
#: "my solver is broken" is one of the likelier reasons somebody opens the
#: report dialog, so the slow path and the reported path are the same path.
#: A report that says the probe timed out is worth more than a report that
#: never downloads.
PROBE_TIMEOUT_SECONDS = 5.0


class _Registry(Protocol):
    async def capabilities(self) -> tuple[Any, ...]: ...

    def cpu_preparation_in_flight(self) -> bool: ...


async def capabilities_payload(engine_registry: _Registry) -> dict[str, Any]:
    """The engine list, the resolved default, module drift, and storage mode."""

    snapshot = await engine_registry.capabilities()
    engines = [asdict(engine) for engine in snapshot]
    from server.solver.bempp_opencl import (
        TRANSIENT_REASONS, qualification_max_seconds, retry_pending,
    )

    for info, engine in zip(snapshot, engines, strict=True):
        engine["label"] = info.display_label()
        # The interface reads this as "still checking OpenCL". Only a row
        # whose published answer is itself a transient failure can be waiting
        # on a retry; any other answer ends the retry state for this row,
        # whatever a check elsewhere left pending in the shared state.
        engine["opencl_retry_pending"] = (
            info.name == "bempp"
            and info.opencl_unavailable_reason in TRANSIENT_REASONS
            and retry_pending()
        )
    available = {item["name"] for item in engines if item.get("available") is True}
    # The planner's own order, asked for the same way it asks: the preference
    # puts ready BEAT CPU ahead of BEMPP on every platform. An interface
    # that advertised a different one from the one AUTO follows would be worse
    # than no order at all.
    order = full3d_engine_order()
    pending = {item["name"] for item in engines if item.get("qualification") == "pending"}
    first = next((name for name in order if name in available or name in pending), None)
    resolved = first if first in available else None
    # "What can this host do" is incomplete without "and is this host the stack
    # it claims to be". A drifted module changes what the probes above report
    # while every version string stays put.
    pinned = pinned_dependency_shas()
    installed, drift = measure_installed_stack(pinned)
    return {
        "hostPlatform": platform.system().lower(),
        "opencl_qualification_max_seconds": qualification_max_seconds(),
        "engines": engines,
        "officialBeatRuntime": getattr(engine_registry, "official_runtime_statuses", None),
        "cpuPreparationInFlight": bool(
            getattr(engine_registry, "cpu_preparation_in_flight", lambda: False)()
        ),
        "engineSelection": {
            "default": "auto",
            "resolvedDefault": resolved,
            "full3dOrder": list(order),
        },
        "dependencies": {"pinned": pinned, "installed": installed, "drift": drift},
        # A store whose filesystem refused write-ahead logging still works, just
        # slowly, so it is reported here rather than refused at boot.
        "storage": journal_mode_statuses(),
        # The one advertisement the interface reads to show or hide Onshape.
        "onshape": onshape_enabled(),
    }


async def capabilities_or_none(
    engine_registry: _Registry, *, timeout: float = PROBE_TIMEOUT_SECONDS
) -> dict[str, Any] | None:
    """The payload, or ``None`` when the probe outran its welcome."""

    try:
        return await asyncio.wait_for(capabilities_payload(engine_registry), timeout)
    except Exception:
        # Every failure below is a fact about the machine being reported on --
        # a detector that raised, a pins file that will not parse, a worker
        # that hung. None of them is a reason to refuse the report that would
        # have carried the evidence. ``CancelledError`` is a BaseException and
        # so still propagates: a cancelled request must not become an answer.
        return None


__all__ = ["PROBE_TIMEOUT_SECONDS", "capabilities_or_none", "capabilities_payload"]
