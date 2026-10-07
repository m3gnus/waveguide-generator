"""Cheap warm-path signatures; full content proofs remain the cache miss policy.

Source metadata is observed recursively so editable installs revoke cached
proofs on nested edits too. No source contents are read on the warm path; full
content identity and depot checks remain mandatory on every cache miss.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import shutil
import threading
from typing import Any

from . import assets, discovery, hardware, locks, paths, probe, state

_lock = threading.Lock()
_generation = 0


def invalidate() -> None:
    global _generation
    with _lock:
        _generation += 1


def generation() -> int:
    with _lock:
        return _generation


def file_signature(path: Path) -> tuple:
    """Observe both a link and its destination, including replacement inodes."""
    path = path.expanduser().absolute()
    try:
        link, target = path.lstat(), path.stat()
        return (str(path), *((s.st_mtime_ns, s.st_size, s.st_ino, s.st_mode) for s in (link, target)))
    except FileNotFoundError:
        return (str(path), None)


def source_signature(root: Path) -> tuple:
    """Stat directories and source inputs with scandir, without hashing bytes."""
    observed = []
    pending = [os.fspath(root)]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(entry.path)
                elif entry.is_symlink() and entry.is_dir():
                    # The full identity proof refuses linked source trees too.
                    raise ValueError("Linked identity directory")
                elif not entry.name.endswith((".py", ".jl", ".toml", ".json")):
                    continue
                stat = entry.stat()
                observed.append((entry.path, stat.st_mtime_ns, stat.st_size, stat.st_ino))
    return tuple(sorted(observed))


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(sorted((key, _freeze(member)) for key, member in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(member) for member in value)
    return str(value) if isinstance(value, Path) else value


def runtime_signature(backend: str, directory: Path | None = None, **options: Any) -> tuple:
    """No hashes, depot checks or provisioning locks on a hit."""
    env = dict(options.get("environ", options.get("environment")) or {})
    if options.get("environ", options.get("environment")) is None:
        env = dict(os.environ)
    root = paths.runtime_dir(environ=env) if directory is None else paths.checked_root(directory, environ=env)
    engine = assets.engine_assets(backend)
    record = state.read_julia(root)
    executable = (options.get("julia_executable") or env.get(discovery.JULIA_ENV_VAR)
                  or (record or {}).get("executable") or shutil.which("julia", path=env.get("PATH", os.defpath)))
    runtime = Path(__file__).parent
    watched = [root, root / f"state-{backend}.json", root / "julia.json",
               root / locks.HOLDER_FILENAME, root / locks.LOCK_FILENAME,
               engine.root, runtime, engine.root / "__init__.py",
               engine.root / "beat_contract" / "system-v1.schema.json",
               engine.system_solver, engine.source_solver, probe._FIXTURE]
    # The other backend's saved proof affects recorded executable selection.
    watched.extend(root / f"state-{name}.json" for name in ("cpu", "metal", "cuda", "rocm"))
    if executable:
        watched.append(Path(executable))
    # A recorded binary may fail its full identity check and discovery then
    # selects PATH. Observe both candidates without hashing either one.
    fallback = shutil.which("julia", path=env.get("PATH", os.defpath))
    if fallback:
        watched.append(Path(fallback))
    project = Path(options.get("julia_project") or engine.project)
    projects = [project]
    if env.get("JULIA_PROJECT") and not env["JULIA_PROJECT"].startswith("@"):
        projects.append(Path(env["JULIA_PROJECT"]))
    for selected in projects:
        selected = selected.expanduser()
        watched.extend([selected, selected / "Project.toml", *selected.glob("*Manifest*.toml")])
    for name in ("julia_sysimage", "solver_script", "compiled_request_policy", "depot"):
        if options.get(name) is not None:
            watched.append(Path(options[name]))
    if options.get("compiled_request_policy") is None:
        watched.append(runtime.parent / "official_beat.py")
    relevant = {key: value for key, value in env.items()
                if key.startswith(("JULIA_", "BLAB_", "WG2_", "HORNLAB_", "XDG_"))
                or key in {"PATH", "HOME", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "TMPDIR", "TEMP"}}
    settings = {key: value for key, value in options.items() if key not in {"environ", "environment"}}
    return (generation(), hardware.cache_generation(), backend, str(root.absolute()), str(Path.cwd()),
            _freeze(relevant), _freeze(settings), tuple(file_signature(path) for path in watched),
            source_signature(engine.root), source_signature(runtime))
