"""Shared file-block expectations for the headless solve-options reader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from server.design.solve_block import solve_options_from_blocks
from server.jobs.models import SolveOptions


FIXTURES = Path(__file__).parent / "fixtures" / "solve_blocks"


def _portable_dump(options: Any) -> dict[str, Any]:
    value = options.model_dump(mode="json")
    polar = value["polar_config"]
    result = {
        key: value[key]
        for key in (
            "engine",
            "symmetry",
            "mesh_validation_mode",
            "verbose",
            "frequency_spacing",
        )
    }
    if value["frequencies_hz"] is not None:
        result["frequencies_hz"] = value["frequencies_hz"]
    result["polar_config"] = {
        key: polar[key]
        for key in (
            "angle_range",
            "angle_step",
            "distance",
            "norm_angle",
            "inclination",
            "enabled_axes",
            "observation_origin",
            "spherical_sampling",
            "field_plane",
        )
    }
    return result


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda path: path.stem)
def test_solve_options_match_shared_fixture(path: Path) -> None:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert _portable_dump(solve_options_from_blocks(fixture["blocks"])) == fixture[
        "expected"
    ]


def test_base_is_overridden_in_abec_then_wg_order() -> None:
    base = solve_options_from_blocks(
        {
            "WG.Solve": {
                "items": {"ObservationOrigin": "throat", "FieldPlane": "0"}
            }
        }
    )
    resolved = solve_options_from_blocks(
        {
            "ABEC.Polars:SPL_H": {
                "items": {
                    "MapAngleRange": "0,90,10",
                    "Distance": "4",
                    "NormAngle": "5",
                }
            },
            "WG.Solve": {
                "items": {"ObservationOrigin": "mouth", "FieldPlane": "1"}
            },
        },
        base,
    )
    assert resolved.polar_config.distance == 4
    assert resolved.polar_config.observation_origin == "mouth"
    assert resolved.polar_config.field_plane is True


def test_explicit_base_is_unchanged_when_the_file_has_no_solve_blocks() -> None:
    base = SolveOptions()
    assert solve_options_from_blocks({}, base) == base
