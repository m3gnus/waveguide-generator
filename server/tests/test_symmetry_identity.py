"""Saved designs keep their symmetry answer under the mesher's one mirror check.

Stage 2 of the unified symmetry process moved the design mirror test from
``server/solver/symmetry.py`` into ``hornlab_mesher.symmetry``. The answers it
gave before the move are frozen in ``fixtures/symmetry-resolution-golden.json``
for the saved-design corpora (FREEFORM and LOOKUP old configs, the ICW default,
the legacy Custom profile, an offset quarter ATH config) and a few enclosure
and morph designs; every one must come back the same.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from server.design.schema import DesignConfig
from server.solver import symmetry

TESTS = Path(__file__).parent
GOLDEN = json.loads((TESTS / "fixtures" / "symmetry-resolution-golden.json").read_text())["designs"]

_ROSSE = {"formula": "R-OSSE", "R": 150, "r0": 12.7, "a": 60, "a0": 15.5}
EXTRA = {
    "extra/enclosure-dim-symmetric": {**_ROSSE, "enclosure": {"depth": 60, "dim_mm": [200, 150, 200, 150]}},
    "extra/enclosure-spacing-tb": {**_ROSSE, "enclosure": {"depth": 60, "space_t": 20, "space_b": 40}},
    "extra/ellipse-enclosure-res": {
        **_ROSSE, "cross_section": {"aspect_ratio": 1.5},
        "enclosure": {"depth": 60, "front_resolution": [20, 24, 24, 20], "space_l": 10, "space_r": 30},
    },
    "extra/osse-morph": {
        "formula": "OSSE", "L": 120, "r0": 12.7, "a": 60, "a0": 15.5,
        "morph": {"target_shape": 1, "target_width": 300, "target_height": 200,
                  "corner_radius": 30, "rate": 3, "fixed_part": 0.1},
    },
}


def _open(text: str) -> DesignConfig:
    """The app's Open path: typed JSON, or the design reader's migrations."""
    from server.jobs.design_reader import parse

    try:
        return DesignConfig.model_validate(json.loads(text))
    except Exception:  # noqa: BLE001 - a table profile needs the Open converter
        return parse(text).design


def _design(name: str) -> DesignConfig:
    if name in EXTRA:
        return DesignConfig.model_validate(EXTRA[name])
    kind, _, case = name.partition("/")
    if name == "tritonia-q":
        from server.design.textcfg import parse

        return parse((TESTS / "data" / "260308tritonia-q.txt").read_text()).design
    if kind == "freeform":
        from server.tests.freeform_oldconfig_support import CASES, load_case

        return load_case(next(row for row in CASES if row["name"] == case))
    if kind == "lookup":
        raw = json.loads((TESTS / "data" / "lookup_oldconfig_corpus" / f"{case}.json").read_text())
        return _open(json.dumps(raw["design"]))
    if name == "icw-default":
        return _open((TESTS / "data" / "icw-default.json").read_text())
    if kind == "custom-profile-legacy":
        legacy = json.loads((TESTS / "data" / "custom-profile-legacy.json").read_text())
        return _open(legacy[case]["json"])
    raise KeyError(name)


def _deviation(resolution: symmetry.SymmetryResolution) -> dict[str, Any]:
    """Each plane's largest sampled distance: the residual, or the miss."""
    out: dict[str, Any] = {"x0": None, "y0": None}
    for plane, verdict in (resolution.plane_verdicts or {}).items():
        out[plane] = (
            verdict["max_residual_step_units"] if verdict["accepted"]
            else verdict["worst_off_model_distance_step_units"]
        )
    return out


@pytest.mark.slow
@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_saved_design_symmetry_is_unchanged(name: str) -> None:
    frozen = GOLDEN[name]
    symmetry._resolution_cache.clear()
    resolution = symmetry.resolve_symmetry(_design(name))
    answer = resolution.as_dict()
    for key in ("quadrants", "xz", "yz", "reasons", "tolerance_mm", "relative_tolerance"):
        assert answer[key] == frozen[key], (name, key)
    deviation = _deviation(resolution)
    for plane, expected in frozen["max_deviation_mm"].items():
        if expected is None:
            assert deviation[plane] is None, (name, plane)
        else:
            assert deviation[plane] == pytest.approx(expected, rel=0, abs=1e-12), (name, plane)


def test_the_golden_covers_every_corpus() -> None:
    kinds = {name.partition("/")[0] for name in GOLDEN}
    assert {"tritonia-q", "freeform", "lookup", "icw-default", "custom-profile-legacy", "extra"} <= kinds
    assert sum(name.startswith("freeform/") for name in GOLDEN) >= 30
