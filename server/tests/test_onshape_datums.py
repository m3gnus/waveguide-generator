"""FeatureScript and API lifecycle for managed Onshape datums."""

from __future__ import annotations

import json
import os

import pytest

from server.cadlink.onshape.adapter import OnshapeAdapter, OnshapeAdapterError
from server.cadlink.onshape.client import OnshapeClient, OnshapeHttpError
from server.cadlink.onshape.credentials import OnshapeCredentials, load_credentials
from server.cadlink.onshape.datums import (
    DATUM_FEATURE_PARAMETER,
    DATUM_FEATURE_TYPE,
    build_datum_featurescript,
)
from server.mesh.gmsh_worker import _run_in_gmsh_session

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


def _feature_response(feature_id: str = "DATUM", status: str = "OK") -> dict[str, object]:
    return {
        "feature": {"message": {"featureId": feature_id}},
        "featureState": {"message": {"featureStatus": status}},
    }


def _source_manifest() -> dict[str, object]:
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
    return manifest


_LIVE_TARGET_ENV = (
    "WG_ONSHAPE_LIVE_DOCUMENT_ID",
    "WG_ONSHAPE_LIVE_WORKSPACE_ID",
    "WG_ONSHAPE_LIVE_PART_STUDIO_ID",
    "WG_ONSHAPE_LIVE_DATUM_STUDIO_ID",
    "WG_ONSHAPE_LIVE_DATUM_FEATURE_ID",
)


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
    source = build_datum_featurescript(_source_manifest())

    assert 'id + "WG_THROAT_SOURCE_SKETCH"' in source
    assert 'opFillSurface(context, id + "WG_THROAT_SOURCE"' in source
    assert "qBodyType(" in source
    assert "qCreatedBy(wgThroatSourceSketchId, EntityType.EDGE)" in source
    assert "BodyType.WIRE" in source
    assert "qNthElement" not in source
    assert 'getVariable(context, "wg_demo_throat_dia"' in source
    assert '"value" : "WG_THROAT_SOURCE"' in source


@pytest.mark.skipif(
    not all(os.environ.get(name) for name in _LIVE_TARGET_ENV),
    reason="configure the five WG_ONSHAPE_LIVE_* ids for a dedicated regression document",
)
def test_live_generated_feature_exports_exactly_one_throat_sheet(tmp_path) -> None:
    adapter = OnshapeAdapter(OnshapeClient(load_credentials()))
    document_id, workspace_id, part_studio_id, datum_studio_id, datum_feature_id = (
        os.environ[name] for name in _LIVE_TARGET_ENV
    )
    adapter.materialize_datums(
        document_id,
        workspace_id,
        part_studio_id,
        build_datum_featurescript(_source_manifest()),
        feature_studio_id=datum_studio_id,
        feature_id=datum_feature_id,
    )

    response = adapter.client.get(
        f"/parts/d/{document_id}/w/{workspace_id}/e/{part_studio_id}"
    )
    parts = response.body if isinstance(response.body, list) else []
    throat_parts = [
        part
        for part in parts
        if isinstance(part, dict) and part.get("name") == "WG_THROAT_SOURCE"
    ]
    assert len(throat_parts) == 1
    assert str(throat_parts[0].get("bodyType")).casefold() == "sheet"

    translation_id = adapter.create_step_export(
        document_id, workspace_id, part_studio_id
    )
    _result, foreign_id = adapter.await_step_export(translation_id)
    step_path = tmp_path / "throat-source.step"
    step_path.write_bytes(adapter.download_external_data(document_id, foreign_id))

    def root_body_counts(path):
        import gmsh

        gmsh.clear()
        gmsh.model.add("live-onshape-throat-source")
        gmsh.model.occ.importShapes(str(path), highestDimOnly=False)
        gmsh.model.occ.synchronize()
        solids = len(gmsh.model.getEntities(3))
        sheets = sum(
            len(gmsh.model.getAdjacencies(2, int(tag))[0]) == 0
            for _dim, tag in gmsh.model.getEntities(2)
        )
        return solids, sheets

    assert _run_in_gmsh_session(root_body_counts, step_path) == (0, 1)


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
        _feature_response(),
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
        _feature_response(),
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


def test_create_rejects_http_success_with_failed_feature_state() -> None:
    transport = FakeTransport()
    transport.route("GET", "/partstudios/d/DID/w/WID/e/PART/features", 200, _feature_versions())
    transport.route(
        "POST",
        "/partstudios/d/DID/w/WID/e/PART/features",
        200,
        _feature_response(status="ERROR"),
    )

    with pytest.raises(OnshapeAdapterError, match="featureStatus 'OK'"):
        _adapter(transport).add_datum_feature("DID", "WID", "PART", "datum::new")


def test_update_rolls_back_http_success_with_failed_feature_state() -> None:
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
        [(200, _feature_response(status="ERROR")), (200, _feature_response())],
    )

    with pytest.raises(OnshapeAdapterError, match="featureStatus 'OK'"):
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
        [(400, {"message": "regeneration failed"}), (200, _feature_response())],
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
