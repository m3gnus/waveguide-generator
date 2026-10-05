"""Locate fork-owned assets through its public API, without HBB fallback."""

from dataclasses import dataclass
from pathlib import Path


class AssetsUnavailable(RuntimeError):
    """The optional engine or required wheel assets are unavailable."""


@dataclass(frozen=True)
class EngineAssets:
    root: Path
    project: Path
    system_solver: Path
    source_solver: Path


def engine_assets(backend: str = "cpu") -> EngineAssets:
    """Resolve and validate assets; no Julia launch or provisioning occurs."""
    try:
        from beat_engine import engine_paths
    except ImportError as exc:
        raise AssetsUnavailable("Optional beat-engine package is not importable") from exc
    paths = engine_paths(backend)
    assets = EngineAssets(*(Path(getattr(paths, name)).resolve() for name in (
        "root", "project", "system_solver", "source_solver",
    )))
    for path in (assets.project / "Project.toml", assets.system_solver, assets.source_solver):
        if not path.is_file():
            raise AssetsUnavailable(f"beat-engine is missing required asset: {path}")
    return assets


def default_project(backend: str = "cpu") -> Path:
    return engine_assets(backend).project
