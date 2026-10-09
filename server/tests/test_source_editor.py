"""Authoring uses the canonical recipe, persistent presets and real native export."""

from collections import Counter
from copy import deepcopy
from io import BytesIO
import json
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import meshio
import numpy as np
import pytest

from server.cadlink.native_source import read_native_source
from server.source_editor import PresetStore, create_router
from server.tests.native_geometry_oracle import certificate, segment


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    app.include_router(create_router(tmp_path))
    return TestClient(app)


def shape(client, kind="cone"):
    dimensions = {
        "flat": {"radius_mm": 8},
        "dome": {
            "radius_mm": 8,
            "height_mm": 3,
            "surround_width_mm": 2,
            "surround_depth_mm": 0.5,
            "land_width_mm": 1,
        },
        "cone": {
            "radius_mm": 10,
            "depth_mm": 4,
            "cap_radius_mm": 3,
            "cap_height_mm": 2,
            "surround_width_mm": 2,
            "surround_depth_mm": 0.6,
            "land_width_mm": 1,
        },
    }[kind]
    result = client.post("/api/source-editor/expand", json={"kind": kind, "dimensions": dimensions})
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.parametrize("kind", ["flat", "dome", "cone"])
def test_expansion_and_preview_share_canonical_model(client, kind):
    from hornlab_mesher.source_contour import SourceContour

    result = shape(client, kind)
    model = SourceContour.from_dict(result["document"]["contour"])
    assert result["geometry_sha256"] == model.geometry_sha256
    for i, patch in enumerate(model.segments):
        assert np.allclose(
            result["meridian"][patch.id], [model.evaluate(i, j / 64) for j in range(65)], atol=1e-12
        )
    assert client.post("/api/source-editor/validate", json=result["document"]).json() == result


def test_weights_roles_and_separate_identities(client):
    result = shape(client)
    document = result["document"]
    document["drive"]["weights"] = {"dust-cap": 1, "cone": -0.5, "surround": 0}
    document["drive"]["motion"] = "axial"
    edited = client.post("/api/source-editor/validate", json=document).json()
    assert edited["geometry_sha256"] == result["geometry_sha256"]
    assert edited["excitation_sha256"] != result["excitation_sha256"]
    assert edited["document"]["drive"] == document["drive"]
    assert edited["document"]["contour"]["segments"][2]["role"] == "moving"
    document["drive"]["weights"]["land"] = 0
    assert client.post("/api/source-editor/validate", json=document).status_code == 422


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_exact_split_preserves_curves_ids_roles_and_drive(client, index):
    from hornlab_mesher.source_contour import SourceContour

    doc = shape(client)["document"]
    doc["drive"]["weights"]["surround"] = 0
    before = SourceContour.from_dict(doc["contour"])
    result = client.post("/api/source-editor/split", json={"document": doc, "segment_index": index})
    assert result.status_code == 200, result.text
    after_doc = result.json()["document"]
    after = SourceContour.from_dict(after_doc["contour"])
    assert after.physical_source_id == before.physical_source_id
    assert after_doc["drive"]["channel_id"] == doc["drive"]["channel_id"]
    assert after.points[index + 1].r_mm == before.evaluate(index, 0.5)[0]
    assert after.points[index + 1].z_mm == before.evaluate(index, 0.5)[1]
    assert after.segments[index].id == before.segments[index].id
    assert after.segments[index + 1].role == before.segments[index].role
    for j in range(11):
        assert np.allclose(
            after.evaluate(index, j / 10), before.evaluate(index, j / 20), atol=1e-10
        )
        assert np.allclose(
            after.evaluate(index + 1, j / 10), before.evaluate(index, 0.5 + j / 20), atol=1e-10
        )
    if before.segments[index].role == "moving":
        assert (
            after_doc["drive"]["weights"][after.segments[index + 1].id]
            == doc["drive"]["weights"][before.segments[index].id]
        )
    else:
        assert after.segments[index + 1].id not in after_doc["drive"]["weights"]


@pytest.mark.parametrize(
    "mutation", ["order", "rim", "circle", "role", "duplicate", "version", "unknown", "boolean"]
)
def test_invalid_drafts_never_save_or_export(client, mutation, tmp_path):
    doc = shape(client)["document"]
    c = doc["contour"]
    if mutation == "order":
        c["points"][1]["r_mm"] = 12
    if mutation == "rim":
        c["points"][-1]["z_mm"] = 1
    if mutation == "circle":
        c["segments"][0]["center_mm"] = [0, 500]
    if mutation == "role":
        c["segments"][0]["role"] = "rigid"
    if mutation == "duplicate":
        c["points"][1]["id"] = "pole"
    if mutation == "version":
        c["version"] = True
    if mutation == "unknown":
        c["fitted_curve"] = True
    if mutation == "boolean":
        c["points"][1]["r_mm"] = True
    assert client.post("/api/source-editor/validate", json=doc).status_code == 422
    assert (
        client.post(
            "/api/source-editor/presets", json={"name": "invalid", "document": doc}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/source-editor/export",
            json={"document": doc, "attachment": {"kind": "baffle", "dimensions": {}}},
        ).status_code
        == 422
    )
    assert not (tmp_path / "source_presets.json").exists()
    assert not (tmp_path / "tmp").exists()


def test_preset_restart_roundtrip_revision_and_delete(client, tmp_path):
    doc = shape(client)["document"]
    doc["drive"]["weights"]["cone"] = -0.5
    doc["drive"]["weights"]["surround"] = 0
    saved = client.post(
        "/api/source-editor/presets", json={"name": "Custom source", "document": doc}
    ).json()
    assert PresetStore(tmp_path / "source_presets.json").list() == [saved]
    assert saved["document"] == doc
    url = "/api/source-editor/presets/" + saved["id"]
    old = deepcopy(saved)
    new = client.put(
        url, json={"name": "Updated", "document": doc, "expected_revision": saved["revision"]}
    ).json()
    assert new["revision"] != old["revision"]
    assert (
        client.put(
            url, json={"name": "stale", "document": doc, "expected_revision": old["revision"]}
        ).status_code
        == 409
    )
    assert client.delete(url, params={"revision": old["revision"]}).status_code == 409
    assert client.get("/api/source-editor/presets").json() == [new]
    assert client.delete(url, params={"revision": new["revision"]}).status_code == 200
    assert PresetStore(tmp_path / "source_presets.json").list() == []


def test_app_mount_and_restart_gate(tmp_path):
    from server.app import create_app

    app = create_app(data_dir=tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1:3100")
    assert client.get("/api/source-editor/presets").json() == []
    doc = shape(client, "flat")["document"]
    app.state.update_restart.approve("test-update")
    response = client.post(
        "/api/source-editor/export",
        json={"document": doc, "attachment": {"kind": "horn", "dimensions": {}}},
    )
    assert response.status_code == 409
    assert not (tmp_path / "tmp").exists()


def test_unreadable_library_and_failed_save_preserve_previous_bytes(client, tmp_path, monkeypatch):
    import server.source_editor as editor

    path = tmp_path / "source_presets.json"
    path.write_text('{"version":1,"presets":{"bad":null}}')
    original = path.read_bytes()
    assert client.get("/api/source-editor/presets").status_code == 500
    assert (
        client.post(
            "/api/source-editor/presets",
            json={"name": "recover", "document": shape(client)["document"]},
        ).status_code
        == 500
    )
    assert path.read_bytes() == original
    path.unlink()
    saved = client.post(
        "/api/source-editor/presets",
        json={"name": "original", "document": shape(client)["document"]},
    ).json()
    original = path.read_bytes()

    def fail(*args):
        raise OSError("simulated unavailable storage")

    monkeypatch.setattr(editor.os, "replace", fail)
    assert (
        client.put(
            "/api/source-editor/presets/" + saved["id"],
            json={
                "name": "replacement",
                "document": saved["document"],
                "expected_revision": saved["revision"],
            },
        ).status_code
        == 503
    )
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".source-presets-*"))


@pytest.mark.parametrize(
    "kind,attachment",
    [
        (
            "flat",
            {
                "kind": "baffle",
                "dimensions": {
                    "width_mm": 30,
                    "height_mm": 40,
                    "depth_mm": 12,
                    "aperture_radius_mm": 8,
                    "center_mm": [0, 0, 0],
                },
            },
        ),
        (
            "cone",
            {
                "kind": "baffle",
                "dimensions": {
                    "width_mm": 44,
                    "height_mm": 54,
                    "depth_mm": 12,
                    "aperture_radius_mm": 14,
                    "center_mm": [3, -4, 7],
                },
            },
        ),
        (
            "flat",
            {
                "kind": "horn",
                "dimensions": {"mouth_radius_mm": 16, "length_mm": 20, "backing_depth_mm": 12},
            },
        ),
    ],
)
def test_real_bundle_reopens_exact_recipe_and_closed_mesh(client, kind, attachment, tmp_path):
    doc = shape(client, kind)["document"]
    if kind == "cone":
        doc["drive"]["weights"].update({"cone": -0.5, "surround": 0})
        doc["drive"]["motion"] = "axial"
    response = client.post(
        "/api/source-editor/export",
        json={"document": doc, "attachment": attachment, "mesh_size_mm": 2},
    )
    assert response.status_code == 200, response.text if response.status_code != 200 else ""
    destination = tmp_path / "reopened"
    with ZipFile(BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"source.json", "geometry.step", "preview.msh"}
        archive.extractall(destination)
    raw, contour, drive, _ = read_native_source(destination)
    assert json.loads(json.dumps(contour.to_dict())) == doc["contour"]
    assert dict(drive.weights) == doc["drive"]["weights"]
    assert drive.motion == doc["drive"]["motion"]
    mesh = meshio.read(destination / "preview.msh")
    triangles = mesh.cells_dict["triangle"]
    tags = mesh.cell_data_dict["gmsh:physical"]["triangle"]
    directed = Counter((int(a), int(b)) for row in triangles for a, b in zip(row, np.roll(row, -1)))
    assert all(count == 1 and directed[b, a] == 1 for (a, b), count in directed.items())
    xyz = mesh.points[triangles]
    normals = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    assert np.all(np.linalg.norm(normals, axis=1) > 1e-12)
    assert np.sum(np.einsum("ij,ij->i", xyz[:, 0], normals)) > 0
    origin = np.array(attachment["dimensions"].get("center_mm", [0, 0, 0]))
    for i, patch in enumerate(raw["patches"]):
        faces = xyz[tags == patch["mesh_tag"]] - origin
        assert len(faces)
        if patch["role"] == "moving":
            assert np.all(normals[tags == patch["mesh_tag"], 2] > 0)
        certificate(faces, segment(contour, i), n=128)
    assert not list((tmp_path / "tmp").iterdir())
