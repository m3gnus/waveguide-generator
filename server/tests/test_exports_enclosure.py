"""Bore exports remove enclosures before translating imported designs."""

import asyncio
import struct

import pytest

from server.design.schema import DesignConfig
from server.design.text_import import TEXT_IMPORT_VERSIONS
from server.exports import core
from server.mesh.gmsh_worker import run_on_gmsh_worker
from server.preview.translate import design_to_mesher_config
from test_design_assembly_record import assembly, woofer


@pytest.mark.parametrize("version", [None, *sorted(TEXT_IMPORT_VERSIONS)])
@pytest.mark.parametrize("with_woofer", [False, True])
def test_stl_exports_positive_enclosure_without_mutating_saved_design(version, with_woofer):
    design = DesignConfig.model_validate({
        "formula": "OSSE", "L": 40, "a": 45,
        "text_import_version": version,
        "enclosure": {"depth": 280, "space_b": 200},
        "assembly": assembly(woofer()) if with_woofer else None,
    })
    saved = design.model_dump(mode="json")
    # STEP's authored translation has the positive depth. The STL intentionally
    # exports the bore only, so its translated execution config has no box.
    assert design_to_mesher_config(design)["enclosure"]["depth"] == 280
    config = core._stl_mesher_config(design)
    assert config["mode"] == "bare"
    assert "enclosure" not in config

    canonical = asyncio.run(run_on_gmsh_worker(core._build_stl_mesh_sync, saved))
    output = core.binary_stl(canonical["vertices"], canonical["indices"], canonical["surfaceTags"])
    triangles = struct.unpack_from("<I", output, 80)[0]
    assert triangles > 0
    assert len(output) == 84 + 50 * triangles
    assert design.model_dump(mode="json") == saved
    assert DesignConfig.model_validate(saved) == design


def test_v3_surface_preparation_removes_box_before_native_validation():
    design = DesignConfig.model_validate({
        "formula": "OSSE", "L": 40, "a": 45,
        "text_import_version": "ath-2026-09a-v3", "ath_axial_scale": 1.2,
        "enclosure": {"depth": 280},
    })
    config = core._bare_grid_config(design)
    assert "enclosure" not in config
    assert core._inner_grid(design).shape[2] == 3
