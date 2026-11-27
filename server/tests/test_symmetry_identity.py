"""Saved designs keep their symmetry answer under the mesher's one mirror check.

Stage 2 of the unified symmetry process moved the design mirror test from
``server/solver/symmetry.py`` into ``hornlab_mesher.symmetry``. The answers it
gave before the move are frozen in ``fixtures/symmetry-resolution-golden.json``
for the saved-design corpora (FREEFORM and LOOKUP old configs, the ICW default,
the legacy Custom profile, an offset quarter ATH config), a few enclosure and
morph designs, and three horns whose surface does not mirror (so the miss, its
distance and its reason text are frozen too); every one must come back the same.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pytest

from server.design.schema import DesignConfig
from server.solver import symmetry

TESTS = Path(__file__).parent

# Floating results are compared to a cross-platform bound, discrete ones exactly.
# The golden was captured on macOS arm64. The same code on CI's Linux and Windows
# runners returns the same answers with different last bits (libm sin/cos/pow
# and vectorised summation differ by platform): tolerance_mm was 0.0882524417475734 here,
# 0.08825244174752592 on Linux and 0.08825244174755312 on Windows for the ICW
# default (relative 5.4e-13, the widest seen), and one ulp apart (~1e-16) for
# five other designs. REL = 1e-9 is about 2000x that spread, yet 1000x tighter
# than the smallest real change worth catching (a 1e-6 tolerance or distance
# change fails here). ABS = 1e-12 mm only matters for the accepted planes'
# residuals, which are rounding noise around zero (<= 3.4e-13 mm in the golden).
# Quadrants, the per-plane verdicts, the relative tolerance constant and the
# reason text with its numbers masked stay exact.
REL = 1e-9
ABS_MM = 1e-12
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
GOLDEN = json.loads((TESTS / "fixtures" / "symmetry-resolution-golden.json").read_text())["designs"]

_ROSSE = {"formula": "R-OSSE", "R": 150, "r0": 12.7, "a": 60, "a0": 15.5}
EXTRA = {
    "extra/enclosure-dim-symmetric": {**_ROSSE, "enclosure": {"depth": 60, "dim_mm": [200, 150, 200, 150]}},
    "extra/enclosure-spacing-tb": {**_ROSSE, "enclosure": {"depth": 60, "space_t": 20, "space_b": 40}},
    "extra/ellipse-enclosure-res": {
        **_ROSSE, "cross_section": {"aspect_ratio": 1.5},
        "enclosure": {"depth": 60, "front_resolution": [20, 24, 24, 20], "space_l": 10, "space_r": 30},
    },
    # Horn surfaces that do not mirror: the miss branch and its reason text.
    "asym/rotated-guiding-curve": {
        "formula": "OSSE", "L": 120, "r0": 12.7, "a": 60, "a0": 15.5,
        "guiding_curve": {"curve_type": 1, "width": 40, "aspect_ratio": 1.5, "distance": 0.5, "rotation": 25},
    },
    "asym/rosse-coverage-cos": {"formula": "R-OSSE", "R": 150, "r0": 12.7, "a": "60+5*cos(p)", "a0": 15.5},
    "asym/osse-coverage-sin-walled": {
        "formula": "OSSE", "L": 100, "r0": 12.7, "a": "45+3*sin(p)", "a0": 10,
        "mesh": {"wall_thickness": 6},
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


def _printed_number_matches(actual: str, expected: str) -> bool:
    """A number printed in a reason (``:.6g``) matches its golden text.

    The value behind it is frozen at REL elsewhere; printing to six digits can
    still round a last-bit difference to the neighbouring last digit, so one
    unit in the last printed place (at least six significant digits; a number
    printed in full keeps its full precision) is allowed and nothing more.
    """
    if actual == expected:
        return True
    a, b = float(actual), float(expected)
    if b == 0.0:
        return a == 0.0
    mantissa = re.split(r"[eE]", expected)[0]
    digits = max(len(re.sub(r"[^0-9]", "", mantissa).lstrip("0")), 6)
    unit = 10.0 ** (math.floor(math.log10(abs(b))) - digits + 1)
    return abs(a - b) <= unit * (1 + 1e-9)


def _assert_reasons_match(name: str, actual: dict[str, list[str]], frozen: dict[str, list[str]]) -> None:
    assert list(actual) == list(frozen), (name, "reasons")
    for plane, texts in frozen.items():
        assert len(actual[plane]) == len(texts), (name, plane, actual[plane])
        for got, want in zip(actual[plane], texts, strict=True):
            assert _NUMBER.sub("#", got) == _NUMBER.sub("#", want), (name, plane, got, want)
            got_numbers, want_numbers = _NUMBER.findall(got), _NUMBER.findall(want)
            assert all(
                _printed_number_matches(g, w) for g, w in zip(got_numbers, want_numbers, strict=True)
            ), (name, plane, got, want)


@pytest.mark.slow
@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_saved_design_symmetry_is_unchanged(name: str) -> None:
    frozen = GOLDEN[name]
    symmetry._resolution_cache.clear()
    resolution = symmetry.resolve_symmetry(_design(name))
    answer = resolution.as_dict()
    assert set(answer) == set(frozen) - {"max_deviation_mm"}, name
    for key in ("quadrants", "xz", "yz", "relative_tolerance"):
        assert answer[key] == frozen[key], (name, key)
    _assert_reasons_match(name, answer["reasons"], frozen["reasons"])
    assert answer["tolerance_mm"] == pytest.approx(frozen["tolerance_mm"], rel=REL, abs=0), (name, "tolerance_mm")
    deviation = _deviation(resolution)
    for plane, expected in frozen["max_deviation_mm"].items():
        if expected is None:
            assert deviation[plane] is None, (name, plane)
        else:
            assert deviation[plane] == pytest.approx(expected, rel=REL, abs=ABS_MM), (name, plane)


def test_the_golden_covers_every_corpus() -> None:
    kinds = {name.partition("/")[0] for name in GOLDEN}
    assert {"tritonia-q", "freeform", "lookup", "icw-default", "custom-profile-legacy", "extra", "asym"} <= kinds
    assert sum(name.startswith("freeform/") for name in GOLDEN) >= 30
