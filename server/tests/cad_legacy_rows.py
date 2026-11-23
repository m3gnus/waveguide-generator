"""Writers of CAD operation rows that only earlier WG versions made.

``CadLinkStore`` no longer advances, binds, approves or records preparations on
``cad_operations`` rows itself: a CAD solve is a job. Rows those versions wrote
are still read (the upgrade sweep, the operations API, the solver frame), so
tests build them with these copies of the retired store methods.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
from typing import Any

from server.cadlink.identity import utc_now
from server.cadlink.operations import (
    CANCELLED,
    CANCEL_REQUESTED,
    MUTATING_KINDS,
    PROCESSING,
    STAGES,
    STAGE_READY,
    TERMINAL_STATES,
    canonical_json,
)
from server.cadlink.store import CadLinkStore, _require_generation


def attempt_is_current(conn: Any, operation_id: str, generation: int) -> bool:
    """The fence, for use inside another write's transaction on the store."""

    row = conn.execute(
        "SELECT state, attempt_generation FROM cad_operations WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()
    return (
        row is not None
        and row["state"] == PROCESSING
        and int(row["attempt_generation"]) == int(generation)
    )


def request_cancel(store: CadLinkStore, operation_id: str) -> dict[str, Any] | None:
    """Dismiss an operation: at once when idle, at the attempt's next step when running.

    An operation no attempt is working on becomes ``cancelled``. One an
    attempt holds becomes ``cancel_requested``, which fences that attempt;
    it then records ``cancelled``. A terminal operation is returned as it
    is, and so is a CAD mutation under way: it may need recovery from the
    document rather than cancellation. Rows earlier builds dismissed this way
    are still read.
    """

    store.initialize()
    with store._lock, store._transaction() as conn:
        row = conn.execute(
            "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        state = str(row["state"])
        if state in TERMINAL_STATES or state == CANCEL_REQUESTED:
            return store._row(row)
        if state == PROCESSING and row["kind"] in MUTATING_KINDS:
            return store._row(row)
        new_state = CANCEL_REQUESTED if state == PROCESSING else CANCELLED
        conn.execute(
            "UPDATE cad_operations SET state = ?, updated_at = ? WHERE operation_id = ?",
            (new_state, utc_now(), operation_id),
        )
        row = conn.execute(
            "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
    return store._row(row)


def advance_operation(
    store: CadLinkStore,
    operation_id: str,
    generation: int,
    *,
    stage: str | None = None,
    snapshot: Mapping[str, Any] | None = None,
    preparation_id: str | None = None,
    setup_revision_id: str | None = None,
    frame_axis: str | None = None,
) -> dict[str, Any] | None:
    """Move the attempt holding ``generation`` on, recording what it made.

    The fence: conditional on that generation being current and the
    operation still ``processing``. A takeover, a cancellation or an
    outcome changes one of those, and the obsolete attempt then gets None
    and must stop without committing anything.

    ``setup_revision_id`` is the setup the attempt selected, recorded
    before it meshes so a later revision-less retry still has it. A bound
    request is never rewritten: its revision stays the one it was bound with.

    ``frame_axis`` is the solver frame axis the user's Solve showed; it
    replaces the one held, and omitting it keeps that one.
    """

    attempt = _require_generation(generation)
    if stage is not None and stage not in STAGES:
        raise ValueError(f"unknown CAD operation stage {stage!r}")
    store.initialize()
    with store._lock, store._transaction() as conn:
        cursor = conn.execute(
            "UPDATE cad_operations SET stage = COALESCE(?, stage), "
            "snapshot_json = COALESCE(?, snapshot_json), "
            "preparation_id = COALESCE(?, preparation_id), "
            "setup_revision_id = CASE WHEN request_json IS NULL "
            "THEN COALESCE(?, setup_revision_id) ELSE setup_revision_id END, "
            "frame_axis = COALESCE(?, frame_axis), updated_at = ? "
            "WHERE operation_id = ? AND attempt_generation = ? AND state = ?",
            (
                stage,
                canonical_json(dict(snapshot)) if snapshot is not None else None,
                preparation_id,
                setup_revision_id,
                frame_axis,
                utc_now(),
                operation_id,
                attempt,
                PROCESSING,
            ),
        )
        if cursor.rowcount != 1:
            return None
        row = conn.execute(
            "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
    return store._row(row)


def bind_request(
    store: CadLinkStore,
    operation_id: str,
    generation: int,
    *,
    setup_revision_id: str,
    request_json: str,
) -> dict[str, Any] | None:
    """Bind the exact solve request, immediately before it is submitted.

    This is the binding point: from here the request is immutable, and a
    recovery resubmits exactly it. Binding again with the same request is
    a no-op; a different one is a ``ValueError`` -- a changed setup
    is a new operation. Fenced like ``advance_operation``.
    """

    attempt = _require_generation(generation)
    store.initialize()
    with store._lock, store._transaction() as conn:
        row = conn.execute(
            "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if (
            row is None
            or row["state"] != PROCESSING
            or int(row["attempt_generation"]) != attempt
        ):
            return None
        if row["request_json"] is not None:
            if row["request_json"] != request_json:
                raise ValueError(
                    f"operation {operation_id!r} is already bound to another request"
                )
            return store._row(row)
        conn.execute(
            "UPDATE cad_operations SET setup_revision_id = ?, request_json = ?, "
            "updated_at = ? WHERE operation_id = ?",
            (setup_revision_id, request_json, utc_now(), operation_id),
        )
        row = conn.execute(
            "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
    return store._row(row)


def add_approvals(
    store: CadLinkStore,
    operation_id: str,
    preparation_id: str,
    finding_ids: Iterable[str],
    *,
    generation: int | None = None,
) -> dict[str, Any] | None:
    """Approve blocking findings of one of this operation's preparations.

    Merged into the stored approvals in one transaction, so two approvals
    never drop each other. Each is bound to its preparation, and only a
    finding that preparation reported as blocking can be approved
    (``ValueError`` otherwise). An attempt passes its ``generation`` and is
    fenced by it; the user's own approval is refused (None) only on a
    finished operation.
    """

    wanted = list(dict.fromkeys(str(item) for item in finding_ids))
    if not preparation_id or not all(wanted):
        raise ValueError("an approval names a preparation and its finding ids")
    store.initialize()
    with store._lock, store._transaction() as conn:
        row = conn.execute(
            "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None or row["state"] in TERMINAL_STATES:
            return None
        if generation is not None and not attempt_is_current(
            conn, operation_id, _require_generation(generation)
        ):
            return None
        preparation = conn.execute(
            "SELECT blocking_findings_json FROM cad_preparations "
            "WHERE preparation_id = ? AND operation_id = ?",
            (preparation_id, operation_id),
        ).fetchone()
        if preparation is None:
            raise ValueError(
                f"preparation {preparation_id!r} is not one of this operation's"
            )
        unknown = sorted(set(wanted) - set(json.loads(preparation["blocking_findings_json"])))
        if unknown:
            raise ValueError(
                f"preparation {preparation_id!r} reported no blocking finding {unknown[0]!r}"
            )
        approvals = json.loads(row["approvals_json"]) if row["approvals_json"] else []
        held = {(item["preparation_id"], item["finding_id"]) for item in approvals}
        approvals += [
            {"preparation_id": preparation_id, "finding_id": finding}
            for finding in wanted
            if (preparation_id, finding) not in held
        ]
        conn.execute(
            "UPDATE cad_operations SET approvals_json = ?, updated_at = ? "
            "WHERE operation_id = ?",
            (canonical_json(approvals), utc_now(), operation_id),
        )
        row = conn.execute(
            "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
    return store._row(row)


def record_preparation(
    store: CadLinkStore,
    operation_id: str,
    generation: int,
    *,
    preparation_id: str,
    snapshot_sha256: str,
    setup_revision_id: str | None,
    ingest_id: str,
    report_sha256: str | None,
    blocking_finding_ids: list[str],
    meshing_semantics: str | None = None,
) -> dict[str, Any] | None:
    """Record what the attempt prepared, and move it to ``ready``. Fenced.

    A preparation the attempt resumed is already recorded, and keeps the
    generation of the attempt that made it.
    """

    attempt = _require_generation(generation)
    store.initialize()
    now = utc_now()
    with store._lock, store._transaction() as conn:
        if not attempt_is_current(conn, operation_id, attempt):
            return None
        conn.execute(
            "INSERT OR IGNORE INTO cad_preparations (preparation_id, operation_id, "
            "attempt_generation, snapshot_sha256, setup_revision_id, ingest_id, "
            "report_sha256, blocking_findings_json, meshing_semantics, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                preparation_id,
                operation_id,
                attempt,
                snapshot_sha256,
                setup_revision_id,
                ingest_id,
                report_sha256,
                canonical_json(list(blocking_finding_ids)),
                meshing_semantics,
                now,
            ),
        )
        conn.execute(
            "UPDATE cad_operations SET preparation_id = ?, stage = ?, updated_at = ? "
            "WHERE operation_id = ?",
            (preparation_id, STAGE_READY, now, operation_id),
        )
        row = conn.execute(
            "SELECT rowid AS accepted_seq, * FROM cad_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
    return store._row(row)
