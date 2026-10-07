"""Content identities for adoption/readiness, distinct from path-specific keys.

Names are relative to logical roots, so byte-identical relocation is stable.
Optional stat-keyed byte caching avoids re-reading unchanged files; trees are
always enumerated again so edits and additions revoke identity.
Missing or unreadable required inputs raise instead of hashing a sentinel.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import os
from pathlib import Path

from .assets import EngineAssets, engine_assets


class IdentityUnavailable(RuntimeError):
    """Required identity input cannot be enumerated or read."""


def _identity_file(path: Path) -> bool:
    return (
        path.suffix in {".py", ".jl", ".json"}
        or path.name in {"Project.toml", "JuliaProject.toml"}
        or (path.name.startswith(("Manifest", "JuliaManifest")) and path.suffix == ".toml")
    )


def _files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise IdentityUnavailable(f"Missing identity directory: {root}")

    def fail(exc: OSError) -> None:
        raise IdentityUnavailable(f"Cannot enumerate identity directory: {root}") from exc

    files = []
    for directory, dirs, names in os.walk(root, onerror=fail):
        # Never silently omit a linked source tree (or follow a directory cycle).
        for name in dirs:
            if (Path(directory) / name).is_symlink():
                raise IdentityUnavailable(f"Linked identity directory: {Path(directory) / name}")
        files.extend(Path(directory) / name for name in names if _identity_file(Path(name)))
    return sorted(files)


def _file_digest(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.digest()


@lru_cache(maxsize=4096)
def _cached_digest(path: Path, mtime_ns: int, size: int, inode: int) -> bytes:
    return _file_digest(path)


def file_digest(path: Path, *, cache: bool = False) -> bytes:
    """Cache bytes by resolved path, modification time and size when requested."""
    try:
        path = path.resolve()
        if cache:
            stat = path.stat()
            return _cached_digest(path, stat.st_mtime_ns, stat.st_size, stat.st_ino)
        return _file_digest(path)
    except OSError as exc:
        raise IdentityUnavailable(f"Cannot read identity input: {path}") from exc


def _fingerprint(files: dict[str, Path], *, cache: bool = False) -> str:
    digest = hashlib.sha256()
    for name, path in sorted(files.items()):
        content = file_digest(path, cache=cache)
        label = name.encode("utf-8")
        digest.update(len(label).to_bytes(4, "big"))
        digest.update(label)
        digest.update(content)
    return digest.hexdigest()


def _tree(root: Path, namespace: str) -> dict[str, Path]:
    return {f"{namespace}/{p.relative_to(root).as_posix()}": p for p in _files(root)}


def engine_fingerprint(
    assets: EngineAssets | None = None, *, backend: str = "cpu",
    julia_project: Path | None = None, julia_sysimage: Path | None = None,
    cache: bool = False,
) -> str:
    """Hash shared engine sources and the selected backend project/manifests.

    Explicit project and sysimage bytes participate too. Their resolved paths,
    executable and environment belong in the future host key, not this hash.
    """
    assets = engine_assets(backend) if assets is None else assets
    files = _tree(assets.root, "engine")
    projects = {"cpu": "julia_local", "metal": "julia_metal", "cuda": "julia_cuda", "rocm": "julia_rocm"}
    for name in tuple(files):
        path = files[name]
        if path.suffix != ".toml":
            continue
        parts = path.relative_to(assets.root).parts
        for other, project in projects.items():
            if other != backend and (project in parts or any(
                part in {f"BeatEngine{other.title()}Bundle", f"Compiled{other.title()}"} for part in parts
            )):
                del files[name]
                break
    # Require entry points/schema even when an incomplete wheel omitted them.
    for path in (assets.root / "__init__.py", assets.root / "beat_contract/system-v1.schema.json",
                 assets.project / "Project.toml", assets.system_solver, assets.source_solver):
        files[f"required/{path.relative_to(assets.root).as_posix()}"] = path
    if julia_project is not None:
        project = Path(julia_project)
        if not project.exists():
            raise IdentityUnavailable(f"Missing selected project: {project}")
        root = project if project.is_dir() else project.parent
        files.update(_tree(root, "selected-project"))
        files["selected-project/Project.toml"] = root / "Project.toml"
        if not project.is_dir():
            files[f"selected-project/{project.name}"] = project
    if julia_sysimage is not None:
        files["selected-sysimage"] = Path(julia_sysimage)
    return _fingerprint(files, cache=cache)


def runtime_fingerprint(
    runtime_root: Path | None = None, *, compiled_request_policy: Path | None = None, cache: bool = False,
) -> str:
    """Hash WG runtime separately, including any selected compiled policy."""
    root = Path(__file__).resolve().parent if runtime_root is None else Path(runtime_root)
    files = _tree(root, "wg-runtime")
    files["wg-runtime/__init__.py"] = root / "__init__.py"
    if compiled_request_policy is not None:
        files["compiled-request-policy"] = Path(compiled_request_policy)
    return _fingerprint(files, cache=cache)
