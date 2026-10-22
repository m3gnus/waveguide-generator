"""Dense-preview diagnoses warn on saved solve meshes without changing bytes."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from server.design.schema import DesignConfig
from server.mesh import builder
from server.preview import core, wall_check
from server.preview.errors import mesher_error_message
from server.preview.translate import design_to_mesher_config


DATA = json.loads((Path(__file__).parent / "fixtures/wall-fold/freeform.json").read_text())
DETAIL = "FREEFORM generated outer offset grid has a normal flip near azimuth row 17, axial interval 382"


@pytest.fixture(autouse=True)
def clear_caches():
    wall_check.clear()
    builder.clear_solver_mesh_cache()
    yield
    wall_check.clear()
    builder.clear_solver_mesh_cache()


def test_real_folded_preview_warns_but_saved_solve_bytes_are_identical():
    data = deepcopy(DATA)
    # Coarse mm elements keep this byte-identity regression below two minutes;
    # the original profile, wall and preview sampling remain unchanged.
    data["mesh"].update(throat_resolution=35, mouth_resolution=40, rear_resolution=40, quadrants=1)
    design = DesignConfig.model_validate(data)
    before = design.model_dump(mode="json")

    async def scenario():
        # The old construction path, which the advisory must not change.
        old = await builder.run_mesh_build(builder._build_sync, before)
        result = await builder.build_solver_mesh(design, {"mesh_validation_mode": "off"})
        assert result["msh_text"] == old["msh_text"]
        assert result["stats"]["preview_wall_check_status"] == "warning"
        message = mesher_error_message(design_to_mesher_config(design), DETAIL)
        assert message in result["stats"]["warnings"]
        repeated = await builder.build_solver_mesh(design, {"mesh_validation_mode": "off"})
        assert repeated["msh_text"] == old["msh_text"]
        assert repeated["stats"]["preview_wall_check_cache_hit"] is True
        assert repeated["stats"]["warnings"].count(message) == 1

    asyncio.run(scenario())
    assert design.model_dump(mode="json") == before


@pytest.mark.parametrize("folded", [True, False])
def test_production_fine_preview_is_reused_without_extra_build(folded, monkeypatch):
    import hornlab_mesher.preview.api as api
    from test_preview_ws_protocol import _small_geometry

    calls = 0

    def preview(*args):
        nonlocal calls
        calls += 1
        if folded:
            raise ValueError(DETAIL)
        return _small_geometry()

    async def forbidden(*args, **kwargs):
        pytest.fail("known fine-preview outcome must not build again")

    monkeypatch.setattr(api, "build_preview_geometry", preview)
    monkeypatch.setattr(builder, "_run_wall_check", forbidden)
    design = DesignConfig.model_validate(DATA)
    protocol = core.PreviewProtocol()
    request = core._Request(kind="preview", seq=1, design_revision=1, lod="fine", design=design)
    try:
        if folded:
            with pytest.raises(ValueError):
                protocol._compute_frame(request)
        else:
            protocol._compute_frame(request)
        result = {"stats": {"warnings": []}}
        asyncio.run(builder._preview_wall_advisory(design, result))
        assert result["stats"]["preview_wall_check_cache_hit"] is True
        assert bool(result["stats"]["warnings"]) is folded
        assert calls == 1
    finally:
        asyncio.run(protocol._preview_service.shutdown())


def test_cache_misses_after_design_or_mesher_change(monkeypatch):
    design = DesignConfig.model_validate(DATA)
    config = core._cache_relevant_config(core._recentred_preview_config(design))
    wall_check.remember(config, DETAIL)
    assert wall_check.lookup(config) == (True, DETAIL)
    changed = deepcopy(config)
    changed["mesh"]["wallThickness"] = 4
    assert wall_check.lookup(changed) == (False, None)
    monkeypatch.setattr(wall_check, "mesher_identity", lambda: "different-pin")
    assert wall_check.lookup(config) == (False, None)


@pytest.mark.parametrize("mode", ["bare", "infinite-baffle", "enclosure"])
def test_no_extra_build_without_freestanding_wall(mode, monkeypatch):
    data = deepcopy(DATA)
    if mode == "bare":
        data["mesh"]["wall_thickness"] = 0
    elif mode == "infinite-baffle":
        data["simulation"]["sim_type"] = mode
    else:
        data["enclosure"]["depth"] = 100

    async def forbidden(*args, **kwargs):
        pytest.fail("inactive wall must not be checked")

    monkeypatch.setattr(builder, "_run_wall_check", forbidden)
    result = {"stats": {"warnings": []}}
    asyncio.run(builder._preview_wall_advisory(DesignConfig.model_validate(data), result))
    assert result["stats"] == {"warnings": []}


def test_unrelated_preview_failure_is_not_a_solve_refusal(monkeypatch, caplog):
    import hornlab_mesher.preview.api as api

    def fail(*args):
        raise ValueError("unrelated preview failure")

    monkeypatch.setattr(api, "build_preview_geometry", fail)
    config = design_to_mesher_config(DesignConfig.model_validate(DATA))
    assert wall_check.check_fine_wall(config) == (False, None)
    wall_check.remember(config, "unrelated preview failure")
    assert wall_check.lookup(config) == (False, None)
    assert "unrelated preview failure" in caplog.text


def test_diagnostic_worker_crash_does_not_refuse_saved_solve(monkeypatch, caplog):
    async def crash(*args, **kwargs):
        raise builder.MesherCrashError("diagnostic worker crashed")

    monkeypatch.setattr(builder, "_run_wall_check", crash)
    result = {"msh_text": "saved mesh", "stats": {"warnings": []}}
    asyncio.run(builder._preview_wall_advisory(DesignConfig.model_validate(DATA), result))
    assert result["msh_text"] == "saved mesh"
    assert result["stats"]["preview_wall_check_status"] == "unavailable"
    assert "diagnostic worker crashed" in caplog.text


@pytest.mark.parametrize("lod,injected", [("coarse", False), ("fine", True), ("coarse", True)])
def test_coarse_and_injected_previews_do_not_certify_dense_wall(lod, injected, monkeypatch):
    import hornlab_mesher.preview.api as api
    from test_preview_ws_protocol import _small_geometry

    preview = lambda *args: _small_geometry()
    monkeypatch.setattr(api, "build_preview_geometry", preview)
    design = DesignConfig.model_validate(DATA)
    protocol = core.PreviewProtocol(preview_builder=preview if injected else None)
    request = core._Request(kind="preview", seq=1, design_revision=1, lod=lod, design=design)
    try:
        protocol._compute_frame(request)
        config = core._cache_relevant_config(core._recentred_preview_config(design))
        assert wall_check.lookup(config) == (False, None)
    finally:
        asyncio.run(protocol._preview_service.shutdown())


def test_optional_diagnosis_preserves_cancellation(monkeypatch):
    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(builder, "_run_wall_check", cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(builder._preview_wall_advisory(
            DesignConfig.model_validate(DATA), {"stats": {"warnings": []}}))
