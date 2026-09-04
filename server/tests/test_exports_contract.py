"""Numerical and HTTP-sized checks for the binding v1 export contracts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import struct

from hornlab_mesher.cad import CadInfo
import numpy as np
import pytest

from server.design.schema import DesignConfig
from server.exports import api
from server.exports.api import ExportRequest
from server.exports import core
from server.exports.core import (
    StepSolidResult,
    StlResult,
    _build_step_solid_sync,
    _build_step_sync,
    binary_stl,
    profile_csv,
    validate_export_segments,
)


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


def test_export_sizing_ignores_the_design_segment_controls() -> None:
    """The retired multipliers: exports size themselves, so these do not move it."""

    from server.exports.core import _stl_grid_plan, _surface_grid_plan

    sparse = _design(length_segments=10, angular_segments=51, corner_segments=2)
    dense = _design(length_segments=100, angular_segments=200, corner_segments=20)
    assert _stl_grid_plan(sparse)[0] == _stl_grid_plan(dense)[0]
    assert _surface_grid_plan(sparse) == _surface_grid_plan(dense)


def test_segment_controls_are_still_validated_even_though_unused() -> None:
    validate_export_segments(_design(length_segments=10))
    with pytest.raises(ValueError, match="supported export range"):
        validate_export_segments(_design(length_segments=1.0e308))


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


def _step_result(step_text: str = _STEP_STUB) -> StepSolidResult:
    return StepSolidResult(
        step_text=step_text,
        cad_info=CadInfo(
            path=Path("waveguide.step"),
            body="solid",
            n_faces=12,
            volume_mm3=345.0,
            bounding_box_mm=((-1.0, -2.0, 0.0), (1.0, 2.0, 120.0)),
            throat_opened=True,
        ),
    )


def test_step_route_returns_geometry_filename_content_type_and_revision(monkeypatch) -> None:
    async def fake_build(_design):
        return _step_result()

    monkeypatch.setattr(api, "build_step_solid", fake_build)
    response = asyncio.run(api.export_step(_request(), body="solid"))
    assert response.body == _STEP_STUB.encode()
    assert response.media_type == "model/step"
    assert response.headers["x-design-revision"] == "57"
    assert response.headers["content-disposition"] == 'attachment; filename="demo_horn.step"'
    assert "ADVANCED_FACE" in response.body.decode()


def test_solid_step_builder_captures_mesher_cad_info() -> None:
    result = _build_step_solid_sync(_design().model_dump(mode="json"))

    assert isinstance(result, StepSolidResult)
    assert result.step_text.startswith("ISO-10303-21;")
    assert result.cad_info.body in ("solid", "surface")
    assert result.cad_info.n_faces > 0
    assert result.cad_info.bounding_box_mm[0] != result.cad_info.bounding_box_mm[1]
    assert result.cad_info.units == "mm"


_STEP_SOLID_STUB = (
    "ISO-10303-21;\n"
    "HEADER;\n"
    "FILE_DESCRIPTION((''),'');\n"
    "FILE_NAME('waveguide.step','',(''),(''),'','','');\n"
    "FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\n"
    "ENDSEC;\n"
    "DATA;\n"
    "#1 = ADVANCED_FACE('',(#2),#3,.T.);\n"
    "#4 = B_SPLINE_SURFACE_WITH_KNOTS('',3,3,((#5)));\n"
    "ENDSEC;\n"
    "END-ISO-10303-21;\n"
)


def test_solid_step_carries_the_cad_placement_on_a_reduced_declared_domain(
    monkeypatch,
) -> None:
    """The solid STEP is a CAD boundary, so it keeps mesh.vertical_offset.

    The solve mesh is always recentred; ``write_step_from_config`` reopens a
    reduced domain but never restores the placement, so the config the server
    hands it has to carry it. An ATH-imported design declaring
    ``Mesh.Quadrants = 1`` used to export an unplaced solid while the same
    design's wglink bundle shipped placed.
    """

    import hornlab_mesher.cad as mesher_cad

    captured: list[dict] = []

    def fake_write(config, path):
        captured.append(config)
        Path(path).write_text(_STEP_SOLID_STUB, encoding="utf-8")
        return path, _step_result().cad_info

    monkeypatch.setattr(mesher_cad, "write_step_from_config", fake_write)
    design = DesignConfig.model_validate(
        {
            "formula": "OSSE",
            "L": 120,
            "a": 45,
            "scale": 2,
            "mesh": {"quadrants": 1, "vertical_offset": 30},
        }
    )
    result = _build_step_solid_sync(design.model_dump(mode="json"))

    assert result.step_text.startswith("ISO-10303-21;")
    assert len(captured) == 1
    assert captured[0]["mesh"]["quadrants"] == 1
    # The design value through the global Scale, exactly as the CAD-link bundle
    # and the Onshape datums place it.
    assert captured[0]["mesh"]["verticalOffset"] == 60.0


def _header_positions(step_text: str) -> list[int]:
    header = step_text.partition("HEADER;")[2].partition("ENDSEC;")[0]
    return [
        header.find(keyword)
        for keyword in ("FILE_DESCRIPTION", "FILE_NAME", "FILE_SCHEMA")
    ]


def test_both_step_bodies_write_an_iso_10303_21_header() -> None:
    """OpenCASCADE 7.7+ writes file_name before file_description.

    Fusion tolerates it; CATIA reads the product structure and drops every
    shape, which looks like an empty tree rather than an import error. Both
    export bodies have to correct it, and a mesher pin bump must not undo it.
    """

    design = _design().model_dump(mode="json")

    for step_text in (
        _build_step_solid_sync(design).step_text,
        _build_step_sync(design),
    ):
        positions = _header_positions(step_text)
        assert all(position >= 0 for position in positions), step_text[:400]
        assert positions == sorted(positions), step_text[:400]


def test_core_all_contains_public_builders() -> None:
    assert {
        "build_profiles",
        "build_step",
        "build_step_solid",
        "build_stl",
    } <= set(core.__all__)


def test_step_route_body_selects_the_solid_or_the_inner_surface(monkeypatch) -> None:
    called: list[str] = []

    async def fake_solid(_design):
        called.append("solid")
        return _step_result()

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
        return _step_result()

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
        return StlResult(data=b" " * 80 + struct.pack("<I", 0))

    def fake_profiles(_design, kind):
        return f"# {kind}\r\n"

    monkeypatch.setattr(api, "build_stl", fake_stl)
    monkeypatch.setattr(api, "build_profiles", fake_profiles)
    stl = asyncio.run(api.export_stl(_request()))
    assert stl.media_type == "application/sla"
    assert stl.headers["content-disposition"].endswith('"demo_horn.stl"')
    assert struct.unpack_from("<I", stl.body, 80)[0] == 0
    assert "x-export-warning" not in stl.headers
    for kind in ("profiles", "slices"):
        response = asyncio.run(api.export_profiles(_request(), kind))
        assert response.media_type == "text/csv"
        assert response.headers["x-design-revision"] == "57"
        assert response.headers["content-disposition"].endswith(f'"demo_horn_{kind}.csv"')


def test_an_oversized_stl_is_served_with_its_warning_not_refused(monkeypatch) -> None:
    """The backstop reports itself. A file the user asked for still arrives."""

    async def fake_stl(_design, _name):
        return StlResult(
            data=b" " * 80 + struct.pack("<I", 0),
            warning="coarsened to roughly 150,000 triangles",
        )

    monkeypatch.setattr(api, "build_stl", fake_stl)
    response = asyncio.run(api.export_stl(_request()))
    assert response.status_code == 200
    assert response.headers["x-export-warning"] == "coarsened to roughly 150,000 triangles"
    assert struct.unpack_from("<I", response.body, 80)[0] == 0


def test_a_warning_with_a_non_latin1_character_does_not_break_the_response(
    monkeypatch,
) -> None:
    async def fake_stl(_design, _name):
        return StlResult(data=b" " * 80 + struct.pack("<I", 0), warning="trimmed \u2014 see log")

    monkeypatch.setattr(api, "build_stl", fake_stl)
    response = asyncio.run(api.export_stl(_request()))
    assert response.status_code == 200
    assert "trimmed" in response.headers["x-export-warning"]
