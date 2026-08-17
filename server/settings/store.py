"""Durable interface settings, kept out of the browser.

Every WG setting used to live in ``localStorage``.  That storage is scoped to
the exact origin the window was opened from, so a settings loss did not need a
bug to happen: the port scan moving off 3100, a browser configured to clear
site data on exit, opening ``localhost`` instead of ``127.0.0.1``, or simply a
different browser than last time were each enough to lose every preference at
once.  The application data directory has none of those failure modes, so it
is the authority and the browser copy is only a cache for first paint.

Namespace payloads are stored opaquely.  The frontend already owns the schema,
its validation, and its migrations; duplicating that here would create a second
definition to keep in step for no gain.  What this module does own is the
envelope, the size ceilings, and the atomic write.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from server.platform.paths import data_paths


logger = logging.getLogger(__name__)

SETTINGS_NAME = "ui_settings.json"
SCHEMA_VERSION = 1

#: A settings namespace is written by the frontend, so the name is constrained
#: to what can safely become a path-free JSON key and a URL segment.
NAMESPACE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")

#: Ceilings exist so a runaway writer cannot grow the file without bound. The
#: largest real namespace (preferences) is well under a kilobyte; the dockview
#: layout is the only one that approaches tens of kilobytes.
MAX_NAMESPACE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_NAMESPACES = 64


class SettingsError(ValueError):
    """A rejected write. The message is shown to the caller verbatim."""


def valid_namespace(name: str) -> bool:
    return bool(NAMESPACE_PATTERN.match(name))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Publish the settings file without ever exposing a torn read."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class SettingsStore:
    """The application's own copy of every remembered interface setting."""

    def __init__(self, data_dir: Path, *, settings_path: Path | None = None) -> None:
        self.settings_path = (
            Path(settings_path).resolve()
            if settings_path is not None
            else (data_paths(data_dir).root / SETTINGS_NAME).resolve()
        )
        self._namespaces: dict[str, Any] = {}
        self._loaded = False

    def _load(self) -> None:
        self._loaded = True
        try:
            raw = self.settings_path.read_text(encoding="utf-8")
        except OSError:
            return
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            # A corrupt file is not a reason to refuse to start. Defaults are a
            # working application; the bad file is kept so it can be inspected.
            logger.warning("Ignoring unreadable settings file: %s", self.settings_path)
            return
        if not isinstance(payload, dict):
            return
        namespaces = payload.get("namespaces")
        if not isinstance(namespaces, dict):
            return
        self._namespaces = {
            name: value for name, value in namespaces.items() if valid_namespace(str(name))
        }

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._load()

    def all(self) -> dict[str, Any]:
        self._ensure_loaded()
        return json.loads(json.dumps(self._namespaces))

    def get(self, namespace: str) -> Any | None:
        self._ensure_loaded()
        return self._namespaces.get(namespace)

    def envelope(self) -> dict[str, Any]:
        return {"schemaVersion": SCHEMA_VERSION, "namespaces": self.all()}

    def put(self, namespace: str, value: Any) -> dict[str, Any]:
        """Replace one namespace and persist the whole envelope."""

        if not valid_namespace(namespace):
            raise SettingsError(
                f"Invalid settings namespace {namespace!r}. Use letters, digits, '-' or '_'."
            )
        self._ensure_loaded()
        try:
            encoded = json.dumps(value)
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"Settings for {namespace!r} are not JSON-serialisable: {exc}") from exc
        if len(encoded.encode("utf-8")) > MAX_NAMESPACE_BYTES:
            raise SettingsError(
                f"Settings for {namespace!r} exceed {MAX_NAMESPACE_BYTES} bytes."
            )
        if namespace not in self._namespaces and len(self._namespaces) >= MAX_NAMESPACES:
            raise SettingsError(f"At most {MAX_NAMESPACES} settings namespaces are supported.")

        candidate = dict(self._namespaces)
        candidate[namespace] = json.loads(encoded)
        envelope = {"schemaVersion": SCHEMA_VERSION, "namespaces": candidate}
        if len(json.dumps(envelope).encode("utf-8")) > MAX_TOTAL_BYTES:
            raise SettingsError(f"Stored settings exceed {MAX_TOTAL_BYTES} bytes.")

        _write_json_atomic(self.settings_path, envelope)
        self._namespaces = candidate
        return envelope

    def delete(self, namespace: str) -> dict[str, Any]:
        self._ensure_loaded()
        if namespace in self._namespaces:
            candidate = {
                name: value for name, value in self._namespaces.items() if name != namespace
            }
            _write_json_atomic(
                self.settings_path,
                {"schemaVersion": SCHEMA_VERSION, "namespaces": candidate},
            )
            self._namespaces = candidate
        return self.envelope()


__all__ = [
    "MAX_NAMESPACES",
    "MAX_NAMESPACE_BYTES",
    "MAX_TOTAL_BYTES",
    "SCHEMA_VERSION",
    "SETTINGS_NAME",
    "SettingsError",
    "SettingsStore",
    "valid_namespace",
]
