"""V1-compatible workspace path, native selection, and open routes."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import platform
import subprocess
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException

from server.platform.paths import data_paths


logger = logging.getLogger(__name__)


def _select_workspace_folder() -> str | None:
    """Open a native folder picker and return its selection, if any."""

    system = platform.system()
    commands: list[list[str]]
    if system == "Darwin":
        commands = [
            [
                "osascript",
                "-e",
                'set theFolder to POSIX path of (choose folder with prompt "Select output folder")',
            ]
        ]
    elif system == "Windows":
        commands = [
            [
                "powershell",
                "-Command",
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$f.Description = 'Select output folder'; "
                "if ($f.ShowDialog() -eq 'OK') { $f.SelectedPath }",
            ]
        ]
    else:
        commands = [
            ["zenity", "--file-selection", "--directory", "--title=Select output folder"],
            ["kdialog", "--getexistingdirectory", "."],
        ]
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().rstrip("/")
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="Select output folder")
        root.destroy()
        return str(selected) if selected else None
    except Exception:
        return None


class WorkspaceState:
    def __init__(self, data_dir: Path) -> None:
        paths = data_paths(data_dir)
        self.default_path = paths.workspace.resolve()
        self.settings_path = (paths.root / "workspace_settings.json").resolve()
        self._selected: Path | None = None
        self._loaded = False

    def path(self) -> Path:
        if not self._loaded:
            self._load()
        path = self._selected or self.default_path
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _load(self) -> None:
        self._loaded = True
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        raw_path = str(payload.get("workspacePath") or "").strip()
        if not raw_path:
            return
        candidate = Path(raw_path).expanduser().resolve()
        if candidate.is_dir():
            self._selected = candidate
        else:
            logger.warning("Persisted workspace path is unavailable: %s", candidate)

    def select(self, path: Path) -> None:
        resolved = path.expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"Selected path is not a directory: {resolved}")
        self._selected = resolved
        self._loaded = True
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(
                {"schemaVersion": 1, "workspacePath": str(resolved)}, indent=2
            )
            + "\n",
            encoding="utf-8",
        )


def create_workspace_router(state: WorkspaceState) -> APIRouter:
    router = APIRouter(prefix="/api/workspace", tags=["workspace"])

    @router.get("/path")
    async def workspace_path() -> dict[str, str]:
        return {"path": str(state.path())}

    @router.post("/select")
    async def workspace_select() -> dict[str, Any]:
        selected = await asyncio.to_thread(_select_workspace_folder)
        if not selected:
            return {"selected": False, "path": str(state.path())}
        try:
            state.select(Path(selected))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"selected": True, "path": str(state.path())}

    @router.post("/open")
    async def workspace_open() -> dict[str, str]:
        path = state.path()
        command = (
            ["open", str(path)]
            if platform.system() == "Darwin"
            else ["explorer", str(path)]
            if platform.system() == "Windows"
            else ["xdg-open", str(path)]
        )
        try:
            subprocess.Popen(command)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to open folder: {exc}") from exc
        return {"status": "opened", "path": str(path)}

    return router


def mount_workspace(application: FastAPI) -> WorkspaceState:
    state = WorkspaceState(Path(application.state.data_dir))
    application.state.workspace = state
    application.include_router(create_workspace_router(state))
    return state


__all__ = [
    "WorkspaceState",
    "create_workspace_router",
    "mount_workspace",
]
