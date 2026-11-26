"""One solve-domain record from every path (``domain-decision-v2``, stage 1).

``server/solver/domain_record.py``: a parametric design, a CAD return (linked
or authored in CAD), an imported mesh file and a compiled design assembly all
state their domain in the same record, with each plane's verdict, what was
expected and a non-blocking finding when a plane is lost. The fixtures are
real: designs through ``DesignConfig``, CAD returns through the production
ingest, a mesh file and an assembly through the models API. The test design
is a generic offset quarter (an OSSE horn 80 mm up, in a symmetric box).
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from server.cadlink.interpretation import domain_decision as dd
from server.engines import registry
from server.jobs.models import JobStatusResponse, SolveRequest
from server.jobs.runtime import JobRuntime, resolve_submission
from server.jobs.store import JobStore
from server.solver import domain_record as dr
from test_assembly_submission import compiled_app  # noqa: F401 - the fixture


#: The generic test design: an offset quarter (symmetric about x = 0 and,
#: once the 80 mm vertical offset is taken out, y = 0).
OFFSET_QUARTER = {
    "formula": "OSSE",
    "L": 60,
    "a": 45,
    "r0": 12.7,
    "mesh": {"vertical_offset": 80, "quadrants": 1},
    "enclosure": {"depth": 120, "space_l": 25, "space_r": 25, "space_t": 25, "space_b": 25},
}
#: The same horn with its box taller above the throat: x = 0 only.
TALL_ABOVE = copy.deepcopy(OFFSET_QUARTER)
TALL_ABOVE["enclosure"]["space_t"] = 185

#: Every field a ``cad-domain-decision-v1`` decision carried, and its frame's.
#: (As the 23 Nov live record has them: written out, not read from the code.)
V1_FIELDS = {
    "cad_cuts", "confidence", "contract", "evidence", "frame", "identity", "input_reading",
    "oblique_cuts", "off_centre_cuts", "offered_changes", "reflected_axes", "reflection",
    "refusal", "resolved_reading", "solver_domain", "sources", "wg_cut_planes",
}
V1_FRAME_FIELDS = {"solver_from_cad", "determinant", "proper", "axis", "up", "allowed_axes"}


def _request(design: dict[str, Any], **options: Any) -> SolveRequest:
    return SolveRequest.model_validate(
        {
            "design": {
                **design,
                "simulation": {"f1": 500, "f2": 8000, "num_frequencies": 3, "sim_type": "freestanding"},
            },
            "options": {"engine": "auto", "symmetry": "auto", **options},
        }
    )


def _bempp_registry() -> registry.EngineRegistry:
    return registry.EngineRegistry(
        detector=lambda: [
            registry.EngineInfo(
                "bempp", True, "test", "1",
                mountings=("free-standing", "ground-plane", "infinite-baffle"),
                ground_plane_axes=("x", "y", "z"),
            )
        ],
        factory=lambda name: object(),
    )


def _resolve(request: SolveRequest) -> dict[str, Any]:
    resolution = asyncio.run(resolve_submission(request, _bempp_registry()))
    assert resolution.domain_decision is not None
    return resolution.domain_decision


def _assert_record(record: dict[str, Any], producer: str) -> None:
    assert record["contract"] == dr.CONTRACT
    assert record["producer"] == producer
    assert set(record["planes"]) == {"x0", "y0"}
    for plane, entry in record["planes"].items():
        assert entry["used"] is (plane in record["solver_domain"]["planes"]), (plane, entry)
    assert record["solver_domain"]["multiplier"] == 2 ** len(record["solver_domain"]["planes"])


# ----------------------------------------------------------------- parametric


def test_a_parametric_offset_quarter_records_a_quarter_it_expected() -> None:
    record = _resolve(_request(OFFSET_QUARTER))

    _assert_record(record, dr.PRODUCER_PARAMETRIC)
    assert record["solver_domain"] == {"planes": ["x0", "y0"], "fraction": "quarter", "multiplier": 4}
    assert record["expected"] == {
        "planes": ["x0", "y0"], "fraction": "quarter", "by": "design", "detail": None,
    }
    for entry in record["planes"].values():
        assert entry["geometry"] == "accepted"
        assert entry["tolerance_class"] == "exact"
        assert entry["tolerance_mm"] > 0
    assert record["findings"] == []


def test_a_parametric_box_taller_above_rejects_y0_with_the_reason() -> None:
    record = _resolve(_request(TALL_ABOVE))

    assert record["solver_domain"]["planes"] == ["x0"]
    assert record["expected"]["planes"] == ["x0"]
    y0 = record["planes"]["y0"]
    assert y0["geometry"] == "rejected" and y0["used"] is False and y0["dropped_by"] is None
    assert "space_t=185" in y0["detail"]
    # Expected and solved agree: nothing was lost.
    assert record["findings"] == []


def test_a_ground_plane_drops_y0_by_the_users_choice_and_raises_nothing() -> None:
    record = _resolve(_request(OFFSET_QUARTER, ground_plane={"enabled": True, "axis": "y", "height_m": 1.0}))

    assert record["solver_domain"]["planes"] == ["x0"]
    assert record["expected"]["planes"] == ["x0", "y0"]
    assert record["planes"]["y0"]["geometry"] == "accepted"
    assert record["planes"]["y0"]["dropped_by"] == dr.DROPPED_BY_GROUND_PLANE
    assert dr.lost_planes(record) == ["y0"]
    assert record["findings"] == []


def test_a_forced_full_domain_is_the_users_choice_and_raises_nothing() -> None:
    record = _resolve(_request(OFFSET_QUARTER, symmetry="full"))

    assert record["solver_domain"]["planes"] == []
    assert {entry["dropped_by"] for entry in record["planes"].values()} == {dr.DROPPED_BY_USER_MODE}
    assert record["findings"] == []


def test_an_engine_that_needs_the_whole_model_loses_both_planes_visibly() -> None:
    """BEMPP's coupled infinite baffle solves the full domain: a loss the user did not choose."""

    design = {**OFFSET_QUARTER, "enclosure": None}
    request = _request(design, engine="bempp")
    request.design.root.simulation.sim_type = "infinite-baffle"
    record = _resolve(request)

    assert record["solver_domain"]["planes"] == []
    assert {entry["dropped_by"] for entry in record["planes"].values()} == {dr.DROPPED_BY_ENGINE}
    (finding,) = record["findings"]
    assert finding["kind"] == dr.LOST_PLANE_KIND
    assert finding["blocking"] is False
    assert finding["lost_planes"] == ["x0", "y0"]
    assert finding["detail"].startswith(
        "This design is a quarter model, but it solved as the whole model: "
        "the x = 0 mirror was lost because the engine needs the whole model"
    )


def test_a_native_formula_expects_nothing_and_loses_nothing() -> None:
    from server.design.native import NATIVE_FORMULAS

    formula = sorted(NATIVE_FORMULAS)[0]
    geometry = type("Verdict", (), {"xz": False, "yz": False, "reasons": {}, "tolerance_mm": 1e-7})()
    record = dr.parametric_decision(
        geometry, resolved_quadrants=1234, requested_mode="auto",
        ground_blocked_plane=None, native_formula=True,
    )
    assert formula
    assert record["expected"] is None
    assert {entry["dropped_by"] for entry in record["planes"].values()} == {dr.DROPPED_BY_NATIVE_FORMULA}
    assert record["findings"] == []


def test_a_parametric_job_keeps_its_record_and_serves_it(tmp_path: Path) -> None:
    from server.engines.dryrun import DryRunEngine

    async def submit() -> tuple[dict[str, Any], dict[str, Any]]:
        runtime = JobRuntime(
            JobStore(tmp_path / "jobs.db"),
            engine_registry=registry.EngineRegistry(
                detector=lambda: [registry.EngineInfo("dryrun", True, "test", "1")],
                factory=lambda _name: DryRunEngine(),
            ),
        )
        try:
            job_id = await runtime.submit(_request(OFFSET_QUARTER, engine="dryrun", stage_delay_ms=1))
            await runtime.wait_idle()
            return runtime.store.get_job_row(job_id), await runtime.get_job(job_id)
        finally:
            await runtime.shutdown()

    row, item = asyncio.run(submit())
    recorded = row["task_metadata"]["domain_decision"]
    _assert_record(recorded, dr.PRODUCER_PARAMETRIC)
    assert recorded["solver_domain"]["planes"] == ["x0", "y0"]
    # Beside the existing symmetry metadata, which stays for replay.
    assert row["task_metadata"]["symmetry"]["resolved_quadrants"] == 1
    assert item["domain_decision"] == recorded
    assert JobStatusResponse.model_validate(item).domain_decision == recorded
    # A run saved before the record existed serves exactly what it served.
    earlier = JobStatusResponse.model_validate({**item, "domain_decision": None})
    assert "domain_decision" not in earlier.model_dump(mode="json")


# ------------------------------------------------------- file mesh / assembly


def test_a_mesh_file_records_its_declared_domain(tmp_path: Path) -> None:
    from server.cadlink.store import CadLinkStore
    from server.jobs.models_api import register_mesh
    from test_mesh_import import registration

    store = CadLinkStore.for_data_dir(tmp_path)
    try:
        record = register_mesh(store, tmp_path, registration())
    finally:
        store.close()
    decision = dr.imported_decision(record)
    assert decision is not None
    _assert_record(decision, dr.PRODUCER_FILE_MESH)
    assert decision["solver_domain"]["planes"] == []
    assert decision["expected"] == {"planes": [], "fraction": "full", "by": "declaration", "detail": None}
    assert {entry["geometry"] for entry in decision["planes"].values()} == {"not-tested"}
    assert decision["findings"] == []


def test_a_mesh_file_declared_half_is_declared_not_lost() -> None:
    record = {"producer": "mesh-file", "symmetry": {"domain_planes": ["x0"], "cut_planes": []}}
    decision = dr.imported_decision(record)
    assert decision is not None
    assert decision["solver_domain"]["planes"] == ["x0"]
    assert decision["planes"]["x0"]["geometry"] == "declared"
    assert decision["expected"]["planes"] == ["x0"]
    assert decision["findings"] == []


def test_a_design_assembly_job_records_the_whole_model(compiled_app: Any) -> None:  # noqa: F811
    from test_assembly_submission import compile_body, setup_for
    from test_models_api import call

    app, runtime, _store = compiled_app

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            compiled = await call(app, "POST", "/api/models/assembly/compile", compile_body())
            record = compiled["prepared"]
            accepted = await call(app, "POST", f"/api/models/{compiled['model']['model_id']}/solve", {
                "prepared_id": record["ingest_id"], "client_request_id": "press-domain",
                "setup": setup_for(record, engine="metal")})
            row = await asyncio.to_thread(runtime.store.get_job_row, accepted["job_id"])
            return row, await runtime.get_job(accepted["job_id"])
        finally:
            await runtime.shutdown()

    row, item = asyncio.run(scenario())
    recorded = row["task_metadata"]["domain_decision"]
    _assert_record(recorded, dr.PRODUCER_DESIGN_ASSEMBLY)
    assert recorded["solver_domain"] == {"planes": [], "fraction": "full", "multiplier": 1}
    assert {entry["dropped_by"] for entry in recorded["planes"].values()} == {dr.DROPPED_BY_WOOFER}
    # Nothing to compare against yet (centred-woofer halves are a later task).
    assert recorded["expected"] is None and recorded["findings"] == []
    assert item["domain_decision"] == recorded


# ------------------------------------------------------------------------ CAD


def test_an_unlinked_cad_quarter_records_each_planes_evidence(tmp_path: Path) -> None:
    pytest.importorskip("gmsh")
    from test_cadlink_domain_automatic import HORN_THROAT, _bundle, _horn, _ingest

    record = _ingest(_bundle(tmp_path, "full", _horn, HORN_THROAT), tmp_path / "data")
    decision = record["domain_decision"]

    _assert_record(decision, dr.PRODUCER_CAD_UNLINKED)
    assert dd.decision_problem(record) is None
    assert decision["solver_domain"]["planes"] == ["x0", "y0"]
    for plane in ("x0", "y0"):
        entry = decision["planes"][plane]
        verdict = record["symmetry"]["planes"][plane]
        assert entry["geometry"] == "accepted" and entry["tolerance_class"] == "fitted"
        assert entry["tolerance_mm"] == pytest.approx(verdict["tolerance_step_units"])
        assert entry["max_residual_mm"] == pytest.approx(verdict["max_residual_step_units"])
        assert entry["points_tested"] == verdict["points_tested"] and entry["points_off"] == 0
    # Authored in CAD: the geometry's own verdict is what is expected.
    assert decision["expected"]["by"] == "geometry"
    assert decision["expected"]["planes"] == ["x0", "y0"]
    assert decision["findings"] == []
    assert decision["verification"]["verified"] is True
    assert decision["frame"]["solver_from_input"] == decision["frame"]["solver_from_cad"]
    assert decision["frame"]["canonical_translation_mm"] == [0.0, 0.0, 0.0]
    assert not any(item["kind"] == dr.LOST_PLANE_KIND for item in record["findings"])


def _sent_return_of(tmp_path: Path, body: dict[str, Any], echoed: dict[str, Any], name: str) -> Path:
    """Send ``body`` to CAD for real, and echo ``echoed`` back as the design config."""

    from server.design.schema import DesignConfig
    from test_cadlink_ingest_symmetry import _sent_design_bundle

    bundle = _sent_design_bundle(tmp_path, DesignConfig.model_validate(body), name=name)
    manifest_path = bundle / "wgreturn.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["instances"][0]["config"] = DesignConfig.model_validate(echoed).model_dump(
        mode="json", by_alias=True
    )
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return bundle


def test_a_linked_offset_quarter_expects_its_designs_quarter_and_keeps_it(tmp_path: Path) -> None:
    pytest.importorskip("gmsh")
    from test_cadlink_ingest_symmetry import _ingest

    record = _ingest(tmp_path, _sent_return_of(tmp_path, OFFSET_QUARTER, OFFSET_QUARTER, "sent"))
    decision = record["domain_decision"]

    _assert_record(decision, dr.PRODUCER_CAD_LINKED)
    assert dd.decision_problem(record) is None
    assert decision["expected"] == {
        "planes": ["x0", "y0"], "fraction": "quarter", "by": "design", "detail": None,
    }
    assert decision["solver_domain"]["planes"] == ["x0", "y0"]
    assert decision["findings"] == []


def test_a_linked_return_that_lost_y0_says_which_plane_and_why(tmp_path: Path) -> None:
    """The design in WG is a quarter; the body came back taller above the throat.

    The mirror check rejects y = 0 and the model solves as a half. The record
    keeps the evidence and raises the non-blocking note the design asks for
    (decision 6), on the model card and on every run solved from it.
    """

    pytest.importorskip("gmsh")
    from test_cadlink_ingest_symmetry import _ingest

    record = _ingest(tmp_path, _sent_return_of(tmp_path, TALL_ABOVE, OFFSET_QUARTER, "sent-centred"))
    decision = record["domain_decision"]

    assert dd.decision_problem(record) is None
    assert decision["solver_domain"]["planes"] == ["x0"]
    assert decision["expected"]["planes"] == ["x0", "y0"]
    y0 = decision["planes"]["y0"]
    verdict = record["symmetry"]["planes"]["y0"]
    assert y0["geometry"] == "rejected" and y0["used"] is False
    assert y0["points_off"] == verdict["points_off_model"] > 0
    assert y0["worst_off_mm"] == pytest.approx(verdict["worst_off_model_distance_step_units"])

    (finding,) = decision["findings"]
    assert finding["kind"] == dr.LOST_PLANE_KIND and finding["blocking"] is False
    assert finding["lost_planes"] == ["y0"]
    worst = f"{verdict['worst_off_model_distance_step_units']:.3g} mm"
    assert finding["detail"] == (
        "This design is a quarter model, but the returned CAD model solved as a half: "
        f"the y = 0 mirror was lost because its mirror check failed by up to {worst}: "
        f"{verdict['points_off_model']} of {verdict['points_tested']} sampled points do not "
        f"mirror onto the model (tolerance {verdict['tolerance_step_units']:.3g} mm)."
    )
    # On the model card, with the record's other notes; never blocking.
    noted = [item for item in record["findings"] if item["kind"] == dr.LOST_PLANE_KIND]
    assert len(noted) == 1 and noted[0]["detail"] == finding["detail"]
    assert noted[0]["blocking"] is False and noted[0]["id"].startswith("finding-symmetry-plane-lost-")


def test_a_linked_quarter_whose_cut_failed_its_mesh_check_names_the_loss(tmp_path: Path) -> None:
    pytest.importorskip("gmsh")
    from server.cadlink.intake.isolated import _inject_mesh_child_fault
    from test_cadlink_ingest_symmetry import _horn_bundle, _ingest

    bundle = _horn_bundle(tmp_path, "capped")
    with _inject_mesh_child_fault("leaking-reduced-domain"):
        record = _ingest(tmp_path, bundle)
    decision = record["domain_decision"]

    assert decision["solver_domain"]["planes"] == []
    for plane in ("x0", "y0"):
        assert decision["planes"][plane]["geometry"] == "accepted"
        assert decision["planes"][plane]["dropped_by"] == dr.DROPPED_BY_VERIFICATION
    (finding,) = decision["findings"]
    assert finding["lost_planes"] == ["x0", "y0"]
    assert finding["detail"].startswith(
        "This design is a quarter model, but the returned CAD model solved as the whole model: "
        "the x = 0 mirror was lost because the model cut there did not pass the mesh check"
    )
    assert decision["verification"]["fallback_reason"]


# -------------------------------------------------------------- v1 readers


def _as_v1(record: dict[str, Any]) -> dict[str, Any]:
    """The same record as an earlier build sealed it (``cad-domain-decision-v1``)."""

    earlier = copy.deepcopy(record)
    earlier["domain_decision"] = dd.v1_projection(record["domain_decision"])
    return earlier


def test_v2_keeps_every_v1_field_and_v1_records_still_read(tmp_path: Path) -> None:
    pytest.importorskip("gmsh")
    from test_cadlink_domain_automatic import HORN_THROAT, _bundle, _horn, _ingest

    record = _ingest(_bundle(tmp_path, "v1", _horn, HORN_THROAT), tmp_path / "data")
    decision = record["domain_decision"]
    assert V1_FIELDS <= set(decision) and V1_FRAME_FIELDS <= set(decision["frame"])
    assert set(dd.V1_FIELDS) == V1_FIELDS and set(dd.V1_FRAME_FIELDS) == V1_FRAME_FIELDS

    earlier = _as_v1(record)
    # A v1 record is still a valid record, read exactly as before.
    assert dd.decision_problem(earlier) is None
    v1_summary = dd.decision_summary(earlier)
    v2_summary = dd.decision_summary(record)
    assert v1_summary is not None and v2_summary is not None
    assert set(v1_summary) == set(v2_summary)
    for key in v1_summary.keys() - {"contract", "decision_sha256"}:
        assert v1_summary[key] == v2_summary[key], key
    # A job saved under the v1 decision still matches its v1 record.
    saved = {"imported_geometry": {"domain_decision": v1_summary}}
    assert dd.job_decision_problem(saved, earlier) is None
    # A v1 record has no v2 record for its job: the job keeps its summary.
    assert dr.imported_decision(earlier) is None
    core = dr.imported_decision(record)
    assert core == {key: decision[key] for key in dr.RECORD_FIELDS} | {
        "decision_sha256": decision["identity"]["decision_sha256"]
    }
