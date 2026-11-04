"""ATH's source direction survives fresh import and WG's existing solve routes."""

import asyncio

import pytest
from fastapi import HTTPException

from hornlab_mesher.config_builder import build_geometry_params
from hornlab_mesher.config_parser import ConfigError, parse_text_config
from hornlab_mesher.profile_sampling import build_point_grid_arrays
from server.design.schema import DesignConfig
from server.design.source_motion import source_motion
from server.design.textcfg import TextConfigError, parse, serialize
from server.design_io.api import open_design, serialize_design
from server.jobs.models import SolveRequest
from server.mesh.builder import _solver_mesher_config
from server.preview.translate import design_to_mesher_config
from server.solver.context import SolverContext


def geometry_bytes(config):
    return build_point_grid_arrays(build_geometry_params(config)[0])["inner_grid"].tobytes()


@pytest.fixture(scope="module")
def mesher_accepts_axial_velocity():
    # Test the installed parser's capability, not its package version: SHA
    # pins can have the same version while accepting different ATH inputs.
    try:
        config = parse_text_config("OSSE = {\nL = 80\nr0 = 12.7\na = 45\n}\nSource.Velocity = 2\n")
    except ConfigError as exc:
        assert "Source.Velocity = 2" in str(exc)
        return False
    assert config["source"]["sourceVelocity"] == 2
    return True


def assert_legacy_axial_refusal(text):
    with pytest.raises(TextConfigError, match=r"Source.Velocity") as caught:
        parse(text)
    assert "axial source motion" in str(caught.value)
    with pytest.raises(HTTPException) as opened:
        asyncio.run(open_design(text))
    assert opened.value.status_code == 422
    assert opened.value.detail["type"] == "parse_error"
    message = opened.value.detail["message"]
    assert "Source.Velocity" in message
    assert "normal" in message


@pytest.mark.parametrize("family, profile", [
    ("OSSE", "L = 80\nr0 = 12.7\na = 45\na0 = 7\n"),
    ("R-OSSE", "R = 100\nr0 = 12.7\na = 40\na0 = 7\n"),
])
@pytest.mark.parametrize("syntax", ["flat", "block", "mixed-before", "mixed-after"])
@pytest.mark.parametrize("shape", [1, 2])
def test_fresh_axial_source_import_reaches_preview_solve_and_saved_cfg(
    family, profile, syntax, shape, mesher_accepts_axial_velocity,
):
    source = f"Shape = {shape}\nRadius = 30\nCurv = -1\nVelocity = 2\n"
    source = f"Source = {{\n{source}}}\n" if syntax != "flat" else "".join(
        f"Source.{line}\n" for line in source.splitlines()
    )
    if syntax == "mixed-before":
        source = "Source.Velocity = 1\n" + source
    elif syntax == "mixed-after":
        source += "Source.Velocity = 1\n"
    text = f"{family} = {{\n{profile}}}\nABEC.SimType = 2\n{source}"
    if not mesher_accepts_axial_velocity:
        assert_legacy_axial_refusal(text)
        return
    parsed = parse(text)
    opened = asyncio.run(open_design(text))
    saved = asyncio.run(serialize_design({"design": opened["design"]}))
    variants = [parsed.design, DesignConfig.model_validate(opened["design"]),
                parse(saved["text"]).design, parse(serialize(parsed.design)).design]
    normal = parse(text.replace("Velocity = 2", "Velocity = 1")).design
    assert normal.root.source.velocity_convention is None
    normal_config = design_to_mesher_config(normal)
    expected = geometry_bytes(normal_config)
    for design in variants:
        assert design.root.source.velocity.constant_value() == 2
        # The editor must display axial motion instead of its normal default.
        assert design.root.source.velocity_convention == "axial"
        assert source_motion(design.root.source) == "axial"
        request = SolveRequest.model_validate({"design": design.model_dump(mode="json")})
        assert SolverContext.from_request(request, solver_mode="full_3d").source_motion == "axial"
        # CFG export can spell defaults explicitly; compare constructed bytes.
        for config in (design_to_mesher_config(design), _solver_mesher_config(design)):
            assert config["source"] == normal_config["source"]
            assert geometry_bytes(config) == expected


@pytest.mark.parametrize("version, velocity, convention", [
    (None, 2, None),
    (None, 2, "normal"),
    ("ath-2026-08c-v1", 2, None),
    ("ath-2026-09a-v2", 2, "legacy"),
    ("ath-2026-09a-v3", 1, None),
    ("ath-2026-09a-v3", 2, "normal"),
])
def test_saved_source_reading_is_not_replaced_by_the_fresh_import_convention(
    version, velocity, convention, mesher_accepts_axial_velocity,
):
    seed = DesignConfig.model_validate({
        "formula": "OSSE", "L": 80, "r0": 12.7, "a": 45,
        "text_import_version": version,
        "source": {"velocity": velocity, "velocity_convention": convention},
        "simulation": {"sim_type": "freestanding"},
    })
    text = serialize(seed)
    if version == "ath-2026-09a-v3" and velocity == 2 and not mesher_accepts_axial_velocity:
        assert_legacy_axial_refusal(text)
        return
    saved = parse(text)
    assert serialize(saved) == text
    assert saved.design.root.source.velocity_convention == convention
    assert saved.design.root.source.velocity.constant_value() == velocity
    assert source_motion(saved.design.root.source) == source_motion(seed.root.source)


@pytest.mark.parametrize("dotted_first", [False, True])
def test_previously_loadable_mixed_source_keeps_its_legacy_motion(dotted_first):
    # The old mesher accepted this because the Source block resolves to normal.
    # WG historically read the dotted 2; changing that now would change a solve.
    dotted = "Source.Velocity = 2\n"
    block = "Source = {\nVelocity = 1\n}\n"
    text = "OSSE = {\nL = 80\nr0 = 12.7\na = 45\n}\n" + (
        dotted + block if dotted_first else block + dotted
    )
    source = parse(text).design.root.source
    assert source.velocity.constant_value() == 2
    assert source.velocity_convention is None
    assert source_motion(source) == "axial"


@pytest.mark.parametrize("version", [None, "ath-2026-08c-v1", "ath-2026-09a-v2", "ath-2026-09a-v3"])
@pytest.mark.parametrize("dotted, block", [(1, 2), (2, 1)])
@pytest.mark.parametrize("convention", [None, "normal"])
def test_saved_mixed_source_retains_its_velocity_and_convention(
    version, dotted, block, convention, mesher_accepts_axial_velocity,
):
    seed = DesignConfig.model_validate({
        "formula": "OSSE", "L": 80, "r0": 12.7, "a": 45,
        "text_import_version": version,
        "source": {"velocity": dotted, "velocity_convention": convention},
        "simulation": {"sim_type": "freestanding"},
    })
    text = serialize(seed) + f"Source = {{\nVelocity = {block}\n}}\n"
    if version == "ath-2026-09a-v3" and block == 2 and not mesher_accepts_axial_velocity:
        assert_legacy_axial_refusal(text)
        return
    saved = parse(text)
    assert serialize(saved) == text
    assert saved.design.root.source.velocity.constant_value() == dotted
    assert saved.design.root.source.velocity_convention == convention
    assert source_motion(saved.design.root.source) == source_motion(seed.root.source)

