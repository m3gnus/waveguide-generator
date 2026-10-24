"""Explicit manual frame choices keep their meaning while Fusion decides automatically.

docs/architecture/CAD-OPERATIONS.md, "Unlinked solver frame". Every test runs the
production ``ingest_bundle`` (bundle reader, gates, record publication); only the
isolated STEP/mesher child is stood in for, as ``test_cad_preparation_design_gate``
does. The mesh-level truth of the frame -- real STEP, real gmsh -- is
``test_cad_solver_frame_real.py``.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.cadlink.intake import ingest as ingest_module
from server.cadlink.joblane import preparation_inputs as preparation
from server.cadlink.joblane.preparation import operation_summary
from server.cadlink.interpretation.solver_frame import CONTRACT, resolution_identity
from server.cadlink.intake.readings import confirm_frame

from cad_backends import Harness, backend_fixture
from test_cad_preparation import _accept, _manifest, _revision, _setup
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


BACKEND_FIXTURES = ("real",)


@pytest.fixture
def real(request, tmp_path, monkeypatch):
    """The CAD-solve backend under test (cad_backends.py), meshing through the real ingest."""

    backend = backend_fixture(request, tmp_path)
    harness = next(backend)
    mesher = Recording()
    monkeypatch.setattr(ingest_module, "build_imported_mesh_isolated", mesher)
    yield harness, mesher
    backend.close()


def _prepare(harness: Harness, operation_id: str = "cmd-1", **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("setup_revision_id", _revision(harness.store, _setup()))
    # Model the manual Solve card, which names the axis currently shown.
    # Fusion scenarios explicitly pass None (no axis was shown to the user).
    lineage = harness.store.get_lineage_for_cad_document(NATIVE_ID)
    confirmed = (
        harness.store.get_frame_confirmation(f"lineage:{lineage['lineage_id']}")
        if lineage else None
    )
    shown = confirmed["axis"] if confirmed else "+z"
    kwargs.setdefault("expected_frame_axis", shown)
    return harness.prepare(operation_id, ingest=ingest_module.ingest_bundle, **kwargs)


def _patch_everywhere(monkeypatch: pytest.MonkeyPatch, name: str, replacement: Any) -> None:
    """Replace a helper the preparation calls, in whichever module the backend calls it from."""

    from server.jobs import cad_preparation

    for module in (preparation, cad_preparation):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, replacement)


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

    waiting = _prepare(harness, setup_revision_id=revision)
    assert _waiting_for_frame(waiting), waiting
    confirm_frame(harness.store, _record(harness, waiting), "+z")

    review = harness.prepare("cmd-1", ingest=ingest_module.ingest_bundle)
    assert (review["state"], review["reason"]) == (
        "needs_user_input", "findings_need_review"
    ), review
    assert review["preparationId"] == waiting["preparationId"]
    blocking = [
        finding["id"]
        for finding in _record(harness, review)["findings"]
        if finding.get("blocking")
    ]

    solved = harness.prepare(
        "cmd-1",
        ingest=ingest_module.ingest_bundle,
        approve_preparation_id=review["preparationId"],
        approve_finding_ids=tuple(blocking),
    )

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

    _patch_everywhere(monkeypatch, "ensure_frame_suggestion", survey)
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    summary = _prepare(harness)
    assert _waiting_for_frame(summary), summary
    assert surveyed == [summary["preparationId"]]


def test_a_failing_survey_never_fails_the_preparation(real, monkeypatch) -> None:
    harness, _mesher = real

    def survey(store, record):
        raise RuntimeError("survey exploded")

    _patch_everywhere(monkeypatch, "ensure_frame_suggestion", survey)
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


def test_a_frame_changed_elsewhere_stops_the_solve_instead_of_changing_its_axis(real) -> None:
    """The axis shown is the axis solved: Solve names the axis it showed."""

    harness, mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    waiting = _prepare(harness)
    # The card showed +z and was about to solve along it; another window
    # confirmed +y for the project in the meantime.
    confirm_frame(harness.store, _record(harness, waiting), "+y")

    stopped = _prepare(harness, expected_frame_axis="+z")

    assert _waiting_for_frame(stopped), stopped
    assert "+y now, not the +z WG showed" in stopped["message"]
    assert harness.submitted == []
    # Named as the axis it now is, it solves (the control).
    solved = _prepare(harness, expected_frame_axis="+y")
    assert (solved["state"], solved["jobId"]) == ("accepted", "job-1"), solved
    assert _record(harness, solved)["normalisation"]["solver_frame"]["axis"] == "+y"


def test_an_automatic_continuation_after_the_update_restart_keeps_the_axis_shown(real) -> None:
    """The reviewer's reproduction: Solve asks for +z, the update restart parks
    it, another window confirms +x, and the continuation the delivery loop
    starts after the restart names no axis. It must not solve along +x."""

    harness, _mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    confirm_frame(harness.store, _record(harness, _prepare(harness)), "+z")
    harness.blocked = "An update restart is pending."

    parked = _prepare(harness, expected_frame_axis="+z")
    assert (parked["state"], parked["reason"]) == ("needs_user_input", "update_restart_pending")
    assert harness.submitted == []

    confirm_frame(harness.store, _record(harness, parked), "+x")
    # The restart is called off. What the latch held continues by itself with no
    # axis of its own (the delivery loop's continuation, or the lane's).
    harness.ingest = ingest_module.ingest_bundle
    harness.release_latch()
    continued = harness.summary()

    assert _waiting_for_frame(continued), continued
    assert "+x now, not the +z WG showed" in continued["message"]
    assert harness.submitted == []


def test_the_axis_shown_holds_across_retries_and_a_restart_until_a_press_names_another(real) -> None:
    harness, _mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    waiting = _prepare(harness, expected_frame_axis="+z")
    assert _waiting_for_frame(waiting)
    # Kept durably: a restarted WG reads it back.
    harness.restart()
    assert harness.held_axis() == "+z"

    # Another window confirms +x; a retry that names no axis is held to +z.
    confirm_frame(harness.store, _record(harness, waiting), "+x")
    retried = _prepare(harness, expected_frame_axis=None)
    assert _waiting_for_frame(retried) and "not the +z WG showed" in retried["message"]
    assert harness.submitted == []

    # A press naming the axis now shown replaces it, and solves along it.
    solved = _prepare(harness, expected_frame_axis="+x")
    assert (solved["state"], solved["jobId"]) == ("accepted", "job-1"), solved
    assert harness.held_axis() == "+x"
    assert _record(harness, solved)["normalisation"]["solver_frame"]["axis"] == "+x"


def test_a_request_no_one_named_an_axis_for_uses_the_confirmed_frame(real) -> None:
    """Fusion's own request, never pressed in WG: the confirmed frame decides (the control)."""

    harness, _mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    confirm_frame(harness.store, _record(harness, _prepare(harness, expected_frame_axis=None, submit=False)), "+y")
    solved = _prepare(harness, expected_frame_axis=None)
    assert (solved["state"], solved["jobId"]) == ("accepted", "job-1"), solved
    assert harness.held_axis() is None


def _stated(document_up: str | None) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "instances": [], "assembly": {}, "coordinate_system": {"export_frame": "root-component"},
        "required_features": [],
    }
    if document_up is not None:
        manifest["required_features"].append("document-up-v1")
        manifest["coordinate_system"]["document_up"] = document_up
    return manifest


class _PreparedStore:
    """The two reads ``_resumable`` makes, answering one prepared record."""

    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record

    def get_preparation(self, _preparation_id: str) -> dict[str, Any]:
        return {
            "setup_revision_id": "wgs_1", "snapshot_sha256": "sha256:s",
            "meshing_semantics": "semantics", "ingest_id": "wgi_1",
        }

    def get_ingest(self, _ingest_id: str) -> dict[str, Any]:
        return {"record_json": json.dumps(self.record)}


def test_a_preparation_resumes_only_in_the_same_complete_frame_not_the_same_axis() -> None:
    """Two preparations along +x with a different resolved up are different
    frames (contract v2 fixes the roll): neither resumes the other."""

    from server.cadlink.interpretation.solver_frame import FrameResolution, record_solver_frame, frame_spec

    def resolution(document_up: str | None) -> FrameResolution:
        return FrameResolution(
            key="lineage:wgl",
            requirement={"contract": CONTRACT, "export_frame": "root-component", "document_up": document_up},
            allowed_axes=("+z", "-z", "+x", "-x", "+y", "-y"), confirmed_axis="+x", document_up=document_up,
        )

    def record(document_up: str | None) -> dict[str, Any]:
        manifest = _stated(document_up)
        frame = record_solver_frame(manifest, frame_spec("+x", manifest))
        return {
            "anchor": {"instance_id": None},
            "normalisation": {"anchor_instance_id": None, "matrix": frame["matrix"], "solver_frame": frame},
        }

    row = {"preparation_id": "wgp_1"}
    z_up, y_up = record(None), record("+y")
    assert z_up["normalisation"]["solver_frame"]["up"] == "+z"
    assert y_up["normalisation"]["solver_frame"]["up"] == "+y"
    same = resolution_identity(resolution(None))
    other = resolution_identity(resolution("+y"))
    assert same["axis"] == other["axis"] == "+x" and same != other
    def resume_by_record(store, _row, *args):
        return preparation.resumable_record(store, store.get_preparation("wgp_1"), *args)

    _resumes_only_in_the_same_frame(resume_by_record, row, z_up, y_up, same, other)


def _resumes_only_in_the_same_frame(resume, row, z_up, y_up, same, other) -> None:
    # The same complete frame resumes (the control)...
    assert resume(_PreparedStore(z_up), row, "wgs_1", "sha256:s", "semantics", same) == z_up
    assert resume(_PreparedStore(y_up), row, "wgs_1", "sha256:s", "semantics", other) == y_up
    # ...the same forward axis with another up does not, either way round.
    assert resume(_PreparedStore(z_up), row, "wgs_1", "sha256:s", "semantics", other) is None
    assert resume(_PreparedStore(y_up), row, "wgs_1", "sha256:s", "semantics", same) is None
    # Nor does a record whose stated transform is not its frame's.
    tampered = copy.deepcopy(z_up)
    tampered["normalisation"]["solver_frame"]["matrix"][0][0] = 0.5
    assert resume(_PreparedStore(tampered), row, "wgs_1", "sha256:s", "semantics", same) is None


def test_the_mesh_cache_key_holds_the_complete_frame_transform() -> None:
    """Same snapshot, same forward axis, another up: another mesh."""


    from server.cadlink.interpretation.solver_frame import frame_spec

    bundle = SimpleNamespace(artifact_sha256="sha256:a", manifest_sha256="sha256:m")
    manifest = {"instances": [], "coordinate_system": {}, "sources": []}

    def key(document_up: str | None) -> str:
        spec = frame_spec("+x", _stated(document_up))
        return ingest_module._cache_key(bundle, manifest, {}, [], {"solver_frame": spec}, "sha256:g")

    assert key(None) == key(None)
    assert key(None) != key("+y")
    # The transform itself is in the key, not only the options that name it:
    # the same options with the matrix they mean changed is another key.
    spec = frame_spec("+x", _stated(None))
    real = ingest_module.spec_matrix
    try:
        ingest_module.spec_matrix = lambda value: real(frame_spec("+x", _stated("+y")))
        swapped = ingest_module._cache_key(bundle, manifest, {}, [], {"solver_frame": spec}, "sha256:g")
    finally:
        ingest_module.spec_matrix = real
    assert swapped != key(None)


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
    harness.refusals.append(FrameRefused)
    summary = _prepare(harness, setup_revision_id=_revision(harness.store, _setup()))

    assert _waiting_for_frame(summary), summary
    assert harness.bound_request() is None


def test_the_delivery_loop_decides_the_frame_and_solves(real, monkeypatch) -> None:
    """Fusion's Solve in WG prepares from the project's setup and infers the axis."""

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
    harness.ingest = ingest_module.ingest_bundle  # the loop meshes through the production ingest
    assert harness.delivery_pass() == ["cmd-1"]
    summary = harness.summary()
    assert summary["state"] == "accepted", summary
    assert summary["frameAxisAutomatic"] == "+z"
    assert len(harness.submitted) == 1
    # Delivery is consumed once.
    assert harness.delivery_pass() == []


def test_a_file_delivery_decides_its_frame_and_still_allows_manual_change(tmp_path, monkeypatch) -> None:
    """The request inbox infers an axis; the existing Change API can override it."""

    from server.cadlink.api import _preparation_context
    from server.tests.cad_file_harness import wg
    from server.tests.test_cad_solve_delivery import _file

    monkeypatch.setattr(ingest_module, "build_imported_mesh_isolated", Recording())
    with wg(tmp_path) as app:
        step = b"STEP authored"
        bundle_path, manifest_sha = _write(app.workspace, "authored", _authored(step), step)
        _file(app.data_dir, "op-1", bundle_path, manifest_sha)
        assert app.run_pass() == ["op-1"]

        revision = _revision(app.store, _setup())

        async def prepare(submit: bool) -> dict[str, Any]:
            from server.jobs.cad_preparation import run_cad_preparation
            runtime = app.app.state.jobs_runtime
            context = _preparation_context(app.app.state)
            job = runtime.store.latest_cad_job("op-1")
            job_id = await runtime.prepare_cad_solve(
                job["id"], setup_revision_id=revision, submit=submit,
            )
            await run_cad_preparation(runtime._cad_port, runtime._cad_host, job_id)
            return operation_summary(context.store.get_operation("op-1"), runtime.store)

        first = asyncio.run(prepare(True))
        # The empty mesher stand-in cannot compose a real solve, but the
        # actual submission boundary must never turn it into a frame question.
        assert first["reason"] == "submission_refused", first
        waiting = asyncio.run(prepare(False))
        assert waiting["reason"] == "ready_to_solve", waiting

        confirmed = app.client.request(
            "PUT", "/api/cadlink/solver-frame",
            headers={"content-type": "application/json"},
            body=json.dumps({"operationId": "op-1", "axis": "+y"}).encode("utf-8"),
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["confirmed"]["axis"] == "+y"

        ready = asyncio.run(prepare(False))
        assert (ready["state"], ready["reason"]) == ("needs_user_input", "ready_to_solve"), ready
        record = json.loads(app.store.get_ingest(ready["preparationId"])["record_json"])
        assert record["normalisation"]["solver_frame"]["axis"] == "+y"


def test_post_ingest_meshes_an_authored_model_in_its_confirmed_frame(real, monkeypatch) -> None:
    """The UI's own ingest takes the project's confirmed frame, never one the request names."""


    from server.app import create_app
    from server.cadlink.api import CadReturnIngestRequest, post_ingest

    harness, mesher = real
    monkeypatch.setattr("server.cadlink.api._schedule_deferred_viewport", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("server.cadlink.api._schedule_cad_document_capture", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("server.cadlink.api._schedule_frame_suggestion", lambda *_args, **_kwargs: None)
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
    _patch_everywhere(monkeypatch, "resolve_solver_frame", lambda *_args, **_kwargs: None)

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

    from server.cadlink.intake.ingest import retained_snapshot_path

    harness, mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    first = _prepare(harness)
    assert _waiting_for_frame(first)
    manifest_sha = harness.snapshot()["manifest_sha256"]
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

    from server.cadlink.intake.ingest import retained_snapshot_path

    harness, mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    assert _waiting_for_frame(_prepare(harness))
    manifest_sha = harness.snapshot()["manifest_sha256"]
    (retained_snapshot_path(harness.data_dir, manifest_sha) / "wgreturn.json").write_text(
        "{not json", encoding="utf-8"
    )
    returns = harness.workspace / "wgreturn"
    returns.rename(harness.workspace / "elsewhere")  # the folder was switched
    calls = len(mesher.calls)

    summary = _prepare(harness)

    assert (summary["state"], summary["reason"]) == ("needs_user_input", "preparation_failed"), summary
    assert summary["message"].startswith("WG could not prepare ")  # internal: calm words
    assert len(mesher.calls) == calls and harness.submitted == []


def _suggest(harness: Harness, record: dict[str, Any], axis: str, status: str = "automatic") -> None:
    from server.cadlink.interpretation.frame_infer import ALGORITHM_VERSION

    harness.store.record_frame_suggestion(
        str(record["manifest_sha256"]), ALGORITHM_VERSION,
        {
            "algorithm": ALGORITHM_VERSION, "status": status, "axis": axis,
            "confidence": 0.9, "snapshotSha256": record["manifest_sha256"],
        },
        str(record["ingest_id"]),
    )


def test_frame_provenance_says_suggested_chosen_or_automatic(real) -> None:
    from server.cadlink.intake.readings import frame_provenance

    harness, _mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    waiting = _prepare(harness)
    record = _record(harness, waiting)
    axis = record["normalisation"]["solver_frame"]["axis"]

    # Nobody confirmed and no survey: unconfirmed.
    bare = frame_provenance(harness.store, record)
    assert (bare["provenance"], bare["confirmed"]) == ("unconfirmed", False)

    # The operation's own statement that WG used its automatic axis: not confirmed.
    _suggest(harness, record, axis)
    automatic = frame_provenance(harness.store, record, automatic=True)
    assert (automatic["axis"], automatic["provenance"], automatic["confirmed"]) == (axis, "automatic", False)
    assert automatic["suggestion"]["status"] == "automatic"
    # A survey alone never labels a frame automatic.
    assert frame_provenance(harness.store, record)["provenance"] == "unconfirmed"

    # A person confirming the suggested axis: suggested, confirmed.
    confirm_frame(harness.store, record, axis)
    suggested = frame_provenance(harness.store, record, automatic=True)
    # Confirmed by a person, so never "automatic" whatever the caller says.
    assert (suggested["provenance"], suggested["confirmed"]) == ("suggested", True)
    assert suggested["requirement"] == record["normalisation"]["solver_frame"]["requirement"]


def test_a_solved_job_records_a_chosen_frame_and_a_suggested_one(real) -> None:
    harness, _mesher = real
    step = b"STEP authored"
    _received(harness, "authored", _authored(step), step)
    waiting = _prepare(harness)
    record = _record(harness, waiting)
    axis = record["normalisation"]["solver_frame"]["axis"]
    confirm_frame(harness.store, record, axis)  # no survey: the user chose it

    assert _prepare(harness)["state"] == "accepted"
    frame = harness.provenance[-1]["frame"]
    assert (frame["axis"], frame["provenance"], frame["confirmed"]) == (axis, "chosen", True)

    # The same project, its next export, after the survey agrees with the axis.
    _suggest(harness, record, axis)
    confirm_frame(harness.store, record, axis)
    _received(harness, "authored2", _authored(step), step, command="cmd-2")
    assert _prepare(harness, "cmd-2")["state"] == "accepted"
    assert harness.provenance[-1]["frame"]["provenance"] == "suggested"
    assert harness.provenance[-1]["operation_id"] == "cmd-2"


def test_a_solve_press_takes_only_a_known_frame_axis() -> None:
    from pydantic import ValidationError

    from server.jobs.model_solve import ModelSolveRequest
    from server.jobs.models import CadSolveAgainRequest

    press = {"prepared_id": "wgi_1", "client_request_id": "p1", "setup_revision_id": "wgs_1"}
    assert ModelSolveRequest.model_validate({**press, "frame_axis": "-y"}).frame_axis == "-y"
    assert ModelSolveRequest.model_validate(press).frame_axis is None
    assert CadSolveAgainRequest.model_validate({"frame_axis": "+x"}).frame_axis == "+x"
    for invalid in (ModelSolveRequest, CadSolveAgainRequest):
        with pytest.raises(ValidationError):
            invalid.model_validate({**(press if invalid is ModelSolveRequest else {}), "frame_axis": "y"})
