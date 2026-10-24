"""Fusion always chooses an axis, retaining weak evidence and manual overrides."""

from __future__ import annotations

from typing import Any

import pytest

from server.jobs import cad_preparation as job_preparation
from server.jobs.cad_intent import intent_of
from server.cadlink.interpretation.frame_infer import ALGORITHM_VERSION
from server.cadlink.interpretation.solver_frame import (
    record_confirmation_key,
)
from server.cadlink.intake.readings import (
    confirm_frame,
    record_frame_refusal,
    record_solved_frame_provenance,
)

from server.cadlink.intake import ingest as ingest_module

from cad_backends import BACKENDS, Harness, backend_fixture
from test_cad_preparation import _accept
from test_cad_preparation_solver_frame import (
    Recording,
    _authored,
    _prepare as _manual_prepare,
    _received,
    _record,
    _waiting_for_frame,
)


def _prepare(harness, operation_id="cmd-1", **kwargs):
    kwargs.setdefault("expected_frame_axis", None)
    return _manual_prepare(harness, operation_id, **kwargs)


@pytest.fixture(params=BACKENDS)
def real(request, tmp_path, monkeypatch) -> tuple[Harness, Recording]:
    backend = backend_fixture(request, tmp_path)
    harness = next(backend)
    mesher = Recording()
    monkeypatch.setattr(ingest_module, "build_imported_mesh_isolated", mesher)
    yield harness, mesher
    next(backend, None)


def _verdict(
    monkeypatch: pytest.MonkeyPatch, *, status: str, axis: str | None,
    evidence: dict | None = None,
) -> list[str]:
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
                "evidence": evidence or {},
                "snapshotSha256": record["manifest_sha256"],
                "reason": "test verdict",
            },
            str(record["ingest_id"]),
        )

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
def test_a_solve_without_a_confident_axis_decides_and_records_a_diagnostic(
    real, monkeypatch, caplog, status, axis
) -> None:
    harness, _mesher = real
    evidence = {"normals": {"axis": "-x", "strength": 0.2}}
    _verdict(monkeypatch, status=status, axis=axis, evidence=evidence)
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)

    summary = _prepare(harness)

    assert summary["state"] == "accepted", summary
    assert summary["frameAxisAutomatic"] == "-x"
    assert _record(harness, summary)["normalisation"]["solver_frame"]["axis"] == "-x"
    assert harness.store.get_frame_confirmation(_key(harness, summary)) is None
    diagnostics = [r.cad_diagnostic for r in caplog.records if hasattr(r, "cad_diagnostic")]
    assert len(diagnostics) == 1
    assert diagnostics[0]["stage"] == "frame-fallback"
    assert diagnostics[0]["evidence"]["suggestion"]["status"] == status
    assert diagnostics[0]["chosen_axis"] == "-x"
    # One decision, recorded on the prepared record as a fallback, never as
    # confident inference; the job states the same.
    record = _record(harness, summary)
    decision = harness.store.get_frame_decision(record["ingest_id"])
    assert (decision["axis"], decision["provenance"]) == ("-x", "automatic-fallback")
    assert record_solved_frame_provenance(harness.store, record) == "automatic-fallback"
    frame = harness.provenance[0]["frame"]
    assert (frame["provenance"], frame["decision"]["provenance"]) == ("automatic", "automatic-fallback")
    # The preparation harness stubs composition. Cross the actual runtime
    # validation boundary too, so a second frame gate cannot reintroduce asking.
    validated = harness._loop.run(harness.runtime._validate_imported_submission(harness.submitted[0]))
    assert validated.record["ingest_id"] == summary["preparationId"]


def test_a_confident_axis_the_snapshot_does_not_allow_is_not_used() -> None:
    from server.cadlink.interpretation.solver_frame import automatic_solve_axis

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
    solved = _prepare(harness, "cmd-3")
    assert solved["state"] == "accepted", solved
    assert solved["frameAxisAutomatic"] == "+z"
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

    # The same record stated as meshed along another axis than WG decided is not solved.
    modelled = _record_meshed_in(harness, record, "+z")
    refusal = record_frame_refusal(harness.store, modelled)
    assert refusal is not None
    assert "Prepare it again in WG" in refusal and "+x" in refusal


def _record_meshed_in(harness, record: dict[str, Any], axis: str) -> dict[str, Any]:
    import copy

    changed = copy.deepcopy(record)
    changed["normalisation"]["solver_frame"]["axis"] = axis
    return changed


def test_the_jobs_second_mesh_is_bounded_when_the_two_sides_disagree(tmp_path, monkeypatch) -> None:
    """The job lane's M1e second pass stops even if the record keeps disagreeing."""

    from cad_backends import JobsHarness

    harness = JobsHarness(tmp_path)
    try:
        mesher = Recording()
        monkeypatch.setattr(ingest_module, "build_imported_mesh_isolated", mesher)
        _verdict(monkeypatch, status="automatic", axis="-y")
        # The record keeps stating another axis than the one asked for.
        monkeypatch.setattr(job_preparation, "_record_frame_axis", lambda record: "+x")
        passes: list[bool] = []
        real_prepare = job_preparation.prepare_job_sync

        def counted(*args, **kwargs):
            passes.append(bool(kwargs.get("automatic_retry")))
            assert len(passes) <= 2, "automatic-axis disagreement recursed beyond the second pass"
            return real_prepare(*args, **kwargs)

        monkeypatch.setattr(job_preparation, "prepare_job_sync", counted)
        step = b"STEP authored"
        _received(harness, "authored", _authored(step), step)

        summary = _prepare(harness)

        assert passes == [False, True]
        assert len(mesher.calls) == 2
        assert mesher.calls[-1]["options"]["solver_frame"]["axis"] == "-y"
        assert summary["reason"] == "preparation_failed", summary
        assert not _waiting_for_frame(summary)
        assert harness.submitted == []
        row = harness.latest_job()
        # A failed preparation is held for the user (Try again), not a failed run.
        assert row["status"] == "held" and intent_of(row) is not None
        events = harness.jobs_store.replay_events(0)
        assert events is not None
        assert sum(event["payload"].get("stage") == "validating" for event in events) == 1
    finally:
        harness.close()


@pytest.mark.parametrize("denial", ["asymmetric-section: section disagrees", "reduced mesh refused"])
@pytest.mark.parametrize("whole_model", [False, True])
def test_failed_cut_recovery_uses_full_domain_and_precedes_frame_and_findings(
    real, monkeypatch, caplog, denial, whole_model
) -> None:
    from server.cadlink.interpretation.cut_recovery import CutRecovery
    from test_cadlink_cut_reflection import _observations

    harness, mesher = real
    observations = _observations()
    calls = []
    monkeypatch.setattr(ingest_module, "observe_record_mesh", lambda built: (
        None if whole_model and calls[-1].get("symmetry_mode") == "full" else observations
    ))
    monkeypatch.setattr(ingest_module, "assess_cut_recovery", lambda *args, **kwargs: CutRecovery(
        planes=("x0",), kept_sides={"x0": "positive"},
    ))
    original = mesher.__call__

    def mesh(*args, **kwargs):
        options = dict(kwargs.get("options") or {})
        calls.append(options)
        if options.get("declared_cut_planes"):
            raise ingest_module.IngestRefusal("stage 6 symmetry", denial)
        return original(*args, **kwargs)

    monkeypatch.setattr(ingest_module, "build_imported_mesh_isolated", mesh)
    # The full-domain fallback still has an open half: it must be refused on
    # that domain, never masked by a frame survey/retry or a blocking finding.
    if whole_model:
        _verdict(monkeypatch, status="ask", axis=None)
    else:
        monkeypatch.setattr(job_preparation, "ensure_frame_suggestion", lambda *_args: pytest.fail(
            "domain failure reached the frame survey"
        ))
    step = b"STEP authored open half"
    _received(harness, "authored", _authored(step), step)
    summary = _prepare(harness)

    if whole_model:
        assert summary["state"] == "accepted", summary
        assert len(harness.submitted) == 1
    else:
        # Deterministic: the same open half can never be prepared, so it is
        # rejected in calm words, never a "Try again" that cannot succeed.
        assert summary["reason"] == "model_cut_unsolvable", summary
        assert "cut open" in summary["message"] and "uncut model" in summary["message"]
        assert denial.split(":")[-1].strip() not in summary["message"]
        assert harness.submitted == []
    record = _record(harness, summary)
    recovery = record["domain_interpretation"]["cut_recovery"]
    assert recovery["recoverable"] is False
    assert recovery["failures"]["x0"][0]["code"] in {"mesh-denied", "asymmetric-section"}
    if whole_model:
        assert record["domain_decision"]["refusal"] is None
    else:
        assert record["domain_decision"]["refusal"]["code"] == "imported_open_half_shell"
    assert record["symmetry"]["cut_planes"] == []
    assert calls[-1]["symmetry_mode"] == "full"
    assert len(calls) == 3
    if not whole_model:
        assert any(getattr(r, "cad_diagnostic", {}).get("stage") == "domain" for r in caplog.records)


def test_fusion_preserves_a_saved_v1_axis_without_asking_again(real, monkeypatch) -> None:
    from server.cadlink.interpretation.solver_frame import CONTRACT_V1

    harness, mesher = real
    _verdict(monkeypatch, status="ask", axis=None)
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    ready = _prepare(harness, submit=False)
    harness.store.record_frame_confirmation(
        _key(harness, ready), {"contract": CONTRACT_V1, "export_frame": "root-component"}, "+y"
    )
    solved = _prepare(harness)
    assert solved["state"] == "accepted", solved
    assert _record(harness, solved)["normalisation"]["solver_frame"]["axis"] == "+y"
    assert mesher.calls[-1]["options"]["solver_frame"]["axis"] == "+y"
    assert harness.provenance[-1]["frame"]["provenance"] == "carried"
    validated = harness._loop.run(harness.runtime._validate_imported_submission(harness.submitted[0]))
    assert validated.record["ingest_id"] == solved["preparationId"]


def test_a_submission_frame_conflict_never_asks_fusion_to_choose_an_axis(real, monkeypatch) -> None:
    class FrameConflict(ValueError):
        reason_code = "frame_confirmation_required"

    harness, _mesher = real
    _verdict(monkeypatch, status="ask", axis=None)
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    harness.submit_error = FrameConflict("frame changed while binding")
    harness.refusals.append(FrameConflict)
    summary = _prepare(harness)
    assert summary["reason"] == "preparation_failed", summary
    assert "choose" not in summary["message"].lower()
    assert "frame changed while binding" not in summary["message"]
    assert harness.bound_request() is None
