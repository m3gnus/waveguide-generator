"""Warm identity cache coverage; all workers and executable bytes are fixtures."""

# Imported pytest fixture names are intentionally used as test parameters.
# ruff: noqa: F811

import os
from time import perf_counter
from statistics import median
from pathlib import Path

import pytest

from server.solver.beat_runtime import assets, identity, manager, paths, readiness, state, warm_cache
from server.solver.beat_runtime.client import HostError
from server.solver.beat_runtime.session import SolveSession
from server.tests.beat_runtime.test_readiness import proved_cpu  # noqa: F401
from server.tests.beat_runtime.test_session import Worker


@pytest.mark.parametrize("component", [
    "state", "identity", "marker", "lock", "julia", "engine", "runtime", "fixture", "project", "environment", "threads",
])
def test_each_component_invalidates_verdict(proved_cpu, monkeypatch, tmp_path, component):
    root, engine, julia, _, query, _, _ = proved_cpu
    source = tmp_path / "runtime-source"
    source.mkdir()
    # Observe a stand-in runtime directory without editing production files.
    original = warm_cache.file_signature
    runtime = Path(warm_cache.__file__).parent
    monkeypatch.setattr(warm_cache, "file_signature", lambda path: original(source if path == runtime else path))
    full_proof = readiness._prove_backend_readiness
    calls = []

    def prove(*args, **kwargs):
        calls.append(True)
        return full_proof(*args, **kwargs)

    monkeypatch.setattr(readiness, "_prove_backend_readiness", prove)
    assert readiness.backend_readiness("cpu", root, **query).ready
    assert readiness.backend_readiness("cpu", root, **query).ready
    assert len(calls) == 1
    targets = {"state": root / "state-cpu.json", "identity": root / "julia.json",
               "marker": root / "provision.holder.json", "lock": root / "provision.lock",
               "julia": julia, "engine": engine.root, "runtime": source,
               "fixture": warm_cache.probe._FIXTURE, "project": engine.project / "Project.toml"}
    if component == "environment":
        query["environ"]["BLAB_CHANGED"] = "1"
    elif component == "threads":
        query["julia_threads"] = 4
    elif component == "fixture":
        fixture = tmp_path / "probe.msh"
        fixture.write_bytes(warm_cache.probe._FIXTURE.read_bytes())
        monkeypatch.setattr(warm_cache.probe, "_FIXTURE", fixture)
    else:
        target = targets[component]
        target.touch(exist_ok=True)
        stat = target.stat()
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    readiness.backend_readiness("cpu", root, **query)
    assert len(calls) == 2


@pytest.mark.parametrize("component", ["mtime", "size", "inode", "missing"])
def test_stat_signature_observes_replacements(tmp_path, component):
    path = tmp_path / "file"
    path.write_bytes(b"one")
    stat = path.stat()
    before = warm_cache.file_signature(path)
    if component == "mtime":
        # 1 ms, not 1 ns: NTFS stores times in 100 ns units and drops smaller steps.
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    elif component == "size":
        path.write_bytes(b"longer")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    elif component == "inode":
        replacement = tmp_path / "replacement"
        replacement.write_bytes(b"two")
        os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        replacement.replace(path)
    else:
        path.unlink()
    assert warm_cache.file_signature(path) != before


def test_explicit_refresh_and_provisioning_reprove(proved_cpu, monkeypatch):
    root, _, _, _, query, workers, setup = proved_cpu
    full = readiness._prove_backend_readiness
    calls = []
    monkeypatch.setattr(readiness, "_prove_backend_readiness", lambda *a, **kw: (calls.append(1), full(*a, **kw))[1])
    readiness.backend_readiness("cpu", root, **query)
    readiness.backend_readiness("cpu", root, force_refresh=True, **query)
    assert len(calls) == 2
    readiness.provision_cpu(root, worker_factory=type(workers[0]), force=True, **setup)
    readiness.backend_readiness("cpu", root, **query)
    assert len(calls) == 3


@pytest.mark.parametrize("failure", ["terminal", "read", "startup"])
def test_stale_runtime_failure_reproves(proved_cpu, monkeypatch, failure):
    root, _, _, _, query, _, _ = proved_cpu
    readiness.backend_readiness("cpu", root, **query)
    full = readiness._prove_backend_readiness
    calls = []
    monkeypatch.setattr(readiness, "_prove_backend_readiness", lambda *a, **kw: (calls.append(1), full(*a, **kw))[1])

    class StaleWorker(Worker):
        def ensure_started(self, **kwargs):
            if failure == "startup":
                raise HostError("stale runtime")

        def submit(self, path, **kwargs):
            stream = super().submit(path, **kwargs)
            def event():
                if failure == "read":
                    raise OSError("stale runtime connection")
                return {"type": "failed", "error": "stale runtime"}
            stream.__class__ = type("StaleStream", (type(stream),), {"__next__": lambda self: event()})
            return stream

    client = manager.ManagedWorker(StaleWorker(), "child")
    with SolveSession() as session:
        if failure == "startup":
            with pytest.raises(RuntimeError, match="stale"):
                session.submit(client, {})
        else:
            session.submit(client, {})
            if failure == "read":
                with pytest.raises(OSError, match="stale"):
                    list(session.events())
            else:
                assert list(session.events())[-1]["type"] == "failed"
    readiness.backend_readiness("cpu", root, **query)
    assert len(calls) == 1


def test_key_hit_skips_tree_walk_and_refresh_revokes(proved_cpu, monkeypatch):
    root, _, julia, _, query, _, _ = proved_cpu
    options = dict(julia_executable=str(julia), julia_threads=3, environment=query["environ"])
    full = manager._resolve_key
    calls = []
    monkeypatch.setattr(manager, "_resolve_key", lambda *a, **kw: (calls.append(1), full(*a, **kw))[1])
    first = manager.resolve_key("cpu", **options)
    assert manager.resolve_key("cpu", **options) == first
    assert len(calls) == 1
    # Mutating a returned key must never corrupt the next cache hit.
    first["environment"]["BLAB_NEW"] = "mutated"
    assert "BLAB_NEW" not in manager.resolve_key("cpu", **options)["environment"]
    readiness.probe_cache_clear(notify=False)
    manager.resolve_key("cpu", **options)
    assert len(calls) == 2
    (root / "provision.holder.json").touch()
    manager.resolve_key("cpu", **options)
    assert len(calls) == 3


@pytest.mark.parametrize("tree", ["engine", "runtime"])
@pytest.mark.parametrize("extension", [".py", ".jl", ".toml", ".json"])
def test_nested_edit_revokes_readiness_and_host_key(proved_cpu, monkeypatch, tmp_path, tree, extension):
    root, engine, julia, saved, query, _, _ = proved_cpu
    source = engine.root
    if tree == "runtime":
        source = tmp_path / "runtime-source"
        source.mkdir()
        (source / "__init__.py").write_text("fixture")
        full_fingerprint = identity.runtime_fingerprint
        monkeypatch.setattr(identity, "runtime_fingerprint", lambda **kw: full_fingerprint(source, **kw))
        monkeypatch.setattr(warm_cache, "__file__", str(source / "warm_cache.py"))
    nested = source / "nested" / "deeper"
    nested.mkdir(parents=True)
    target = nested / ("Project.toml" if extension == ".toml" else "policy" + extension)
    target.write_text("original")
    # Establish a stored proof for this fixture's source tree, never real Julia.
    saved.update(readiness.expected_identity("cpu", root, **query))
    state.write_state(saved, root)
    options = dict(julia_executable=str(julia), julia_threads=3, environment=query["environ"])
    assert readiness.backend_readiness("cpu", root, **query).ready
    before = manager.resolve_key("cpu", **options)
    parent_stamp = nested.stat().st_mtime_ns
    target.write_text("changed nested bytes")
    assert nested.stat().st_mtime_ns == parent_stamp
    assert readiness.backend_readiness("cpu", root, **query).state == "stale"
    after = manager.resolve_key("cpu", **options)
    assert after != before
    assert after[tree + "_fingerprint"] != before[tree + "_fingerprint"]


@pytest.mark.parametrize("change", ["add", "remove", "rename", "directory"])
def test_source_walk_observes_nested_membership(tmp_path, change):
    nested = tmp_path / "nested"
    nested.mkdir()
    target = nested / "policy.py"
    target.write_text("fixture")
    before = warm_cache.source_signature(tmp_path)
    if change == "add":
        (nested / "new.jl").write_text("fixture")
    elif change == "remove":
        target.unlink()
    elif change == "rename":
        target.rename(nested / "renamed.py")
    else:
        (nested / "empty").mkdir()
    assert warm_cache.source_signature(tmp_path) != before


def test_source_walk_timing(capsys):
    roots = (assets.engine_assets("cpu").root, Path(warm_cache.__file__).parent)
    samples = []
    for _ in range(20):
        start = perf_counter()
        signatures = [warm_cache.source_signature(root) for root in roots]
        samples.append((perf_counter() - start) * 1000)
    # Informational only: scheduling and cold filesystem caches vary by machine.
    with capsys.disabled():
        print(f"source stat walk: median={median(samples):.3f} ms entries={sum(map(len, signatures))}")


@pytest.mark.parametrize("bad_config", ["linked", "conflict", "corrupt"])
def test_bad_runtime_signature_falls_back_to_uncached_resolution(proved_cpu, monkeypatch, tmp_path, bad_config):
    root, _, julia, _, query, _, _ = proved_cpu
    env = dict(query["environ"])
    if bad_config == "linked":
        target = tmp_path / "linked-runtime"
        target.symlink_to(root, target_is_directory=True)
        env[paths.RUNTIME_DIR_ENV] = str(target)
    elif bad_config == "conflict":
        env["HORNLAB_BEAT_RUNTIME_DIR"] = str(root)
    else:
        (root / "julia.json").write_text("{")
    calls = []
    original = manager._resolve_key
    monkeypatch.setattr(manager, "_resolve_key", lambda *a, **kw: (calls.append(1), original(*a, **kw))[1])
    options = dict(julia_executable=str(julia), julia_threads=3, environment=env)
    if bad_config == "conflict":
        with pytest.raises(paths.RootConflict):
            manager.resolve_key("cpu", **options)
    else:
        assert manager.resolve_key("cpu", **options)["backend"] == "cpu"
    assert calls == [1]


@pytest.mark.parametrize("backend,negative", [("metal", "no-device"), ("metal", "detection-failed"),
                                             ("cuda", "unsupported"), ("rocm", "unsupported")])
def test_hardware_negative_cache_expires_and_refresh_clears(monkeypatch, tmp_path, backend, negative):
    readiness.probe_cache_clear(notify=False)
    now = [0.0]
    calls = []
    monkeypatch.setattr(readiness.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(warm_cache, "runtime_signature", lambda *a, **kw: (backend,))
    monkeypatch.setattr(readiness, "_prove_backend_readiness", lambda *a, **kw:
                        (calls.append(1), readiness.BackendReadiness(False, negative, "fixture"))[1])
    readiness.backend_readiness(backend, tmp_path)
    now[0] = 29.9
    readiness.backend_readiness(backend, tmp_path)
    assert calls == [1]
    now[0] = 30.0
    readiness.backend_readiness(backend, tmp_path)
    assert calls == [1, 1]
    readiness.backend_readiness(backend, tmp_path, force_refresh=True)
    assert calls == [1, 1, 1]
    readiness.probe_cache_clear(notify=False)
    readiness.backend_readiness(backend, tmp_path)
    assert calls == [1, 1, 1, 1]


@pytest.mark.parametrize("signature_error", [ValueError, paths.RootConflict])
def test_signature_errors_preserve_unavailable_bridge_semantics(monkeypatch, tmp_path, signature_error):
    from types import SimpleNamespace
    from server.solver import official_beat
    from server.solver.beat_runtime import discovery

    def signature(*a, **kw):
        raise signature_error("bad runtime configuration")
    def unavailable(*a, **kw):
        raise discovery.JuliaDiscoveryError("runtime unavailable")
    monkeypatch.setattr(warm_cache, "runtime_signature", signature)
    monkeypatch.setattr(manager, "_resolve_key", unavailable)
    monkeypatch.setattr(official_beat, "validated_negotiator", lambda *a: None)
    runtime = manager.WorkerManager(mode="host", directory=tmp_path / "workers")
    request = SimpleNamespace(wire={"solver_options": {"bem_backend": "cpu"}})
    before = warm_cache.generation()
    with pytest.raises(official_beat.OfficialBeatUnavailable, match="runtime unavailable"):
        official_beat.solve_compiled(request, channel_id="fixture", worker_manager=runtime)
    assert warm_cache.generation() > before


@pytest.mark.parametrize("cache", ["readiness", "key"])
def test_signature_failure_after_full_resolution_does_not_replace_verdict(monkeypatch, tmp_path, cache):
    calls = []
    def signature(*a, **kw):
        calls.append(1)
        if len(calls) % 2 == 0:
            raise ValueError("configuration changed during proof")
        return (cache, tmp_path)
    monkeypatch.setattr(warm_cache, "runtime_signature", signature)
    if cache == "readiness":
        expected = readiness.BackendReadiness(True, "ready", "fixture")
        monkeypatch.setattr(readiness, "_prove_backend_readiness", lambda *a, **kw: expected)
        assert readiness.backend_readiness("cpu", tmp_path) is expected
    else:
        monkeypatch.setattr(manager, "_resolve_key", lambda *a, **kw: {"fixture": True})
        assert manager.resolve_key("cpu") == {"fixture": True}
    assert len(calls) == 2


@pytest.mark.parametrize("backend", ["cuda", "rocm", "metal"])
def test_explicit_hardware_facts_bypass_cached_verdict(monkeypatch, tmp_path, backend):
    monkeypatch.setattr(warm_cache, "runtime_signature", lambda *a, **kw: pytest.fail("explicit facts were cached"))
    monkeypatch.setattr(readiness, "expected_identity", lambda *a, **kw: dict(julia_executable=None))
    missing = dict(available=False, reason="no fixture device")
    present = dict(available=True, reason="fixture device")
    assert readiness.backend_readiness(backend, tmp_path, hardware_facts=missing).state == "no-device"
    assert readiness.backend_readiness(backend, tmp_path, hardware_facts=present).state == "no-julia"


@pytest.mark.parametrize("backend", ["cuda", "rocm", "metal"])
def test_direct_hardware_refresh_revokes_gpu_verdict(monkeypatch, tmp_path, backend):
    from server.solver.beat_runtime import hardware

    calls = []
    monkeypatch.setattr(readiness, "_prove_backend_readiness", lambda *a, **kw:
                        (calls.append(1), readiness.BackendReadiness(False, "no-device", "fixture"))[1])
    readiness.backend_readiness(backend, tmp_path)
    readiness.backend_readiness(backend, tmp_path)
    assert calls == [1]
    hardware.clear_hardware_cache()
    readiness.backend_readiness(backend, tmp_path)
    assert calls == [1, 1]


@pytest.mark.parametrize("selection", ["argument", "environment"])
def test_external_project_nested_edit_revokes_verdict_and_key(proved_cpu, monkeypatch, tmp_path, selection):
    root, _, julia, saved, query, _, _ = proved_cpu
    project = tmp_path / "external-project"
    nested = project / "src" / "nested"
    nested.mkdir(parents=True)
    (project / "Project.toml").write_text('name = "Fixture"\n')
    target = nested / "solver.jl"
    target.write_text("original")
    options = dict(julia_executable=str(julia), julia_threads=3, environment=query["environ"])
    if selection == "argument":
        query["julia_project"] = project
        options["julia_project"] = project
    else:
        query["environ"]["JULIA_PROJECT"] = str(project)
    saved.update(readiness.expected_identity("cpu", root, **query))
    state.write_state(saved, root)
    proofs, keys = [], []
    prove, resolve = readiness._prove_backend_readiness, manager._resolve_key
    monkeypatch.setattr(readiness, "_prove_backend_readiness", lambda *a, **kw: (proofs.append(1), prove(*a, **kw))[1])
    monkeypatch.setattr(manager, "_resolve_key", lambda *a, **kw: (keys.append(1), resolve(*a, **kw))[1])
    assert readiness.backend_readiness("cpu", root, **query).ready
    assert readiness.backend_readiness("cpu", root, **query).ready
    before = manager.resolve_key("cpu", **options)
    assert manager.resolve_key("cpu", **options) == before
    assert proofs == keys == [1]
    parent_stamp = nested.stat().st_mtime_ns
    target.write_text("changed nested project source")
    assert nested.stat().st_mtime_ns == parent_stamp
    # JULIA_PROJECT does not override an explicit --project path, but must still
    # invalidate both caches because its selected environment is a launch input.
    after = manager.resolve_key("cpu", **options)
    if selection == "argument":
        assert readiness.backend_readiness("cpu", root, **query).state == "stale"
        assert after["engine_fingerprint"] != before["engine_fingerprint"]
    else:
        assert readiness.backend_readiness("cpu", root, **query).ready
    assert proofs == keys == [1, 1]


def test_warm_solve_shares_source_walks_and_next_solve_observes_edit(proved_cpu, monkeypatch):
    root, engine, julia, _, query, _, _ = proved_cpu
    options = dict(julia_executable=str(julia), julia_threads=3, environment=query["environ"])
    readiness.backend_readiness("cpu", root, **query)
    before = manager.resolve_key("cpu", **options)
    walked = []
    original = warm_cache.source_signature
    monkeypatch.setattr(warm_cache, "source_signature", lambda path: (walked.append(path), original(path))[1])

    @warm_cache.signature_scope
    def solve():
        verdict = readiness.backend_readiness("cpu", root, **query)
        return verdict, manager.resolve_key("cpu", **options)

    verdict, key = solve()
    assert verdict.ready and key == before
    assert walked == [engine.root, Path(warm_cache.__file__).parent]
    (engine.root / "__init__.py").write_text("changed between solves")
    verdict, key = solve()
    assert verdict.state == "stale" and key != before


def test_scope_keeps_postproof_walk_fresh(proved_cpu, monkeypatch):
    root, engine, _, _, query, _, _ = proved_cpu
    calls = []

    def changed_during_proof(*args, **kwargs):
        calls.append(1)
        (engine.root / "__init__.py").write_text("new source" * len(calls))
        return readiness.BackendReadiness(True, "ready", "fixture")

    monkeypatch.setattr(readiness, "_prove_backend_readiness", changed_during_proof)

    @warm_cache.signature_scope
    def solve():
        readiness.backend_readiness("cpu", root, **query)

    solve()
    solve()
    assert calls == [1, 1]


def test_source_walk_refuses_junction_before_descending(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from contextlib import nullcontext

    entry = SimpleNamespace(is_junction=lambda: True,
                            is_dir=lambda **kw: pytest.fail("descended into junction"))
    monkeypatch.setattr(warm_cache.os, "scandir", lambda path: nullcontext([entry]))
    with pytest.raises(ValueError, match="Linked identity directory"):
        warm_cache.source_signature(tmp_path)
