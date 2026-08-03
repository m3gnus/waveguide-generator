"""Golden checks for the typed v2 design-to-mesher preview translation."""

from __future__ import annotations

import pytest

from server.design.schema import DesignConfig
from server.preview.translate import design_to_mesher_config


def _translate(payload: dict[str, object]) -> dict[str, object]:
    return design_to_mesher_config(DesignConfig.model_validate(payload))


def test_osse_family_and_guiding_curve_golden() -> None:
    config = _translate(
        {
            "formula": "OSSE",
            "L": 120,
            "a": "45 - 2*cos(p)",
            "a0": 10,
            "r0": 12.7,
            "k": 7,
            "s": 0.8,
            "n": 4,
            "q": 0.99,
            "rotation": 6,
            "guiding_curve": {"curve_type": 2, "width": 180, "sf_m1": 4},
        }
    )
    assert config["profile"] == {
        "formula": "OSSE",
        "r0": 12.7,
        "a": "45 - 2*cos(p)",
        "a0": 10.0,
        "k": 7.0,
        "q": 0.99,
        "L": 120.0,
        "n": 4.0,
        "s": 0.8,
        "rot": 6.0,
    }
    assert config["gcurve"] == {"gcurveType": 2.0, "gcurveWidth": 180.0, "gcurveSfM1": 4.0}


def test_rosse_family_golden_excludes_other_family_keys() -> None:
    config = _translate(
        {
            "formula": "R-OSSE",
            "R": 180,
            "r": 100,
            "b": 0.5,
            "m": 0.85,
            "tmax": 1.2,
            "r0": 12.7,
            "a": 50,
            "a0": 12,
            "k": 1,
            "q": 1,
        }
    )
    profile = config["profile"]
    assert profile["formula"] == "R-OSSE"
    assert {key: profile[key] for key in ("R", "r", "b", "m", "tmax")} == {
        "R": 180.0,
        "r": 100.0,
        "b": 0.5,
        "m": 0.85,
        "tmax": 1.2,
    }
    assert not {"L", "n", "s", "rot"}.intersection(profile)


def test_icw_family_golden_threads_positive_coverage_only() -> None:
    config = _translate(
        {
            "formula": "ICW",
            "r0": 12.7,
            "a": 50,
            "a0": 12,
            "k": 1,
            "q": 0.995,
            "L": 120,
            "R": 150,
            "termination": "flat_baffle",
            "n_coeff": 18,
            "coverage_angle": 55,
            "hold_start": 0.25,
            "hold_end": 0.75,
        }
    )
    profile = config["profile"]
    assert profile["termination"] == "flat_baffle"
    assert profile["n_coeff"] == 18.0
    assert profile["coverage_angle"] == 55.0
    assert profile["hold_start"] == 0.25
    assert profile["hold_end"] == 0.75
    assert not {"n", "s", "rot", "m", "r", "b", "tmax"}.intersection(profile)


def test_freeform_family_golden_uses_rows_and_station_camel_case() -> None:
    config = _translate(
        {
            "formula": "FREEFORM",
            "profile_h": {
                "points": [
                    {"z": 0, "r": 12.7, "angle_deg": 10, "strength": 0.8},
                    {"z": 120, "r": 100},
                ],
                "mouth_angle_deg": 0,
            },
            "profile_v": {"points": [{"z": 0, "r": 12.7}, {"z": 120, "r": 80}]},
            "cross_sections": [
                {"t": 0, "shape": "circle"},
                {"t": 0.5, "shape": "superellipse", "exponent": 4},
                {"t": 1, "shape": "rounded_rectangle", "corner_radius_mm": 12},
            ],
            "overshoot_policy": "allow",
        }
    )
    assert config["profile"] == {
        "formula": "FREEFORM",
        "profileH": {"points": [[0.0, 12.7, 10.0, 0.8], [120.0, 100.0]], "mouthAngleDeg": 0.0},
        "profileV": {"points": [[0.0, 12.7], [120.0, 80.0]]},
        "crossSections": [
            {"t": 0.0, "shape": "circle"},
            {"t": 0.5, "shape": "superellipse", "exponent": 4.0},
            {"t": 1.0, "shape": "rounded_rectangle", "cornerRadiusMm": 12.0},
        ],
        "overshootPolicy": "allow",
    }


@pytest.mark.parametrize(
    ("extra", "mode"),
    [
        ({"enclosure": {"depth": 80}}, "enclosure"),
        ({"simulation": {"sim_type": "infinite-baffle"}}, "infinite-baffle"),
        ({"mesh": {"wall_thickness": 3}}, "freestanding"),
        ({}, "bare"),
    ],
)
def test_mode_precedence_and_full_viewport_symmetry(extra: dict[str, object], mode: str) -> None:
    payload: dict[str, object] = {"formula": "OSSE", **extra}
    if "mesh" in payload:
        payload["mesh"] = {"quadrants": 1, **payload["mesh"]}  # type: ignore[dict-item]
    else:
        payload["mesh"] = {"quadrants": 1}
    config = _translate(payload)
    assert config["mode"] == mode
    assert config["mesh"]["quadrants"] == 1234


def test_source_shape_mapping_and_unsupported_contours() -> None:
    flat = _translate({"formula": "OSSE", "source": {"shape": 2, "radius": 20, "curvature": -1}})
    assert flat["source"] == {"sourceShape": 0.0, "sourceRadius": 20.0, "sourceCurv": -1.0}
    with pytest.raises(ValueError, match="contours"):
        _translate({"formula": "OSSE", "source": {"contours": "1, 2"}})
