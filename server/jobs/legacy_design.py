"""Decide what an imported v1 job's design is, and say so out loud.

v2 writes ``script_snapshot`` as ``{"version": 1, "design": <DesignConfig>}``.
A job imported from v1 by ``scripts/migrate_v1.py`` carries something else --
or nothing -- so every reader of that column would otherwise have to guess.
This module answers the question once, in one place:

* an imported job whose design can be recovered is rewritten into the v2 shape,
  so the rest of the product -- reopen, rerun, compare, export -- treats it
  exactly like a natively solved one and needs no legacy branch at all;
* an imported job whose design cannot be recovered gets a stated, actionable
  reason instead of a silently dead button.

Recovery has two sources, in decreasing fidelity: v1's own design state
(:mod:`server.design.legacy_snapshot`) and, when a row predates the
``script_snapshot_json`` column, the prepared mesher payload it still stores
beside it (:mod:`server.design.legacy_payload`).
"""

from __future__ import annotations

from dataclasses import dataclass
import functools
import json
import logging
from typing import Any, Mapping

from server.design.legacy_payload import job_config_payload, payload_to_design
from server.design.legacy_snapshot import (
    LegacySnapshotError,
    resolve_legacy_params,
    snapshot_to_design,
)


logger = logging.getLogger(__name__)

FREEFORM_REASON = (
    "This job's FREEFORM design was stored in the v1 format, which cannot be "
    "translated into a v2 design. Re-enter its profiles to run it again."
)
MISSING_REASON = (
    "This job predates design snapshots in v1, and no design was stored with "
    "it. There is nothing to reopen or rerun."
)
PAYLOAD_NOTE = (
    "Recovered from the mesher payload this job was solved with, because v1 "
    "stored no design snapshot for it. The geometry is exact; ATH passthrough "
    "blocks, the Scale factor and the sweep bounds were never part of that "
    "payload and are not restored."
)


@dataclass(frozen=True)
class DesignResolution:
    """What v2 can do with one job's stored design, and why."""

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
    """v2's own two shapes: the versioned envelope and the bare wire."""

    if snapshot.get("version") == 1 and isinstance(snapshot.get("design"), Mapping):
        return True
    return isinstance(snapshot.get("formula"), str)


def _recovered(parsed: Any, source: str, note: str | None) -> DesignResolution:
    return DesignResolution(
        reopenable=True,
        source=source,
        reason_code="recovered",
        note=note,
        snapshot={"version": 1, "design": parsed.design.model_dump(mode="json")},
    )


def _resolve(snapshot: Mapping[str, Any] | None, config: Any) -> DesignResolution:
    if isinstance(snapshot, Mapping) and _is_v2_snapshot(snapshot):
        return DesignResolution(
            reopenable=True, source="v2-snapshot", reason_code="ok", snapshot=dict(snapshot)
        )

    snapshot_error: LegacySnapshotError | None = None
    if isinstance(snapshot, Mapping) and snapshot:
        try:
            return _recovered(snapshot_to_design(snapshot), "v1-design-state", None)
        except LegacySnapshotError as exc:
            snapshot_error = exc

    payload = job_config_payload(config)
    if payload is not None:
        try:
            return _recovered(payload_to_design(payload), "v1-mesher-payload", PAYLOAD_NOTE)
        except LegacySnapshotError as exc:
            # Both sources refuse FREEFORM for the same reason, so a payload
            # failure never gets to override a more specific snapshot message.
            if snapshot_error is None:
                snapshot_error = exc

    if snapshot_error is None:
        return _UNAVAILABLE_NO_DESIGN
    if _is_freeform(snapshot, payload):
        return DesignResolution(
            reopenable=False,
            source="none",
            reason_code="freeform_legacy_design",
            reason=FREEFORM_REASON,
        )
    return DesignResolution(
        reopenable=False,
        source="none",
        reason_code="unreadable_design",
        reason=(
            "This job's stored design could not be read back: "
            f"{snapshot_error}. It cannot be reopened or rerun."
        ),
    )


def _is_freeform(snapshot: Mapping[str, Any] | None, payload: Mapping[str, Any] | None) -> bool:
    if isinstance(snapshot, Mapping):
        try:
            params, _ = resolve_legacy_params(snapshot)
        except LegacySnapshotError:
            params = {}
        if str(params.get("type") or "").upper() == "FREEFORM":
            return True
    if isinstance(payload, Mapping):
        return str(payload.get("formula_type") or "").upper() == "FREEFORM"
    return False


@functools.lru_cache(maxsize=512)
def _resolve_cached(key: str) -> DesignResolution:
    snapshot, config = json.loads(key)
    return _resolve(snapshot, config)


def resolve_job_design(
    snapshot: Mapping[str, Any] | None, config: Any = None
) -> DesignResolution:
    """Resolve one job's design, memoised on the stored bytes.

    Conversion runs the ATH writer, the text parser and full model validation:
    about 2 ms per *legacy* design, which the job list and every fresh WebSocket
    snapshot would otherwise pay for every row, every time, on the event loop.
    Measured over the 31-job reference import, one snapshot costs 69 ms cold and
    3.4 ms warm. A natively solved job never pays it at all -- it is recognised
    structurally -- so the cold cost scales with imported jobs, not with history.

    The cache key is the stored material rather than the job id, because the
    stored design does not change for a finished job and a rerun that rewrites
    the column should simply miss.

    The returned ``snapshot`` is **shared** with every other caller holding the
    same bytes. Every consumer today serialises it -- through a FastAPI response
    model, or straight to JSON on the jobs socket -- and none mutates it; a
    caller that wants to edit one must copy it first.
    """

    if snapshot is None and config is None:
        return _UNAVAILABLE_NO_DESIGN
    try:
        key = json.dumps([snapshot, config], sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return _resolve(snapshot, config)
    try:
        return _resolve_cached(key)
    except Exception:  # pragma: no cover - a converter defect must not 500 a list
        logger.exception("Could not classify a stored job design")
        return DesignResolution(
            reopenable=False,
            source="none",
            reason_code="unreadable_design",
            reason=(
                "This job's stored design could not be read back. It cannot be "
                "reopened or rerun."
            ),
        )


def reset_design_cache() -> None:
    """Drop the memo, for tests that convert the same bytes twice."""

    _resolve_cached.cache_clear()


__all__ = [
    "FREEFORM_REASON",
    "MISSING_REASON",
    "PAYLOAD_NOTE",
    "DesignResolution",
    "reset_design_cache",
    "resolve_job_design",
]
