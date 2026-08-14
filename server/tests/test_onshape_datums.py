"""FeatureScript and API lifecycle for managed Onshape datums."""

from __future__ import annotations

import json

import pytest

from server.cadlink.onshape.adapter import OnshapeAdapter
from server.cadlink.onshape.client import OnshapeClient, OnshapeHttpError
from server.cadlink.onshape.credentials import OnshapeCredentials
from server.cadlink.onshape.datums import (
    DATUM_FEATURE_PARAMETER,
    DATUM_FEATURE_TYPE,
    build_datum_featurescript,
)

from test_onshape_adapter import FakeTransport
from test_onshape_native import _manifest


def _adapter(transport: FakeTransport) -> OnshapeAdapter:
    return OnshapeAdapter(
        OnshapeClient(
            OnshapeCredentials(access_key="ACCESS", secret_key="SECRET"),
            transport=transport,
        )
    )


def _feature_versions() -> dict[str, object]:
    return {
        "serializationVersion": "1.2.3",
        "sourceMicroversion": "micro",
        "libraryVersion": 3044,
    }


def test_enclosure_source_is_deterministic_and_materializes_every_contract_datum() -> None:
    source = build_datum_featurescript(_manifest())

    assert source == build_datum_featurescript(_manifest())
    for name in (
        "WG_AXIS",
        "WG_THROAT_PLANE",
        "WG_MOUTH_PLANE",
        "WG_MOUTH_OUTLINE_INNER",
        "WG_MOUTH_OUTLINE_OUTER",
        "WG_BAFFLE_PLANE",
        "WG_BAFFLE_OUTLINE_FACE",
        "WG_BAFFLE_OUTLINE_ENVELOPE",
        "WG_ENC_BACK_PLANE",
        "WG_GEOM_MIDPLANE_Y",
        "WG_SOLVER_CUT_PLANE_Y",
        "WG_SOLVER_CUT_PLANE_X",
    ):
        assert f'"{name}"' in source
    assert source.count("opPlane(context") == 7
    assert source.count("opPolyline(context") == 4
    assert 'getVariable(context, "wg_demo_enc_z_front"' in source
    assert 'getVariable(context, "wg_demo_enc_depth"' in source
    assert 'getVariable(context, "wg_demo_vertical_offset"' in source
    assert '"construction" : true' in source
    assert "PropertyType.APPEARANCE" not in source
    assert "color(" not in source


def test_nonplanar_mouth_does_not_invent_a_mouth_plane() -> None:
    manifest = _manifest()
    manifest["datums"].pop("WG_MOUTH_PLANE")
    manifest["datums"]["rim_planar"] = False

    source = build_datum_featurescript(manifest)

    assert 'opPlane(context, id + "WG_MOUTH_PLANE"' not in source
    assert 'opPolyline(context, id + "WG_MOUTH_OUTLINE_INNER"' in source


def test_source_interface_materializes_a_stable_exportable_throat_sheet() -> None:
    manifest = _manifest()
    manifest["parameters"].append(
        {
            "name": "wg_demo_throat_dia",
            "value": 25.4,
            "unit": "mm",
            "role": "interface",
        }
    )
    manifest["required_features"] = ["source-interface-v1"]
    manifest["interface"] = {
        "sources": [
            {
                "id": "source-hf",
                "role": "HF",
                "required": True,
            }
        ]
    }

    source = build_datum_featurescript(manifest)

    assert 'id + "WG_THROAT_SOURCE_SKETCH"' in source
    assert 'opFillSurface(context, id + "WG_THROAT_SOURCE"' in source
    assert 'qCreatedBy(wgThroatSourceSketchId, EntityType.EDGE)' in source
    assert 'getVariable(context, "wg_demo_throat_dia"' in source
    assert '"value" : "WG_THROAT_SOURCE"' in source


def test_legacy_datum_contract_does_not_invent_a_source_sheet() -> None:
    source = build_datum_featurescript(_manifest())

    assert "WG_THROAT_SOURCE" not in source


def test_source_rejects_nonfinite_datum_evidence() -> None:
    manifest = _manifest()
    manifest["datums"]["WG_AXIS"]["origin_mm"][0] = float("inf")
    with pytest.raises(ValueError, match="finite"):
        build_datum_featurescript(manifest)


def test_create_uses_the_custom_feature_payload_and_returns_its_stable_id() -> None:
    transport = FakeTransport()
    transport.route("POST", "/featurestudios/d/DID/w/WID", 200, {"id": "DATUM-FS"})
    transport.route("POST", "/featurestudios/d/DID/w/WID/e/DATUM-FS", 204, None)
    transport.route(
        "GET",
        "/featurestudios/d/DID/w/WID/e/DATUM-FS/featurespecs",
        200,
        {"featureSpecs": [{"message": {"namespace": "datum::new"}}]},
    )
    transport.route("GET", "/partstudios/d/DID/w/WID/e/PART/features", 200, _feature_versions())
    transport.route(
        "POST",
        "/partstudios/d/DID/w/WID/e/PART/features",
        200,
        {"feature": {"message": {"featureId": "DATUM"}}},
    )

    assert _adapter(transport).materialize_datums("DID", "WID", "PART", "new source") == (
        "DATUM-FS",
        "DATUM",
    )

    create = next(
        call
        for call in transport.calls
        if call["method"] == "POST" and call["path"] == "/partstudios/d/DID/w/WID/e/PART/features"
    )
    message = json.loads(create["body"])["feature"]["message"]
    assert message["featureType"] == DATUM_FEATURE_TYPE
    assert message["name"] == "WG Datums"
    assert message["parameters"][0]["message"] == {
        "parameterId": DATUM_FEATURE_PARAMETER,
        "value": True,
    }


def test_update_reuses_the_feature_id() -> None:
    transport = FakeTransport()
    transport.route(
        "GET",
        "/featurestudios/d/DID/w/WID/e/DATUM-FS/featurespecs",
        200,
        {"featureSpecs": [{"message": {"namespace": "datum::new"}}]},
    )
    transport.route(
        "GET",
        "/featurestudios/d/DID/w/WID/e/DATUM-FS",
        200,
        {"contents": "old source"},
    )
    transport.route("POST", "/featurestudios/d/DID/w/WID/e/DATUM-FS", 204, None)
    transport.route("GET", "/partstudios/d/DID/w/WID/e/PART/features", 200, _feature_versions())
    transport.route(
        "POST",
        "/partstudios/d/DID/w/WID/e/PART/features/featureid/DATUM",
        200,
        {"feature": {"message": {"featureId": "DATUM"}}},
    )

    assert _adapter(transport).materialize_datums(
        "DID",
        "WID",
        "PART",
        "new source",
        feature_studio_id="DATUM-FS",
        feature_id="DATUM",
    ) == ("DATUM-FS", "DATUM")

    update = transport.calls[-1]
    assert update["path"].endswith("/features/featureid/DATUM")
    assert json.loads(update["body"])["feature"]["message"]["featureId"] == "DATUM"


def test_failed_update_restores_previous_source_and_feature_namespace() -> None:
    transport = FakeTransport()
    transport.route_many(
        "GET",
        "/featurestudios/d/DID/w/WID/e/DATUM-FS/featurespecs",
        [
            (200, {"featureSpecs": [{"message": {"namespace": "datum::new"}}]}),
            (200, {"featureSpecs": [{"message": {"namespace": "datum::old"}}]}),
        ],
    )
    transport.route(
        "GET",
        "/featurestudios/d/DID/w/WID/e/DATUM-FS",
        200,
        {"contents": "old source"},
    )
    transport.route("POST", "/featurestudios/d/DID/w/WID/e/DATUM-FS", 204, None)
    transport.route("GET", "/partstudios/d/DID/w/WID/e/PART/features", 200, _feature_versions())
    transport.route_many(
        "POST",
        "/partstudios/d/DID/w/WID/e/PART/features/featureid/DATUM",
        [(400, {"message": "regeneration failed"}), (200, {})],
    )

    with pytest.raises(OnshapeHttpError, match="regeneration failed"):
        _adapter(transport).materialize_datums(
            "DID",
            "WID",
            "PART",
            "new source",
            feature_studio_id="DATUM-FS",
            feature_id="DATUM",
        )

    source_updates = [
        json.loads(call["body"])["contents"]
        for call in transport.calls
        if call["method"] == "POST" and call["path"] == "/featurestudios/d/DID/w/WID/e/DATUM-FS"
    ]
    assert source_updates == ["new source", "old source"]
    feature_updates = [
        json.loads(call["body"])["feature"]["message"]
        for call in transport.calls
        if call["method"] == "POST" and call["path"].endswith("featureid/DATUM")
    ]
    assert [item["featureId"] for item in feature_updates] == ["DATUM", "DATUM"]
    assert [item["namespace"] for item in feature_updates] == ["datum::new", "datum::old"]
