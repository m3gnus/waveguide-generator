"""Discover executables without claiming backend readiness."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from .paths import PROVIDER_ID, STATE_SCHEMA, runtime_dir

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
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


# TODO: use state.py's executable record accessors when that parallel PR lands.
def read_julia_record(root: Path | None = None) -> dict[str, Any] | None:
    record = _read_json((runtime_dir() if root is None else root) / "julia.json")
    if not record or record.get("provider") != PROVIDER_ID or record.get("state_schema") != STATE_SCHEMA:
        return None
    return record


def write_julia_record(root: Path, executable: Path, *, origin: str, version: str | None) -> None:
    """Atomically record executable provenance, independently of backend state."""
    record = {
        "provider": PROVIDER_ID, "state_schema": STATE_SCHEMA,
        "julia_executable": str(executable.resolve()), "julia_version": version,
        "origin": origin, "julia_identity": executable_identity(executable),
    }
    root.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".julia-", suffix=".json", dir=root)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / "julia.json")
    finally:
        temporary.unlink(missing_ok=True)


def recorded_julia(root: Path | None = None) -> str | None:
    record = read_julia_record(root)
    if not record or not isinstance(record.get("julia_executable"), str):
        return None
    path = Path(record["julia_executable"]).expanduser()
    try:
        if executable_file(path) and record.get("julia_identity") == executable_identity(path):
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
            return str(path)
    recorded = recorded_julia(runtime_dir(environ=env) if root is None else root)
    if recorded is not None:
        return recorded
    return shutil.which("julia", path=env.get("PATH", os.defpath))


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
