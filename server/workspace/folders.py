"""User-visible folders, migrated without moving any existing content.

Initialize after the legacy states have performed their adoption checks, under
backend ownership and before mounting routers. Mirrors are published first;
folders.json is the final atomic commit of the chosen configuration.
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


def read_settings(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def stored_path(value: Any) -> Path | None:
    return Path(value.strip()).expanduser() if isinstance(value, str) and value.strip() and "\x00" not in value else None


class FolderRoot:
    def __init__(self, data_dir: Path, *, default_root: Path, legacy_runs: Path,
                 writer: Callable[[Path, dict[str, Any]], None], system: str | None = None) -> None:
        self.data_dir = data_dir
        self.settings_path = data_dir / "folders.json"
        self.default_root = default_root
        self.system = system
        self.writer = writer
        self.on_change: Callable[[], None] = lambda: None
        self.notice: str | None = None
        payload = read_settings(self.settings_path)
        # An unsupported schema belongs to another version. Work from mirrors,
        # but never rewrite its configuration, even through the chooser.
        self.read_only = "schemaVersion" in payload and (type(payload["schemaVersion"]) is not int or payload["schemaVersion"] != 1)
        mirrors = {name: stored_path(read_settings(data_dir / file).get(key))
                   for name, (file, key) in NAMES.items()}
        mirrors = {name: value.absolute() if value is not None else None for name, value in mirrors.items()}
        root = stored_path(payload.get("rootPath"))
        overrides = payload.get("overrides", {})
        valid = (not self.read_only and payload.get("schemaVersion") == 1
                 and root is not None and root.is_absolute() and isinstance(overrides, dict)
                 and all(name in NAMES and stored_path(value) is not None
                         and stored_path(value).is_absolute() for name, value in overrides.items()))
        if valid:
            self.root = root
            self.overrides = {name: Path(value.strip()).expanduser() for name, value in overrides.items()}
            drift = []
            for name, selected in mirrors.items():
                if selected is not None and not self.same(selected, self.effective(name)):
                    self.overrides[name] = selected
                    drift.append(name)
            if drift:
                self.notice = "Folder choices from an earlier WG version were kept: " + ", ".join(drift) + "."
                log.warning(self.notice)
        else:
            runs, cad = mirrors["runs"], mirrors["cadlink"]
            self.root = default_root
            self.overrides: dict[str, Path] = {}
            if runs is None and cad is None:
                # A fresh install uses the new defaults, rather than pinning the
                # retired data-override workspace or creating a CAD selection UI.
                pass
            elif (runs is not None and cad is not None
                  and self.same(runs, runs.parent / "runs")
                  and self.same(cad, cad.parent / "cadlink")
                  and self.same(runs.parent, cad.parent)):
                self.root = runs.parent
            else:
                for name, selected in (("runs", runs or legacy_runs), ("cadlink", cad)):
                    if selected is not None and not self.same(selected, self.root / name):
                        self.overrides[name] = selected
            if self.read_only:
                # Missing mirrors still have safe defaults. Never apply an
                # unknown root/override format based on guessed semantics.
                self.notice = "This WG version uses the existing folder settings; folders.json has an unsupported schema."
                log.warning(self.notice)
        self.persist()

    def same(self, left: Path, right: Path) -> bool:
        return same_folder(left, right, system=self.system)

    def effective(self, name: str) -> Path:
        return self.overrides.get(name, self.root / name)

    def persist(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for name, (file, key) in NAMES.items():
            if name not in self.overrides:
                try:
                    self.effective(name).mkdir(parents=True, exist_ok=True)
                except OSError:
                    # A disconnected drive remains the recorded choice. Reads
                    # report it unavailable; startup must not repoint it.
                    log.warning("Folder is unavailable: %s", self.effective(name))
            target = self.data_dir / file
            payload = read_settings(target)
            payload.setdefault("schemaVersion", 1)
            payload[key] = str(self.effective(name))
            # Capture keys and other legacy values are intentionally untouched.
            if payload != read_settings(target):
                self.writer(target, payload)
        if not self.read_only:
            payload = {"schemaVersion": 1, "rootPath": str(self.root)}
            if self.overrides:
                payload["overrides"] = {name: str(path) for name, path in self.overrides.items()}
            if payload != read_settings(self.settings_path):
                self.writer(self.settings_path, payload)

    def select(self, path: Path) -> None:
        if self.read_only:
            raise ValueError("Update WG to change the root folder saved by a newer version.")
        chosen = path.expanduser().absolute()  # keep chosen spelling, including mapped drives
        validate_folder_root(chosen, system=self.system)
        if not chosen.is_dir():
            raise ValueError(f"Selected path is not a directory: {chosen}")
        previous = self.root
        self.root = chosen
        try:
            self.persist()
        except OSError:
            self.root = previous
            self._recover_mirror_choices()
            raise
        self.on_change()

    def set_override(self, name: str, path: Path) -> None:
        chosen = path.expanduser().absolute()
        if not chosen.is_dir():
            raise ValueError(f"Selected path is not a directory: {chosen}")
        self.overrides[name] = chosen
        try:
            self.persist()
        except OSError:
            self._recover_mirror_choices()
            raise
        self.on_change()

    def _recover_mirror_choices(self) -> None:
        """An IO refusal also keeps the running process aligned with WGLink.

        Writes may have reached one or both mirrors. Re-read their durable
        choices before reporting failure; the next start commits these same
        conservative overrides using the normal rollback-drift check.
        """
        for name, (file, key) in NAMES.items():
            selected = stored_path(read_settings(self.data_dir / file).get(key))
            if selected is not None:
                if self.same(selected, self.root / name):
                    self.overrides.pop(name, None)
                else:
                    self.overrides[name] = selected
        self.on_change()

    def info(self) -> dict[str, Any]:
        return {"path": str(self.root), "selected": True,
                "runs": str(self.effective("runs")), "cadlink": str(self.effective("cadlink")),
                "overrides": {name: str(path) for name, path in self.overrides.items()},
                "notice": self.notice, "readOnly": self.read_only}
