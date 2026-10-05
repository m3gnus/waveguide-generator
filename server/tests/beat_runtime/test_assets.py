import builtins
import sys
from types import SimpleNamespace

import pytest

from server.solver.beat_runtime import assets


@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    root = tmp_path / "wheel/beat_engine"
    project = root / "julia_local"
    project.mkdir(parents=True)
    paths = SimpleNamespace(root=root, project=project, system_solver=project / "coupled_solver.jl",
                            source_solver=project / "solver.jl")
    for path in (project / "Project.toml", paths.system_solver, paths.source_solver):
        path.write_text("fixture")
    calls = []
    monkeypatch.setitem(sys.modules, "beat_engine", SimpleNamespace(
        engine_paths=lambda backend: calls.append(backend) or paths,
    ))
    return paths, calls


def test_public_api_selects_resolved_project(fake_engine):
    paths, calls = fake_engine
    assert assets.default_project("metal") == paths.project.resolve()
    assert calls == ["metal"]
    assert assets.engine_assets().system_solver == paths.system_solver.resolve()


@pytest.mark.parametrize("name", ["Project.toml", "coupled_solver.jl", "solver.jl"])
def test_missing_wheel_assets_are_errors(fake_engine, name):
    paths, _ = fake_engine
    (paths.project / name).unlink()
    with pytest.raises(assets.AssetsUnavailable, match="missing required asset"):
        assets.engine_assets()


def test_optional_import_is_lazy_and_never_falls_back_to_hbb(monkeypatch):
    original = builtins.__import__
    attempted = []

    def deny(name, *args, **kwargs):
        if name in {"beat_engine", "hornlab_beat_bem"}:
            attempted.append(name)
            raise ModuleNotFoundError(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", deny)
    import importlib
    from server.solver import beat_runtime
    importlib.reload(beat_runtime)
    importlib.reload(assets)
    assert attempted == []
    with pytest.raises(assets.AssetsUnavailable, match="Optional beat-engine"):
        assets.default_project()
    assert attempted == ["beat_engine"]
