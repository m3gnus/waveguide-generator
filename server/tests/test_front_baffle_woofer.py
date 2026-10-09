"""Real native woofer STEP ingestion and CPU-only production BC dispatch."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import shutil

import meshio
import numpy as np
import pytest

from hornlab_mesher.front_baffle import FrontBaffle
from hornlab_mesher.source_contour import ContourDrive, cone, flat, digest
from hornlab_mesher.woofer_artifact import export_woofer
from server.cadlink.native_source import ingest_native_source, read_native_source
from server.cadlink.store import CadLinkStore
from server.contracts.source_contour import validate_contour_request
from server.solver import metal
from server.solver.imported import imported_anchor_frame, read_verified_import_mesh
from server.tests.native_geometry_oracle import segment
from server.tests.test_native_source_contour import request as contour_request
from server.tests.test_imported_jobs import _native_result


def request(record):
    # The production request schema must negotiate both required features.
    from server.jobs.models import SolveRequest

    raw = contour_request(record).model_dump(mode="json")
    raw["geometry"]["required_features"] = record["native_source"]["required_features"]
    return SolveRequest.model_validate(raw)


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):
    root = tmp_path_factory.mktemp("woofer-artifacts")
    result = []
    for i in range(3):
        model = (
            flat(8)
            if i == 0
            else cone(
                10,
                4,
                cap_radius_mm=3,
                cap_height_mm=2,
                surround_width_mm=2,
                surround_depth_mm=0.6 if i == 1 else -0.6,
                land_width_mm=1,
            )
        )
        baffle = [
            FrontBaffle(30, 40, 12, 8),
            FrontBaffle(44, 54, 12, 14, (3, -4, 7)),
            FrontBaffle(44, 54, 12, 13, (-5, 6, -3)),
        ][i]
        weights = [[1], [1, -0.5, 0], [1, 0.4, 0]][i]
        drive = ContourDrive(
            "motor", tuple(zip([s.id for s in model.segments if s.role == "moving"], weights))
        )
        path = root / str(i)
        export_woofer(model, drive, baffle, path)
        result.append(path)
    return result


def ingest(path, tmp_path):
    store = CadLinkStore(tmp_path / "registry.sqlite")
    try:
        store.initialize()
        _, model, _, _ = read_native_source(path)
        return ingest_native_source(
            path,
            store=store,
            data_dir=tmp_path,
            sizes={
                "rigid_size_mm": 4,
                "transition_mm": 3,
                "source_size_mm": {s.id: 2 for s in model.segments if s.role == "moving"},
            },
        )
    finally:
        store.close()


def certificate_xyz(xyz, distance):
    worst = 0
    for n in (32, 128):
        uv = np.array([(a / n, b / n) for a in range(n + 1) for b in range(n + 1 - a)])
        pending = []
        for batch in np.array_split(xyz, max(1, len(xyz) // 24 + 1)):
            p = (
                batch[:, 0, None, :]
                + uv[None, :, 0, None] * (batch[:, 1, None, :] - batch[:, 0, None, :])
                + uv[None, :, 1, None] * (batch[:, 2, None, :] - batch[:, 0, None, :])
            )
            bounds = (
                distance(p).max(axis=1)
                + np.linalg.norm(batch - np.roll(batch, 1, axis=1), axis=2).max(axis=1) / n
            )
            good = bounds <= 0.15
            if good.any():
                worst = max(worst, float(bounds[good].max()))
            pending.extend(batch[~good])
        if not pending:
            return worst
        xyz = np.asarray(pending)
    pytest.fail(f"independent finite facet bound exceeded: {bounds.max()}")


def independent_rigid(model, baffle):
    cx, cy, z = baffle.center_mm
    w, h, d = baffle.width_mm / 2, baffle.height_mm / 2, baffle.depth_mm

    def rectangle(p, axis, plane, a, b):
        other = [i for i in range(3) if i != axis]
        delta = [
            np.maximum(np.maximum(low - p[..., k], p[..., k] - high), 0)
            for k, (low, high) in zip(other, [a, b])
        ]
        return np.sqrt((p[..., axis] - plane) ** 2 + delta[0] ** 2 + delta[1] ** 2)

    def distance(p):
        radius = np.hypot(p[..., 0] - cx, p[..., 1] - cy)
        rim = model.points[-1].r_mm
        front = np.where(
            radius < rim,
            np.hypot(z - p[..., 2], rim - radius),
            rectangle(p, 2, z, (-w, w), (-h, h)),
        )
        ds = [
            front,
            rectangle(p, 2, z - d, (-w, w), (-h, h)),
            rectangle(p, 0, -w, (-h, h), (z - d, z)),
            rectangle(p, 0, w, (-h, h), (z - d, z)),
            rectangle(p, 1, -h, (-w, w), (z - d, z)),
            rectangle(p, 1, h, (-w, w), (z - d, z)),
        ]
        for i, s in enumerate(model.segments):
            if s.role == "rigid":
                ds.append(segment(model, i)(radius, p[..., 2] - z))
        return np.minimum.reduce(ds)

    return distance


@pytest.mark.parametrize("index", range(3))
def test_real_woofer_ingestion_geometry_and_bc(index, artifacts, tmp_path, monkeypatch):
    from hornlab_metal_bem.bie import _build_source_face_scale

    record = ingest(artifacts[index], tmp_path)
    manifest, model, drive, _ = read_native_source(artifacts[index])
    baffle = FrontBaffle.from_dict(manifest["recipe"]["baffle"])
    assert record["requested_mesh_sizes"]["rigid_size_mm"] == 4
    assert all(s["role"] == "LF" for s in record["sources"])
    assert record["domain_interpretation"]["type"] == "native-front-baffle-woofer-closed-enclosure"
    frame = imported_anchor_frame(record)
    assert np.allclose(frame["origin"], np.asarray(baffle.center_mm) * 0.001)
    assert "source_frame" in record["normalisation"]
    assert "anchor_throat_frame" not in record["normalisation"]
    assert record["mesh"]["stats"]["triangle_count"] < 22000
    mesh = meshio.read(record["mesh_store_path"])
    tri = mesh.get_cells_type("triangle")
    tags = mesh.get_cell_data("gmsh:physical", "triangle")
    xyz = mesh.points[tri]
    edges = np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])
    _, inverse, counts = np.unique(
        np.sort(edges, axis=1), axis=0, return_inverse=True, return_counts=True
    )
    assert set(counts) == {2}
    assert not np.any(np.bincount(inverse, weights=np.where(edges[:, 0] < edges[:, 1], 1, -1)))
    n = np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0])
    assert np.all(np.linalg.norm(n, axis=1) > 1e-12)
    assert len(np.unique(np.sort(tri, axis=1), axis=0)) == len(tri)
    n /= np.linalg.norm(n, axis=1)[:, None]
    for i, s in enumerate(model.segments):
        if s.role == "moving":

            def distance(p, i=i):
                local = p - np.asarray(baffle.center_mm)
                return segment(model, i)(np.hypot(local[..., 0], local[..., 1]), local[..., 2])

            certificate_xyz(xyz[tags == record["source_tags"][s.id]] * 1000, distance)
    certificate_xyz(xyz[tags == 1] * 1000, independent_rigid(model, baffle))
    assert (
        max(
            [
                record["mesh"]["geometric_quality"]["rigid_bound_mm"],
                *record["mesh"]["geometric_quality"]["moving_patch_bounds_mm"].values(),
            ]
        )
        <= 0.15
    )
    captured = {}

    def dispatch(path, sources, config, frequencies_hz=None):
        captured.update(sources=sources, config=config)
        return [_native_result()]

    monkeypatch.setattr(
        metal, "metal_status", lambda: {"available": True, "reason": "CPU BC inspection"}
    )
    monkeypatch.setattr(metal, "native_solve_multi_source", dispatch)
    modes = {}
    for motion in ("normal", "axial"):
        local = deepcopy(record)
        local["native_source"]["channel"]["motion"] = motion
        local["native_source"]["excitation_sha256"] = ContourDrive(
            "motor", drive.weights, motion
        ).excitation_sha256
        req = request(local)
        validate_contour_request(req.geometry, local)
        response = metal.solve_imported_metal_from_msh_text(
            read_verified_import_mesh(local), req, local
        )
        spec = captured["sources"][0]
        assert spec == {
            record["source_tags"][key]: complex(weight) for key, weight in drive.weights
        }
        assert 1 not in spec
        config = replace(captured["config"], velocity_sources=spec)
        scale = _build_source_face_scale(
            SimpleNamespace(vertices=mesh.points.T, elements=tri.T),
            tags,
            config,
            np.array([0, 0, 1]),
            frame["origin"],
        )
        actual = np.zeros(len(tri), complex)
        expected = np.zeros(len(tri), complex)
        for key, weight in drive.weights:
            mask = tags == record["source_tags"][key]
            actual[mask] = spec[record["source_tags"][key]] * (1 if scale is None else scale[mask])
            expected[mask] = weight * (1 if motion == "normal" else n[mask, 2])
        assert np.max(abs(actual - expected)) <= 1e-12
        modes[motion] = actual
        assert response["channels"]["motor"]["metadata"]["patch_weights"] == dict(drive.weights)
    assert (
        (np.max(abs(modes["normal"] - modes["axial"])) <= 1e-12)
        if index == 0
        else (np.max(abs(modes["normal"] - modes["axial"])) > 0.01)
    )
    req = request(record)
    req.geometry.required_features = ["native-source-contour-v1"]
    with pytest.raises(ValueError, match="negotiation"):
        validate_contour_request(req.geometry, record)


@pytest.mark.parametrize(
    "mutation", ["feature", "frame", "area", "swap-rigid", "swap-moving-rigid", "aperture", "step"]
)
def test_tampered_woofer_is_atomic(mutation, artifacts, tmp_path):
    path = tmp_path / "bad"
    shutil.copytree(artifacts[1], path)
    m = json.loads((path / "source.json").read_text())
    if mutation == "feature":
        m["required_features"] = ["native-source-contour-v1"]
    elif mutation == "frame":
        m["recipe"]["baffle"]["center_mm"][0] += 1
        m["geometry_sha256"] = digest(m["recipe"])
    elif mutation == "area":
        m["baffle_faces"][0]["area_mm2"] = -1
    elif mutation == "swap-rigid":
        a = next(f for f in m["baffle_faces"] if f["role"] == "left")
        b = next(f for f in m["baffle_faces"] if f["role"] == "right")
        a["advanced_face_indices"], b["advanced_face_indices"] = (
            b["advanced_face_indices"],
            a["advanced_face_indices"],
        )
    elif mutation == "swap-moving-rigid":
        a = m["patches"][0]
        b = m["baffle_faces"][0]
        m["rigid_face_indices"].remove(b["advanced_face_indices"][0])
        m["rigid_face_indices"].append(a["advanced_face_indices"][0])
        a["advanced_face_indices"], b["advanced_face_indices"] = (
            b["advanced_face_indices"],
            a["advanced_face_indices"],
        )
    elif mutation == "aperture":
        m["recipe"]["baffle"]["aperture_radius_mm"] = 12
        m["geometry_sha256"] = digest(m["recipe"])
    else:
        with (path / "geometry.step").open("ab") as f:
            f.write(b"tampered")
    (path / "source.json").write_text(json.dumps(m))
    with pytest.raises(ValueError):
        ingest(path, tmp_path)
    import sqlite3

    with sqlite3.connect(tmp_path / "registry.sqlite") as db:
        assert db.execute("SELECT count(*) FROM ingests").fetchone()[0] == 0
    assert not list((tmp_path / "imports/native-source").glob("*"))


def test_production_job_admission(artifacts, tmp_path):
    import asyncio
    from server.jobs.runtime import JobRuntime, ImportedSolveRefusal
    from server.jobs.store import JobStore
    from server.tests.test_imported_jobs import _PausedRegistry

    record = ingest(artifacts[0], tmp_path)

    async def scenario():
        store = CadLinkStore(tmp_path / "registry.sqlite")
        runtime = JobRuntime(
            JobStore(tmp_path / "jobs.sqlite"),
            engine_registry=_PausedRegistry(),
            cadlink_store=store,
        )
        try:
            bad = request(record)
            bad.geometry.required_features = ["native-source-contour-v1"]
            with pytest.raises(ImportedSolveRefusal, match="negotiation"):
                await runtime.submit(bad)
            job_id = await runtime.submit(request(record))
            row = runtime.store.get_job_row(job_id)
            assert (
                row["config_json"]["geometry"]["required_features"]
                == record["native_source"]["required_features"]
            )
            assert row["config_json"]["geometry"]["drive_channels"][0]["patch_weights"] == {
                "piston": 1
            }
        finally:
            await runtime.shutdown()
            store.close()

    asyncio.run(scenario())
