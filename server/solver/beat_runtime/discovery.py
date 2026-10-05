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


def discover_julia(
    explicit: str | None = None, *, configured: str | None = None,
    root: Path | None = None, environ: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve explicit → configured → WG-recorded → PATH; never run Julia."""
    env = os.environ if environ is None else environ
    configured = configured if configured is not None else env.get(JULIA_ENV_VAR)
    for source, candidate in (("explicit", explicit), ("configured", configured)):
        if candidate and candidate.strip():
            path = Path(candidate.strip()).expanduser()
            if not executable_file(path):
                raise JuliaDiscoveryError(f"Invalid {source} Julia executable: {path}")
            return None if paths.hbb_executable(path, environ=env) else str(path)
    directory = runtime_dir(environ=env) if root is None else root
    record = read_julia_record(directory)
    recorded = None if record and record.get("selection") == "explicit" else recorded_julia(directory, environ=env)
    if recorded is not None:
        return recorded
    candidate = shutil.which("julia", path=env.get("PATH", os.defpath))
    return candidate if candidate and not paths.hbb_executable(Path(candidate), environ=env) else None


def legacy_executable_hint(legacy_root: Path) -> str | None:
    """Read an opt-in HBB executable hint; legacy status is never WG readiness."""
    for name in ("state-cpu.json", "state-metal.json", "state.json"):
        record = _read_json(legacy_root / name)
        candidate = record.get("julia_executable") if record else None
        if isinstance(candidate, str) and candidate.strip():
            path = Path(candidate).expanduser()
            if executable_file(path):
                return str(path)
    return None
