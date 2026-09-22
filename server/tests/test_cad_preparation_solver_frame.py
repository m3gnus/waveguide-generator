"""No path prepares an unlinked (CAD-authored) snapshot into a solve before its frame is confirmed.

docs/architecture/CAD-OPERATIONS.md, "Unlinked solver frame". Every test runs the
production ``ingest_bundle`` (bundle reader, gates, record publication); only the
isolated STEP/mesher child is stood in for, as ``test_cad_preparation_design_gate``
does. The mesh-level truth of the frame -- real STEP, real gmsh -- is
``test_cad_solver_frame_real.py``.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from server.cadlink import ingest as ingest_module
from server.cadlink import preparation, solve_command
from server.cadlink.preparation import PreparationInput, prepare_operation, run_delivery_pass
from server.cadlink.solver_frame import CONTRACT, confirm_frame

from test_cad_preparation import Harness, _accept, _manifest, _revision, _setup
from test_cad_preparation_design_gate import MesherStandIn


NATIVE_ID = "urn:adsk.wipprod:dm.lineage:authored-horn"


def _authored(step: bytes, *, native_id: str | None = NATIVE_ID, **coordinates: Any) -> dict[str, Any]:
    """A return drawn from scratch in Fusion: no WG instance, no throat frame."""

    manifest = _manifest(step)
    manifest["document"] = {"name": "Authored horn", "native_id": native_id}
    manifest["instances"] = []
    manifest["coordinate_system"]["solver_anchor_instance_id"] = None
    manifest["coordinate_system"].update(coordinates)
    manifest["scope"]["included"][0]["wglink_instance_id"] = None
    source = manifest["sources"][0]
    source["instance_id"] = None
    source["selectors"].pop("linked_throat")
    return manifest


def _write(workspace: Path, name: str, manifest: dict[str, Any], step: bytes) -> tuple[str, str]:
    bundle = workspace / "wgreturn" / f"{name}.wgreturn"
    bundle.mkdir(parents=True)
    (bundle / "assembly.step").write_bytes(step)
    body = json.dumps(manifest).encode("utf-8")
    (bundle / "wgreturn.json").write_bytes(body)
    return f"wgreturn/{name}.wgreturn", "sha256:" + hashlib.sha256(body).hexdigest()


class Recording(MesherStandIn):
    def __call__(self, assembly_path: Any, manifest: Any, mesh: Any, **kwargs: Any) -> dict[str, Any]:
        result = super().__call__(assembly_path, manifest, mesh, **kwargs)
        self.calls[-1]["options"] = dict(kwargs.get("options") or {})
        return result


@pytest.fixture
def real(tmp_path, monkeypatch) -> tuple[Harness, Recording]:
    harness = Harness(tmp_path)
    mesher = Recording()
    monkeypatch.setattr(ingest_module, "build_imported_mesh_isolated", mesher)
    return harness, mesher


def _context(harness: Harness):
    return dataclasses.replace(harness.context(), ingest=ingest_module.ingest_bundle)


def _prepare(harness: Harness, operation_id: str = "cmd-1", **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("setup_revision_id", _revision(harness.store, _setup()))
    return asyncio.run(prepare_operation(_context(harness), operation_id, PreparationInput(**kwargs)))


def _received(harness: Harness, name: str, manifest: dict[str, Any], step: bytes, command: str = "cmd-1") -> None:
    bundle_path, digest = _write(harness.workspace, name, manifest, step)
    _accept(harness.store, command, bundle_path, digest)


def _record(harness: Harness, summary: dict[str, Any]) -> dict[str, Any]:
    assert summary["preparationId"], summary
    ingest = harness.store.get_ingest(str(summary["preparationId"]))
    assert ingest is not None
    return json.loads(ingest["record_json"])


def _waiting_for_frame(summary: dict[str, Any]) -> bool:
    return (summary["state"], summary["reason"]) == ("needs_user_input", "frame_confirmation_required")


def _with_degraded_skip(manifest: dict[str, Any]) -> dict[str, Any]:
    manifest["scope"]["skipped"].append({
        "component": "component-1",
        "kind": "unsupported_body",
        "name": "Unsupported body",
        "object_id": "unsupported-1",
        "path": "component-1/Unsupported body",
        "reason": "the body cannot be exported",
        "severity": "degraded",
    })
    manifest["scope"]["status"] = "degraded"
    return manifest


def test_an_unconfirmed_authored_model_is_prepared_as_modelled_and_waits(real) -> None:
    harness, mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)

    summary = _prepare(harness)

    assert _waiting_for_frame(summary), summary
    assert summary["jobId"] is None and harness.submitted == []
    assert "solver frame" in summary["message"]
    # Prepared as modelled, so the confirmation step has geometry to preview.
    record = _record(harness, summary)
    assert record["normalisation"]["solver_frame"]["axis"] == "+z"
    assert "solver_frame" not in mesher.calls[-1]["options"]
    # Waiting is not a rejection, and asking again does not get past it.
    again = _prepare(harness)
    assert _waiting_for_frame(again) and harness.submitted == []


def test_confirming_the_modelled_frame_resumes_that_preparation_and_solves(real) -> None:
    harness, mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    waiting = _prepare(harness)
    confirm_frame(harness.store, _record(harness, waiting), "+z")

    solved = _prepare(harness)

    assert (solved["state"], solved["jobId"]) == ("accepted", "job-1"), solved
    assert solved["preparationId"] == waiting["preparationId"]
    assert len(mesher.calls) == 1


def test_revisionless_manual_solve_followups_reuse_the_preparations_settings(real) -> None:
    """The UI sends no revision when confirming a frame or approving a finding."""

    harness, mesher = real
    step = b"STEP authored with one degraded skip"
    _received(harness, "authored", _with_degraded_skip(_authored(step)), step)
    revision = _revision(harness.store, _setup())

    waiting = asyncio.run(prepare_operation(
        _context(harness), "cmd-1", PreparationInput(setup_revision_id=revision)
    ))
    assert _waiting_for_frame(waiting), waiting
    confirm_frame(harness.store, _record(harness, waiting), "+z")

    review = asyncio.run(prepare_operation(_context(harness), "cmd-1", PreparationInput()))
    assert (review["state"], review["reason"]) == (
        "needs_user_input", "findings_need_review"
    ), review
    assert review["preparationId"] == waiting["preparationId"]
    blocking = [
        finding["id"]
        for finding in _record(harness, review)["findings"]
        if finding.get("blocking")
    ]

    solved = asyncio.run(prepare_operation(
        _context(harness),
        "cmd-1",
        PreparationInput(
            approve_preparation_id=review["preparationId"],
            approve_finding_ids=tuple(blocking),
        ),
    ))

    assert (solved["state"], solved["jobId"]) == ("accepted", "job-1"), solved
    assert solved["preparationId"] == waiting["preparationId"]
    assert len(mesher.calls) == 1
    # Exactly one job: the approval was not lost to a new preparation.
    assert len(harness.submitted) == 1 and len(harness.jobs) == 1


def test_preparation_surveys_the_frame_inside_its_command(real, monkeypatch) -> None:
    """M1e: the suggestion is computed by the preparation, for the record it made."""

    harness, _mesher = real
    surveyed: list[str] = []

    def survey(store, record):
        surveyed.append(str(record["ingest_id"]))
        return None

    monkeypatch.setattr(preparation, "ensure_frame_suggestion", survey)
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    summary = _prepare(harness)
    assert _waiting_for_frame(summary), summary
    assert surveyed == [summary["preparationId"]]


def test_a_failing_survey_never_fails_the_preparation(real, monkeypatch) -> None:
    harness, _mesher = real

    def survey(store, record):
        raise RuntimeError("survey exploded")

    monkeypatch.setattr(preparation, "ensure_frame_suggestion", survey)
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    assert _waiting_for_frame(_prepare(harness))


def test_confirming_another_axis_prepares_again_in_that_frame(real) -> None:
    harness, mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    waiting = _prepare(harness)
    confirm_frame(harness.store, _record(harness, waiting), "+y")

    solved = _prepare(harness)

    assert (solved["state"], solved["jobId"]) == ("accepted", "job-1"), solved
    assert solved["preparationId"] != waiting["preparationId"]
    # The complete frame reaches the mesher (contract v2: the roll too).
    assert mesher.calls[-1]["options"]["solver_frame"] == {
        "contract": CONTRACT, "axis": "+y", "up": "+z", "up_source": "default", "document_up": None,
    }
    assert _record(harness, solved)["normalisation"]["solver_frame"]["axis"] == "+y"
    assert harness.submitted[0].geometry.ingest_id == solved["preparationId"]


def test_a_later_export_of_the_same_project_reuses_the_confirmed_frame(real) -> None:
    harness, mesher = real
    first = b"STEP authored"
    _received(harness, "authored", _authored(first), first)
    confirm_frame(harness.store, _record(harness, _prepare(harness)), "-x")

    second = b"STEP authored, edited in Fusion"
    _received(harness, "authored-2", _authored(second), second, command="cmd-2")
    solved = _prepare(harness, "cmd-2")

    assert (solved["state"], solved["jobId"]) == ("accepted", "job-1"), solved
    assert mesher.calls[-1]["options"]["solver_frame"]["axis"] == "-x"


def test_an_export_in_another_components_coordinates_asks_again(real) -> None:
    harness, mesher = real
    first = b"STEP authored"
    _received(harness, "authored", _authored(first), first)
    confirm_frame(harness.store, _record(harness, _prepare(harness)), "-x")

    second = b"STEP of one occurrence"
    moved = _authored(second, export_frame="selected-occurrence-component")
    _received(harness, "occurrence", moved, second, command="cmd-2")
    summary = _prepare(harness, "cmd-2")

    assert _waiting_for_frame(summary), summary
    assert "solver_frame" not in mesher.calls[-1]["options"]
    assert _record(harness, summary)["normalisation"]["solver_frame"]["requirement"] == {
        "contract": CONTRACT, "export_frame": "selected-occurrence-component", "document_up": None,
    }


def test_an_unsaved_document_is_confirmed_for_that_export_only(real) -> None:
    harness, _mesher = real
    first = b"STEP unsaved"
    _received(harness, "unsaved", _authored(first, native_id=None), first)
    confirm_frame(harness.store, _record(harness, _prepare(harness)), "+z")
    assert _prepare(harness)["state"] == "accepted"

    second = b"STEP unsaved again"
    _received(harness, "unsaved-2", _authored(second, native_id=None), second, command="cmd-2")
    assert _waiting_for_frame(_prepare(harness, "cmd-2"))


def test_a_linked_return_is_never_asked_and_its_options_are_unchanged(real) -> None:
    harness, mesher = real
    step = b"STEP linked"
    manifest = _manifest(step)
    _received(harness, "linked", manifest, step)
    summary = _prepare(harness)
    assert summary["reason"] != "frame_confirmation_required", summary
    assert "solver_frame" not in mesher.calls[-1]["options"]


def test_a_jobs_refusal_for_the_frame_keeps_that_reason_and_releases_the_binding(real) -> None:
    harness, _mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    confirm_frame(harness.store, _record(harness, _prepare(harness)), "+z")

    class FrameRefused(ValueError):
        reason_code = "frame_confirmation_required"

    harness.submit_error = FrameRefused("frame_confirmation_required: confirm it again")
    context = dataclasses.replace(_context(harness), submission_refusals=(FrameRefused,))
    summary = asyncio.run(prepare_operation(
        context, "cmd-1", PreparationInput(setup_revision_id=_revision(harness.store, _setup()))
    ))

    assert _waiting_for_frame(summary), summary
    assert harness.row()["request_json"] is None


def test_the_delivery_loop_reaches_the_frame_gate(real, monkeypatch) -> None:
    """Fusion's Solve in WG: the loop prepares from the project's setup, then waits."""

    harness, _mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    # A document WG has seen before, with its project's setup recorded.
    harness.store.claim_cad_document_lineage(NATIVE_ID, "Authored horn")
    monkeypatch.setattr(
        preparation, "project_setup",
        lambda store, lineage, sources: (
            preparation.validate_setup(_setup()), _revision(store, _setup())
        ),
    )
    context = _context(harness)
    tasks: list[Any] = []

    async def one_pass() -> list[str]:
        started = await run_delivery_pass(
            context, spawn=lambda _operation_id, coroutine: tasks.append(asyncio.ensure_future(coroutine))
        )
        await asyncio.gather(*tasks)
        return started

    assert asyncio.run(one_pass()) == ["cmd-1"]
    summary = preparation.operation_summary(harness.row())
    assert _waiting_for_frame(summary), summary
    assert harness.submitted == []
    # Waiting for the user is never retried unasked.
    tasks.clear()
    assert asyncio.run(one_pass()) == []


def test_a_live_delivery_reaches_the_frame_gate(tmp_path, monkeypatch) -> None:
    """The HTTP delivery route (CADLINK-LIVE-PROTOCOL.md section 8) meets the same gate."""

    from server.cadlink.api import _preparation_context
    from test_cadlink_live_deliveries import _item, wg

    monkeypatch.setattr(ingest_module, "build_imported_mesh_isolated", Recording())
    getattr(solve_command, "_live_waits", {}).clear()
    with wg(tmp_path) as app:
        step = b"STEP authored"
        bundle_path, manifest_sha = _write(app.workspace, "authored", _authored(step), step)
        token = app.token()
        delivered = app.deliver(token, _item(bundle_path, manifest_sha))
        assert delivered.status_code == 200, delivered.text

        revision = _revision(app.store, _setup())

        async def prepare(submit: bool) -> dict[str, Any]:
            return await preparation.prepare_operation(
                _preparation_context(app.app.state), "op-1",
                PreparationInput(setup_revision_id=revision, submit=submit),
            )

        waiting = asyncio.run(prepare(True))
        assert _waiting_for_frame(waiting), waiting

        confirmed = app.client.request(
            "PUT", "/api/cadlink/solver-frame",
            headers={"content-type": "application/json"},
            body=json.dumps({"operationId": "op-1", "axis": "+z"}).encode("utf-8"),
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["confirmed"]["axis"] == "+z"

        ready = asyncio.run(prepare(False))
        assert (ready["state"], ready["reason"]) == ("needs_user_input", "ready_to_solve"), ready
    getattr(solve_command, "_live_waits", {}).clear()


def test_post_ingest_meshes_an_authored_model_in_its_confirmed_frame(real, monkeypatch) -> None:
    """The UI's own ingest takes the project's confirmed frame, never one the request names."""

    from types import SimpleNamespace

    from server.app import create_app
    from server.cadlink.api import CadReturnIngestRequest, post_ingest

    harness, mesher = real
    monkeypatch.setattr("server.cadlink.api._schedule_deferred_viewport", lambda *_args: None)
    monkeypatch.setattr("server.cadlink.api._schedule_cad_document_capture", lambda *_args: None)
    monkeypatch.setattr("server.cadlink.api._schedule_frame_suggestion", lambda *_args: None)
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    waiting = _prepare(harness)
    app = create_app(data_dir=harness.data_dir)
    app.state.cadlink_store = harness.store
    app.state.cad_workspace.select(harness.workspace)
    confirm_frame(harness.store, _record(harness, waiting), "+y")

    payload = CadReturnIngestRequest.model_validate({
        "bundlePath": "wgreturn/authored.wgreturn",
        "mesh": {"rigidSizeMm": 20, "transitionMm": 30, "sourceSizeMm": {"source-hf": 8}},
        "skippedSourceIds": [],
    })
    record = asyncio.run(post_ingest(payload, SimpleNamespace(app=app)))

    assert record["normalisation"]["solver_frame"]["axis"] == "+y"
    assert mesher.calls[-1]["options"]["solver_frame"]["axis"] == "+y"
    with pytest.raises(Exception):
        CadReturnIngestRequest.model_validate({**copy.deepcopy(payload.model_dump(by_alias=True)), "solverFrame": "+x"})


def test_the_record_is_gated_even_when_the_manifest_resolution_says_linked(real, monkeypatch) -> None:
    """The gate reads what was prepared, not only what the manifest was read as."""

    harness, _mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    monkeypatch.setattr(preparation, "resolve_solver_frame", lambda *_args: None)

    summary = _prepare(harness)

    assert _waiting_for_frame(summary), summary
    assert harness.submitted == []


def test_an_unreadable_retained_manifest_is_replaced_and_still_never_read_as_linked(
    real,
) -> None:
    """Never read as linked -- and never a dead end either.

    A retained copy WG cannot read is replaced from the WGLink folder
    (``ingest._stage_bundle_cas``), so the snapshot goes on as what it is: an
    unlinked model still waiting for its frame. What must not happen is a solve
    submitted on a guessed frame.
    """

    from server.cadlink.ingest import retained_snapshot_path

    harness, mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    first = _prepare(harness)
    assert _waiting_for_frame(first)
    manifest_sha = json.loads(harness.row()["snapshot_json"])["manifest_sha256"]
    copy = retained_snapshot_path(harness.data_dir, manifest_sha)
    (copy / "wgreturn.json").write_text("{not json", encoding="utf-8")

    summary = _prepare(harness)

    assert _waiting_for_frame(summary), summary
    assert summary["jobId"] is None and harness.submitted == []
    assert json.loads((copy / "wgreturn.json").read_text(encoding="utf-8"))["document"] == {
        "name": "Authored horn",
        "native_id": NATIVE_ID,
    }


def test_an_unreadable_retained_manifest_with_the_return_gone_waits(real) -> None:
    """Nothing to replace it with: the snapshot waits rather than being guessed at."""

    from server.cadlink.ingest import retained_snapshot_path

    harness, mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    assert _waiting_for_frame(_prepare(harness))
    manifest_sha = json.loads(harness.row()["snapshot_json"])["manifest_sha256"]
    (retained_snapshot_path(harness.data_dir, manifest_sha) / "wgreturn.json").write_text(
        "{not json", encoding="utf-8"
    )
    returns = harness.workspace / "wgreturn"
    returns.rename(harness.workspace / "elsewhere")  # the folder was switched
    calls = len(mesher.calls)

    summary = _prepare(harness)

    assert (summary["state"], summary["reason"]) == ("needs_user_input", "preparation_failed"), summary
    assert "no copy it can use" in summary["message"]
    assert len(mesher.calls) == calls and harness.submitted == []
