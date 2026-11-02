"""A driver cut through in CAD still loads the solve as the whole driver."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from server.cadlink.interpretation.domain_decision import decision_problem
from server.mesh.surface_topology import completion_factor
from server.solver.imported_channels import record_source_area_m2
from test_cadlink_domain_automatic import (
    HORN_THROAT,
    _bundle,
    _cut_open,
    _faces_near,
    _horn,
    _ingest,
    _legacy,
    _mirrored_pair,
    _open_half,
    _open_quarter,
)


def _disc(sector: tuple[float, float], *, centre_x: float = 0.0, segments: int = 24) -> tuple[np.ndarray, np.ndarray]:
    angles = np.linspace(*sector, segments + 1)
    rim = np.column_stack([centre_x + 10.0 * np.cos(angles), 10.0 * np.sin(angles), np.zeros(segments + 1)])
    points = np.vstack([[centre_x, 0.0, 0.0], rim])
    triangles = np.array([[0, index, index + 1] for index in range(1, segments + 1)])
    if sector[1] - sector[0] == 2 * np.pi:  # The full circle's seam is one edge, not a rim.
        triangles[-1, 2] = 1
    return points, triangles


HALF = (-np.pi / 2, np.pi / 2)
QUARTER = (0.0, np.pi / 2)


@pytest.mark.parametrize(
    ("surface", "axes", "expected"),
    [
        (_disc((0.0, 2 * np.pi)), (0, 1), 1.0),
        (_disc(HALF), (0,), 2.0),
        (_disc(HALF), (1,), 1.0),
        (_disc(HALF), (), 1.0),
        (_disc(QUARTER), (0, 1), 4.0),
        (_disc((0.0, 2 * np.pi), centre_x=30.0), (0, 1), 1.0),
    ],
    ids=["whole", "half", "other-plane", "no-plane", "quarter", "clear-of-plane"],
)
def test_completion_factor_doubles_once_per_plane_along_the_rim(surface, axes, expected) -> None:
    points, triangles = surface
    assert completion_factor(points, triangles, axes, 1e-6) == pytest.approx(expected)


def test_completion_factor_weighs_a_whole_driver_and_a_cut_one_by_area() -> None:
    half_points, half = _disc(HALF)
    whole_points, whole = _disc((0.0, 2 * np.pi), centre_x=30.0)
    points = np.vstack([half_points, whole_points])
    triangles = np.vstack([half, whole + len(half_points)])
    # Half a driver and a whole one: 0.5 + 1 of area stands for 1 + 1 drivers.
    assert completion_factor(points, triangles, (0,), 1e-6) == pytest.approx(2.0 / 1.5, rel=1e-2)


@pytest.mark.parametrize(
    ("geometry", "domain"),
    [(_open_half, "automatic"), (_open_half, ("x0",)), (_open_quarter, "automatic"), (_open_quarter, ("x0", "y0"))],
    ids=["half", "declared-half", "quarter", "declared-quarter"],
)
def test_a_cut_return_drives_the_area_of_the_whole_return(tmp_path: Path, geometry, domain) -> None:
    whole = _ingest(_bundle(tmp_path / "whole", "whole", _horn, HORN_THROAT), tmp_path / "whole" / "data")
    cut = _ingest(_bundle(tmp_path / "cut", "cut", geometry, HORN_THROAT, domain=domain), tmp_path / "cut" / "data")

    assert cut["domain_decision"]["cad_cuts"]
    assert decision_problem(cut) is None
    (whole_id,), (cut_id,) = whole["source_tags"], cut["source_tags"]
    assert record_source_area_m2(cut, cut_id) == pytest.approx(record_source_area_m2(whole, whole_id), rel=1e-3)


def test_a_half_holding_a_whole_driver_keeps_that_drivers_area(tmp_path: Path) -> None:
    def half_of_the_pair(path: Path) -> None:
        _mirrored_pair(path)
        _cut_open(path, {"x": (0.0, math.inf)})

    record = _ingest(_bundle(tmp_path, "half-pair", half_of_the_pair, _faces_near((25.0, 0.0, 0.0))), tmp_path / "data")

    assert record["domain_decision"]["cad_cuts"]
    (source_id,) = record["source_tags"]
    observed = next(item for item in record["sources"] if item["id"] == source_id)["observed"]["total_area_mm2"]
    assert observed == pytest.approx(math.pi * 10.0**2, rel=1e-3)
    assert record_source_area_m2(record, source_id) == pytest.approx(observed * 1e-6)


def test_a_wg_cut_never_completes_the_area_a_return_already_reports_whole(tmp_path: Path) -> None:
    record = _ingest(_bundle(tmp_path, "whole", _horn, HORN_THROAT), tmp_path / "data")

    assert record["domain_decision"]["wg_cut_planes"] and not record["domain_decision"]["cad_cuts"]
    (source_id,) = record["source_tags"]
    assert record["domain_decision"]["sources"]["by_id"][source_id]["completion_factor"] == 1.0


def test_a_record_saved_before_completion_reads_its_recorded_area(tmp_path: Path) -> None:
    record = _ingest(_bundle(tmp_path, "half", _open_half, HORN_THROAT), tmp_path / "data")

    (source_id,) = record["source_tags"]
    observed = next(item for item in record["sources"] if item["id"] == source_id)["observed"]["total_area_mm2"]
    assert record_source_area_m2(_legacy(record), source_id) == pytest.approx(observed * 1e-6)
