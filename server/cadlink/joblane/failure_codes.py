"""Which reason a failed preparation stands for: one reading for both paths.

A Fusion request WG prepares itself (``server/jobs/cad_preparation.py``) and
the browser's own Prepare (``POST /api/cadlink/ingest``) fail in the same
ways, and are classified here, once, in the codes of
``server.cadlink.operations``:

- reading the return (:func:`return_failure_code`): the return is gone or
  cannot be read now (``return_unavailable``), no WGLink folder is selected
  (``wglink_folder_unset``), it changed (``snapshot_changed``), it needs a
  newer WG (``return_unsupported``), the disk is full (``disk_full``), or it
  is not a valid return (``snapshot_invalid``);
- preparing it (:func:`preparation_failure_code`): the same, for a refusal
  whose cause was reading the return; the disk filling up; and every other
  failure of WG's own pipeline -- a worker, the mesher, its normalisation,
  WG's own code -- is ``preparation_failed``.

A refusal stated in the user's terms (a project or instance gate, a symmetry
rule, an area drift with its override) is :data:`USER_WORDED`: it is not a
WG failure, and the browser shows its words. The triangle ceiling and the
memory guard are their own choices and are handled by their callers.
"""

from __future__ import annotations

import errno

from server.cadlink.intake.ingest import IngestRefusal
from server.cadlink.joblane.preparation_inputs import SnapshotUnavailable
from server.cadlink.wgreturn import WgReturnChanged, WgReturnError, WgReturnUnsupported

#: A refusal that states what the user can change, in their terms.
USER_WORDED = None
#: The stage whose refusal wraps reading the return itself.
BUNDLE_VALIDATION_STAGE = "stage 1 bundle validation"
#: Gates whose refusals are written for the user (``intake/ingest.py``).
_USER_WORDED_STAGES = frozenset({
    "stage 2 project gate", "stage 2 instance gate", "stage 2 scope gate", "stage 6 symmetry",
})


def _disk_full(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}


def return_failure_code(exc: BaseException, *, folder_selected: bool = True) -> str:
    """The reason reading a return failed."""

    if isinstance(exc, SnapshotUnavailable):
        return "return_unavailable" if folder_selected else "wglink_folder_unset"
    if isinstance(exc, WgReturnChanged):
        return "snapshot_changed"
    if isinstance(exc, WgReturnUnsupported):
        return "return_unsupported"
    if _disk_full(exc):
        return "disk_full"
    if isinstance(exc, OSError):
        return "return_unavailable"
    return "snapshot_invalid"


def preparation_failure_code(exc: BaseException) -> str | None:
    """The reason preparing a return failed, or :data:`USER_WORDED`."""

    if isinstance(exc, IngestRefusal):
        if exc.area_drift_sources or exc.stage in _USER_WORDED_STAGES:
            return USER_WORDED
        if exc.corruption:
            return "snapshot_invalid"
        if exc.stage == BUNDLE_VALIDATION_STAGE:
            cause = exc.__cause__
            return return_failure_code(cause) if isinstance(cause, BaseException) else "snapshot_invalid"
        return "preparation_failed"
    if isinstance(exc, WgReturnError):
        return return_failure_code(exc)
    if _disk_full(exc):
        return "disk_full"
    return "preparation_failed"
