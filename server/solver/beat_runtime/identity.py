"""Content identities for adoption/readiness, distinct from path-specific keys.

Names are relative to logical roots, so byte-identical relocation is stable.
There is no process-lifetime cache: an in-place edit must revoke identity.
Missing or unreadable required inputs raise instead of hashing a sentinel.
"""

import hashlib
import os
from pathlib import Path

from .assets import EngineAssets, engine_assets


class IdentityUnavailable(RuntimeError):
    """Required identity input cannot be enumerated or read."""


def _identity_file(path: Path) -> bool:
    return (
        path.suffix in {".py", ".jl", ".json"}
        or path.name == "Project.toml"
        or (path.name.startswith("Manifest") and path.suffix == ".toml")
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


def _fingerprint(files: dict[str, Path]) -> str:
    digest = hashlib.sha256()
    for name, path in sorted(files.items()):
        content = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    content.update(chunk)
        except OSError as exc:
            raise IdentityUnavailable(f"Cannot read identity input: {path}") from exc
        label = name.encode("utf-8")
        digest.update(len(label).to_bytes(4, "big"))
        digest.update(label)
        digest.update(content.digest())
    return digest.hexdigest()


def _tree(root: Path, namespace: str) -> dict[str, Path]:
    return {f"{namespace}/{p.relative_to(root).as_posix()}": p for p in _files(root)}


def engine_fingerprint(
    assets: EngineAssets | None = None, *, backend: str = "cpu",
    julia_project: Path | None = None, julia_sysimage: Path | None = None,
) -> str:
    """Hash fork Python/contracts, Julia and every bundled project/manifest.

    Explicit project and sysimage bytes participate too. Their resolved paths,
    executable and environment belong in the future host key, not this hash.
    """
    assets = engine_assets(backend) if assets is None else assets
    files = _tree(assets.root, "engine")
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
    return _fingerprint(files)


def runtime_fingerprint(
    runtime_root: Path | None = None, *, compiled_request_policy: Path | None = None,
) -> str:
    """Hash WG runtime separately, including any selected compiled policy."""
    root = Path(__file__).resolve().parent if runtime_root is None else Path(runtime_root)
    files = _tree(root, "wg-runtime")
    files["wg-runtime/__init__.py"] = root / "__init__.py"
    if compiled_request_policy is not None:
        files["compiled-request-policy"] = Path(compiled_request_policy)
    return _fingerprint(files)
