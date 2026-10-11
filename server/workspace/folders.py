"""User-visible folders, migrated without moving existing content.

Mirrors are published first; folders.json is the final atomic publication.
Read failures disable publication, including legacy adoption, for this start.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from server.platform.paths import same_folder, validate_folder_root

log = logging.getLogger(__name__)
NAMES = {"runs": ("workspace_settings.json", "workspacePath"),
         "cadlink": ("cadlink_settings.json", "cadLinkPath")}
RUNS_SELECTED_KEY = "workspacePathSelected"


def read_settings(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def stored_path(value: Any) -> Path | None:
    return Path(value.strip()).expanduser() if isinstance(value, str) and value.strip() and "\x00" not in value else None


def read_folder_settings(data_dir: Path) -> tuple[dict[str, dict[str, Any]], bool]:
    """Read everything before any writer (including legacy adoption) runs."""
    settings = {}
    failed = False
    for file in ("folders.json", *(file for file, _ in NAMES.values())):
        try:
            settings[file] = read_settings(data_dir / file)
        except OSError as exc:
            settings[file] = {}
            failed = True
            log.warning("Folder settings are read-only: cannot read %s: %s", data_dir / file, exc)
    return settings, failed


class FolderRoot:
    def __init__(self, data_dir: Path, *, default_root: Path, legacy_runs: Path,
                 writer: Callable[[Path, dict[str, Any]], None], system: str | None = None,
                 create_implicit_runs: bool = False, settings_read_failed: bool = False,
                 explicit_runs: bool = False) -> None:
        self.data_dir = data_dir
        self.settings_path = data_dir / "folders.json"
        self.default_root = default_root
        self.system = system
        self.writer = writer
        self.on_change: Callable[[], None] = lambda: None
        self.notice: str | None = None
        settings, failed = read_folder_settings(data_dir)
        failed |= settings_read_failed
        payload = settings["folders.json"]
        unsupported = "schemaVersion" in payload and (type(payload["schemaVersion"]) is not int or payload["schemaVersion"] != 1)
        self.read_only = failed or unsupported
        self._extra: dict[str, Any] = {}
        self._unknown_overrides: dict[str, Any] = {}
        mirrors = {name: stored_path(settings[file].get(key)) for name, (file, key) in NAMES.items()}
        mirrors = {name: value.absolute() if value is not None else None for name, value in mirrors.items()}
        root = stored_path(payload.get("rootPath"))
        overrides = payload.get("overrides", {})
        valid = (not unsupported and payload.get("schemaVersion") == 1
                 and root is not None and root.is_absolute() and isinstance(overrides, dict)
                 and all(stored_path(value) is not None and stored_path(value).is_absolute()
                         for name, value in overrides.items() if name in NAMES))
        fresh = not payload and not any(settings[file] for file, _ in NAMES.values()) and not legacy_runs.exists()
        if valid:
            self.root = root
            self._extra = {key: value for key, value in payload.items() if key not in {"schemaVersion", "rootPath", "overrides"}}
            self._unknown_overrides = {name: value for name, value in overrides.items() if name not in NAMES}
            self.overrides = {name: stored_path(value) for name, value in overrides.items() if name in NAMES}
            drift = [name for name, selected in mirrors.items()
                     if selected is not None and str(selected) != str(self.effective(name))]
            # Both mirrors may have landed before a root-change publication
            # failed. Their shared recorded parent is already the chosen root.
            paired = self._paired_root(mirrors["runs"], mirrors["cadlink"])
            if set(drift) == set(NAMES) and paired is not None:
                self.root = paired
                self.overrides = {name: path for name, path in mirrors.items()
                                  if path is not None and str(path) != str(self.root / name)}
            else:
                for name in drift:
                    self.overrides[name] = mirrors[name]
            if drift:
                self.notice = "Folder choices from an earlier WG version were kept: " + ", ".join(drift) + "."
                log.warning(self.notice)
        else:
            # The caller supplies the exact 0.3.5 implicit runs path, including
            # USERPROFILE Documents and <data>/workspace for data overrides.
            runs = mirrors["runs"] or legacy_runs
            cad = mirrors["cadlink"]
            self.root = self._paired_root(runs, cad) or default_root
            self.overrides: dict[str, Path] = {}
            for name, selected in (("runs", runs), ("cadlink", cad)):
                if selected is not None and str(selected) != str(self.root / name):
                    self.overrides[name] = selected
        self._cleanup_overrides()
        # Legacy mirrors have no marker: their path was a choice or adoption.
        # New implicit mirrors retain the distinction across backend restarts.
        recorded_runs = ((mirrors["runs"] is not None or valid)
                         and settings["workspace_settings.json"].get(RUNS_SELECTED_KEY) is not False)
        self.runs_selected = explicit_runs or recorded_runs
        self.implicit_runs_path = (legacy_runs if not self.read_only
                                   and not recorded_runs
                                   and self.effective("runs") == legacy_runs else None)
        if self.read_only:
            self.notice = ("Folder settings could not be read; folder changes are disabled for this start." if failed else
                           "This WG version uses the existing folder settings; folders.json has an unsupported schema.")
            log.warning(self.notice)
        else:
            # Never create adopted or recorded selections. Only a genuinely
            # fresh default, or an explicit caller's implicit output choice.
            if fresh and str(legacy_runs) == str(default_root / "runs"):
                self._create_children()
            elif create_implicit_runs and self.implicit_runs_path is not None and not self.effective("runs").exists():
                self.effective("runs").mkdir(parents=True, exist_ok=True)
            self.persist()

    def _paired_root(self, runs: Path | None, cad: Path | None) -> Path | None:
        if (runs is not None and cad is not None
                and str(runs.parent) == str(cad.parent)
                and self.same(runs, runs.parent / "runs")
                and self.same(cad, cad.parent / "cadlink")):
            return runs.parent
        return None

    def same(self, left: Path, right: Path) -> bool:
        return same_folder(left, right, system=self.system)

    def effective(self, name: str) -> Path:
        return self.overrides.get(name, self.root / name)

    def _cleanup_overrides(self, *, selected: bool = False) -> None:
        for name, path in list(self.overrides.items()):
            # Startup must retain even spelling-only rollback drift. An
            # explicit root choice may clear aliases of that chosen child.
            if str(path) == str(self.root / name) or (selected and self.same(path, self.root / name)):
                del self.overrides[name]

    def _create_children(self) -> None:
        for name in NAMES:
            if name not in self.overrides:
                self.effective(name).mkdir(parents=True, exist_ok=True)

    def persist(self) -> None:
        if self.read_only:
            return
        settings, failed = read_folder_settings(self.data_dir)
        if failed:
            self.read_only = True
            raise OSError("Folder settings could not be read; no settings were published.")
        self._cleanup_overrides()
        for name, (file, key) in NAMES.items():
            payload = dict(settings[file])
            payload.setdefault("schemaVersion", 1)
            # An absent, unchosen CAD child is still "nothing selected" for
            # released WGLink and older WG. Preserve capture settings as-is.
            if (name != "cadlink" or self.effective(name).is_dir()
                    or name in self.overrides or stored_path(payload.get(key)) is not None):
                payload[key] = str(self.effective(name))
            if name == "runs":
                # Older WG and WGLink ignore this extra key and still read the
                # effective path. Older WG's chooser replaces the mirror, so a
                # later explicit choice (even the same path) drops the marker.
                if self.runs_selected:
                    payload.pop(RUNS_SELECTED_KEY, None)
                else:
                    payload[RUNS_SELECTED_KEY] = False
            if payload != settings[file]:
                self.writer(self.data_dir / file, payload)
        payload = {**self._extra, "schemaVersion": 1, "rootPath": str(self.root)}
        overrides = {**self._unknown_overrides, **{name: str(path) for name, path in self.overrides.items()}}
        if overrides:
            payload["overrides"] = overrides
        if payload != settings["folders.json"]:
            self.writer(self.settings_path, payload)
        if not self.overrides:
            self.notice = None

    def select(self, path: Path) -> None:
        if self.read_only:
            raise ValueError("Folder settings are read-only; restore access or update WG to change settings saved by a newer version.")
        chosen = path.expanduser().absolute()
        validate_folder_root(chosen, system=self.system)
        if not chosen.is_dir():
            raise ValueError(f"Selected path is not a directory: {chosen}")
        previous, previous_overrides = self.root, dict(self.overrides)
        previous_selected, previous_implicit = self.runs_selected, self.implicit_runs_path
        self.runs_selected = True
        self.implicit_runs_path = None
        self.root = chosen
        self._cleanup_overrides(selected=True)
        try:
            self._create_children()
            self.persist()
        except OSError:
            self.root, self.overrides = previous, previous_overrides
            self.runs_selected, self.implicit_runs_path = previous_selected, previous_implicit
            self._recover_mirror_choices()
            raise
        self.on_change()

    def set_override(self, name: str, path: Path) -> None:
        if self.read_only:
            raise ValueError("Folder settings are read-only; restore access before changing folders.")
        chosen = path.expanduser().absolute()
        if not chosen.is_dir():
            raise ValueError(f"Selected path is not a directory: {chosen}")
        previous_overrides = dict(self.overrides)
        previous_selected, previous_implicit = self.runs_selected, self.implicit_runs_path
        if name == "runs":
            self.runs_selected = True
            self.implicit_runs_path = None
        self.overrides[name] = chosen
        try:
            self.persist()
        except OSError:
            self.overrides = previous_overrides
            self.runs_selected, self.implicit_runs_path = previous_selected, previous_implicit
            self._recover_mirror_choices()
            raise
        self.on_change()

    def _recover_mirror_choices(self) -> None:
        settings, failed = read_folder_settings(self.data_dir)
        self.read_only |= failed
        mirrors = {name: stored_path(settings[file].get(key)) for name, (file, key) in NAMES.items()}
        if mirrors["runs"] is not None:
            self.runs_selected = settings["workspace_settings.json"].get(RUNS_SELECTED_KEY) is not False
            if self.runs_selected:
                self.implicit_runs_path = None
        paired = self._paired_root(mirrors["runs"], mirrors["cadlink"])
        if paired is not None and all(mirrors.values()):
            self.root = paired
            self.overrides = {name: path for name, path in mirrors.items()
                              if path is not None and str(path) != str(self.root / name)}
        else:
            for name, selected in mirrors.items():
                if selected is not None:
                    self.overrides[name] = selected
            self._cleanup_overrides()
        self.on_change()

    def info(self) -> dict[str, Any]:
        return {"path": str(self.root), "selected": True,
                "runs": str(self.effective("runs")), "cadlink": str(self.effective("cadlink")),
                "overrides": {name: str(path) for name, path in self.overrides.items()},
                "notice": self.notice, "readOnly": self.read_only}
