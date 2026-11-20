"""Solver meshes persist on disk under the in-memory cache's key (server/mesh/cache.py)."""

from __future__ import annotations

import asyncio
import json
import os

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
def disk(tmp_path):
    builder.clear_solver_mesh_cache()
    builder.configure_solver_mesh_disk_cache(tmp_path / "solver-mesh-cache")
    yield builder._solver_mesh_disk_cache
    builder.configure_solver_mesh_disk_cache(None)
    builder.clear_solver_mesh_cache()


# Per-request facts, not artifact: they differ between any two requests, a
# memory-cache hit included.
_PER_REQUEST = ("mesh_cache_hit", "preview_wall_check_ms", "preview_wall_check_cache_hit")


def _without_hit_flag(result: dict) -> dict:
    stats = {key: value for key, value in result["stats"].items() if key not in _PER_REQUEST}
    return {**result, "stats": stats}


@pytest.mark.usefixtures("in_process_mesher")
def test_a_restart_reads_back_the_identical_solve_mesh(disk, monkeypatch) -> None:
    design = _design()
    built = asyncio.run(builder.build_solver_mesh(design, {}))
    assert built["stats"]["mesh_cache_hit"] is False
    assert len(list(disk.folder.glob("*.json"))) == 1

    builder._solver_mesh_cache.clear()  # what a restart loses
    real_build = builder.run_mesh_build

    async def no_build(*_args, **_kwargs):
        raise AssertionError("a persisted mesh was rebuilt")

    monkeypatch.setattr(builder, "run_mesh_build", no_build)
    restored = asyncio.run(builder.build_solver_mesh(design, {}))
    assert restored["stats"]["mesh_cache_hit"] is True
    assert restored["msh_text"].encode("utf-8") == built["msh_text"].encode("utf-8")
    assert _without_hit_flag(restored) == _without_hit_flag(built)
    assert json.dumps(_without_hit_flag(restored)) == json.dumps(_without_hit_flag(built))

    monkeypatch.setattr(builder, "run_mesh_build", real_build)
    rebuilt = asyncio.run(builder.build_solver_mesh(design, {}, force_rebuild=True))
    assert rebuilt["msh_text"] == built["msh_text"]
    assert _without_hit_flag(rebuilt) == _without_hit_flag(built)


@pytest.mark.parametrize("damage", ["garbage", "digest", "key"])
def test_a_damaged_entry_is_discarded_and_rebuilt(disk, monkeypatch, damage) -> None:
    builds = []

    async def worker(_fn, _dump, **_kwargs):
        builds.append(1)
        return {"msh_text": "$MeshFormat\nmesh\n", "stats": {"warnings": [], "triangle_count": 4},
                "integrity": {"valid": True}, "metadata": {}}

    monkeypatch.setattr(builder, "run_mesh_build", worker)
    design = _design()
    asyncio.run(builder.build_solver_mesh(design, {}))
    [path] = disk.folder.glob("*.json")
    if damage == "garbage":
        path.write_bytes(b"\x00not json")
    else:
        record = json.loads(path.read_text(encoding="utf-8"))
        if damage == "digest":
            record["artifact"]["msh_text"] = "$MeshFormat\nother\n"
        else:
            record["key"] = "0" * 64
        path.write_text(json.dumps(record), encoding="utf-8")
    builder._solver_mesh_cache.clear()
    result = asyncio.run(builder.build_solver_mesh(design, {}))
    assert len(builds) == 2
    assert result["stats"]["mesh_cache_hit"] is False
    assert json.loads(path.read_text(encoding="utf-8"))["artifact"]["msh_text"] == "$MeshFormat\nmesh\n"


def test_a_mesher_change_starts_an_empty_folder_and_deletes_the_old(tmp_path) -> None:
    old = SolverMeshDiskCache(tmp_path, {"mesher": "1"})
    old.open()
    assert old.put("a" * 64, {"msh_text": "old"})
    new = SolverMeshDiskCache(tmp_path, {"mesher": "2"})
    assert new.folder != old.folder
    new.open()
    assert not old.folder.exists()
    assert new.get("a" * 64) is None


def test_the_folder_is_bounded_least_recently_used_first(tmp_path) -> None:
    cache = SolverMeshDiskCache(tmp_path, {"mesher": "1"}, max_entries=2)
    cache.open()
    keys = [character * 64 for character in "abc"]
    for index, key in enumerate(keys[:2]):
        assert cache.put(key, {"msh_text": key})
        os.utime(cache.folder / f"{key}.json", ns=(index * 10**9, index * 10**9))
    assert cache.get(keys[0]) == {"msh_text": keys[0]}  # now the most recent
    assert cache.put(keys[2], {"msh_text": keys[2]})
    assert sorted(path.stem for path in cache.folder.glob("*.json")) == sorted([keys[0], keys[2]])

    small = SolverMeshDiskCache(tmp_path / "small", {"mesher": "1"}, max_bytes=200)
    small.open()
    assert not small.put("d" * 64, {"msh_text": "x" * 500})
    assert not list(small.folder.glob("*.json"))


def test_only_an_artifact_json_carries_exactly_is_written(tmp_path) -> None:
    cache = SolverMeshDiskCache(tmp_path, {"mesher": "1"})
    cache.open()
    exact = {"msh_text": "m", "stats": {"bounds": [0.0, -0.0, float("inf")], "count": 3, "ok": True}}
    assert cache.put("a" * 64, exact)
    loaded = cache.get("a" * 64)
    assert loaded == exact and list(loaded["stats"]) == ["bounds", "count", "ok"]
    assert type(loaded["stats"]["count"]) is int
    for inexact in (
        {"msh_text": "m", "stats": {"bounds": (0.0, 1.0)}},
        {"msh_text": "m", "stats": {1: "integer key"}},
        {"msh_text": "m", "stats": {"value": object()}},
    ):
        assert not cache.put("b" * 64, inexact)
    assert cache.get("b" * 64) is None
    with pytest.raises(ValueError):
        cache.get("../escape")
