"""Legacy general-horn artifacts still import with their exact excitation and frame."""

from copy import deepcopy
from dataclasses import replace

import meshio
import numpy as np
import pytest

from hornlab_mesher.assembly_artifact import export_assembly
from hornlab_mesher.general_horn import FEATURE
from hornlab_mesher.phase_plug import PhasePlug
from hornlab_mesher.source_assembly import SourceAssembly
from server.cadlink.intake.native_source import read_native_source
from server.contracts.source_contour import validate_contour_request
from server.solver.imported import imported_anchor_frame
from server.tests.test_shared_source_assembly import case, ingest, request


def assembly(family, woofer=False):
    model, drives = case(False)
    horn = model.horn
    hf = replace(drives[0], weights=(("piston", -.5),), motion="axial")
    lf = replace(drives[1], weights=(("piston", 0),))
    # Compact horns keep artifact meshing and dense-memory admission small.
    if family == "OSSE":
        profile = {"L_mm": 8, "r0_mm": 4, "a_deg": 32, "a0_deg": 6,
                   "k": 1.25, "s": .7, "n": 4, "q": .995}
    else:
        profile = {"R_mm": 18, "r0_mm": 4, "a_deg": 44, "a0_deg": 6,
                   "k": 1, "q": 1, "tmax": .6}
    config = {"formula": family, "mode": "bare", "profile": profile,
              "mesh": {"angular_segments": 64, "length_segments": 32,
                       "throat_res_mm": 2, "mouth_res_mm": 3, "rear_res_mm": 3}}
    attached = SourceAssembly.attach(
        horn, config, woofer=model.woofer if woofer else None,
        width_mm=80, height_mm=80, depth_mm=50, front_z_mm=7,
        horn_xy_mm=(-12, 10), woofer_xy_mm=(25, -20) if woofer else (0, 0),
        aperture_radius_mm=8 if woofer else 0,
        phase_plugs=(PhasePlug("vane/A", 2, 6, 2, 2.5, 2.4, 2.9),),
    )
    return attached, [hf, lf] if woofer else [hf]


@pytest.mark.parametrize("family,woofer", [("OSSE", False), ("ROSSE", True)])
def test_native_artifact_ingestion_mesh_and_excitation(tmp_path, family, woofer):
    model, drives = assembly(family, woofer)
    artifact = tmp_path / "readback"
    export_assembly(model, drives, artifact, mesh_size_mm=4)
    manifest, restored, _, _ = read_native_source(artifact)
    assert restored.geometry_sha256 == model.geometry_sha256
    assert FEATURE in manifest["required_features"]
    assert len(model.parts) == (2 if woofer else 1)
    assert model.horn_wall is not None
    record = ingest(artifact, tmp_path, mesh_size_mm=4)
    req = request(record)
    validate_contour_request(req.geometry, record)
    np.testing.assert_array_equal(imported_anchor_frame(record)["origin"], [0, 0, .007])
    assert record["native_source"]["channels"][0]["patch_weights"] == {"horn/piston": -.5}
    assert record["native_source"]["channels"][0]["motion"] == "axial"
    if woofer:
        assert record["native_source"]["channels"][1]["patch_weights"] == {"woofer/piston": 0}
    assert record["native_source"]["source_frames"]["horn"]["origin_m"] == pytest.approx(
        [-.012, .01, (7 - model.horn_length_mm) / 1000])
    assert record["mesh"]["geometric_quality"]["passages"]["certified_clearance_mm"] > 0
    # The actual selected artifact is immutable and has exact source selectors.
    parsed = meshio.read(tmp_path / "readback" / "preview.msh")
    assert np.isfinite(parsed.points).all()
    bad = deepcopy(req)
    bad.geometry.required_features.remove(FEATURE)
    with pytest.raises(ValueError, match="feature"):
        validate_contour_request(bad.geometry, record)
