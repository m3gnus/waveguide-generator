"""Numerical and HTTP-sized checks for the binding v1 export contracts."""

from __future__ import annotations

import asyncio
import struct

import numpy as np
import pytest

from server.design.schema import DesignConfig
from server.exports import api
from server.exports.api import ExportRequest
from server.exports.core import binary_stl, profile_csv, smooth_segments


def _design(**mesh: int) -> DesignConfig:
    return DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "L": 120,
            "a": 45,
            "mesh": mesh,
        }
    )


def _request() -> ExportRequest:
    return ExportRequest.model_validate(
        {
            "design": _design().model_dump(mode="json"),
            "designRevision": 57,
            "baseName": "demo horn.cfg",
        }
    )


def test_smooth_density_clamps_and_snaps_angular_up_to_multiple_of_four() -> None:
    assert smooth_segments(_design(length_segments=10, angular_segments=51, corner_segments=2)) == (60, 104, 4)
    assert smooth_segments(_design(length_segments=100, angular_segments=200, corner_segments=20)) == (160, 240, 12)


def test_binary_stl_filters_to_horn_and_applies_mm_axis_transform() -> None:
    vertices = [
        0.001, 0.002, 0.003,
        0.004, 0.002, 0.003,
        0.001, 0.006, 0.003,
        0.001, 0.002, 0.008,
    ]
    data = binary_stl(vertices, [0, 1, 2, 0, 2, 3], [1, 2], "contract")
    assert len(data) == 84 + 50
    assert data[:8] == b"contract"
    assert struct.unpack_from("<I", data, 80)[0] == 1
    record = struct.unpack_from("<12fH", data, 84)
    assert record[-1] == 0
    assert record[:3] == pytest.approx((0.0, 0.0, 1.0))
    # Solver coordinates are (x, vertical, axial); this is v1's named
    # (x, axial, vertical) -> (x, -vertical, axial) compatibility transform.
    # That reflection reverses handedness, so the exporter must swap the last
    # two vertices to retain the solver mesh's +Z normal.
    assert record[3:6] == pytest.approx((1.0, -2.0, 3.0))
    assert record[6:9] == pytest.approx((1.0, -6.0, 3.0))
    assert record[9:12] == pytest.approx((4.0, -2.0, 3.0))


def test_profile_csv_axes_units_rows_and_closed_slices() -> None:
    points = np.asarray(
        [
            [[10, 20, 30], [40, 50, 60]],
            [[70, 80, 90], [100, 110, 120]],
        ],
        dtype=float,
    )
    profiles = profile_csv(points, "profiles")
    assert profiles.startswith("# x_cm;y_cm;z_cm\r\n1.000000;2.000000;3.000000\r\n")
    assert "\r\n\r\n" in profiles
    slices = profile_csv(points, "slices")
    rows = slices.split("\r\n")
    assert rows[1] == "1.000000;2.000000;3.000000"
    assert rows[3] == rows[1]


def test_step_route_returns_geometry_filename_content_type_and_revision(monkeypatch) -> None:
    content = "ISO-10303-21;\nADVANCED_FACE\nB_SPLINE_SURFACE\nEND-ISO-10303-21;\n"

    async def fake_build(_design):
        return content

    monkeypatch.setattr(api, "build_step", fake_build)
    response = asyncio.run(api.export_step(_request()))
    assert response.body.decode() == content
    assert response.media_type == "model/step"
    assert response.headers["x-design-revision"] == "57"
    assert response.headers["content-disposition"] == 'attachment; filename="demo_horn.step"'
    assert "ADVANCED_FACE" in response.body.decode()


def test_stl_and_each_profile_route_echo_revision_and_contract_filenames(monkeypatch) -> None:
    async def fake_stl(_design, _name):
        return b" " * 80 + struct.pack("<I", 0)

    def fake_profiles(_design, kind):
        return f"# {kind}\r\n"

    monkeypatch.setattr(api, "build_stl", fake_stl)
    monkeypatch.setattr(api, "build_profiles", fake_profiles)
    stl = asyncio.run(api.export_stl(_request()))
    assert stl.media_type == "application/sla"
    assert stl.headers["content-disposition"].endswith('"demo_horn.stl"')
    assert struct.unpack_from("<I", stl.body, 80)[0] == 0
    for kind in ("profiles", "slices"):
        response = asyncio.run(api.export_profiles(_request(), kind))
        assert response.media_type == "text/csv"
        assert response.headers["x-design-revision"] == "57"
        assert response.headers["content-disposition"].endswith(f'"demo_horn_{kind}.csv"')
