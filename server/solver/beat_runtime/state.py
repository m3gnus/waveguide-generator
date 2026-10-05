"""Atomic WG executable and backend records; readiness is decided separately.

Backend records retain resolved paths, content identities, the effective depot
and environment, probe contract/fixture identity and completion evidence.
Callers supply these values; storage does not resolve or prove them. Unknown
identity fields are explicit nulls, with an empty completion object until probed.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any

from . import paths

# Identity fields may be unknown during a failed or interrupted provisioning.
# A readiness verdict must match them and validate completion evidence (PR 13).
_IDENTITY_FIELDS = (
    "project", "engine_fingerprint", "runtime_fingerprint", "julia_executable",
    "julia_version", "julia_identity", "depot", "sysimage", "sysimage_identity",
    "probe_contract", "probe_fixture_identity",
)
_STATUSES = ("in_progress", "ready", "failed", "skipped")
_BACKENDS = ("cpu", "metal")
_WINDOWS = os.name == "nt"
_REPLACE_ATTEMPTS = 5


def _directory(directory: Path | None) -> Path:
    return paths.runtime_dir() if directory is None else paths.checked_root(directory)


def backend_state_path(directory: Path | None = None, *, backend: str) -> Path:
    """Select one backend file, refusing unknown or unsafe backend names."""
    if backend not in _BACKENDS:
        raise ValueError(f"Unsupported BEAT runtime backend: {backend!r}")
    return _directory(directory) / f"state-{backend}.json"


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-JSON numeric constant: {value}")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (OSError, ValueError, RecursionError):
        return None
    return raw if isinstance(raw, dict) else None


def _owned(record: dict[str, Any]) -> bool:
    return (
        record.get("provider") == paths.PROVIDER_ID
        and type(record.get("state_schema")) is int
        and record["state_schema"] == paths.STATE_SCHEMA
        and isinstance(record.get("updated_at"), str)
    )


def _backend_valid(record: dict[str, Any], backend: str) -> bool:
    return (
        _owned(record)
        and record.get("backend") == backend
        and record.get("status") in _STATUSES
        and isinstance(record.get("step"), str)
        and "error" in record and isinstance(record["error"], (str, type(None)))
        and all(name in record and isinstance(record[name], (str, type(None)))
                for name in _IDENTITY_FIELDS)
        and isinstance(record.get("environment"), dict)
        and all(isinstance(k, str) and isinstance(v, str)
                for k, v in record["environment"].items())
        and isinstance(record.get("completion"), dict)
    )


def _julia_valid(record: dict[str, Any]) -> bool:
    return (
        _owned(record)
        and record.get("origin") in ("managed", "external")
        and record.get("selection") in (None, "explicit", "configured", "path")
        and all(isinstance(record.get(name), str) and bool(record[name])
                for name in ("executable", "identity"))
        and "version" in record
        and (record["version"] is None and record["origin"] == "external"
             or isinstance(record["version"], str) and bool(record["version"]))
    )


def _stamped(record: Mapping[str, Any]) -> dict[str, Any]:
    # Never silently relabel a foreign or future-schema record as our own.
    for name, expected in (("provider", paths.PROVIDER_ID), ("state_schema", paths.STATE_SCHEMA)):
        if name in record and (type(record[name]) is not type(expected) or record[name] != expected):
            raise ValueError(f"Invalid BEAT record {name}")
    return dict(record, provider=paths.PROVIDER_ID, state_schema=paths.STATE_SCHEMA,
                updated_at=datetime.now(timezone.utc).isoformat())


def _atomic_write_json(path: Path, record: Mapping[str, Any]) -> None:
    """Replace one complete record via a private, unique sibling temporary."""
    paths.checked_root(path.parent)
    payload = json.dumps(record, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Unique siblings can survive a killed writer. Do not sweep active writers.
    for stale in path.parent.glob(f".{path.name}.*.tmp"):
        with suppress(OSError):
            if not paths.is_link(stale) and stale.is_file() and stale.stat().st_mtime < time.time() - 86400:
                stale.unlink(missing_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    scratch = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(scratch, path)
                break
            except PermissionError:
                # Windows readers can briefly deny replacement of an open file.
                if not _WINDOWS or attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(0.025 * (attempt + 1))
    finally:
        scratch.unlink(missing_ok=True)


def read_state(directory: Path | None = None, *, backend: str) -> dict[str, Any] | None:
    """Read only the named backend; missing, corrupt or foreign records are absent."""
    record = _read_json(backend_state_path(directory, backend=backend))
    return record if record is not None and _backend_valid(record, backend) else None


def read_backend_states(directory: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return valid CPU/Metal records independently, without a legacy mirror."""
    return {backend: record for backend in _BACKENDS
            if (record := read_state(directory, backend=backend)) is not None}


def write_state(record: Mapping[str, Any], directory: Path | None = None) -> dict[str, Any]:
    """Write one backend without changing Julia discovery or another backend."""
    backend = record.get("backend")
    target = backend_state_path(directory, backend=backend)
    stamped = _stamped(record)
    if not _backend_valid(stamped, backend):
        raise ValueError("Invalid BEAT backend record")
    _atomic_write_json(target, stamped)
    return stamped


def read_julia(directory: Path | None = None) -> dict[str, Any] | None:
    """Read executable origin/version/identity, without asserting readiness."""
    record = _read_json(_directory(directory) / "julia.json")
    return record if record is not None and _julia_valid(record) else None


def write_julia(record: Mapping[str, Any], directory: Path | None = None) -> dict[str, Any]:
    """Record a verified managed or external executable independently of backends."""
    stamped = _stamped(record)
    if not _julia_valid(stamped):
        raise ValueError("Invalid BEAT Julia record")
    _atomic_write_json(_directory(directory) / "julia.json", stamped)
    return stamped
