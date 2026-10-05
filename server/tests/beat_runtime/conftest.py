from __future__ import annotations

import pytest

from server.solver.beat_runtime import assets, installer, paths, provision


@pytest.fixture
def cpu_provisioning(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.RUNTIME_DIR_ENV, str(tmp_path / "wg"))
    root = paths.runtime_dir()
    engine = tmp_path / "engine"
    project = engine / "cpu"
    project.mkdir(parents=True)
    (engine / "beat_contract").mkdir()
    for name in ("__init__.py", "beat_contract/system-v1.schema.json", "cpu/Project.toml", "cpu/solver.jl"):
        (engine / name).write_text("fixture")
    selected = assets.EngineAssets(engine, project, project / "solver.jl", project / "solver.jl")
    monkeypatch.setattr(assets, "engine_assets", lambda backend: selected)
    julia = tmp_path / "julia"
    julia.write_bytes(b"fake Julia")
    julia.chmod(0o755)
    calls = []

    def step(executable, code, **kwargs):
        calls.append((code, kwargs))
        kwargs["status_cb"]("Pkg progress \u2713")

    def probe(**kwargs):
        calls.append(("probe", kwargs))
        kwargs["status_cb"]("compiled solve")
        return {"finite": True, "nonzero": True, "terminal_count": 1}

    def forbidden(*args, **kwargs):
        raise AssertionError("real Julia or download forbidden")

    monkeypatch.setattr(installer, "download_archive", forbidden)
    monkeypatch.setattr(provision.julia_steps.subprocess, "Popen", forbidden)
    options = dict(
        environ={"PATH": "", paths.RUNTIME_DIR_ENV: str(tmp_path / "wg"), "SECRET": "not persisted"},
        julia_executable=str(julia), julia_threads=3, run_step=step, probe=probe,
        probe_contract="system-v1", probe_fixture_identity="fixture-sha", status_cb=lambda _: None,
    )
    return root, selected, julia, calls, options
