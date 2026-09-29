"""The CAD intent a job holds before it has a request, and Solve again.

A CAD solve WG accepts is a job in status ``preparing`` whose ``config_json`` is a
:class:`CadSolveIntent` instead of a ``SolveRequest`` (docs/architecture/
CAD-OPERATIONS.md, "A CAD solve is a job"). This module is the intent and what
continues it, and it imports nothing that imports the jobs package, so the
runtime can load it eagerly. The preparation itself is ``cad_preparation``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
from dataclasses import dataclass, replace
from typing import Any

from server.cadlink.operations import (
    PREPARE_AND_SOLVE,
    prepare_and_solve_request,
    request_digest,
)

#: ``config_json["type"]`` of a job that has an intent instead of a request.
CAD_INTENT = "cad_intent"

#: The stage a job waits in when an approved update restart handed it back
#: (contract §4.2). The lane takes it up again by itself.
STAGE_WAITING_FOR_RESTART = "waiting-for-update-restart"

#: What binding a prepared CAD solve came to (``JobRuntime._bind_cad_job``): queued;
#: the job was stopped meanwhile; an approved update restart came first.
BOUND, BIND_STOPPED, BIND_BLOCKED = "bound", "stopped", "blocked"

#: What a preparation that was running when WG stopped reads as. The words of
#: ``recover_operations`` today, so the reason and message are the same whichever
#: lifecycle owns the solve; F1 changes "Press Solve now" to "Solve again" for both.
INTERRUPTED_MESSAGE = (
    "WG stopped while preparing this request. Press Solve now to prepare it again."
)



@dataclass(frozen=True)
class CadSolveIntent:
    """What WG accepted: the solve of one return, before it has a request.

    The identity fields (operation, return, bundle, manifest) are the delivery's
    and never change. The rest is what the user's press asked for:

    - ``setup_revision_id``: an explicit setup. It always wins; without one the
      setup the previous run recorded, then the project's own, then WG's
      defaults apply.
    - ``frame_axis``: the solver frame axis the user saw when they pressed
      Solve. An unlinked snapshot is solved only along it, and Solve again holds
      it until a press names another.
    - ``approvals``: ``{preparation_id, finding_ids}`` the user reviewed. Applied
      only when this preparation is that one, and never carried to another.
    - ``submit``: false only for a preparation the user asked to stop short of
      solving (it ends ``ready_to_solve``).

    What a job has *recorded* (its setup, its preparation and the approvals given
    on it) is in ``task_metadata.cad``, not here: a Solve again copies that record
    into the new job, which resumes the preparation when nothing it depends on
    changed.
    """

    operation_id: str
    bundle_path: str
    manifest_sha256: str
    return_id: str
    setup_revision_id: str | None = None
    frame_axis: str | None = None
    approvals: Mapping[str, Any] | None = None
    submit: bool = True
    label: str | None = None
    parent_job_id: str | None = None
    submission_key: str | None = None

    def to_config(self) -> dict[str, Any]:
        """The ``config_json`` of a preparing job."""

        config: dict[str, Any] = {
            "type": CAD_INTENT,
            "operation_id": self.operation_id,
            "bundle_path": self.bundle_path,
            "manifest_sha256": self.manifest_sha256,
            "return_id": self.return_id,
        }
        optional: dict[str, Any] = {
            "setup_revision_id": self.setup_revision_id,
            "frame_axis": self.frame_axis,
            "approvals": dict(self.approvals) if self.approvals else None,
            "label": self.label,
            "parent_job_id": self.parent_job_id,
            "submission_key": self.submission_key,
        }
        config.update({key: value for key, value in optional.items() if value is not None})
        if not self.submit:
            config["submit"] = False
        return config

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "CadSolveIntent":
        if config.get("type") != CAD_INTENT:
            raise ValueError("this job holds no CAD intent")
        required = {
            name: config.get(name)
            for name in ("operation_id", "bundle_path", "manifest_sha256", "return_id")
        }
        missing = sorted(name for name, value in required.items() if not isinstance(value, str) or not value)
        if missing:
            raise ValueError(f"a CAD intent names {', '.join(missing)}")
        approvals = config.get("approvals")
        return cls(
            **required,
            setup_revision_id=_optional_text(config.get("setup_revision_id")),
            frame_axis=_optional_text(config.get("frame_axis")),
            approvals=dict(approvals) if isinstance(approvals, Mapping) else None,
            submit=config.get("submit", True) is not False,
            label=_optional_text(config.get("label")),
            parent_job_id=_optional_text(config.get("parent_job_id")),
            submission_key=_optional_text(config.get("submission_key")),
        )

    def delivery_digest(self) -> str:
        """The digest of the delivery this intent came from (``request_digest``).

        The same sha256 the operations ledger holds for the command, so a key
        replayed for a different return is a conflict, not a second solve.
        """

        target, inputs = prepare_and_solve_request(
            return_id=self.return_id,
            bundle_path=self.bundle_path,
            manifest_sha256=self.manifest_sha256,
        )
        return request_digest(PREPARE_AND_SOLVE, target, inputs)


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def intent_of(row: Mapping[str, Any]) -> CadSolveIntent | None:
    """The intent a job row holds, or None for a job that has a request."""

    config = row.get("config_json")
    if not isinstance(config, Mapping) or config.get("type") != CAD_INTENT:
        return None
    return CadSolveIntent.from_config(config)


#: What a solve records of itself (``task_metadata.cad``) that its Solve again
#: continues from: its retained snapshot, the captured-document state cleanup
#: keeps for it, the setup it prepared with, and its preparation with the
#: approvals given on it.
CARRIED = ("snapshot", "return_state_hash", "setup", "preparation")


def solve_again_intent(
    parent: Mapping[str, Any],
    *,
    setup_revision_id: str | None = None,
    frame_axis: str | None = None,
    approve_preparation_id: str | None = None,
    approve_finding_ids: Sequence[str] = (),
    submit: bool = True,
) -> CadSolveIntent:
    """The intent of the job that continues an unbound CAD job (Solve again).

    What the user names now replaces what the parent had; what they did not name
    is carried, as an operation's follow-up carried it: the frame axis the parent
    was held to (the setup, the preparation and its approvals travel in the
    record, ``carried_record``).
    """

    intent = intent_of(parent)
    if intent is None:
        raise ValueError("only a CAD solve that never had a request can be solved again")
    return replace(
        intent,
        setup_revision_id=setup_revision_id,
        frame_axis=frame_axis or intent.frame_axis,
        approvals=(
            {"preparation_id": approve_preparation_id, "finding_ids": list(approve_finding_ids)}
            if approve_preparation_id and approve_finding_ids
            else None
        ),
        submit=submit,
        parent_job_id=str(parent["id"]),
    )


def carried_record(parent: Mapping[str, Any]) -> dict[str, Any]:
    """The parts of a job's ``task_metadata.cad`` its Solve again starts from."""

    cad = cad_of(parent)
    return {key: copy.deepcopy(cad[key]) for key in CARRIED if cad.get(key)}


def approval_map(items: Any) -> dict[tuple[str, str], Mapping[str, str]]:
    return {
        (str(item["preparation_id"]), str(item["finding_id"])): {
            "preparation_id": str(item["preparation_id"]),
            "finding_id": str(item["finding_id"]),
        }
        for item in items or ()
        if isinstance(item, Mapping) and item.get("preparation_id") and item.get("finding_id")
    }


def cad_of(row: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = row.get("task_metadata")
    cad = metadata.get("cad") if isinstance(metadata, Mapping) else None
    return cad if isinstance(cad, Mapping) else {}


__all__ = [
    "BIND_BLOCKED",
    "BIND_STOPPED",
    "BOUND",
    "CAD_INTENT",
    "CadSolveIntent",
    "CARRIED",
    "approval_map",
    "carried_record",
    "INTERRUPTED_MESSAGE",
    "STAGE_WAITING_FOR_RESTART",
    "cad_of",
    "intent_of",
    "solve_again_intent",
]
