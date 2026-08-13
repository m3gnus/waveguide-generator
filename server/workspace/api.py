"""V1-compatible workspace path, native selection, and open routes."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
from typing import Any, Literal
import unicodedata

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from server.platform.paths import data_paths
from server.platform.process import background_process_kwargs


logger = logging.getLogger(__name__)

MAX_EXPORT_MEMBERS = 100
# Automatic bundles can include tessellated STL/STEP geometry and rendered
# plots, so the old text-export ceiling was too small for otherwise valid runs.
MAX_EXPORT_BYTES = 256 * 1024 * 1024
_WINDOWS_DEVICE_NAME = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE
)
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class ExportMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str = Field(min_length=1)
    text: str | None = None
    content_base64: str | None = None

    @model_validator(mode="after")
    def one_content_source(self) -> "ExportMember":
        if (self.text is None) == (self.content_base64 is None):
            raise ValueError("exactly one of text or content_base64 is required")
        return self


class WriteExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subdirectory: str = Field(min_length=1)
    members: list[ExportMember] = Field(min_length=1, max_length=MAX_EXPORT_MEMBERS)
    existing: Literal["reject", "merge_identical"] = "reject"


def _member_bytes(member: ExportMember) -> bytes:
    if member.text is not None:
        return member.text.encode("utf-8")
    try:
        return base64.b64decode(member.content_base64 or "", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{member.relative_path!r} contains invalid base64 data") from exc


def _path_segments(raw: str, label: str) -> list[str]:
    if raw.startswith(("/", "\\")) or _WINDOWS_DRIVE.match(raw):
        raise ValueError(f"{label} must be a relative path")
    segments = re.split(r"[\\/]", raw)
    if any(segment in ("", ".", "..") for segment in segments):
        raise ValueError(f"{label} contains an empty, '.' or '..' path segment")
    for segment in segments:
        if len(segment.encode("utf-8")) > 255:
            raise ValueError(f"{label} contains a path segment exceeding the 255-byte limit")
        if segment.endswith((".", " ")):
            raise ValueError(f"{label} contains a segment ending in a dot or space")
        if any(unicodedata.category(character) == "Cc" for character in segment):
            raise ValueError(f"{label} contains a control character")
        if _WINDOWS_DEVICE_NAME.fullmatch(unicodedata.normalize("NFKC", segment)):
            raise ValueError(f"{label} contains reserved Windows device name {segment!r}")
    return segments


def _portable_path_key(segments: list[str]) -> tuple[str, ...]:
    """Key names the same way case-insensitive, Unicode-normalizing filesystems do."""

    return tuple(unicodedata.normalize("NFKC", segment).casefold() for segment in segments)


def _strictly_inside(path: Path, root: Path, label: str) -> None:
    if path == root or root not in path.parents:
        raise ValueError(f"{label} resolves outside the selected workspace")


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
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=120,
                **background_process_kwargs(system=system),
            )
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
    def __init__(self, data_dir: Path, *, default_path: Path | None = None) -> None:
        paths = data_paths(data_dir)
        self.default_path = (
            Path(default_path).expanduser().resolve()
            if default_path is not None
            else paths.workspace.resolve()
        )
        self.settings_path = (paths.root / "workspace_settings.json").resolve()
        self._selected: Path | None = None
        self._loaded = False

    def path(self) -> Path:
        if not self._loaded:
            self._load()
        path = self._selected or self.default_path
        path.mkdir(parents=True, exist_ok=True)
        return path

    def selected_path(self) -> Path | None:
        if not self._loaded:
            self._load()
        if self._selected is not None and not self._selected.is_dir():
            logger.warning("Selected workspace path is unavailable: %s", self._selected)
            self._selected = None
        return self._selected

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
    async def workspace_path() -> dict[str, Any]:
        selected = state.selected_path()
        return {"path": str(selected or state.path()), "selected": selected is not None}

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
            subprocess.Popen(command, **background_process_kwargs())
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to open folder: {exc}") from exc
        return {"status": "opened", "path": str(path)}

    @router.post("/write-export")
    async def workspace_write_export(request: WriteExportRequest) -> dict[str, Any]:
        # Automatic exports must work on first launch without a native folder
        # picker. Production supplies ``<checkout>/output`` as this fallback;
        # an explicit selection still overrides it.
        workspace_root = state.path().resolve()
        try:
            subdirectory_segments = _path_segments(request.subdirectory, "subdirectory")
            export_directory = workspace_root.joinpath(*subdirectory_segments).resolve()
            _strictly_inside(export_directory, workspace_root, "subdirectory")

            prepared: list[tuple[list[str], bytes, Path]] = []
            total_bytes = 0
            seen: set[Path] = set()
            portable_seen: set[tuple[str, ...]] = set()
            for index, member in enumerate(request.members):
                label = f"members[{index}].relative_path"
                segments = _path_segments(member.relative_path, label)
                destination = export_directory.joinpath(*segments).resolve()
                _strictly_inside(destination, workspace_root, label)
                if destination == export_directory or export_directory not in destination.parents:
                    raise ValueError(f"{label} resolves outside the export subdirectory")
                portable_key = _portable_path_key(segments)
                if destination in seen or portable_key in portable_seen:
                    raise ValueError(f"{label} duplicates another member path")
                seen.add(destination)
                portable_seen.add(portable_key)
                encoded = _member_bytes(member)
                total_bytes += len(encoded)
                if total_bytes > MAX_EXPORT_BYTES:
                    raise ValueError(
                        f"Export set exceeds the {MAX_EXPORT_BYTES}-byte binary size limit"
                    )
                prepared.append((segments, encoded, destination))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        export_exists = export_directory.exists() or export_directory.is_symlink()
        if export_exists and request.existing == "reject":
            raise HTTPException(status_code=409, detail=f"Export directory already exists: {export_directory}")
        if export_exists:
            if export_directory.is_symlink() or not export_directory.is_dir():
                raise HTTPException(status_code=409, detail=f"Export path is not a directory: {export_directory}")
            for _segments, encoded, destination in prepared:
                if not (destination.exists() or destination.is_symlink()):
                    continue
                try:
                    identical = (
                        not destination.is_symlink()
                        and destination.is_file()
                        and destination.read_bytes() == encoded
                    )
                except OSError:
                    identical = False
                if not identical:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Export file already exists with different content: {destination}",
                    )

        export_directory.parent.mkdir(parents=True, exist_ok=True)
        staging_directory = Path(
            tempfile.mkdtemp(prefix=".wg2-export-staging-", dir=export_directory.parent)
        )
        try:
            for segments, encoded, _destination in prepared:
                staged_file = staging_directory.joinpath(*segments)
                staged_file.parent.mkdir(parents=True, exist_ok=True)
                staged_file.write_bytes(encoded)
            if request.existing == "reject":
                os.replace(staging_directory, export_directory)
            else:
                export_directory.mkdir(exist_ok=True)
                for segments, _encoded, destination in prepared:
                    if destination.exists():
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(staging_directory.joinpath(*segments), destination)
                shutil.rmtree(staging_directory, ignore_errors=True)
        except Exception as exc:
            shutil.rmtree(staging_directory, ignore_errors=True)
            raise HTTPException(status_code=500, detail=f"Failed to write export set: {exc}") from exc

        return {
            "directory": str(export_directory),
            "files": [str(destination) for _segments, _encoded, destination in prepared],
        }

    return router


def mount_workspace(
    application: FastAPI, *, default_path: Path | None = None
) -> WorkspaceState:
    state = WorkspaceState(Path(application.state.data_dir), default_path=default_path)
    application.state.workspace = state
    application.include_router(create_workspace_router(state))
    return state


__all__ = [
    "WorkspaceState",
    "WriteExportRequest",
    "create_workspace_router",
    "mount_workspace",
]
