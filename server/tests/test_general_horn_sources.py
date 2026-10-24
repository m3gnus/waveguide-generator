"""Actual general-horn source ingestion and unchanged authored excitation/frame."""

from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import meshio
import numpy as np
import pytest

from hornlab_mesher.general_horn import FEATURE
from server.cadlink.store import CadLinkStore
from server.cadlink.intake.native_source import read_native_source
from server.contracts.source_contour import validate_contour_request
from server.solver.imported import imported_anchor_frame
from server.source_editor import create_router
from server.tests.test_source_assembly_editor import document
from server.tests.test_shared_source_assembly import request


def body(family, woofer=False):
    value = document()
    value["mesh_size_mm"] = 4
    value["dimensions"].update(width_mm=80, height_mm=80, depth_mm=50,
                               horn_xy_mm=[-12, 10])
    value["dimensions"].pop("horn_length_mm")
    value["dimensions"].pop("mouth_radius_mm")
    value["horn"]["drive"].update(weights={"piston": -.5}, motion="axial")
    if not woofer:
        value["woofer"] = None
        value["dimensions"].update(woofer_xy_mm=[0, 0], aperture_radius_mm=0)
    else:
        value["woofer"]["drive"]["weights"]["piston"] = 0
        value["dimensions"].update(woofer_xy_mm=[25, -20])
    # Compact horns: the mouth rim and horn wall are refined by a fixed 0.1 mm
    # surface deviation, whatever the mm resolutions, so the horn's size is what
    # keeps the dense solve under a 7 GiB host's memory ceiling (3.5 GiB).
    if family == "OSSE":
        profile = {"L_mm": 8, "r0_mm": 4, "a_deg": 32, "a0_deg": 6,
                   "k": 1.25, "s": .7, "n": 4, "q": .995}
    else:
        profile = {"R_mm": 18, "r0_mm": 4, "a_deg": 44, "a0_deg": 6,
                   "k": 1, "q": 1, "tmax": .6}
    value["horn_config"] = {"formula": family, "mode": "bare", "profile": profile,
        "mesh": {"angular_segments": 64, "length_segments": 32,
                 "throat_res_mm": 2, "mouth_res_mm": 3, "rear_res_mm": 3}}
    return value


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    store = CadLinkStore(tmp_path / "registry.sqlite")
    store.initialize()
    app.state.cadlink_store = store
    app.include_router(create_router(tmp_path))
    try:
        yield TestClient(app)
    finally:
        store.close()


@pytest.mark.parametrize("family,woofer", [("OSSE", False), ("ROSSE", True)])
def test_real_editor_export_ingestion_mesh_and_excitation(client, tmp_path, family, woofer):
    value = body(family, woofer)
    validated = client.post("/api/source-editor/assembly/validate", json=value)
    assert validated.status_code == 200, validated.text
    validation = validated.json()
    assert len(validation["horn_section_mm"]["horn-wall"]) == 257
    exported = client.post("/api/source-editor/assembly/export", json=value)
    assert exported.status_code == 200, exported.text
    with ZipFile(BytesIO(exported.content)) as archive:
        archive.extractall(tmp_path / "readback")
    manifest, model, _, _ = read_native_source(tmp_path / "readback")
    assert FEATURE in manifest["required_features"]
    assert len(model.parts) == (2 if woofer else 1)
    assert model.horn_wall is not None
    assert model.geometry_sha256 == validation["geometry_sha256"]
    response = client.post("/api/source-editor/assembly/ingest", json=value)
    assert response.status_code == 200, response.text
    record = response.json()["ingestion"]
    req = request(record)
    validate_contour_request(req.geometry, record)
    np.testing.assert_array_equal(imported_anchor_frame(record)["origin"], [0, 0, .007])
    assert record["native_source"]["channels"][0]["patch_weights"] == {"horn/piston": -.5}
    assert record["native_source"]["channels"][0]["motion"] == "axial"
    if woofer:
        assert record["native_source"]["channels"][1]["patch_weights"] == {"woofer/piston": 0}
    assert record["native_source"]["source_frames"]["horn"]["origin_m"] == pytest.approx(
        [-.012, .01, (7 - model.horn_length_mm) / 1000])
    assert response.json()["geometric_quality"]["passages"]["certified_clearance_mm"] > 0
    # The actual selected artifact is immutable and has exact source selectors.
    parsed = meshio.read(tmp_path / "readback" / "preview.msh")
    assert np.isfinite(parsed.points).all()
    bad = deepcopy(req)
    bad.geometry.required_features.remove(FEATURE)
    with pytest.raises(ValueError, match="feature"):
        validate_contour_request(bad.geometry, record)


def test_memory_ceiling_refusal_reaches_the_user(client, monkeypatch, caplog):
    # The worker subprocess inherits the lowered ceiling; the 422 carries the
    # guard's one wording and next step, never its DOF or memory figures, which
    # are recorded for the developers instead.
    import logging

    from server.mesh.errors import SolverMemoryTooLarge

    monkeypatch.setenv("WG2_DENSE_SOLVER_MEMORY_LIMIT_BYTES", str(1024**2))
    with caplog.at_level(logging.WARNING, logger="server.cadlink.intake.readings"):
        response = client.post("/api/source-editor/assembly/ingest", json=body("OSSE"))
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail == SolverMemoryTooLarge.USER_MESSAGE
    assert "DOFs" not in detail and "GiB" not in detail and "could not be built" not in detail
    diagnostics = [record.cad_diagnostic for record in caplog.records if hasattr(record, "cad_diagnostic")]
    assert any(item["stage"] == "memory-guard" and "1,048,576 bytes safety ceiling" in item["evidence"]["detail"]
               for item in diagnostics)


@pytest.mark.parametrize("mutation", ["noncircular", "rim", "datum", "shell", "unknown"])
def test_general_profile_refusal_precedes_publication(client, tmp_path, mutation):
    value = body("OSSE")
    if mutation == "noncircular":
        value["horn_config"]["cross_section"] = {"exponent": 2, "aspect_ratio": 1.2}
    elif mutation == "rim":
        value["horn"]["contour"]["points"][-1]["r_mm"] = 3
    elif mutation == "datum":
        value["dimensions"]["horn_length_mm"] = 25
    elif mutation == "shell":
        value["horn_config"]["mode"] = "freestanding"
        value["horn_config"]["mesh"]["wall_thickness_mm"] = 1
    else:
        value["unknown"] = True
    for action in ("validate", "export", "ingest"):
        response = client.post("/api/source-editor/assembly/" + action, json=value)
        assert response.status_code == 422, response.text
    assert not (tmp_path / "tmp").exists()


@pytest.mark.parametrize("formula", ["OSSE", "R-OSSE"])
def test_current_design_action_reuses_profile_and_preserves_design(client, formula):
    # Existing finite-wall, reduced-domain design: the explicit action selects
    # its full horn profile on the assembly enclosure, preserving the profile.
    design = {"formula": formula, "r0": 4, "a": 32, "a0": 6,
              "mesh": {"wall_thickness": 2, "quadrants": 1,
                       "length_segments": 32, "angular_segments": 64},
              "simulation": {"sim_type": "freestanding"}}
    design["L" if formula == "OSSE" else "R"] = 24
    retained = deepcopy(design)
    response = client.post("/api/source-editor/assembly/horn-profile", json=design)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["throat_radius_mm"] == pytest.approx(4, abs=1e-9)
    assert value["horn_config"]["mode"] == "bare"
    from hornlab_mesher.general_horn import GeneralHornWall
    wall = GeneralHornWall.from_config(value["horn_config"])
    assert value["wall_max_radius_mm"] == max(pole[0] for pole in wall.poles_mm)
    assert value["wall_max_radius_mm"] >= value["mouth_radius_mm"]
    assert value["horn_config"]["mesh"]["wallThickness"] == 0
    assert value["horn_config"]["mesh"]["quadrants"] == 1234
    assert design == retained


def test_horn_profile_reports_widest_wall_radius_beyond_a_rollback_mouth(client):
    from hornlab_mesher.general_horn import GeneralHornWall

    design = {"formula": "R-OSSE", "R": 60, "r0": 12.7, "a": 40, "a0": 15,
              "tmax": 1.0, "r": 0.5, "b": 0.6, "m": 0.9, "q": 3.4,
              "mesh": {"length_segments": 16, "angular_segments": 32}}
    response = client.post("/api/source-editor/assembly/horn-profile", json=design)
    assert response.status_code == 200, response.text
    value = response.json()
    wall = GeneralHornWall.from_config(value["horn_config"])
    assert value["wall_max_radius_mm"] == max(pole[0] for pole in wall.poles_mm)
    assert value["wall_max_radius_mm"] > value["mouth_radius_mm"]


def test_current_design_attachment_keeps_imported_circle_interpretation(client):
    from hornlab_mesher.general_horn import GeneralHornWall
    from hornlab_mesher.text_import import TEXT_IMPORT_VERSION

    design = {"formula": "OSSE", "r0": 4, "L": 18, "a": 32, "a0": 6,
              "text_import_version": TEXT_IMPORT_VERSION,
              "morph": {"target_shape": 2, "target_width": 120, "target_height": 120},
              "mesh": {"wall_thickness": 2, "quadrants": 1,
                       "length_segments": 32, "angular_segments": 64,
                       "throat_resolution": 2, "mouth_resolution": 3}}
    retained = deepcopy(design)
    imported = client.post("/api/source-editor/assembly/horn-profile", json=design)
    assert imported.status_code == 200, imported.text
    frozen = imported.json()["horn_config"]
    assert frozen["_textImportVersion"] == TEXT_IMPORT_VERSION
    assert imported.json()["mouth_radius_mm"] < 60
    assert GeneralHornWall.from_config(frozen).mouth_radius_mm == pytest.approx(
        imported.json()["mouth_radius_mm"], abs=1e-9)
    native = client.post("/api/source-editor/assembly/horn-profile",
                         json={key: value for key, value in design.items()
                               if key != "text_import_version"})
    assert native.status_code == 200, native.text
    assert native.json()["mouth_radius_mm"] == pytest.approx(60, abs=1e-9)
    assert design == retained


def test_current_design_extension_reports_driver_end_rim_and_refuses_mismatched_contour(client):
    from hornlab_mesher.config_builder import resolve_geometry
    design = {"formula": "OSSE", "L": 24, "r0": 4, "a": 32, "a0": 6,
              "throat_ext_length": 5, "throat_ext_angle": 10,
              "mesh": {"length_segments": 32, "angular_segments": 64}}
    response = client.post("/api/source-editor/assembly/horn-profile", json=design)
    assert response.status_code == 200, response.text
    profile = response.json()
    resolved = resolve_geometry(profile["horn_config"]).geometry.inner_points
    actual_rim = float(np.linalg.norm(resolved[0, 0, :2]))
    assert profile["throat_radius_mm"] == pytest.approx(actual_rim, abs=1e-9)
    assert abs(actual_rim - 4) > .1
    value = body("OSSE")
    retained = deepcopy(value["horn"])
    value["horn_config"] = profile["horn_config"]
    refused = client.post("/api/source-editor/assembly/validate", json=value)
    assert refused.status_code == 422
    assert "source rim" in refused.text
    assert value["horn"] == retained
