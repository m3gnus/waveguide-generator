"""A solve's settings belong to its operation (CAD-OPERATIONS.md, "Setup revisions").

A pure continuation -- frame confirmation, approval, retry -- sends no new
settings, and the backend reuses the setup revision the operation already
selected. An action whose purpose is to choose settings sends them, and an
explicit revision wins over the one the operation holds. Every test runs the
production preparation and ``ingest_bundle``; only the isolated mesher child is
stood in for.
"""

from __future__ import annotations

from typing import Any

import pytest

from server.cadlink import ingest as ingest_module
from server.cadlink.isolation import ChildRefusal

from test_cad_preparation import Harness, Refused, _manifest, _revision, _setup
from test_cad_preparation_design_gate import MesherStandIn, _current, _prepare, _received
from test_cad_project_setup import _project, _record_setup


@pytest.fixture
def real(tmp_path, monkeypatch) -> tuple[Harness, MesherStandIn]:
    harness = Harness(tmp_path)
    mesher = MesherStandIn()
    monkeypatch.setattr(ingest_module, "build_imported_mesh_isolated", mesher)
    return harness, mesher


def _exported(harness: Any, name: str) -> None:
    """A Fusion "Solve in WG" command for a return of a saved WG design."""

    design_id, _lineage_id = _project(harness, 45.0)
    step = b"STEP " + name.encode()
    manifest = _manifest(step)
    manifest["instances"][0]["design_id"] = design_id
    _received(harness, name, _current(harness, manifest, design_id), step)


def test_a_revisionless_retry_after_a_mesher_failure_keeps_the_chosen_settings(
    real, monkeypatch
) -> None:
    harness, mesher = real
    _exported(harness, "mesher-fails")
    chosen = _revision(harness.store, _setup(engine="bempp"))

    def crashes(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise ChildRefusal("mesh", "the isolated CAD child crashed")

    monkeypatch.setattr(ingest_module, "build_imported_mesh_isolated", crashes)
    failed = _prepare(harness, setup_revision_id=chosen)
    # Failed before any preparation was recorded ...
    assert (failed["state"], failed["reason"]) == ("needs_user_input", "preparation_failed"), failed
    assert failed["preparationId"] is None
    # ... yet the operation already holds the settings the user chose.
    assert harness.row()["setup_revision_id"] == chosen

    monkeypatch.setattr(ingest_module, "build_imported_mesh_isolated", mesher)
    retried = _prepare(harness, setup_revision_id=None)

    assert (retried["state"], retried["jobId"]) == ("accepted", "job-1"), retried
    assert harness.submitted[-1].options.engine == "bempp"
    assert len(harness.jobs) == 1


def test_an_engine_chosen_after_engine_unavailable_is_the_one_submitted(real) -> None:
    harness, _mesher = real
    design_id, lineage_id = _project(harness, 45.0)
    step = b"STEP exported from WG"
    manifest = _manifest(step)
    manifest["instances"][0]["design_id"] = design_id
    _received(harness, "engine", _current(harness, manifest, design_id), step)
    # A Fusion request: the backend prepares it from its project's own setup.
    _record_setup(harness, lineage_id, _setup(engine="metal"))
    harness.submit_error = Refused("the selected engine cannot take this record; pick one of: bempp")

    refused = _prepare(harness, setup_revision_id=None)
    assert (refused["state"], refused["reason"]) == ("needs_user_input", "engine_unavailable"), refused
    assert harness.submitted[-1].options.engine == "metal"

    # A plain retry is a continuation: it keeps the operation's settings.
    harness.submit_error = Refused("the selected engine cannot take this record; pick one of: bempp")
    again = _prepare(harness, setup_revision_id=None)
    assert (again["state"], again["reason"]) == ("needs_user_input", "engine_unavailable"), again
    assert harness.submitted[-1].options.engine == "metal"

    # Choosing another engine sends it: an explicit revision wins.
    chosen = _prepare(harness, setup_revision_id=_revision(harness.store, _setup(engine="bempp")))

    assert (chosen["state"], chosen["jobId"]) == ("accepted", "job-3"), chosen
    assert harness.submitted[-1].options.engine == "bempp"
    assert len(harness.jobs) == 1


def test_settings_chosen_after_a_failure_replace_the_ones_the_operation_holds(
    real, monkeypatch
) -> None:
    harness, mesher = real
    _exported(harness, "chosen-again")
    first = _revision(harness.store, _setup(engine="metal"))

    def crashes(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise ChildRefusal("mesh", "the isolated CAD child crashed")

    monkeypatch.setattr(ingest_module, "build_imported_mesh_isolated", crashes)
    failed = _prepare(harness, setup_revision_id=first)
    assert failed["reason"] == "preparation_failed" and harness.row()["setup_revision_id"] == first

    monkeypatch.setattr(ingest_module, "build_imported_mesh_isolated", mesher)
    second = _revision(harness.store, _setup(engine="bempp"))
    solved = _prepare(harness, setup_revision_id=second)

    assert (solved["state"], solved["jobId"]) == ("accepted", "job-1"), solved
    assert harness.submitted[-1].options.engine == "bempp"
    assert harness.row()["setup_revision_id"] == second
