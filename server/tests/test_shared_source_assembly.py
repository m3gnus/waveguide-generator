"""Real shared native ingestion, production BC dispatch and complex combination.

The native accelerator is intercepted. The coupled linear operator below tests
dispatch/recombination algebra only; it is not an acoustic qualification.
"""

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import shutil
from types import SimpleNamespace

import meshio
import numpy as np
import pytest

from hornlab_mesher.source_contour import ContourDrive, cone, dome, flat
from hornlab_mesher.source_assembly import SourceAssembly
from hornlab_mesher.assembly_artifact import export_assembly
from server.cadlink.native_source import ingest_native_source, read_native_source
from server.cadlink.store import CadLinkStore
from server.contracts.source_contour import validate_contour_request
from server.jobs.models import SolveRequest, ChannelFilterSpec
from server.jobs.runtime import JobRuntime, ImportedSolveRefusal
from server.jobs.store import JobStore
from server.solver import metal
from server.solver.combine import combine_drive_channels
from server.solver.imported import imported_anchor_frame, read_verified_import_mesh
from server.tests.native_geometry_oracle import segment, line
from server.tests.test_front_baffle_woofer import certificate_xyz
from server.tests.test_imported_jobs import _native_result, _PausedRegistry


def case(curved):
    horn = replace(dome(4, 1) if curved else flat(4), physical_source_id="horn", rim_id="horn.rim")
    woofer = replace(
        cone(
            10,
            4,
            cap_radius_mm=3,
            cap_height_mm=2,
            surround_width_mm=2,
            surround_depth_mm=0.6,
            land_width_mm=1,
        )
        if curved
        else flat(8),
        physical_source_id="woofer",
        rim_id="woofer.rim",
    )
    model = SourceAssembly(
        horn, woofer, 70, 90, 40, 7, (-13, 17), 24, 10, (9, -15), 14 if curved else 8
    )
    drives = [
        ContourDrive("hf", tuple((s.id, 1) for s in horn.segments if s.role == "moving")),
        ContourDrive(
            "lf",
            tuple(
                zip(
                    [s.id for s in woofer.segments if s.role == "moving"],
                    [1, -0.5, 0] if curved else [1],
                )
            ),
            "axial",
        ),
    ]
    return model, drives


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):
    root = tmp_path_factory.mktemp("assembly-artifacts")
    result = []
    for curved in (False, True):
        model, drives = case(curved)
        path = root / str(curved)
        export_assembly(model, drives, path, mesh_size_mm=3)
        result.append(path)
    return result


def ingest(path, root):
    manifest, _, _, _ = read_native_source(path)
    store = CadLinkStore(root / "registry.sqlite")
    try:
        store.initialize()
        return ingest_native_source(
            path,
            store=store,
            data_dir=root,
            sizes={
                "rigid_size_mm": 4,
                "transition_mm": 3,
                "source_size_mm": {
                    p["id"]: 2 for p in manifest["patches"] if p["role"] == "moving"
                },
            },
        )
    finally:
        store.close()


def request(record):
    return SolveRequest.model_validate(
        {
            "geometry": {
                "type": "imported",
                "ingest_id": record["ingest_id"],
                "manifest_sha256": record["manifest_sha256"],
                "artifact_sha256": record["artifact_sha256"],
                "required_features": record["native_source"]["required_features"],
                "mesh": record["mesh_sizes"],
                "drive_channels": record["native_source"]["channels"],
            },
            "options": {
                "engine": "metal",
                "frequencies_hz": [100, 200],
                "polar_config": {"field_plane": False, "angle_range": [-180, 180, 37]},
            },
        }
    )


def independent_surfaces(model):
    def radial(origin, fn):
        def distance(p):
            q = p - np.asarray(origin)
            return fn(np.hypot(q[..., 0], q[..., 1]), q[..., 2])

        return distance

    moving, rigid = {}, []
    for key, c, i, origin, _ in model.patches:
        distance = radial(origin, segment(c, i))
        if c.segments[i].role == "moving":
            moving[key] = distance
        else:
            rigid.append(distance)
    z, w, h, d = model.front_z_mm, model.width_mm / 2, model.height_mm / 2, model.depth_mm

    def rectangle(p, axis, plane, a, b):
        others = [i for i in range(3) if i != axis]
        return np.sqrt(
            (p[..., axis] - plane) ** 2
            + sum(
                np.maximum(np.maximum(low - p[..., k], p[..., k] - high), 0) ** 2
                for k, (low, high) in zip(others, [a, b])
            )
        )

    def front(p):
        result = rectangle(p, 2, z, (-w, w), (-h, h))
        for center, radius in (
            (model.horn_xy_mm, model.mouth_radius_mm),
            (model.woofer_xy_mm, model.aperture_radius_mm),
        ):
            r = np.hypot(p[..., 0] - center[0], p[..., 1] - center[1])
            result = np.where(r < radius, np.hypot(p[..., 2] - z, radius - r), result)
        return result

    rigid += [
        front,
        lambda p: rectangle(p, 2, z - d, (-w, w), (-h, h)),
        lambda p: rectangle(p, 0, -w, (-h, h), (z - d, z)),
        lambda p: rectangle(p, 0, w, (-h, h), (z - d, z)),
        lambda p: rectangle(p, 1, -h, (-w, w), (z - d, z)),
        lambda p: rectangle(p, 1, h, (-w, w), (z - d, z)),
        radial(
            model.parts[0][1],
            line((model.horn.points[-1].r_mm, 0), (model.mouth_radius_mm, model.horn_length_mm)),
        ),
    ]
    if model.aperture_radius_mm > model.woofer.points[-1].r_mm:
        rigid.append(
            radial(
                model.parts[1][1],
                line((model.woofer.points[-1].r_mm, 0), (model.aperture_radius_mm, 0)),
            )
        )
    return moving, lambda p: np.minimum.reduce([f(p) for f in rigid])


@pytest.mark.parametrize("index", [0, 1])
def test_real_ingestion_dispatch_frame_and_combination(index, artifacts, tmp_path, monkeypatch):
    from hornlab_metal_bem.bie import _build_source_face_scale

    record = ingest(artifacts[index], tmp_path)
    manifest, model, _, _ = read_native_source(artifacts[index])
    req = request(record)
    validate_contour_request(req.geometry, record)
    frame = imported_anchor_frame(record)
    np.testing.assert_array_equal(frame["origin"], [0, 0, 0.007])
    mesh = meshio.read(record["mesh_store_path"])
    tri, tags = mesh.get_cells_type("triangle"), mesh.get_cell_data("gmsh:physical", "triangle")
    xyz = mesh.points[tri]
    normals = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    moving, rigid = independent_surfaces(model)
    bounds = {
        key: certificate_xyz(xyz[tags == record["source_tags"][key]] * 1000, distance)
        for key, distance in moving.items()
    }
    bounds["rigid"] = certificate_xyz(xyz[tags == 1] * 1000, rigid)
    assert max(bounds.values()) <= 0.15
    assert len(tri) <= 22000
    captured = {}

    def dispatch(path, sources, config, frequencies_hz=None):
        from pathlib import Path

        captured.update(sources=sources, config=config, mesh_text=Path(path).read_text())
        return [_native_result(), _native_result()]

    monkeypatch.setattr(
        metal, "metal_status", lambda: {"available": True, "reason": "CPU dispatch inspection"}
    )
    monkeypatch.setattr(metal, "native_solve_multi_source", dispatch)
    response = metal.solve_imported_metal_from_msh_text(
        read_verified_import_mesh(record), req, record
    )
    assert len(captured["sources"]) == 2
    assert captured["mesh_text"] == read_verified_import_mesh(record)
    np.testing.assert_array_equal(captured["config"].frame_override.origin, frame["origin"])
    np.testing.assert_array_equal(captured["config"].frame_override.axis, [0, 0, 1])
    fields = []
    for channel, spec in zip(req.geometry.drive_channels, captured["sources"]):
        assert spec == {
            record["source_tags"][key]: complex(weight)
            for key, weight in channel.patch_weights.items()
        }
        config = replace(captured["config"], velocity_sources=spec)
        scale = _build_source_face_scale(
            SimpleNamespace(vertices=mesh.points.T, elements=tri.T),
            tags,
            config,
            frame["axis"],
            frame["origin"],
        )
        actual, expected = np.zeros(len(tri), complex), np.zeros(len(tri), complex)
        for key, weight in channel.patch_weights.items():
            mask = tags == record["source_tags"][key]
            actual[mask] = weight * (1 if scale is None else scale[mask])
            expected[mask] = weight * (1 if channel.motion == "normal" else normals[mask, 2])
        np.testing.assert_allclose(actual, expected, atol=1e-12, rtol=0)
        inactive = set(record["source_tags"]) - set(channel.source_ids)
        assert not actual[np.isin(tags, [record["source_tags"][s] for s in inactive])].any()
        assert not actual[tags == 1].any()
        fields.append(actual)
        metadata = response["channels"][channel.id]["metadata"]
        assert metadata["physical_source_id"] == channel.physical_source_id
        assert (
            metadata["source_frame"]
            == record["native_source"]["source_frames"][channel.physical_source_id]
        )
        assert metadata["observation_frame"] == record["native_source"]["observation_frame"]
    # A coupled CPU linear-system fixture, driven by the actual entire mesh BC.
    # Dense reduction is explicit test algebra, not a substitute acoustic engine.
    centroids = xyz.mean(axis=1)
    probes = np.array([[0, 0, 0.3], [0.1, 0, 0.3], [0, -0.1, 0.3]]) + frame["origin"]
    projection = np.exp(1j * 15 * np.linalg.norm(probes[:, None] - centroids, axis=2))
    projection *= (
        np.linalg.norm(np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0]), axis=1)[None, :] / 2
    )
    operator = np.array(
        [[2 + 0.3j, 0.2 + 0.1j, -0.1j], [0.2j, 1.7 + 0.4j, 0.1], [0.15, -0.2j, 2.1 + 0.2j]]
    )
    rhs = projection @ np.column_stack(fields)
    basis = np.linalg.solve(operator, rhs)
    freqs = np.array([100.0, 200.0])
    native_results = {}
    for i, name in enumerate(["hf", "lf"]):
        result = _native_result()
        result.frequencies_hz = freqs
        result.observation_angles_deg = np.array([-30.0, 0.0, 30.0])
        result.observation_planes = ["probe"]
        result.pressure_complex = np.tile(basis[:, i][None, None, :], (2, 1, 1))
        native_results[name] = result
    gains, delays, signs = [0.0, -3.0], [0.13, 0.47], [1, -1]
    settings = {
        name: ChannelFilterSpec(
            gain={"mode": "manual", "db": gains[i]},
            delay={"mode": "manual", "ms": delays[i]},
            invert=signs[i] < 0,
        ).model_dump(mode="json")
        for i, name in enumerate(["hf", "lf"])
    }
    summed, _ = combine_drive_channels(
        native_results, members=["hf", "lf"], channels=settings, reference="hf"
    )
    for f, frequency in enumerate(freqs):
        engineering = (
            np.array(signs)
            * 10 ** (np.array(gains) / 20)
            * np.exp(-2j * np.pi * frequency * np.array(delays) * 0.001)
        )
        direct = np.linalg.solve(
            operator, projection @ (np.column_stack(fields) @ engineering.conj())
        )
        np.testing.assert_allclose(summed.pressure_complex[f, 0], direct, rtol=1e-10, atol=1e-12)
        assert np.max(abs(direct - np.abs(basis) @ engineering.conj())) > 1e-6


@pytest.mark.parametrize(
    "mutation",
    [
        "feature",
        "step",
        "preview",
        "overlap",
        "role",
        "channel",
        "weight",
        "area",
        "identity",
        "rebound",
    ],
)
def test_reject_mapping_preserve_registry(mutation, artifacts, tmp_path):
    path = tmp_path / "bad"
    shutil.copytree(artifacts[1], path)
    manifest = json.loads((path / "source.json").read_text())
    if mutation == "feature":
        manifest["required_features"] = ["native-source-contour-v1"]
    elif mutation in ("step", "preview"):
        member = "geometry.step" if mutation == "step" else "preview.msh"
        with (path / member).open("ab") as file:
            file.write(b"tamper")
    elif mutation == "overlap":
        manifest["patches"][1]["advanced_face_indices"] = manifest["patches"][0][
            "advanced_face_indices"
        ]
    elif mutation == "role":
        manifest["patches"][0]["role"] = "rigid"
    elif mutation == "channel":
        manifest["channels"][0]["physical_source_id"] = "woofer"
    elif mutation == "weight":
        manifest["channels"][1]["patch_weights"]["woofer/cone"] = 1
    elif mutation == "area":
        manifest["patches"][0]["area_mm2"] = 1
    elif mutation == "identity":
        manifest["recipe"]["horn_xy_mm"][0] += 1
    else:
        a, b = manifest["patches"][:2]
        a["advanced_face_indices"], b["advanced_face_indices"] = (
            b["advanced_face_indices"],
            a["advanced_face_indices"],
        )
    (path / "source.json").write_text(json.dumps(manifest))
    with pytest.raises((ValueError, KeyError)):
        ingest(path, tmp_path)
    assert not list((tmp_path / "imports/native-source").glob("*"))


def test_real_job_admission_and_common_frame_refusal(artifacts, tmp_path):
    record = ingest(artifacts[0], tmp_path)

    async def scenario():
        store = CadLinkStore(tmp_path / "registry.sqlite")
        runtime = JobRuntime(
            JobStore(tmp_path / "jobs.sqlite"),
            engine_registry=_PausedRegistry(),
            cadlink_store=store,
        )
        try:
            for change in ("feature", "weight", "skip"):
                bad = request(record)
                if change == "feature":
                    bad.geometry.required_features = ["native-source-contour-v1"]
                elif change == "weight":
                    bad.geometry.drive_channels[1].patch_weights["woofer/piston"] = 0
                else:
                    bad.geometry.skipped_source_ids = ["woofer/piston"]
                with pytest.raises(ImportedSolveRefusal):
                    await runtime.submit(bad)
            job = await runtime.submit(request(record))
            assert (
                len(runtime.store.get_job_row(job)["config_json"]["geometry"]["drive_channels"])
                == 2
            )
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())
    bad = deepcopy(record)
    bad["normalisation"]["source_frame"]["origin_m"][0] = 0.1
    with pytest.raises(ValueError, match="frame"):
        validate_contour_request(request(record).geometry, bad)


def test_real_authoring_ingestion_api(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.source_editor import canonical_document, create_router

    app = FastAPI()
    store = CadLinkStore(tmp_path / "registry.sqlite")
    store.initialize()
    app.state.cadlink_store = store
    app.include_router(create_router(tmp_path))
    model, drives = case(True)
    recipe = model.to_dict()
    body = {
        "horn": canonical_document(model.horn, drives[0]),
        "woofer": canonical_document(model.woofer, drives[1]),
        "dimensions": {
            k: v for k, v in recipe.items() if k not in ("horn", "woofer", "version", "frame")
        },
        "mesh_size_mm": 3,
    }
    try:
        with TestClient(app) as client:
            validated = client.post("/api/source-editor/assembly/validate", json=body)
            assert validated.status_code == 200, validated.text
            assert validated.json()["source_origins_mm"] == {
                "horn": [-13, 17, -17],
                "woofer": [9, -15, 7],
            }
            bad = deepcopy(body)
            bad["dimensions"]["horn_xy_mm"] = [9, -15]
            assert client.post("/api/source-editor/assembly/ingest", json=bad).status_code == 422
            result = client.post("/api/source-editor/assembly/ingest", json=body)
            assert result.status_code == 200, result.text
            geometry = result.json()["geometry"]
            raw = {
                "geometry": geometry,
                "options": {"engine": "metal", "frequencies_hz": [100, 200]},
            }
            req = SolveRequest.model_validate(raw)
            record = json.loads(store.get_ingest(geometry["ingest_id"])["record_json"])
            validate_contour_request(req.geometry, record)
            assert result.json()["geometric_quality"]["tolerance_mm"] == 0.15
            assert len(req.geometry.drive_channels) == 2
            assert "woofer/surround" in req.geometry.drive_channels[1].source_ids
            assert req.geometry.drive_channels[1].patch_weights["woofer/surround"] == 0
            assert "woofer/land" not in req.geometry.drive_channels[1].source_ids
    finally:
        store.close()


@pytest.mark.parametrize(
    "kind", ["unknown", "source", "negative", "noninteger", "nan", "notmapping"]
)
def test_rigid_face_size_refusals(kind, artifacts):
    import gmsh
    from server.mesh.imported import build_imported_mesh, ImportedMeshError

    manifest, _, _, _ = read_native_source(artifacts[0])
    moving = [p for p in manifest["patches"] if p["role"] == "moving"]
    imported = {
        "sources": [
            {
                "id": p["id"],
                "role": p["band"],
                "instance_id": None,
                "required": True,
                "patch_policy": "single-connected",
                "expected_connected_components": 1,
                "selectors": {"advanced_face_indices": p["advanced_face_indices"]},
                "observed": {"face_count": 1, "total_area_mm2": p["area_mm2"]},
            }
            for p in moving
        ],
        "instances": [],
        "coordinate_system": {"solver_anchor_instance_id": None},
        "assembly": {"n_bodies_expected": 1},
    }
    wall = next(p for p in manifest["rigid_faces"] if p["role"] == "horn-wall")[
        "advanced_face_indices"
    ][0]
    options = {
        "unknown": {999999: 2},
        "source": {moving[0]["advanced_face_indices"][0]: 2},
        "negative": {wall: -1},
        "noninteger": {str(wall): 2},
        "nan": {wall: float("nan")},
        "notmapping": [],
    }[kind]
    gmsh.initialize()
    try:
        with pytest.raises(ImportedMeshError):
            build_imported_mesh(
                artifacts[0] / "geometry.step",
                imported,
                {
                    "rigid_size_mm": 4,
                    "transition_mm": 3,
                    "source_size_mm": {p["id"]: 2 for p in moving},
                },
                options={"symmetry_mode": "full", "rigid_face_sizes_mm": options},
                include_viewport_mesh=False,
            )
    finally:
        gmsh.finalize()
