"""Cache invalidation follows installed mesher code, even at the same version."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from server.integration import installed, mesher_identity as identity
from server.mesh import builder
from server.preview import core
from test_mesh_builder import _tiny_design
from test_preview_ws_protocol import _small_geometry


@pytest.fixture(autouse=True)
def clear_identities():
    identity.mesher_identity.cache_clear()
    builder.clear_solver_mesh_cache()
    yield
    identity.mesher_identity.cache_clear()
    builder.clear_solver_mesh_cache()


@pytest.fixture
def revision(monkeypatch):
    state = {"commit": "a" * 40}

    def distribution(name):
        assert name == "hornlab-waveguide-mesher"
        return SimpleNamespace(read_text=lambda _filename: json.dumps({
            "vcs_info": {"vcs": "git", "commit_id": state["commit"]},
        }))

    monkeypatch.setattr(installed, "distribution", distribution)
    monkeypatch.setattr(builder, "_distribution_version", lambda _name: "same-version")
    return state


def test_identity_uses_installed_commit_and_measures_once(revision):
    assert identity.mesher_identity() == "git:" + "a" * 40
    revision["commit"] = "b" * 40
    assert identity.mesher_identity() == "git:" + "a" * 40
    # Model a fresh process's identity measurement without losing cache entries.
    identity.mesher_identity.cache_clear()
    assert identity.mesher_identity() == "git:" + "b" * 40


def test_solver_cache_rebuilds_on_same_version_revision_change(monkeypatch, revision):
    builds = 0

    async def build(*_args, **_kwargs):
        nonlocal builds
        builds += 1
        return {
            "msh_text": f"artifact-{builds}", "stats": {"warnings": []},
            "integrity": {"valid": True}, "metadata": {},
        }

    monkeypatch.setattr(builder, "run_mesh_build", build)

    async def scenario():
        first = await builder.build_solver_mesh(_tiny_design(), {})
        repeated = await builder.build_solver_mesh(_tiny_design(), {})
        assert repeated["stats"]["mesh_cache_hit"] is True
        revision["commit"] = "b" * 40
        identity.mesher_identity.cache_clear()
        changed = await builder.build_solver_mesh(_tiny_design(), {})
        assert changed["stats"]["mesh_cache_hit"] is False
        assert changed["stats"]["mesh_cache_key"] != first["stats"]["mesh_cache_key"]
        assert changed["msh_text"] != first["msh_text"]
        assert builds == 2

    asyncio.run(scenario())


def test_preview_cache_rebuilds_on_same_version_revision_change(monkeypatch, revision):
    from hornlab_mesher.preview import api

    builds = 0

    def build(*_args, **_kwargs):
        nonlocal builds
        builds += 1
        return _small_geometry()

    # Exercise the production namespace, shared across preview connections.
    monkeypatch.setattr(api, "build_preview_geometry", build)
    protocol = core.PreviewProtocol()
    request = core._Request(
        kind="preview", seq=1, design_revision=1, lod="coarse", design=_tiny_design(),
    )
    try:
        protocol._compute_frame(request)
        protocol._compute_frame(request)
        assert builds == 1
        revision["commit"] = "b" * 40
        identity.mesher_identity.cache_clear()
        protocol._compute_frame(request)
        assert builds == 2
    finally:
        asyncio.run(protocol._preview_service.shutdown())


@pytest.mark.parametrize("key_name", [
    "_cache_key", "_cache_lookup_key", "_viewport_cache_lookup_key",
])
def test_imported_cache_misses_on_same_version_revision_change(monkeypatch, revision, key_name):
    from server.cadlink import ingest
    from server.mesh.cache import SolverMeshArtifactCache

    monkeypatch.setattr(ingest, "_package_version", lambda _name: "same-version")
    bundle = SimpleNamespace(artifact_sha256="geometry", manifest_sha256="manifest")
    manifest = {"instances": [], "coordinate_system": {}, "sources": []}
    kwargs = dict(bundle=bundle, manifest=manifest, skipped_source_ids=[], options={})
    if key_name != "_viewport_cache_lookup_key":
        kwargs["sizes"] = {}
    if key_name == "_cache_key":
        kwargs["transformed_geometry_hash"] = "transformed"
    key = getattr(ingest, key_name)
    cache = SolverMeshArtifactCache()
    first = key(**kwargs)
    cache.put(first, {"msh_text": "old-mesh"})
    assert cache.get(key(**kwargs)) is not None
    revision["commit"] = "b" * 40
    identity.mesher_identity.cache_clear()
    assert key(**kwargs) != first
    assert cache.get(key(**kwargs)) is None


@pytest.mark.parametrize("changed_file", ["__init__.py", "data.json"])
def test_content_fallback_tracks_source_and_data_but_ignores_bytecode(
    monkeypatch, tmp_path, changed_file,
):
    root = tmp_path / "hornlab_mesher"
    root.mkdir()
    (root / "__init__.py").write_text("VERSION = 'same-version'\n")
    (root / "data.json").write_text("{}")
    monkeypatch.setattr(identity, "measure_installed_commit", lambda _name: None)
    monkeypatch.setattr(identity, "find_spec", lambda _name: SimpleNamespace(origin=root / "__init__.py"))
    first = identity.mesher_identity()
    assert first.startswith("sha256:")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "module.pyc").write_bytes(b"bytecode")
    identity.mesher_identity.cache_clear()
    assert identity.mesher_identity() == first
    (root / changed_file).write_text("changed code or data")
    identity.mesher_identity.cache_clear()
    assert identity.mesher_identity() != first


def test_unmeasured_identity_is_isolated_per_process(monkeypatch):
    monkeypatch.setattr(identity, "measure_installed_commit", lambda _name: None)
    monkeypatch.setattr(identity, "find_spec", lambda _name: None)
    first = identity.mesher_identity()
    assert first.startswith("unmeasured:")
    assert identity.mesher_identity() == first
    identity.mesher_identity.cache_clear()
    assert identity.mesher_identity() != first
