"""Solve settings Fusion's panel sends with a Solve (cadlink/joblane/fusion_settings.py).

WG owns every solve setting. A Solve request may carry ``solveSettings``,
the sweep fields the user changed in Fusion; the delivery's first
preparation applies them to the model's setup, records the result as the
model's own setup and prepares with it. The request status then reports the
sweep the solve used, which is what the panel shows next time.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.cadlink.intake import ingest as ingest_module
from server.cadlink.intake.solve_command import InvalidRequest, _command_from_payload
from server.cadlink.joblane.fusion_settings import (
    apply_solve_settings,
    revision_sweep,
    sweep_settings,
)
from server.cadlink.joblane.project_setup import project_setup
from server.cadlink.joblane.request_status import project_request_status
from server.cadlink.roles import inventory_sha256
from server.cadlink.operations import PREPARE_AND_SOLVE, prepare_and_solve_request, request_digest
from server.cadlink.wglink_protocol import SOLVE_SETTINGS_FIELD
from server.jobs.cad_intent import CadSolveIntent
from server.jobs.imported_setup import validate_setup
from server.mesh.errors import MeshTooLarge
from server.solver.frequency_sweep import canonical_frequencies

from cad_backends import BACKENDS, Harness, backend_fixture
from test_cad_preparation import _manifest, _setup
from test_cad_preparation_design_gate import MesherStandIn, _current, _prepare, _record
from test_cad_preparation_solver_frame import _authored, _write
from test_cad_project_setup import _project, _record_setup


@pytest.fixture(params=BACKENDS)
def real(request, tmp_path, monkeypatch) -> tuple[Harness, MesherStandIn]:
    backend = backend_fixture(request, tmp_path)
    harness = next(backend)
    mesher = MesherStandIn()
    monkeypatch.setattr(ingest_module, "build_imported_mesh_isolated", mesher)
    yield harness, mesher
    next(backend, None)


def _delivered(harness: Harness, name: str, settings: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """A Fusion "Solve in WG" of a saved WG design's return, carrying ``settings``."""

    design_id, lineage_id = _project(harness, 45.0)
    step = b"STEP " + name.encode()
    manifest = _manifest(step)
    manifest["instances"][0]["design_id"] = design_id
    manifest = _current(harness, manifest, design_id)
    bundle = harness.workspace / "wgreturn" / f"{name}.wgreturn"
    bundle.mkdir(parents=True)
    (bundle / "assembly.step").write_bytes(step)
    body = json.dumps(manifest).encode("utf-8")
    (bundle / "wgreturn.json").write_bytes(body)
    target, inputs = prepare_and_solve_request(
        return_id="wgr_1", bundle_path=f"wgreturn/{name}.wgreturn",
        manifest_sha256="sha256:" + hashlib.sha256(body).hexdigest(), solve_settings=settings,
    )
    harness.store.accept_operation(
        "cmd-1", PREPARE_AND_SOLVE, request_digest(PREPARE_AND_SOLVE, target, inputs), target, inputs,
    )
    return lineage_id, manifest


def _model_setup(harness: Harness, lineage_id: str, manifest: dict[str, Any]):
    loaded = project_setup(harness.store, lineage_id, manifest["sources"])
    assert loaded is not None
    return loaded


def test_a_solve_with_settings_uses_them_and_makes_them_the_models_own(real) -> None:
    harness, _mesher = real
    lineage_id, manifest = _delivered(harness, "with-settings", {
        "frequencyStartHz": 100.0, "frequencyEndHz": 10000.0, "frequencyCount": 60,
    })
    # The model already had settings of its own: an explicit frequency list.
    _record_setup(harness, lineage_id, _setup(engine="bempp"))

    solved = _prepare(harness, setup_revision_id=None)

    assert (solved["state"], solved["jobId"]) == ("accepted", "job-1"), solved
    options = harness.submitted[-1].options
    assert options.frequency_range == [100.0, 10000.0]
    assert options.num_frequencies == 60
    assert options.frequencies_hz is None
    assert options.engine == "bempp"  # everything else is the model's own
    setup, _revision_id = _model_setup(harness, lineage_id, manifest)
    assert setup.origin is None
    assert sweep_settings(setup.options) == {
        "frequencyStartHz": 100.0, "frequencyEndHz": 10000.0, "frequencyCount": 60,
        "frequencySpacing": "log",
    }
    # Recorded as the user's own choice, as an edit in WG would be.
    assert harness.store.model_setup_has_user_choice(lineage_id, inventory_sha256(manifest["sources"]))


def test_a_first_time_model_starts_from_wgs_defaults_with_the_changes(real) -> None:
    harness, _mesher = real
    lineage_id, manifest = _delivered(harness, "first-time", {"frequencyCount": 12, "frequencySpacing": "linear"})

    solved = _prepare(harness, setup_revision_id=None)

    assert solved["state"] == "accepted", solved
    options = harness.submitted[-1].options
    assert options.frequency_range == [50.0, 20000.0]  # WG's default band
    assert (options.num_frequencies, options.frequency_spacing) == (12, "linear")
    setup, _revision_id = _model_setup(harness, lineage_id, manifest)
    assert setup.origin is None
    assert sweep_settings(setup.options)["frequencyCount"] == 12


def test_a_continuation_keeps_the_setup_its_first_preparation_made(real) -> None:
    harness, _mesher = real
    _delivered(harness, "continued", {"frequencyCount": 20})
    first = _prepare(harness, setup_revision_id=None)
    assert first["state"] == "accepted", first
    bound = harness.bound_setup_revision()
    assert bound is not None and revision_sweep(harness.store, bound)["frequencyCount"] == 20


def test_settings_that_cannot_be_combined_with_the_models_refuse_calmly(real) -> None:
    harness, _mesher = real
    lineage_id, _manifest = _delivered(harness, "too-low", {"frequencyEndHz": 400.0})
    # The model's band starts at 500 Hz: an end below it cannot be solved.
    _record_setup(harness, lineage_id, _setup(engine="bempp"))

    refused = _prepare(harness, setup_revision_id=None)

    assert refused["state"] == "needs_user_input", refused
    assert "solve settings sent from Fusion cannot be used" in str(refused.get("message")), refused
    assert harness.submitted == []


# -- the sweep, in and out ---------------------------------------------------------


def test_sweep_settings_read_a_band_or_an_explicit_list() -> None:
    assert sweep_settings({"frequency_range": [50, 20000], "num_frequencies": 36}) == {
        "frequencyStartHz": 50.0, "frequencyEndHz": 20000.0, "frequencyCount": 36,
        "frequencySpacing": "log"}
    assert sweep_settings({"frequencies_hz": [2000.0, 500.0, 1000.0]}) == {
        "frequencyStartHz": 500.0, "frequencyEndHz": 2000.0, "frequencyCount": 3}


def test_a_spacing_edit_replaces_an_explicit_list_with_the_requested_axis() -> None:
    setup = validate_setup(_setup())
    options = apply_solve_settings(setup, {"frequencySpacing": "linear"}).options
    assert "frequencies_hz" not in options
    axis = canonical_frequencies(SimpleNamespace(
        frequencies_hz=options.get("frequencies_hz"), frequency_range=options["frequency_range"],
        num_frequencies=options["num_frequencies"], frequency_spacing=options["frequency_spacing"],
    ))
    assert axis.tolist() == [500.0, 1250.0, 2000.0]
    changed = apply_solve_settings(setup, {"frequencyCount": 10}).options
    assert "frequencies_hz" not in changed
    assert (changed["frequency_range"], changed["num_frequencies"]) == ([500.0, 2000.0], 10)
    with pytest.raises(ValueError):
        apply_solve_settings(setup, {"frequencyStartHz": 3000.0})


def test_a_new_cad_models_settings_are_recorded_after_a_held_preparation_resumes(real, monkeypatch) -> None:
    harness, mesher = real
    step = b"STEP newly authored model"
    manifest = _authored(step)
    path, digest = _write(harness.workspace, "held-first-model", manifest, step)
    target, inputs = prepare_and_solve_request(
        return_id="wgr_1", bundle_path=path, manifest_sha256=digest,
        solve_settings={"frequencyCount": 12},
    )
    harness.store.accept_operation(
        "cmd-1", PREPARE_AND_SOLVE, request_digest(PREPARE_AND_SOLVE, target, inputs), target, inputs,
    )
    first = True

    def held_once(*args, **kwargs):
        nonlocal first
        if first:
            first = False
            raise MeshTooLarge(30000, 22000)
        return mesher(*args, **kwargs)

    monkeypatch.setattr(ingest_module, "build_imported_mesh_isolated", held_once)
    held = _prepare(harness)
    assert (held["state"], held["reason"]) == ("needs_user_input", "mesh_too_large"), held
    revision = harness.bound_setup_revision()
    assert revision is not None

    solved = _prepare(harness, setup_revision_id=revision)

    assert solved["state"] == "accepted", solved
    lineage = _record(harness, solved)["project"]["lineage_id"]
    setup, _revision = _model_setup(harness, lineage, manifest)
    assert setup.options["num_frequencies"] == 12
    assert harness.store.model_setup_has_user_choice(lineage, inventory_sha256(manifest["sources"]))


def test_the_request_status_reports_the_sweep_its_job_prepares_with() -> None:
    receipt = {"commandId": "cmd-1", "kind": PREPARE_AND_SOLVE, "manifestSha256": "sha256:" + "0" * 64,
               "outcome": "accepted"}
    job = {"id": "job-1", "status": "queued", "run_number": 1}
    sweep = {"frequencyStartHz": 50.0, "frequencyEndHz": 20000.0, "frequencyCount": 36}
    record, _incident = project_request_status(receipt, {"kind": PREPARE_AND_SOLVE}, job, {},
                                               settings=sweep)
    assert record["settings"] == sweep
    record, _incident = project_request_status(receipt, {"kind": PREPARE_AND_SOLVE}, job, {})
    assert "settings" not in record


# -- the delivery ---------------------------------------------------------------


def _request(**extra: Any) -> dict[str, Any]:
    return {"schemaVersion": 4, "target": "waveguide-generator", "kind": PREPARE_AND_SOLVE,
            "commandId": "cmd-1", "operationId": "cmd-1", "returnId": "wgr_1",
            "bundlePath": "wgreturn/a.wgreturn", "manifestSha256": "sha256:" + "0" * 64,
            "requestedAt": "2026-10-27T10:00:00Z", **extra}


def test_a_solve_request_carries_its_settings_into_the_operation_and_its_digest(tmp_path: Path) -> None:
    plain = _command_from_payload(_request(), tmp_path / "a.json", schema_versions=frozenset({4}))
    command = _command_from_payload(
        _request(**{SOLVE_SETTINGS_FIELD: {"frequencyCount": 60, "newer": 1}}), tmp_path / "a.json",
        schema_versions=frozenset({4}))
    assert dict(command.solve_settings) == {"frequencyCount": 60}
    assert command.payload()[SOLVE_SETTINGS_FIELD] == {"frequencyCount": 60}
    plain_digest = request_digest(PREPARE_AND_SOLVE, *prepare_and_solve_request(
        return_id="wgr_1", bundle_path="b", manifest_sha256="m"))
    digest = request_digest(PREPARE_AND_SOLVE, *prepare_and_solve_request(
        return_id="wgr_1", bundle_path="b", manifest_sha256="m", solve_settings={"frequencyCount": 60}))
    assert plain_digest != digest and plain.solve_settings == ()
    intent = CadSolveIntent(operation_id="cmd-1", bundle_path="b", manifest_sha256="m", return_id="wgr_1",
                            solve_settings=command.solve_settings)
    assert intent.delivery_digest() == digest
    assert CadSolveIntent.from_config(intent.to_config()) == intent


@pytest.mark.parametrize("extra", [
    {SOLVE_SETTINGS_FIELD: {"frequencyCount": 0}},
    {SOLVE_SETTINGS_FIELD: "60"},
    {SOLVE_SETTINGS_FIELD: {"frequencyCount": 60}, "kind": "receive_snapshot"},
])
def test_settings_wg_cannot_use_refuse_the_request(tmp_path: Path, extra: dict[str, Any]) -> None:
    payload = _request(**extra)
    if payload["kind"] != PREPARE_AND_SOLVE:
        payload.pop("returnId")
    with pytest.raises(InvalidRequest):
        _command_from_payload(copy.deepcopy(payload), tmp_path / "a.json", schema_versions=frozenset({4}))
