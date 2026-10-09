"""Actual native STEP -> imported ingestion -> authoritative job/dispatch contract.

The native solve is intercepted. CPU BC evaluation is real; no acoustic result
from this fixture is qualification of a native accelerator or acoustic solution.
"""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import meshio
import numpy as np
import pytest
from pydantic import ValidationError

from hornlab_mesher.contour_artifact import export_contour
from hornlab_mesher.source_contour import ContourDrive, cone, dome, flat
from server.cadlink.native_source import (
    ingest_native_source,
    read_native_source,
)
from server.cadlink.store import CadLinkStore
from server.contracts.source_contour import validate_contour_request
from server.jobs.models import DriveChannel, SolveRequest
from server.solver.imported import read_verified_import_mesh
from server.solver import metal
from server.tests.test_imported_jobs import _native_result


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):
    root = tmp_path_factory.mktemp("native-source")
    models = [
        flat(24),
        dome(18, 6, surround_width_mm=4, surround_depth_mm=-1, land_width_mm=2),
        cone(
            18,
            8,
            cap_radius_mm=6,
            cap_height_mm=3,
            surround_width_mm=4,
            surround_depth_mm=1,
            land_width_mm=2,
        ),
    ]
    result = []
    for i, model in enumerate(models):
        moving = [s.id for s in model.segments if s.role == "moving"]
        weights = [[1], [1, 0.4], [1, -0.5, 0]][i]
        path = root / str(i)
        export_contour(
            model,
            ContourDrive("motor", tuple(zip(moving, weights))),
            path,
            mouth_radius_mm=48,
            length_mm=70,
            mesh_size_mm=2,
        )
        result.append(path)
    return result


def ingest(path, tmp_path):
    manifest, model, drive, _ = read_native_source(path)
    sizes = {
        "rigid_size_mm": 6,
        "transition_mm": 5,
        "source_size_mm": {s.id: 2 for s in model.segments if s.role == "moving"},
    }
    store = CadLinkStore(tmp_path / "registry.sqlite")
    record = ingest_native_source(path, store=store, data_dir=tmp_path, sizes=sizes)
    store.close()
    return record


def request(record):
    return SolveRequest.model_validate(
        {
            "geometry": {
                "type": "imported",
                "ingest_id": record["ingest_id"],
                "required_features": ["native-source-contour-v1"],
                "manifest_sha256": record["manifest_sha256"],
                "artifact_sha256": record["artifact_sha256"],
                "mesh": record["mesh_sizes"],
                "drive_channels": [record["native_source"]["channel"]],
            },
            "options": {
                "engine": "metal",
                "frequencies_hz": [100, 200],
                "polar_config": {"field_plane": False, "angle_range": [-180, 180, 37]},
            },
        }
    )


@pytest.mark.parametrize("index", [0, 1, 2], ids=["flat", "dome", "cone"])
def test_actual_ingestion_dispatch_and_independent_bc(index, artifacts, tmp_path, monkeypatch):
    from hornlab_metal_bem.bie import _build_source_face_scale

    record = ingest(artifacts[index], tmp_path)
    req = request(record)
    text = read_verified_import_mesh(record)
    mesh = meshio.read(record["mesh_store_path"])
    tri = mesh.get_cells_type("triangle")
    tags = mesh.get_cell_data("gmsh:physical", "triangle")
    xyz = mesh.points[tri]
    n = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    n /= np.linalg.norm(n, axis=1)[:, None]
    from server.tests.native_geometry_oracle import line, segment, certificate

    _, model, _, _ = read_native_source(artifacts[index])
    for i, s in enumerate(model.segments):
        if s.role == "moving":
            certificate(xyz[tags == record["source_tags"][s.id]] * 1000, segment(model, i))
    rigid = [
        line(a, b)
        for a, b in zip(
            [(24, 0), (48, 70), (49, 70), (49, -15)], [(48, 70), (49, 70), (49, -15), (0, -15)]
        )
    ]
    rigid += [segment(model, i) for i, s in enumerate(model.segments) if s.role == "rigid"]
    certificate(
        xyz[tags == 1] * 1000, lambda r, z: np.minimum.reduce([f(r, z) for f in rigid]), n=128
    )
    edges = np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])
    _, inverse, counts = np.unique(
        np.sort(edges, axis=1), axis=0, return_inverse=True, return_counts=True
    )
    assert set(counts) == {2}
    assert not np.any(np.bincount(inverse, weights=np.where(edges[:, 0] < edges[:, 1], 1, -1)))
    quality = record["mesh"]["geometric_quality"]
    assert max([quality["rigid_bound_mm"], *quality["moving_patch_bounds_mm"].values()]) <= 0.15
    assert record["native_source"]["mesh_density_sha256"]
    if index:
        assert record["mesh_sizes"] != record["requested_mesh_sizes"]
    expected_weights = record["native_source"]["channel"]["patch_weights"]
    captured = {}

    def dispatch(path, sources, config, frequencies_hz=None):
        captured.update(sources=sources, config=config)
        return [_native_result()]

    monkeypatch.setattr(
        metal, "metal_status", lambda: {"available": True, "reason": "CPU dispatch inspection"}
    )
    monkeypatch.setattr(metal, "native_solve_multi_source", dispatch)
    modes = {}
    for motion in ["normal", "axial"]:
        # Exercise a separately authored excitation record; geometry is identical.
        local_record = deepcopy(record)
        local_record["native_source"]["channel"]["motion"] = motion
        local_record["native_source"]["excitation_sha256"] = ContourDrive(
            "motor", tuple(expected_weights.items()), motion
        ).excitation_sha256
        local_req = request(local_record)
        response = metal.solve_imported_metal_from_msh_text(text, local_req, local_record)
        spec = captured["sources"][0]
        assert spec == {
            record["source_tags"][key]: complex(weight) for key, weight in expected_weights.items()
        }
        assert 1 not in spec
        config = replace(captured["config"], velocity_sources=spec)
        grid = SimpleNamespace(vertices=mesh.points.T, elements=tri.T)
        scale = _build_source_face_scale(grid, tags, config, np.array([0, 0, 1]), np.zeros(3))
        actual = np.zeros(len(tri), complex)
        expected = np.zeros(len(tri), complex)
        for key, weight in expected_weights.items():
            tag = record["source_tags"][key]
            mask = tags == tag
            actual[mask] = spec[tag] * (1 if scale is None else scale[mask])
            expected[mask] = weight * (1 if motion == "normal" else n[mask, 2])
        assert np.max(abs(actual - expected)) <= 1e-12
        modes[motion] = actual
        assert response["channels"]["motor"]["metadata"]["patch_weights"] == expected_weights
        assert response["channels"]["motor"]["metadata"]["physical_source_id"] == "diaphragm"
    if index == 0:
        assert np.max(abs(modes["normal"] - modes["axial"])) <= 1e-12
    else:
        assert np.max(abs(modes["normal"] - modes["axial"])) > 0.01
    validate_contour_request(req.geometry, record)
    req.geometry.drive_channels[0].patch_weights[next(iter(expected_weights))] = 99
    with pytest.raises(ValueError, match="contradicts"):
        validate_contour_request(req.geometry, record)


@pytest.mark.parametrize(
    "mutation",
    [
        "feature",
        "step",
        "preview",
        "overlap",
        "role",
        "identity",
        "negative-area",
        "nan-area",
        "wrong-area",
        "rebound",
    ],
)
def test_bad_native_mappings_preserve_registry(mutation, artifacts, tmp_path):
    import shutil

    path = tmp_path / "bad"
    shutil.copytree(artifacts[1], path)
    manifest = json.loads((path / "source.json").read_text())
    if mutation == "feature":
        manifest["required_features"] = ["future"]
    elif mutation in {"step", "preview"}:
        name = "geometry.step" if mutation == "step" else "preview.msh"
        with (path / name).open("ab") as file:
            file.write(b"tamper")
    elif mutation == "overlap":
        manifest["patches"][1]["advanced_face_indices"] = manifest["patches"][0][
            "advanced_face_indices"
        ]
    elif mutation == "role":
        manifest["patches"][0]["role"] = "rigid"
    elif mutation in {"negative-area", "nan-area", "wrong-area"}:
        manifest["patches"][0]["area_mm2"] = {
            "negative-area": -1,
            "nan-area": float("nan"),
            "wrong-area": 1,
        }[mutation]
    elif mutation == "rebound":
        a, b = manifest["patches"][:2]
        a["advanced_face_indices"], b["advanced_face_indices"] = (
            b["advanced_face_indices"],
            a["advanced_face_indices"],
        )
    else:
        manifest["recipe"]["contour"]["points"][0]["z_mm"] += 1
    (path / "source.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        ingest(path, tmp_path)
    if (tmp_path / "registry.sqlite").exists():
        import sqlite3

        with sqlite3.connect(tmp_path / "registry.sqlite") as db:
            assert db.execute("SELECT count(*) FROM ingests").fetchone()[0] == 0
    assert not list((tmp_path / "imports/native-source").glob("*"))


def test_request_required_features_and_weights():
    for weights in [{"a": float("nan")}, {"a": float("inf")}, {"other": 1}]:
        with pytest.raises(ValidationError):
            DriveChannel(id="motor", source_ids=["a"], patch_weights=weights)
    assert DriveChannel(id="motor", source_ids=["a"], patch_weights={"a": 0}).patch_weights == {
        "a": 0
    }
