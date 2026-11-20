"""Solver meshes persist on disk under the in-memory cache's key (server/mesh/cache.py)."""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from server.design.schema import DesignConfig
from server.mesh import builder
from server.mesh.cache import SolverMeshDiskCache


def _design(**mesh) -> DesignConfig:
    return DesignConfig.model_validate({
        "formula": "OSSE", "L": 60, "a": 30, "a0": 10, "r0": 10, "k": 1, "n": 4, "q": 0.99, "s": 0.8,
        "mesh": {"angular_segments": 12, "length_segments": 4, "throat_resolution": 8,
                 "mouth_resolution": 15, "quadrants": 1, "wall_thickness": 2, "max_triangles": 50000, **mesh},
        "source": {"shape": 2, "radius": -1, "curvature": 0},
        "simulation": {"f1": 500, "f2": 1000, "num_frequencies": 2},
    })


@pytest.fixture()
def root(tmp_path):
    builder.clear_solver_mesh_cache()
    yield tmp_path / "solver-mesh-cache"
    builder.configure_solver_mesh_disk_cache(None)
    builder.clear_solver_mesh_cache()
    builder.dense_solver_memory_limit.cache_clear()
    builder._wg_mesh_code_identity.cache_clear()


@pytest.fixture()
def disk(root):
    return builder.configure_solver_mesh_disk_cache(root)


def _restart(root):
    """What a restart keeps: the data folder, not the memory cache."""
    builder.configure_solver_mesh_disk_cache(None)
    builder._solver_mesh_cache.clear()
    builder._mesh_estimate_cache.clear()
    return builder.configure_solver_mesh_disk_cache(root)


def _build(design, **options):
    result = asyncio.run(builder.build_solver_mesh(design, options))
    cache = builder._solver_mesh_disk_cache
    if cache is not None:
        cache.flush()
    return result


# Per-request facts, not artifact: they differ between any two requests, a
# memory-cache hit included.
_PER_REQUEST = ("mesh_cache_hit", "preview_wall_check_ms", "preview_wall_check_cache_hit")


def _without_hit_flag(result: dict) -> dict:
    stats = {key: value for key, value in result["stats"].items() if key not in _PER_REQUEST}
    return {**result, "stats": stats}


def _entries(cache: SolverMeshDiskCache) -> list:
    return sorted(cache.folder.glob(f"*{cache.ENTRY_SUFFIX}"))


@pytest.mark.usefixtures("in_process_mesher")
def test_a_restart_reads_back_the_identical_solve_mesh(root, disk, monkeypatch) -> None:
    design = _design()
    built = _build(design)
    assert built["stats"]["mesh_cache_hit"] is False
    assert len(_entries(disk)) == 1

    disk = _restart(root)
    real_build = builder.run_mesh_build

    async def no_build(*_args, **_kwargs):
        raise AssertionError("a persisted mesh was rebuilt")

    monkeypatch.setattr(builder, "run_mesh_build", no_build)
    restored = _build(design)
    assert restored["stats"]["mesh_cache_hit"] is True
    assert restored["msh_text"].encode("utf-8") == built["msh_text"].encode("utf-8")
    assert _without_hit_flag(restored) == _without_hit_flag(built)
    assert json.dumps(_without_hit_flag(restored)) == json.dumps(_without_hit_flag(built))

    monkeypatch.setattr(builder, "run_mesh_build", real_build)
    rebuilt = asyncio.run(builder.build_solver_mesh(design, {}, force_rebuild=True))
    assert rebuilt["msh_text"] == built["msh_text"]
    assert _without_hit_flag(rebuilt) == _without_hit_flag(built)


@pytest.mark.usefixtures("in_process_mesher")
def test_a_lower_memory_ceiling_after_a_restart_refuses_instead_of_hitting(root, disk, monkeypatch) -> None:
    from server.mesh.errors import SolverMemoryTooLarge

    design = _design()
    _build(design)
    first_folder = disk.folder
    monkeypatch.setenv("WG2_DENSE_SOLVER_MEMORY_LIMIT_BYTES", str(1024 * 1024))
    builder.dense_solver_memory_limit.cache_clear()
    restarted = _restart(root)
    assert restarted.folder != first_folder
    with pytest.raises(SolverMemoryTooLarge):
        _build(design)


def test_a_ceiling_changed_under_a_running_cache_is_not_served(root, disk, monkeypatch) -> None:
    builds = []

    async def worker(_fn, _dump, **_kwargs):
        builds.append(1)
        return {"msh_text": "$MeshFormat\nmesh\n", "stats": {"warnings": [], "triangle_count": 4},
                "integrity": {"valid": True}, "metadata": {}}

    monkeypatch.setattr(builder, "run_mesh_build", worker)
    _build(_design())
    builder._solver_mesh_cache.clear()
    monkeypatch.setenv("WG2_DENSE_SOLVER_MEMORY_LIMIT_BYTES", str(3 * 1024**3))
    builder.dense_solver_memory_limit.cache_clear()
    _build(_design())
    assert len(builds) == 2


def test_a_wg_mesh_code_change_misses_and_keeps_the_other_build_entries(root, disk, monkeypatch) -> None:
    builds = []

    async def worker(_fn, _dump, **_kwargs):
        builds.append(1)
        return {"msh_text": f"$MeshFormat\nmesh {len(builds)}\n", "stats": {"warnings": [], "triangle_count": 4},
                "integrity": {"valid": True}, "metadata": {}}

    monkeypatch.setattr(builder, "run_mesh_build", worker)
    _build(_design())
    old_folder = disk.folder
    monkeypatch.setattr(builder, "_wg_mesh_code_identity", lambda: "sha256:" + "f" * 64)
    other = _restart(root)
    other.flush()
    assert other.folder != old_folder
    assert _build(_design())["msh_text"] == "$MeshFormat\nmesh 2\n"
    assert len(builds) == 2
    # The first build's folder is in use and stays; switching back hits it.
    assert old_folder.is_dir()
    monkeypatch.undo()
    monkeypatch.setattr(builder, "run_mesh_build", worker)
    _restart(root)
    assert _build(_design())["msh_text"] == "$MeshFormat\nmesh 1\n"
    assert len(builds) == 2


def test_two_generators_share_a_root_and_stale_ones_are_tidied_in_the_background(tmp_path) -> None:
    first = SolverMeshDiskCache(tmp_path, {"mesher": "1"})
    first.open()
    assert first.put("a" * 64, {"msh_text": "one"})
    second = SolverMeshDiskCache(tmp_path, {"mesher": "2"})
    second.open()
    second.flush()
    assert first.get("a" * 64) == {"msh_text": "one"}
    stale = (tmp_path / ("0" * 32))
    stale.mkdir()
    old = time.time() - SolverMeshDiskCache.stale_seconds - 60
    os.utime(stale, (old, old))
    not_ours = tmp_path / "keep-me"
    not_ours.mkdir()
    os.utime(not_ours, (old, old))
    third = SolverMeshDiskCache(tmp_path, {"mesher": "3"})
    third.open()
    third.flush()
    assert not stale.exists() and not_ours.exists() and first.folder.exists()
    for cache in (first, second, third):
        cache.close()


@pytest.mark.parametrize("damage", ["garbage", "digest", "key"])
def test_a_damaged_entry_is_discarded_and_rebuilt(disk, monkeypatch, damage) -> None:
    builds = []

    async def worker(_fn, _dump, **_kwargs):
        builds.append(1)
        return {"msh_text": "$MeshFormat\nmesh\n", "stats": {"warnings": [], "triangle_count": 4},
                "integrity": {"valid": True}, "metadata": {}}

    monkeypatch.setattr(builder, "run_mesh_build", worker)
    design = _design()
    _build(design)
    [path] = _entries(disk)
    header, _, body = path.read_bytes().partition(b"\n")
    if damage == "garbage":
        path.write_bytes(b"\x00not json")
    elif damage == "digest":
        path.write_bytes(header + b"\n" + b"$MeshFormat\nother\n")
    else:
        record = json.loads(header)
        record["key"] = "0" * 64
        path.write_bytes(json.dumps(record).encode() + b"\n" + body)
    builder._solver_mesh_cache.clear()
    result = _build(design)
    assert len(builds) == 2
    assert result["stats"]["mesh_cache_hit"] is False
    assert path.read_bytes().partition(b"\n")[2] == b"$MeshFormat\nmesh\n"


def test_the_folder_is_bounded_and_never_evicts_the_entry_just_written(tmp_path) -> None:
    cache = SolverMeshDiskCache(tmp_path, {"mesher": "1"}, max_entries=2)
    cache.open()
    keys = [character * 64 for character in "abcd"]
    for key in keys[:2]:
        assert cache.put(key, {"msh_text": key})
    assert cache.get(keys[0]) == {"msh_text": keys[0]}
    # Coarse (1 s) timestamps: everything shares one mtime, the newest included.
    same = time.time()
    for path in cache.folder.iterdir():
        os.utime(path, (same, same))
    assert cache.put(keys[3], {"msh_text": keys[3]})
    names = {path.stem for path in _entries(cache)}
    assert keys[3] in names and len(names) == 2

    small = SolverMeshDiskCache(tmp_path / "small", {"mesher": "1"}, max_bytes=300)
    small.open()
    assert not small.put("d" * 64, {"msh_text": "x" * 500})
    assert not _entries(small)
    cache.close()
    small.close()


def test_crash_leftovers_count_toward_the_bound_and_are_swept(tmp_path) -> None:
    cache = SolverMeshDiskCache(tmp_path, {"mesher": "1"}, max_bytes=3700)
    cache.open()
    cache.flush()
    fresh = cache.folder / f"{cache.STAGE_PREFIX}fresh"
    fresh.write_bytes(b"x" * 3000)
    orphan = cache.folder / f"{cache.STAGE_PREFIX}orphan"
    orphan.write_bytes(b"x" * 3000)
    old = time.time() - cache.ORPHAN_SECONDS - 60
    os.utime(orphan, (old, old))
    assert cache.put("a" * 64, {"msh_text": "a" * 200})
    assert cache.put("b" * 64, {"msh_text": "b" * 200})
    assert not orphan.exists() and fresh.exists()
    # The unfinished write uses the room: only the newest entry fits beside it.
    assert [path.stem for path in _entries(cache)] == ["b" * 64]
    cache.close()


def test_only_an_artifact_json_carries_exactly_is_written(tmp_path) -> None:
    cache = SolverMeshDiskCache(tmp_path, {"mesher": "1"})
    cache.open()
    exact = {"stats": {"bounds": [0.0, -0.0, float("inf")], "count": 3, "ok": True}, "msh_text": "m\r\n"}
    assert cache.put("a" * 64, exact)
    loaded = cache.get("a" * 64)
    assert loaded == exact and list(loaded) == ["stats", "msh_text"]
    assert list(loaded["stats"]) == ["bounds", "count", "ok"] and type(loaded["stats"]["count"]) is int
    for inexact in (
        {"msh_text": "m", "stats": {"bounds": (0.0, 1.0)}},
        {"msh_text": "m", "stats": {1: "integer key"}},
        {"msh_text": "m", "stats": {"value": object()}},
        {"msh_text": "lone \ud800 surrogate", "stats": {}},
        {"stats": {}},
    ):
        assert not cache.put("b" * 64, inexact)
    assert cache.get("b" * 64) is None
    with pytest.raises(ValueError):
        cache.get("../escape")
    cache.close()


def test_one_app_closing_its_cache_leaves_another_apps_cache_on(tmp_path) -> None:
    try:
        first = builder.configure_solver_mesh_disk_cache(tmp_path / "a")
        second = builder.configure_solver_mesh_disk_cache(tmp_path / "b")
        assert builder._solver_mesh_disk_cache is second
        builder.close_solver_mesh_disk_cache(first)
        assert builder._solver_mesh_disk_cache is second
        builder.close_solver_mesh_disk_cache(second)
        assert builder._solver_mesh_disk_cache is None
    finally:
        builder.configure_solver_mesh_disk_cache(None)


def test_an_unmeasured_identity_keeps_the_disk_cache_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(builder, "_wg_mesh_code_identity", lambda: None)
    assert builder.configure_solver_mesh_disk_cache(tmp_path) is None
    assert builder._solver_mesh_disk_cache is None
