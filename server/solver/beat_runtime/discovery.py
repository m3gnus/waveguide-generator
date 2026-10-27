"""Discover executables without claiming backend readiness."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any

from . import paths, state
from .paths import runtime_dir

JULIA_ENV_VAR = "WG2_BEAT_JULIA"


class JuliaDiscoveryError(ValueError):
    """An explicitly selected executable is invalid."""


def executable_file(path: Path) -> bool:
    return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))


def executable_identity(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return record if isinstance(record, dict) else None


def read_julia_record(root: Path | None = None) -> dict[str, Any] | None:
    return state.read_julia(root)


def write_julia_record(
    root: Path, executable: Path, *, origin: str, version: str | None,
    selection: str | None = None,
) -> None:
    """Atomically record executable provenance, independently of backend state."""
    root = paths.checked_root(root)
    executable = executable.expanduser().absolute()
    record = {
        "executable": str(executable), "version": version,
        "origin": origin, "identity": executable_identity(executable),
    }
    if selection is not None:
        record["selection"] = selection
    state.write_julia(record, root)


def recorded_julia(
    root: Path | None = None, *, environ: Mapping[str, str] | None = None,
) -> str | None:
    record = read_julia_record(root)
    if not record:
        return None
    path = Path(record["executable"]).expanduser()
    try:
        if (not paths.hbb_executable(path, environ=environ)
                and executable_file(path) and record["identity"] == executable_identity(path)):
            return str(path)
    except OSError:
        pass
    return None


def _path_julia(env: Mapping[str, str]) -> str | None:
    """The first PATH Julia WG can read and hash.

    juliaup and the Microsoft Store install ``julia.exe`` as an App Execution
    Alias under WindowsApps. Opening that reparse point fails (OSError 22), so
    it, and any other unreadable candidate, falls through to the next entry.
    """
    seen: set[str] = set()
    for directory in env.get("PATH", os.defpath).split(os.pathsep):
        candidate = shutil.which("julia", path=directory) if directory else None
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        path = Path(candidate)
        if (paths.hbb_executable(path, environ=env) or paths.reparse_point(path)
                or any(part.lower() == "windowsapps" for part in path.parts)):
            continue
        try:
            executable_identity(path)
        except OSError:
            continue
        return candidate
    return None


def discover_julia(
    explicit: str | None = None, *, configured: str | None = None,
    root: Path | None = None, environ: Mapping[str, str] | None = None,
    include_path: bool = True,
) -> str | None:
    """Resolve explicit → configured → WG-recorded → WG's pinned → PATH; never run Julia.

    ``include_path=False`` is for provisioning, which installs WG's pinned
    Julia rather than adopting whichever Julia PATH happens to name.
    """
    env = os.environ if environ is None else environ
    configured = configured if configured is not None else env.get(JULIA_ENV_VAR)
    for source, candidate in (("explicit", explicit), ("configured", configured)):
        if candidate and candidate.strip():
            path = Path(candidate.strip()).expanduser().absolute()
            if not executable_file(path):
                raise JuliaDiscoveryError(f"Invalid {source} Julia executable: {path}")
            return None if paths.hbb_executable(path, environ=env) else str(path)
    directory = runtime_dir(environ=env) if root is None else root
    record = read_julia_record(directory)
    # Installer-only explicit choices remain one-off. Once provisioning has
    # proved a backend with that executable, it is the runtime's saved choice.
    # Readiness still validates the entire launch identity and compiled proof.
    provisioned = record and any(
        backend["status"] == "ready"
        and backend["julia_executable"] == record["executable"]
        and backend["julia_identity"] == record["identity"]
        for backend in state.read_backend_states(directory).values()
    )
    # A PATH Julia adopted by an earlier WG is kept only while it is proved.
    recorded = (None if record and record.get("selection") in {"explicit", "path"} and not provisioned
                else recorded_julia(directory, environ=env))
    if recorded is not None:
        return recorded
    from .installer import pinned_julia

    pinned = pinned_julia(directory)
    if pinned is not None:
        return str(pinned)
    return _path_julia(env) if include_path else None
