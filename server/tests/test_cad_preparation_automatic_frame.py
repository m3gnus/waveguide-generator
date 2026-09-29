"""A Solve that was never shown an axis uses WG's confident automatic one.

docs/architecture/CAD-OPERATIONS.md, "Unlinked solver frame". A Fusion-initiated
Solve of a never-confirmed model: when ``frame_infer`` is confident (status
``automatic``) the model is meshed and solved along that axis and the operation
records it as automatic; no confirmation is written. When it is not confident the
solve still stops at the frame gate. Uses the same production ingest as
``test_cad_preparation_solver_frame``; only the survey's verdict is set.
"""

from __future__ import annotations

from typing import Any

import pytest

from server.cadlink import preparation, solver_frame
from server.jobs import cad_preparation as job_preparation
from server.cadlink.frame_infer import ALGORITHM_VERSION
from server.cadlink.solver_frame import (
    confirm_frame,
    record_confirmation_key,
    record_frame_refusal,
    record_solved_frame_provenance,
)

from server.cadlink import ingest as ingest_module

from cad_backends import BACKENDS, Harness, backend_fixture, old_only
from test_cad_preparation import _accept
from test_cad_preparation_solver_frame import (
    Recording,
    _authored,
    _prepare,
    _received,
    _record,
    _waiting_for_frame,
)


@pytest.fixture(params=BACKENDS)
def real(request, tmp_path, monkeypatch) -> tuple[Harness, Recording]:
    backend = backend_fixture(request, tmp_path)
    harness = next(backend)
    mesher = Recording()
    monkeypatch.setattr(ingest_module, "build_imported_mesh_isolated", mesher)
    yield harness, mesher
    next(backend, None)


def _verdict(monkeypatch: pytest.MonkeyPatch, *, status: str, axis: str | None) -> list[str]:
    """The survey's answer for a snapshot, cached as ``ensure_frame_suggestion`` caches it."""

    surveyed: list[str] = []

    def survey(store, record):
        surveyed.append(str(record["ingest_id"]))
        return store.record_frame_suggestion(
            str(record["manifest_sha256"]),
            ALGORITHM_VERSION,
            {
                "algorithm": ALGORITHM_VERSION,
                "status": status,
                "axis": axis,
                "confidence": 0.9,
                "snapshotSha256": record["manifest_sha256"],
                "reason": "test verdict",
            },
            str(record["ingest_id"]),
        )

    monkeypatch.setattr(preparation, "ensure_frame_suggestion", survey)
    monkeypatch.setattr(job_preparation, "ensure_frame_suggestion", survey)
    return surveyed


def _key(harness, summary) -> str:
    return record_confirmation_key(_record(harness, summary))


def test_a_confident_never_confirmed_solve_solves_along_the_automatic_axis(real, monkeypatch) -> None:
    harness, mesher = real
    _verdict(monkeypatch, status="automatic", axis="+x")
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)

    summary = _prepare(harness)  # no expected axis: Fusion asked, nothing was shown

    assert (summary["state"], summary["jobId"]) == ("accepted", "job-1"), summary
    assert summary["frameAxisAutomatic"] == "+x"
    record = _record(harness, summary)
    assert record["normalisation"]["solver_frame"]["axis"] == "+x"
    assert mesher.calls[-1]["options"]["solver_frame"]["axis"] == "+x"
    assert harness.submitted[0].geometry.ingest_id == summary["preparationId"]
    # Not a confirmation: nothing was written, and the record says automatic.
    assert harness.store.get_frame_confirmation(_key(harness, summary)) is None
    assert record_solved_frame_provenance(harness.store, record) == "automatic"
    # The job's own record says the same, from the same rule, before it existed.
    frame = harness.provenance[0]["frame"]
    assert (frame["axis"], frame["provenance"], frame["confirmed"]) == ("+x", "automatic", False)


def test_a_second_solve_of_the_snapshot_reuses_the_mesh_along_the_automatic_axis(real, monkeypatch) -> None:
    harness, mesher = real
    _verdict(monkeypatch, status="automatic", axis="-y")
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    first = _prepare(harness)
    meshed = len(mesher.calls)
    assert first["frameAxisAutomatic"] == "-y"
    # As modelled to survey, then again along the axis it found.
    assert meshed == 2

    _accept(harness.store, "cmd-2", *_bundle_of(harness, "authored"))
    second = _prepare(harness, "cmd-2")

    assert second["state"] == "accepted" and second["frameAxisAutomatic"] == "-y"
    # The prepared mesh along -y is reused: no as-modelled pass, no new mesh.
    assert len(mesher.calls) == meshed


def _bundle_of(harness, name: str) -> tuple[str, str]:
    import hashlib

    bundle = harness.workspace / "wgreturn" / f"{name}.wgreturn"
    return f"wgreturn/{name}.wgreturn", "sha256:" + hashlib.sha256((bundle / "wgreturn.json").read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("status", "axis"),
    [("ask", None), ("unavailable", None), ("automatic", "+z-not-an-axis")],
)
def test_a_solve_without_a_confident_axis_still_stops_with_the_reason(real, monkeypatch, status, axis) -> None:
    harness, _mesher = real
    _verdict(monkeypatch, status=status, axis=axis)
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)

    summary = _prepare(harness)

    assert _waiting_for_frame(summary), summary
    assert "solver frame" in summary["message"]
    assert summary["frameAxisAutomatic"] is None
    assert harness.submitted == []


def test_a_confident_axis_the_snapshot_does_not_allow_is_not_used() -> None:
    from server.cadlink.solver_frame import automatic_solve_axis

    confident = {"status": "automatic", "axis": "+x"}
    assert automatic_solve_axis(confident, ("+z",)) is None  # a declared half: only as modelled
    assert automatic_solve_axis(confident, ("+z", "+x")) == "+x"
    assert automatic_solve_axis({"status": "ask", "axis": "+x"}, ("+x",)) is None
    assert automatic_solve_axis(None, ("+x",)) is None


def test_the_automatic_axis_is_never_taken_as_a_confirmation_later(real, monkeypatch) -> None:
    harness, _mesher = real
    verdicts = _verdict(monkeypatch, status="automatic", axis="+x")
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    first = _prepare(harness)
    assert first["frameAxisAutomatic"] == "+x"

    # A different, unsaved snapshot: WG is not confident about it, and the
    # earlier automatic solve does not stand in for a confirmation.
    verdicts.clear()
    _verdict(monkeypatch, status="ask", axis=None)
    other = b"STEP another authored"
    _received(harness, "other", _authored(other, native_id=None), other, command="cmd-3")
    stopped = _prepare(harness, "cmd-3")
    assert _waiting_for_frame(stopped), stopped
    assert harness.store.get_frame_confirmation(_key(harness, first)) is None


def test_a_later_change_is_solved_along_the_chosen_axis(real, monkeypatch) -> None:
    harness, mesher = real
    _verdict(monkeypatch, status="automatic", axis="+x")
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    first = _prepare(harness)
    assert first["frameAxisAutomatic"] == "+x"

    confirm_frame(harness.store, _record(harness, first), "+y")  # the user's Change
    _accept(harness.store, "cmd-2", *_bundle_of(harness, "authored"))
    second = _prepare(harness, "cmd-2")

    assert second["state"] == "accepted", second
    assert second["frameAxisAutomatic"] is None
    assert _record(harness, second)["normalisation"]["solver_frame"]["axis"] == "+y"
    assert mesher.calls[-1]["options"]["solver_frame"]["axis"] == "+y"
    changed = harness.provenance[-1]["frame"]
    assert (changed["axis"], changed["provenance"], changed["confirmed"]) == ("+y", "chosen", True)
    # The automatic record of the first solve is a confirmed-elsewhere frame now.
    assert record_solved_frame_provenance(harness.store, _record(harness, first)) is None


def test_the_jobs_gate_accepts_only_the_frame_the_record_was_meshed_in(real, monkeypatch) -> None:
    harness, _mesher = real
    _verdict(monkeypatch, status="automatic", axis="+x")
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    summary = _prepare(harness)
    record = _record(harness, summary)
    assert record_frame_refusal(harness.store, record) is None

    # A record meshed as modelled while WG is confident about +x is not solved.
    modelled = _record_meshed_in(harness, record, "+z")
    refusal = record_frame_refusal(harness.store, modelled)
    assert refusal is not None
    assert "Prepare it again in WG" in refusal and "+x" in refusal


def _record_meshed_in(harness, record: dict[str, Any], axis: str) -> dict[str, Any]:
    import copy

    changed = copy.deepcopy(record)
    changed["normalisation"]["solver_frame"]["axis"] = axis
    return changed


@old_only("operation attempt stage instrumentation", "test_a_confident_never_confirmed_solve_solves_along_the_automatic_axis")
def test_the_second_mesh_is_made_at_most_once_even_if_the_two_sides_disagree(real, monkeypatch) -> None:
    """A drift between the manifest side and the record side must not loop."""

    harness, mesher = real
    _verdict(monkeypatch, status="automatic", axis="+x")
    # The record side keeps naming an axis nothing is ever meshed in.
    monkeypatch.setattr(preparation, "record_automatic_axis", lambda store, record: "-y")
    # The gate reads the same record side, so it refuses the +x mesh too.
    monkeypatch.setattr(solver_frame, "record_automatic_axis", lambda store, record: "-y")
    stages: list[str] = []
    real_advance = preparation._advance

    def advance(ctx, operation_id, generation, **fields):
        if fields.get("stage"):
            stages.append(fields["stage"])
        return real_advance(ctx, operation_id, generation, **fields)

    monkeypatch.setattr(preparation, "_advance", advance)
    passes: list[bool] = []
    real_prepare = preparation._prepare_sync

    def counted(*args, **kwargs):
        passes.append(bool(kwargs.get("automatic_retry")))
        return real_prepare(*args, **kwargs)

    monkeypatch.setattr(preparation, "_prepare_sync", counted)
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)

    summary = _prepare(harness)

    assert passes == [False, True]
    assert len(mesher.calls) <= 2
    # The bound ends at the frame gate, never at an accepted solve.
    assert _waiting_for_frame(summary), summary
    assert harness.submitted == []
    # A stage only moves forward: validating is entered once.
    assert stages.count("validating") == 1


@old_only("operation summary reads its bound job", "test_a_confident_never_confirmed_solve_solves_along_the_automatic_axis")
def test_the_operations_automatic_axis_is_derived_from_the_jobs_own_record(real, monkeypatch) -> None:
    """One read: were the frame confirmed meanwhile, both say so, never one each."""

    from server.cadlink import preparation as prep

    harness, _mesher = real
    _verdict(monkeypatch, status="automatic", axis="+x")
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    waiting = _prepare(harness, submit=False)
    record = _record(harness, waiting)
    # The rule still says automatic (a stale first read) while a confirmation
    # exists by the time the record is collected.
    confirm_frame(harness.store, record, "+x")
    monkeypatch.setattr(prep, "record_solved_frame_provenance", lambda *_a: "automatic")

    summary = _prepare(harness)

    assert summary["state"] == "accepted", summary
    assert harness.provenance[-1]["frame"]["provenance"] in {"chosen", "suggested"}
    assert summary["frameAxisAutomatic"] is None
