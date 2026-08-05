"""Bounded solver-mesh cache behavior independent of Gmsh."""

from server.mesh.cache import SolverMeshArtifactCache


def _result(text: str) -> dict:
    return {
        "msh_text": text,
        "stats": {"warnings": []},
        "integrity": {"valid": True},
        "metadata": {},
    }


def test_solver_mesh_cache_evicts_least_recently_used_entry() -> None:
    cache = SolverMeshArtifactCache(max_entries=2, max_bytes=1_000_000)
    cache.put("a", _result("a"))
    cache.put("b", _result("b"))
    assert cache.get("a") is not None
    cache.put("c", _result("c"))

    assert cache.get("a") is not None
    assert cache.get("b") is None
    assert cache.get("c") is not None


def test_solver_mesh_cache_rejects_oversized_artifact_and_copies_hits() -> None:
    cache = SolverMeshArtifactCache(max_entries=2, max_bytes=32 * 1024 + 8)
    assert cache.put("too-large", _result("abc")) is False
    assert cache.info().entries == 0

    cache = SolverMeshArtifactCache(max_entries=2, max_bytes=1_000_000)
    assert cache.put("mesh", _result("mesh")) is True
    hit = cache.get("mesh")
    assert hit is not None
    hit["stats"]["warnings"].append("request-local")
    assert cache.get("mesh")["stats"]["warnings"] == []
