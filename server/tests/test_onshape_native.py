"""Native Onshape generation and its create/update API lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.cadlink.onshape.adapter import (
    OnshapeAdapter,
    OnshapeAdapterError,
    OnshapeTarget,
    send_bundle,
)
from server.cadlink.onshape.client import OnshapeClient
from server.cadlink.onshape.credentials import OnshapeCredentials
from server.cadlink.onshape.native import build_featurescript, ring_stations

from test_onshape_adapter import FakeTransport


def _grid() -> dict:
    return {
        "inner_points": [
            [[1.0, 0.0, 0.0], [2.0, 0.0, 10.0], [3.0, 0.0, 20.0]],
            [[0.0, 1.0, 0.0], [0.0, 2.0, 10.0], [0.0, 3.0, 20.0]],
            [[-1.0, 0.0, 0.0], [-2.0, 0.0, 10.0], [-3.0, 0.0, 20.0]],
        ],
        "ring_z_mm": [0.0, 10.0, 20.0],
    }


def _manifest(edge_type: int = 2) -> dict:
    values = {
        "enc_w": 100.0,
        "enc_h": 80.0,
        "enc_depth": 30.0,
        "enc_edge": 4.0,
        "enc_x0": -50.0,
        "enc_y0": -40.0,
        "enc_z_front": 20.0,
        "vertical_offset": 0.0,
    }
    return {
        "parameters": [
            {
                "name": f"wg_demo_{name}",
                "value": value,
                "unit": "mm",
                "role": "interface",
            }
            for name, value in values.items()
        ],
        "enclosure": {"edge_type": edge_type},
        "datums": {
            "rim_planar": True,
            "WG_AXIS": {"type": "axis", "origin_mm": [0, 0, 0], "direction": [0, 0, 1]},
            "WG_THROAT_PLANE": {
                "type": "plane",
                "origin_mm": [0, 0, 0],
                "normal": [0, 0, 1],
                "exact": True,
            },
            "WG_MOUTH_PLANE": {
                "type": "plane",
                "origin_mm": [0, 0, 20],
                "normal": [0, 0, 1],
                "exact": True,
            },
            "WG_MOUTH_OUTLINE_INNER": {
                "type": "polyline",
                "closed": True,
                "points_mm": [[3, 0, 20], [0, 3, 20], [-3, 0, 20]],
            },
            "WG_MOUTH_OUTLINE_OUTER": {
                "type": "polyline",
                "closed": True,
                "points_mm": [[50, -40, 20], [50, 40, 20], [-50, 40, 20], [-50, -40, 20]],
            },
            "WG_BAFFLE_PLANE": {
                "type": "plane",
                "origin_mm": [0, 0, 20],
                "normal": [0, 0, 1],
                "exact": True,
            },
            "WG_BAFFLE_OUTLINE_FACE": {
                "type": "polyline",
                "closed": True,
                "points_mm": [[46, -36, 20], [46, 36, 20], [-46, 36, 20], [-46, -36, 20]],
            },
            "WG_BAFFLE_OUTLINE_ENVELOPE": {
                "type": "polyline",
                "closed": True,
                "points_mm": [[50, -40, 20], [50, 40, 20], [-50, 40, 20], [-50, -40, 20]],
            },
            "WG_ENC_BACK_PLANE": {
                "type": "plane",
                "origin_mm": [0, 0, -10],
                "normal": [0, 0, 1],
                "exact": True,
            },
            "WG_GEOM_MIDPLANE_Y": {
                "type": "plane",
                "origin_mm": [0, 0, 0],
                "normal": [0, 1, 0],
                "exact": True,
            },
            "WG_SOLVER_CUT_PLANE_Y": {
                "type": "plane",
                "origin_mm": [0, 0, 0],
                "normal": [0, 1, 0],
                "exact": True,
            },
            "WG_SOLVER_CUT_PLANE_X": {
                "type": "plane",
                "origin_mm": [0, 0, 0],
                "normal": [1, 0, 0],
                "exact": True,
            },
        },
    }


def _bundle(tmp_path: Path, *, edge_type: int = 2) -> Path:
    root = tmp_path / "native.wglink"
    root.mkdir()
    (root / "waveguide.step").write_text(
        "ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8"
    )
    (root / "wglink.json").write_text(
        json.dumps(_manifest(edge_type)), encoding="utf-8"
    )
    (root / "point-grid.json").write_text(json.dumps(_grid()), encoding="utf-8")
    return root


def _adapter(transport: FakeTransport) -> OnshapeAdapter:
    return OnshapeAdapter(
        OnshapeClient(
            OnshapeCredentials(access_key="ACCESS", secret_key="SECRET"),
            transport=transport,
        ),
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
    )


def _native_transport() -> FakeTransport:
    transport = FakeTransport()
    transport.route(
        "GET", "/users/current", 200, {"activePlan": {"group": "Professional"}}
    )
    transport.route(
        "POST",
        "/documents",
        200,
        {"id": "DID", "defaultWorkspace": {"id": "WID"}, "public": False},
    )
    transport.route(
        "GET", "/documents/DID", 200, {"name": "Horn", "public": False, "trash": False}
    )
    transport.route("POST", "/featurestudios/d/DID/w/WID/e/FS", 204, None)
    transport.route_many(
        "POST",
        "/featurestudios/d/DID/w/WID",
        [(200, {"id": "FS"}), (200, {"id": "DATUM-FS"})],
    )
    transport.route(
        "GET",
        "/featurestudios/d/DID/w/WID/e/FS/featurespecs",
        200,
        {"featureSpecs": [{"message": {"namespace": "authoritative::namespace"}}]},
    )
    transport.route("POST", "/featurestudios/d/DID/w/WID/e/DATUM-FS", 204, None)
    transport.route(
        "GET",
        "/featurestudios/d/DID/w/WID/e/DATUM-FS/featurespecs",
        200,
        {"featureSpecs": [{"message": {"namespace": "datum::namespace"}}]},
    )
    transport.route(
        "GET",
        "/featurestudios/d/DID/w/WID/e/DATUM-FS",
        200,
        {"contents": "previous datum source"},
    )
    transport.route(
        "GET",
        "/partstudios/d/DID/w/WID/e/PART/features",
        200,
        {
            "serializationVersion": "1.2.3",
            "sourceMicroversion": "micro",
            "libraryVersion": 3044,
        },
    )
    transport.route_many(
        "POST",
        "/partstudios/d/DID/w/WID/e/PART/features",
        [
            (200, {"feature": {"message": {"featureId": "NATIVE"}}}),
            (200, {"feature": {"message": {"featureId": "DATUM"}}}),
        ],
    )
    transport.route("POST", "/partstudios/d/DID/w/WID", 200, {"id": "PART"})
    transport.route(
        "GET",
        "/documents/d/DID/w/WID/elements",
        200,
        [{"id": "PART", "name": "Demo Horn", "elementType": "PARTSTUDIO"}],
    )
    transport.route(
        "POST", "/variables/d/DID/w/WID/variablestudio", 200, {"id": "VARS"}
    )
    transport.route(
        "POST", "/variables/d/DID/w/WID/e/VARS/variablestudioscope", 204, None
    )
    transport.route("POST", "/variables/d/DID/w/WID/e/VARS/variables", 204, None)
    transport.route(
        "POST",
        "/variables/d/DID/w/WID/e/PART/variablestudioreferences",
        204,
        None,
    )
    return transport


def test_generated_source_uses_real_closed_fit_splines() -> None:
    source = build_featurescript(_grid(), _manifest(), sections=3)
    assert "skFitSpline" in source
    assert "skInterpolatedSpline" not in source
    assert "var box" not in source
    # The first point in the first station is repeated after the other points.
    assert source.count("vector(1.000000, 0.000000) * millimeter") == 2


def test_edge_type_selects_the_live_proven_treatment_branch() -> None:
    chamfer = build_featurescript(_grid(), _manifest(2))
    fillet = build_featurescript(_grid(), _manifest(1))
    assert '"edgeType" : 2' in chamfer and "opChamfer" in chamfer
    assert '"edgeType" : 1' in fillet and "opFillet" in fillet


@pytest.mark.parametrize("n_stations,wanted", [(1, 1), (2, 1), (10, 3), (10, 40)])
def test_ring_stations_always_keeps_both_ends(n_stations: int, wanted: int) -> None:
    stations = ring_stations(n_stations, wanted)
    assert stations[0] == 0
    assert stations[-1] == n_stations - 1


def test_native_send_creates_studios_pushes_source_and_adds_feature(
    tmp_path: Path,
) -> None:
    transport = _native_transport()
    result = send_bundle(
        _adapter(transport),
        _bundle(tmp_path),
        document_name="Demo Horn",
        step_filename="demo.step",
        build_mode="native",
    )
    assert result.build_mode == "native"
    assert result.target.feature_studio_element_id == "FS"
    assert result.target.part_studio_element_id == "PART"
    assert result.target.native_feature_id == "NATIVE"
    assert result.target.datum_feature_studio_element_id == "DATUM-FS"
    assert result.target.datum_feature_id == "DATUM"
    assert not [call for call in transport.calls if call["path"].startswith("/blobelements")]

    source_call = next(
        call
        for call in transport.calls
        if call["path"] == "/featurestudios/d/DID/w/WID/e/FS"
    )
    assert "skFitSpline" in json.loads(source_call["body"])["contents"]
    feature_call = next(
        call
        for call in transport.calls
        if call["path"] == "/partstudios/d/DID/w/WID/e/PART/features"
        and call["method"] == "POST"
    )
    payload = json.loads(feature_call["body"])
    assert payload["feature"]["typeName"] == "BTMFeature"
    assert payload["feature"]["message"]["namespace"] == "authoritative::namespace"


def test_second_native_send_updates_feature_without_creating_elements(
    tmp_path: Path,
) -> None:
    transport = _native_transport()
    result = send_bundle(
        _adapter(transport),
        _bundle(tmp_path),
        document_name="Demo Horn",
        step_filename="demo.step",
        build_mode="native",
        target=OnshapeTarget(
            document_id="DID",
            workspace_id="WID",
            blob_element_id="",
            part_studio_element_id="PART",
            variable_studio_element_id="VARS",
            feature_studio_element_id="FS",
            native_feature_id="NATIVE",
            datum_feature_studio_element_id="DATUM-FS",
            datum_feature_id="DATUM",
        ),
    )
    assert result.created_document is False
    paths = [(call["method"], call["path"]) for call in transport.calls]
    assert ("POST", "/partstudios/d/DID/w/WID/e/PART/features/featureid/NATIVE") in paths
    assert ("POST", "/documents") not in paths
    assert ("POST", "/featurestudios/d/DID/w/WID") not in paths
    assert ("POST", "/partstudios/d/DID/w/WID") not in paths


def test_empty_feature_specs_reports_the_hidden_compile_error() -> None:
    transport = FakeTransport()
    transport.route("POST", "/featurestudios/d/DID/w/WID/e/FS", 204, None)
    transport.route(
        "GET", "/featurestudios/d/DID/w/WID/e/FS/featurespecs", 200, {"featureSpecs": []}
    )
    with pytest.raises(OnshapeAdapterError, match="compile|featureSpecs"):
        _adapter(transport).push_feature_studio_source("DID", "WID", "FS", "source")


def test_import_mode_creates_only_the_datum_feature_studio(tmp_path: Path) -> None:
    from test_onshape_adapter import _bundle as import_bundle
    from test_onshape_adapter import _send_transport

    transport = _send_transport()
    result = send_bundle(
        _adapter(transport),
        import_bundle(tmp_path),
        document_name="Demo Horn",
        step_filename="demo.step",
    )
    assert result.build_mode == "import"
    sources = [
        json.loads(call["body"])["contents"]
        for call in transport.calls
        if call["method"] == "POST" and "/featurestudios/" in call["path"] and "/e/" in call["path"]
    ]
    assert len(sources) == 1
    assert "wgManagedDatums" in sources[0]
    assert "wgWaveguide" not in sources[0]
