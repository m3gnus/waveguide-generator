"""Numerical and HTTP-sized checks for the binding v1 export contracts."""

from __future__ import annotations

import asyncio
import json
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


def _post_asgi(path: str, payload: dict) -> tuple[int, bytes]:
    """POST JSON through the real ASGI app so FastAPI resolves query defaults."""

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(api.router)
    target, _, query = path.partition("?")
    body = json.dumps(payload, default=str).encode()

    async def call() -> tuple[int, bytes]:
        messages: list[dict] = []
        delivered = False

        async def receive() -> dict:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            messages.append(message)

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": target,
                "raw_path": target.encode(),
                "query_string": query.encode(),
                "root_path": "",
                "headers": [
                    (b"host", b"testserver"),
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
                "client": ("testclient", 123),
                "server": ("testserver", 80),
            },
            receive,
            send,
        )
        status = next(
            int(m["status"]) for m in messages if m["type"] == "http.response.start"
        )
        chunks = b"".join(
            m.get("body", b"") for m in messages if m["type"] == "http.response.body"
        )
        return status, chunks

    return asyncio.run(call())


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


_STEP_STUB = "ISO-10303-21;\nADVANCED_FACE\nB_SPLINE_SURFACE\nEND-ISO-10303-21;\n"


def test_step_route_returns_geometry_filename_content_type_and_revision(monkeypatch) -> None:
    async def fake_build(_design):
        return _STEP_STUB

    monkeypatch.setattr(api, "build_step_solid", fake_build)
    response = asyncio.run(api.export_step(_request(), body="solid"))
    assert response.body.decode() == _STEP_STUB
    assert response.media_type == "model/step"
    assert response.headers["x-design-revision"] == "57"
    assert response.headers["content-disposition"] == 'attachment; filename="demo_horn.step"'
    assert "ADVANCED_FACE" in response.body.decode()


def test_step_route_body_selects_the_solid_or_the_inner_surface(monkeypatch) -> None:
    called: list[str] = []

    async def fake_solid(_design):
        called.append("solid")
        return _STEP_STUB

    async def fake_surface(_design):
        called.append("surface")
        return _STEP_STUB

    monkeypatch.setattr(api, "build_step_solid", fake_solid)
    monkeypatch.setattr(api, "build_step", fake_surface)
    asyncio.run(api.export_step(_request(), body="solid"))
    asyncio.run(api.export_step(_request(), body="surface"))
    assert called == ["solid", "surface"]


def test_step_route_defaults_to_the_solid_over_http(monkeypatch) -> None:
    """A request with no ?body= must reach the solid builder.

    Calling ``export_step`` directly leaves ``body`` as the unresolved Query
    default, which compares equal to nothing and quietly takes the surface
    branch -- so the default has to be checked through the ASGI app.
    """

    called: list[str] = []

    async def fake_solid(_design):
        called.append("solid")
        return _STEP_STUB

    async def fake_surface(_design):
        called.append("surface")
        return _STEP_STUB

    monkeypatch.setattr(api, "build_step_solid", fake_solid)
    monkeypatch.setattr(api, "build_step", fake_surface)

    status, payload = _post_asgi("/api/export/step", _request().model_dump(by_alias=True))
    assert status == 200, payload
    assert called == ["solid"]

    status, payload = _post_asgi(
        "/api/export/step?body=surface", _request().model_dump(by_alias=True)
    )
    assert status == 200, payload
    assert called == ["solid", "surface"]


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
