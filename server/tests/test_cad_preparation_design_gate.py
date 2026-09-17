"""Backend preparation names the design its snapshot belongs to, through the real ingest.

docs/architecture/CAD-OPERATIONS.md, "Preparation". The backend prepares a solve
into the snapshot's own project: the lineage of the solver anchor instance's WG
design, the one ``project_setup.snapshot_project`` resolves. So the ingest's
project gate is given that design and that instance. No open model is involved.

Every test here runs the production ``ingest_bundle``: the bundle reader, the
project and instance gates, the registry echoes, freshness and the record's
publication. Only the isolated STEP/mesher child is stood in for, as the
ingestion tests do (``test_cadlink_ingest.py``); ``FakeIngest`` never reaches the
gate, which is how a solve of any WG design waited as ``preparation_failed``.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import hashlib
import json
from typing import Any

import pytest

from server.cadlink import ingest as ingest_module
from server.cadlink.preparation import PreparationInput, prepare_operation
from server.cadlink.project_setup import snapshot_project
from server.cadlink.solver_frame import confirm_frame
from server.design.textcfg import parse
from server.exports.geometry_identity import geometry_hash_for_design
from server.mesh.imported import polar_grid_from_symmetry

from test_cad_preparation import Harness, _accept, _manifest, _revision, _setup
from test_cad_project_setup import _project


_SYMMETRY = {
    "cut_planes": [],
    "planes": {axis: {"accepted": False} for axis in ("x0", "y0", "z0")},
}


class MesherStandIn:
    """``build_imported_mesh_isolated`` without the child process: a valid result."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, assembly_path: Any, manifest: Any, mesh: Any, **kwargs: Any) -> dict[str, Any]:
        anchor = manifest["coordinate_system"].get("solver_anchor_instance_id")
        self.calls.append({"assembly_path": str(assembly_path), "anchor": anchor})
        return {
            "msh_text": "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n",
            "transformed_geometry_hash": "sha256:" + "3" * 64,
            "normalisation": {"anchor_instance_id": anchor},
            "role_resolution": {"source-hf": {"surfaces": [101]}},
            "role_findings": [],
            "symmetry": _SYMMETRY,
            "healing": {"performed": False, "mode": "none", "options": []},
            "polar_grid_derivation": polar_grid_from_symmetry(_SYMMETRY),
            "sizing_estimate": {"triangles": 1},
            "tag_allocation": {
                "tag_namespace": "wg-import-v1",
                "tag_map": {
                    "1": {"source_id": None, "instance_id": None, "role": "rigid"},
                    "101": {"source_id": "source-hf", "instance_id": anchor, "role": "HF"},
                },
                "source_tags": {"source-hf": 101},
            },
            "stats": {"triangle_count": 1},
            "metadata": {},
            "integrity": {"valid": True},
            "viewport_mesh": {"available": False, "reason": "not built in this test"},
        }


@pytest.fixture
def real(tmp_path, monkeypatch) -> tuple[Harness, MesherStandIn]:
    harness = Harness(tmp_path)
    mesher = MesherStandIn()
    monkeypatch.setattr(ingest_module, "build_imported_mesh_isolated", mesher)
    return harness, mesher


def _prepare(harness: Harness, operation_id: str = "cmd-1", **kwargs: Any) -> dict[str, Any]:
    # The production ingest, not the harness's FakeIngest.
    context = dataclasses.replace(harness.context(), ingest=ingest_module.ingest_bundle)
    return asyncio.run(prepare_operation(context, operation_id, PreparationInput(**kwargs)))


def _write(harness: Harness, name: str, manifest: dict[str, Any], step: bytes) -> tuple[str, str]:
    bundle = harness.workspace / "wgreturn" / f"{name}.wgreturn"
    bundle.mkdir(parents=True)
    (bundle / "assembly.step").write_bytes(step)
    body = json.dumps(manifest).encode("utf-8")
    (bundle / "wgreturn.json").write_bytes(body)
    return f"wgreturn/{name}.wgreturn", "sha256:" + hashlib.sha256(body).hexdigest()


def _received(harness: Harness, name: str, manifest: dict[str, Any], step: bytes) -> str:
    """A Fusion solve command for this return, accepted as the delivery loop accepts it."""

    bundle_path, digest = _write(harness, name, manifest, step)
    _accept(harness.store, "cmd-1", bundle_path, digest)
    return bundle_path


def _current(harness: Harness, manifest: dict[str, Any], design_id: str) -> dict[str, Any]:
    """Echo the registered head of ``design_id`` in its instances, so freshness reads current."""

    row = harness.store.get_design(design_id)
    assert row is not None
    geometry = geometry_hash_for_design(parse(str(row["snapshot_text"])).design)
    for instance in manifest["instances"]:
        if instance["design_id"] == design_id:
            instance["design_hash"] = row["design_hash"]
            instance["geometry_hash"] = geometry
            instance["lineage_id"] = row["lineage_id"]
    return manifest


def _record(harness: Harness, summary: dict[str, Any]) -> dict[str, Any]:
    assert summary["preparationId"], summary
    ingest = harness.store.get_ingest(str(summary["preparationId"]))
    assert ingest is not None
    return json.loads(ingest["record_json"])


def _freshness(record: dict[str, Any]) -> list[tuple[str, bool]]:
    return [
        (str(item.get("verdict")), bool(item.get("blocking")))
        for item in record["findings"]
        if item.get("kind") == "freshness"
    ]


def test_a_wg_exported_return_prepares_and_submits_through_the_real_ingest(real) -> None:
    harness, mesher = real
    design_id, lineage_id = _project(harness, 45.0)
    step = b"STEP exported from WG"
    manifest = _manifest(step)
    manifest["instances"][0]["design_id"] = design_id
    _received(harness, "exported", _current(harness, manifest, design_id), step)

    summary = _prepare(harness, setup_revision_id=_revision(harness.store, _setup()))

    assert (summary["state"], summary["stage"], summary["jobId"]) == (
        "accepted", "submitted", "job-1",
    ), summary
    assert harness.submitted[0].client_request_id == "cad-solve:cmd-1"
    assert len(mesher.calls) == 1
    record = _record(harness, summary)
    # Prepared into the snapshot's own project, through its solver anchor.
    assert record["identity"]["selected_instance_id"] == "instance-1"
    assert record["anchor"]["design_id"] == design_id
    assert record["project"]["lineage_id"] == lineage_id == snapshot_project(harness.store, manifest)
    assert _freshness(record) == []


def test_a_design_repeated_in_the_assembly_resolves_to_its_solver_anchor_exactly(real) -> None:
    harness, _mesher = real
    design_id, _lineage_id = _project(harness, 45.0)
    step = b"STEP two copies of one design"
    manifest = _manifest(step)
    first = manifest["instances"][0]
    first["design_id"] = design_id
    second = copy.deepcopy(first)
    first["instance_id"], second["instance_id"] = "instance-a", "instance-b"
    manifest["instances"] = [first, second]
    manifest["coordinate_system"]["solver_anchor_instance_id"] = "instance-b"
    body = manifest["scope"]["included"][0]
    manifest["scope"]["included"] = [
        {**body, "object_id": "speaker-a", "name": "speaker-a", "wglink_instance_id": "instance-a"},
        {**body, "object_id": "speaker-b", "name": "speaker-b", "wglink_instance_id": "instance-b"},
    ]
    manifest["assembly"]["n_bodies_expected"] = 2
    source = manifest["sources"][0]
    source["instance_id"] = "instance-b"
    source["selectors"]["linked_throat"]["instance_id"] = "instance-b"
    source["observed"]["bodies"] = ["speaker-b"]
    _received(harness, "two-copies", _current(harness, manifest, design_id), step)

    summary = _prepare(harness, setup_revision_id=_revision(harness.store, _setup()))

    # Neither "choose the linked instance" nor the other copy: the anchor, exactly.
    assert summary["state"] == "accepted", summary
    record = _record(harness, summary)
    assert record["identity"]["selected_instance_id"] == "instance-b"
    assert record["anchor"]["instance_id"] == "instance-b"


def test_a_return_naming_a_design_wg_does_not_know_meets_the_ingests_own_finding(real) -> None:
    """The probe that found the defect, kept as its regression test.

    A Fusion return of a WG design, prepared with the real ingest. Preparation
    used to name no design, so the project gate refused it -- "belongs to the WG
    design it was exported from" -- and the solve waited as
    ``preparation_failed`` whatever the user did. Now the anchor's design is
    named. A design this workspace does not know still meets exactly what the
    UI's ingest meets for it: a blocking missing-design finding to review.
    """

    harness, _mesher = real
    step = b"STEP"
    manifest = _manifest(step)
    unknown = manifest["instances"][0]["design_id"]
    assert harness.store.get_design(unknown) is None
    bundle_path = _received(harness, "speaker", manifest, step)
    revision = _revision(harness.store, _setup())

    summary = _prepare(harness, setup_revision_id=revision)

    assert (summary["state"], summary["reason"]) == ("needs_user_input", "findings_need_review"), summary
    assert "project gate" not in str(summary["message"])
    assert harness.submitted == []
    record = _record(harness, summary)
    assert _freshness(record) == [("missing_design", True)]
    # The same finding the UI's ingest route reaches when it names that design.
    through_the_ui = ingest_module.ingest_bundle(
        harness.workspace / bundle_path,
        _setup()["geometry"]["mesh"],
        [],
        harness.store,
        harness.data_dir,
        expected_design_id=unknown,
    )
    assert _freshness(through_the_ui) == _freshness(record)
    # Not a dead end: reviewed on that preparation, it is solved.
    blocking = [item["id"] for item in record["findings"] if item.get("blocking")]
    solved = _prepare(
        harness,
        setup_revision_id=revision,
        approve_preparation_id=summary["preparationId"],
        approve_finding_ids=tuple(blocking),
    )
    assert (solved["state"], solved["jobId"]) == ("accepted", "job-1"), solved


def test_a_cad_authored_return_is_ingested_as_before_with_no_design_named(
    real, monkeypatch
) -> None:
    """An anchor that names no design leaves the gate as it was: nothing is named."""

    harness, _mesher = real
    step = b"STEP authored in CAD"
    manifest = _manifest(step)
    manifest["instances"] = []
    manifest["coordinate_system"]["solver_anchor_instance_id"] = None
    manifest["scope"]["included"][0]["wglink_instance_id"] = None
    source = manifest["sources"][0]
    source["instance_id"] = None
    source["selectors"].pop("linked_throat")
    _received(harness, "authored", manifest, step)
    named: list[dict[str, Any]] = []
    real_ingest = ingest_module.ingest_bundle

    def recording_ingest(*args: Any, **kwargs: Any) -> dict[str, Any]:
        named.append(
            {key: kwargs[key] for key in ("expected_design_id", "expected_instance_id") if key in kwargs}
        )
        return real_ingest(*args, **kwargs)

    monkeypatch.setattr(ingest_module, "ingest_bundle", recording_ingest)

    revision = _revision(harness.store, _setup())
    waiting = _prepare(harness, setup_revision_id=revision)
    # Prepared as modelled, then held for its solver frame (test_cad_preparation_solver_frame.py).
    assert (waiting["state"], waiting["reason"]) == (
        "needs_user_input", "frame_confirmation_required"
    ), waiting
    assert named == [{}]
    confirm_frame(harness.store, _record(harness, waiting), "+z")

    summary = _prepare(harness, setup_revision_id=revision)

    assert (summary["state"], summary["jobId"]) == ("accepted", "job-1"), summary
    assert named == [{}]
    assert _freshness(_record(harness, summary)) == [("unlinked", False)]
