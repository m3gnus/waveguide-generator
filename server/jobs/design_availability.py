"""Decide whether a stored job design can be reopened, and say why not.

v2 writes ``script_snapshot`` as ``{"version": 1, "design": <DesignConfig>}``
(or, on older rows, the bare design wire). Anything else -- or nothing -- is
not something this version can read back, so every reader of that column asks
here once and gets either the snapshot or a stated, actionable reason instead
of a silently dead button.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


MISSING_REASON = (
    "This job has no design snapshot, because no design was stored with it. "
    "There is nothing to reopen or rerun."
)
UNREADABLE_REASON = (
    "This job's stored design is not in a format this version can read back. "
    "It cannot be reopened or rerun."
)


@dataclass(frozen=True)
class DesignResolution:
    """What can be done with one job's stored design, and why."""

    reopenable: bool
    source: str
    reason_code: str
    reason: str | None = None
    note: str | None = None
    snapshot: dict[str, Any] | None = None

    def as_availability(self) -> dict[str, Any]:
        return {
            "reopenable": self.reopenable,
            "source": self.source,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "note": self.note,
        }


_UNAVAILABLE_NO_DESIGN = DesignResolution(
    reopenable=False,
    source="none",
    reason_code="no_stored_design",
    reason=MISSING_REASON,
)


def _is_v2_snapshot(snapshot: Mapping[str, Any]) -> bool:
    """The versioned envelope and the bare wire."""

    if snapshot.get("version") == 1 and isinstance(snapshot.get("design"), Mapping):
        return True
    return isinstance(snapshot.get("formula"), str)


def resolve_job_design(snapshot: Mapping[str, Any] | None) -> DesignResolution:
    """Classify one job's stored ``script_snapshot``."""

    if not isinstance(snapshot, Mapping) or not snapshot:
        return _UNAVAILABLE_NO_DESIGN
    if _is_v2_snapshot(snapshot):
        # Response-only adaptation: stored result artifacts and snapshot bytes
        # remain historical. Only editor/re-solve consumers receive the import.
        wire = snapshot.get("design", snapshot)
        note = None
        if isinstance(wire, Mapping) and wire.get("formula") == "LOOKUP":
            from server.design.migrate import apply_migrations
            try:
                wire, applications = apply_migrations(wire)
            except Exception:
                # A historical result must remain listable/downloadable even
                # if today's geometry authority cannot read its design. This
                # failure affects editor/rebuild availability only, never bytes.
                return DesignResolution(reopenable=False, source="v2-snapshot",
                    reason_code="unreadable_design", reason=UNREADABLE_REASON,
                    snapshot=dict(snapshot))
            note = applications[-1].note
            snapshot = ({**snapshot, "design": wire} if "design" in snapshot else wire)
        return DesignResolution(
            reopenable=True,
            source="v2-snapshot",
            reason_code="ok",
            snapshot=dict(snapshot),
            note=note,
        )
    return DesignResolution(
        reopenable=False,
        source="none",
        reason_code="unreadable_design",
        reason=UNREADABLE_REASON,
    )


__all__ = [
    "MISSING_REASON",
    "UNREADABLE_REASON",
    "DesignResolution",
    "resolve_job_design",
]
