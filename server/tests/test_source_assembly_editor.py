"""Assembly authoring's real export, ingestion envelope and refusal boundaries."""

import builtins
from io import BytesIO
import json
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from server.app import create_app
from server.cadlink.intake.native_source import read_native_source
from server.cadlink.store import CadLinkStore
from server.source_editor import canonical_document, create_router
from server.tests.test_shared_source_assembly import case, request


def document():
    model, drives = case(False)
    recipe = model.to_dict()
    return {
        "horn": canonical_document(model.horn, drives[0]),
        "woofer": canonical_document(model.woofer, drives[1]),
        "dimensions": {
            k: v for k, v in recipe.items() if k not in ("horn", "woofer", "version", "frame")
        },
        "phase_plugs": [
            {
                "id": "vane/A",
                "z0_mm": 2,
                "z1_mm": 6,
                "inner0_mm": 2,
                "outer0_mm": 2.5,
                "inner1_mm": 2.4,
                "outer1_mm": 2.9,
            }
        ],
        "mesh_size_mm": 4,
        "passage_refinement": 1,
    }


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    store = CadLinkStore(tmp_path / "registry.sqlite")
    store.initialize()
    app.state.cadlink_store = store
    app.include_router(create_router(tmp_path))
    try:
        yield TestClient(app)
    finally:
        store.close()


@pytest.mark.parametrize(
    "mutation", ["boolean", "fraction", "refinement", "contact", "overlap", "unknown"]
)
def test_invalid_passage_draft_never_exports(client, tmp_path, mutation):
    body = document()
    if mutation == "boolean":
        body["phase_plugs"][0]["inner0_mm"] = True
    elif mutation == "fraction":
        body["passage_refinement"] = 1.0
    elif mutation == "refinement":
        body["passage_refinement"] = True
    elif mutation == "contact":
        body["phase_plugs"][0]["z0_mm"] = 0
    elif mutation == "overlap":
        body["phase_plugs"].append({**body["phase_plugs"][0], "id": "other"})
    else:
        body["phase_plugs"][0]["driven"] = True
    for action in ("validate", "export", "ingest"):
        response = client.post("/api/source-editor/assembly/" + action, json=body)
        assert response.status_code == 422, response.text
    assert not (tmp_path / "tmp").exists()


@pytest.mark.parametrize("with_vane", [False, True])
def test_actual_export_and_ingestion_envelope(client, tmp_path, with_vane):
    body = document()
    if not with_vane:
        body["phase_plugs"] = []
    validation = client.post("/api/source-editor/assembly/validate", json=body)
    assert validation.status_code == 200, validation.text
    data = validation.json()
    if with_vane:
        assert data["passage_contract"]["open_passage_count"] == 2
        assert "plug/vane%2FA/inner" in data["horn_section_mm"]
    else:
        assert data["passage_contract"] is None
    exported = client.post("/api/source-editor/assembly/export", json=body)
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "application/zip"
    with ZipFile(BytesIO(exported.content)) as bundle:
        assert set(bundle.namelist()) == {"source.json", "geometry.step", "preview.msh"}
        bundle.extractall(tmp_path / "readback")
    manifest, model, _, _ = read_native_source(tmp_path / "readback")
    assert model.geometry_sha256 == data["geometry_sha256"]
    assert manifest.get("passage_contract") == data["passage_contract"]
    ingested = client.post("/api/source-editor/assembly/ingest", json=body)
    assert ingested.status_code == 200, ingested.text
    published = ingested.json()
    record = published["ingestion"]
    assert record["return_id"] == "native:" + record["ingest_id"]
    assert record["scope"]["degraded_skip_count"] == 0
    assert record["freshness"]["verdict"] == "unlinked"
    assert record["mesh"]["stats"]["triangle_count"] <= 22000
    assert published["geometry"]["drive_channels"] == record["native_source"]["channels"]
    assert record["native_source"]["recipe"] == json.loads(json.dumps(model.to_dict()))
    if with_vane:
        assert published["geometric_quality"]["passages"]["certified_clearance_mm"] > 0
    assert "mesh_store_path" not in record
    assert str(tmp_path) not in json.dumps(record)
    assert request(record).geometry.required_features[-1] == (
        "native-phase-plug-passages-v1" if with_vane else "native-shared-horn-woofer-v1"
    )
    assert not list((tmp_path / "tmp").iterdir())


@pytest.mark.parametrize("action", ["validate", "export", "ingest"])
def test_old_mesher_has_actionable_refusal(client, monkeypatch, action):
    original = builtins.__import__

    def old_mesher(name, *args, **kwargs):
        if name == "hornlab_mesher.phase_plug":
            raise ImportError("older installed mesher")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", old_mesher)
    result = client.post("/api/source-editor/assembly/" + action, json=document())
    assert result.status_code == 503
    assert "Update the mesher" in result.json()["detail"]


@pytest.mark.parametrize("action", ["export", "ingest"])
def test_restart_gate_precedes_assembly_work(tmp_path, action):
    app = create_app(data_dir=tmp_path)
    app.state.update_restart.approve("assembly-update")
    result = TestClient(app, base_url="http://127.0.0.1:3100").post(
        "/api/source-editor/assembly/" + action, json=document()
    )
    assert result.status_code == 409
    assert not (tmp_path / "tmp").exists()


def test_horn_wall_is_independent_of_invalid_source_and_matches_profile(client):
    from hornlab_mesher.general_horn import GeneralHornWall

    copied = client.post("/api/source-editor/assembly/horn-profile", json={
        "formula": "OSSE", "r0": 4, "a": 32, "a0": 6, "L": 24,
        "mesh": {"length_segments": 32, "angular_segments": 64},
    })
    assert copied.status_code == 200, copied.text
    profile = copied.json()
    # The pre-existing horn-profile wire contract must not gain the dense samples.
    assert set(profile) == {"horn_config", "throat_radius_mm", "horn_length_mm",
                            "mouth_radius_mm", "wall_max_radius_mm"}
    response = client.post("/api/source-editor/assembly/horn-wall", json={"horn_config": profile["horn_config"]})
    assert response.status_code == 200, response.text
    wall = response.json()
    assert set(wall) == {"wall_mm", "throat_radius_mm", "horn_length_mm", "mouth_radius_mm"}
    assert len(wall["wall_mm"]) == 257
    assert wall["wall_mm"][0] == pytest.approx([4, 0], abs=1e-9)
    expected = GeneralHornWall.from_config(profile["horn_config"])
    assert wall["wall_mm"] == expected.evaluate([j / 256 for j in range(257)]).tolist()
    assert wall["horn_length_mm"] == profile["horn_length_mm"]
    assert wall["mouth_radius_mm"] == profile["mouth_radius_mm"]
    invalid = document()
    invalid["horn"]["contour"]["points"][0]["r_mm"] = -1
    assert client.post("/api/source-editor/assembly/validate", json=invalid).status_code == 422
    assert client.post("/api/source-editor/assembly/horn-wall", json={"horn_config": profile["horn_config"]}).json() == wall


def test_horn_wall_maps_invalid_configuration_to_422(client):
    response = client.post("/api/source-editor/assembly/horn-wall", json={"horn_config": {}})
    assert response.status_code == 422, response.text


def test_horn_wall_maps_unavailable_mesher_to_503(client, monkeypatch):
    original = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "hornlab_mesher.general_horn":
            raise ImportError("old mesher")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    response = client.post("/api/source-editor/assembly/horn-wall", json={"horn_config": {}})
    assert response.status_code == 503, response.text


def test_horn_profile_uses_shared_errors_without_sampling(client, monkeypatch):
    from hornlab_mesher.general_horn import GeneralHornWall

    def unexpected_sample(*args, **kwargs):
        raise AssertionError("horn-profile must not sample the wall")

    monkeypatch.setattr(GeneralHornWall, "evaluate", unexpected_sample)
    design = {"formula": "OSSE", "r0": 4, "a": 32, "a0": 6, "L": 24,
              "mesh": {"length_segments": 32, "angular_segments": 64}}
    response = client.post("/api/source-editor/assembly/horn-profile", json=design)
    assert response.status_code == 200, response.text
    assert "wall_mm" not in response.json()

    def invalid(*args, **kwargs):
        raise ValueError("bad wall")

    monkeypatch.setattr(GeneralHornWall, "from_config", invalid)
    response = client.post("/api/source-editor/assembly/horn-profile", json=design)
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "bad wall"

    original = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "hornlab_mesher.general_horn":
            raise ImportError("old mesher")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    response = client.post("/api/source-editor/assembly/horn-profile", json=design)
    assert response.status_code == 503, response.text
