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

A refusal stated in the user's terms -- a modelling mistake the user fixes
in their model: a project, instance or scope gate, role resolution (a
required source missing, no throat candidate), a symmetry rule, an area
drift with its override, a FEM region rule or its wall-clock budget -- is
:data:`USER_WORDED`. It is never a WG failure and never recorded as one: the
browser shows its words, and a Fusion request ends as
``model_needs_change`` in them (:func:`refusal_code`). The triangle ceiling
and the memory guard are their own choices and are handled by their callers.
"""

from __future__ import annotations

import errno

from server.cadlink.intake.ingest import IngestRefusal
from server.cadlink.wgreturn import SnapshotUnavailable, WgReturnChanged, WgReturnError, WgReturnUnsupported
from server.mesh.imported import RoleResolutionError

#: A refusal that states what the user can change, in their terms.
USER_WORDED = None
#: The stage whose refusal wraps reading the return itself.
BUNDLE_VALIDATION_STAGE = "stage 1 bundle validation"
#: Gates whose refusals are written for the user (``intake/ingest.py``).
_USER_WORDED_STAGES = frozenset({
    "stage 2 project gate", "stage 2 instance gate", "stage 2 scope gate", "stage 5 role resolution",
    "stage 6 symmetry",
})
#: The code a Fusion request ends with for a :data:`USER_WORDED` refusal.
MODEL_NEEDS_CHANGE = "model_needs_change"


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
        if exc.corruption:
            return "snapshot_invalid"
        if exc.stage == BUNDLE_VALIDATION_STAGE:
            # Classified by what reading the return raised: WG's own bug while
            # reading (not a refusal of the return) is WG's failure.
            cause = exc.__cause__
            if isinstance(cause, (OSError, ValueError)):
                return return_failure_code(cause)
            return "preparation_failed"
        # A source selector that does not exist in its checksum-bound STEP
        # is inconsistent export metadata. Keep that raw identifier detail
        # with the developers, even though it failed during role resolution.
        cause = exc.__cause__
        if isinstance(cause, RoleResolutionError) and cause.invalid_selector:
            return "preparation_failed"
        if exc.area_drift_sources or exc.user_worded or exc.stage in _USER_WORDED_STAGES:
            return USER_WORDED
        return "preparation_failed"
    if isinstance(exc, WgReturnError):
        return return_failure_code(exc)
    if _disk_full(exc):
        return "disk_full"
    return "preparation_failed"


def refusal_code(exc: BaseException) -> str:
    """The reason a Fusion request's failed preparation ends with: a
    :data:`USER_WORDED` refusal is ``model_needs_change`` (its own words,
    never recorded as WG's failure); anything else as classified."""

    return preparation_failure_code(exc) or MODEL_NEEDS_CHANGE
