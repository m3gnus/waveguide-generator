"""Acoustic and viewport caches share integrity and cache-miss behavior."""

import json
from pathlib import Path

import pytest

from server.cadlink.intake.ingest import _read_mesh_cache, _write_cache, _write_viewport_cache


@pytest.mark.parametrize("kind", ["acoustic", "viewport"])
@pytest.mark.parametrize("state", [
    "valid", "absent", "missing_mesh", "missing_metadata", "malformed_json",
    "missing_digest", "mismatched_digest", "unreadable_mesh", "unreadable_metadata",
    "invalid_utf8", "non_mapping_metadata",
])
def test_mesh_cache_integrity_and_exact_contents(tmp_path, monkeypatch, kind, state):
    mesh = tmp_path / kind / "mesh.msh"
    sidecar = mesh.with_suffix(".json")
    msh_text = "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n# é 0.125 -2.5\n"
    result = {"msh_text": msh_text, "stats": {"area": 0.125}, "metadata": {"scale": -2.5}}
    if kind == "acoustic":
        _write_cache(mesh, sidecar, result)
    else:
        _write_viewport_cache(mesh, sidecar, result, transformed_geometry_hash="sha256:geometry", healing={})
    metadata = json.loads(sidecar.read_bytes())
    original_mesh, original_metadata = mesh.read_bytes(), sidecar.read_bytes()
    if state in {"absent", "missing_mesh"}:
        mesh.unlink()
    if state in {"absent", "missing_metadata"}:
        sidecar.unlink()
    if state == "malformed_json":
        sidecar.write_text("{", encoding="utf-8")
    if state == "missing_digest":
        sidecar.write_text("{}", encoding="utf-8")
    if state == "mismatched_digest":
        mesh.write_text("altered geometry", encoding="utf-8")
    if state == "non_mapping_metadata":
        sidecar.write_text("[]", encoding="utf-8")
    if state == "invalid_utf8":
        mesh.write_bytes(b"\xff")
    if state in {"unreadable_mesh", "unreadable_metadata"}:
        unreadable = mesh if state == "unreadable_mesh" else sidecar
        read_text = Path.read_text

        def read(path, *args, **kwargs):
            if path == unreadable:
                raise PermissionError("cache file is unreadable")
            return read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read)

    cached = _read_mesh_cache(mesh, sidecar)
    if state == "valid":
        assert cached == {**metadata, "msh_text": msh_text}
        assert cached["msh_text"].encode("utf-8") == original_mesh
        assert mesh.read_bytes() == original_mesh
        assert sidecar.read_bytes() == original_metadata
    else:
        assert cached is None
