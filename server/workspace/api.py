"""V1-compatible workspace path, native selection, and open routes."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
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
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from server.platform.paths import data_paths, proposed_cadlink_dir
from server.platform.process import background_process_kwargs


logger = logging.getLogger(__name__)

MAX_EXPORT_MEMBERS = 100
# Automatic bundles can include tessellated STL/STEP geometry and rendered
# plots, so the old text-export ceiling was too small for otherwise valid runs.
MAX_EXPORT_BYTES = 256 * 1024 * 1024
# Binary members use base64 in the JSON request (4/3 expansion). This route-only
# envelope leaves another 42 MiB for member metadata and JSON framing while
# keeping the binary-content limit above as the user-facing export constraint.
MAX_EXPORT_REQUEST_BODY_BYTES = 384 * 1024 * 1024
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
    #: ``reject`` refuses an existing directory outright. ``merge_identical``
    #: adds only what is missing and refuses to change a file that differs; it
    #: is what automatic post-run export uses, so a background write can never
    #: overwrite anything. ``overwrite`` replaces the members it is given and
    #: is for a user asking for an export a second time: several builders stamp
    #: the current time into their output, so a repeat export is *never* byte
    #: identical and ``merge_identical`` rejected the whole bundle.
    existing: Literal["reject", "merge_identical", "overwrite"] = "reject"


class SelectCadWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)


class SelectWorkspaceRequest(BaseModel):
    """A folder typed instead of chosen from the native picker.

    The picker runs on the machine hosting the server, which is the right
    behaviour for the desktop launcher and useless when WG is reached from a
    browser on another machine. Accepting a path keeps that case workable
    without asking the browser for a directory handle only Chromium grants.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)


#: Where a captured CAD document is filed in the run archive.
#:
#: ``project`` keeps only the newest model state under
#: ``runs/<project>/cad/`` -- archiving a later state deletes the last;
#: ``run`` additionally places that document beside the run that was solved
#: from it, which is where people look for it and is never pruned; ``off``
#: asks the add-in not to carry the document at all.
CaptureMode = Literal["off", "project", "run"]
CAPTURE_MODES: tuple[str, ...] = ("off", "project", "run")


class CaptureDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Superseded by ``mode``; still accepted so an older client keeps working.
    enabled: bool | None = None
    mode: CaptureMode | None = None


class WorkspaceUnavailableError(OSError):
    """An explicitly selected run folder is temporarily unavailable."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"The selected workspace folder is unavailable: {path}")


def _workspace_unavailable_response(exc: WorkspaceUnavailableError) -> JSONResponse:
    """Return a stable error shape without hiding the configured folder."""

    return JSONResponse(
        status_code=409,
        content={
            "code": "workspace_unavailable",
            "detail": str(exc),
            "path": str(exc.path),
        },
    )


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Publish a small cross-process setting without exposing a torn file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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


def open_folder_command(path: Path) -> list[str]:
    """The desktop file-manager command that reveals a folder on this platform."""

    if platform.system() == "Darwin":
        return ["open", str(path)]
    if platform.system() == "Windows":
        return ["explorer", str(path)]
    return ["xdg-open", str(path)]


def _picker_start_directory(start_in: Path | None) -> Path | None:
    """The deepest part of a proposed location that actually exists.

    The path is embedded in an AppleScript string and a PowerShell string, so a
    quote or newline anywhere in it -- a home directory may legally contain one
    -- would break the dialog rather than position it. Positioning is a
    convenience; drop it instead of mangling the command.
    """

    if start_in is None or any(character in str(start_in) for character in "\"'\n\r"):
        return None
    for candidate in (start_in, *start_in.parents):
        # The filesystem root is not a helpful place to open a picker, and it
        # is what walking up an entirely absent path arrives at.
        if candidate == candidate.parent:
            return None
        if candidate.is_dir():
            return candidate
    return None


def _select_workspace_folder(
    prompt: str = "Select output folder", start_in: Path | None = None
) -> str | None:
    """Open a native folder picker and return its selection, if any.

    ``start_in`` only positions the dialog. Opening it on the folder the
    application would suggest saves the user from navigating to a location they
    are about to accept, and costs nothing when the location does not exist.
    """

    system = platform.system()
    opening = _picker_start_directory(start_in)
    commands: list[list[str]]
    if system == "Darwin":
        location = (
            f' default location POSIX file "{opening}"' if opening is not None else ""
        )
        commands = [
            [
                "osascript",
                "-e",
                "set theFolder to POSIX path of (choose folder with prompt "
                f'"{prompt}"{location})',
            ]
        ]
    elif system == "Windows":
        selected_path = (
            f"$f.SelectedPath = '{opening}'; " if opening is not None else ""
        )
        commands = [
            [
                "powershell",
                "-Command",
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                f"$f.Description = '{prompt}'; "
                f"{selected_path}"
                "if ($f.ShowDialog() -eq 'OK') { $f.SelectedPath }",
            ]
        ]
    else:
        zenity = ["zenity", "--file-selection", "--directory", f"--title={prompt}"]
        if opening is not None:
            zenity.append(f"--filename={opening}/")
        commands = [
            zenity,
            ["kdialog", "--getexistingdirectory", str(opening or ".")],
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
        selected = filedialog.askdirectory(
            title=prompt,
            **({"initialdir": str(opening)} if opening is not None else {}),
        )
        root.destroy()
        return str(selected) if selected else None
    except Exception:
        return None


class WorkspaceState:
    def __init__(
        self,
        data_dir: Path,
        *,
        default_path: Path | None = None,
        legacy_defaults: Sequence[Path] = (),
    ) -> None:
        paths = data_paths(data_dir)
        self.default_path = (
            Path(default_path).expanduser().resolve()
            if default_path is not None
            else paths.workspace.resolve()
        )
        self.settings_path = (paths.root / "workspace_settings.json").resolve()
        self.legacy_defaults = tuple(
            Path(candidate).expanduser().resolve() for candidate in legacy_defaults
        )
        self._selected: Path | None = None
        self._loaded = False
        self._adopt_legacy_default()

    def _adopt_legacy_default(self) -> None:
        """Keep an install writing where it already writes.

        The default moved to the user's documents folder. An install that has
        been exporting into one of the old defaults must not appear to have lost
        its runs, and moving a user's files is not ours to do -- so a legacy
        default that actually holds runs is adopted as an explicit selection
        instead. Emptiness is the test: a directory the application created and
        nothing was ever written to carries no history worth pinning.
        """

        if not self.legacy_defaults or self.settings_path.exists():
            return
        for candidate in self.legacy_defaults:
            if candidate == self.default_path or not candidate.is_dir():
                continue
            if not any(
                child.is_dir() and not child.name.startswith(".")
                for child in candidate.iterdir()
            ):
                continue
            _write_json_atomic(
                self.settings_path,
                {"schemaVersion": 1, "workspacePath": str(candidate)},
            )
            logger.info("Adopted the existing run-export folder %s", candidate)
            return

    def path(self) -> Path:
        if not self._loaded:
            self._load()
        if self._selected is not None:
            if not self._selected.is_dir():
                raise WorkspaceUnavailableError(self._selected)
            return self._selected
        path = self.default_path
        path.mkdir(parents=True, exist_ok=True)
        return path

    def selected_path(self) -> Path | None:
        if not self._loaded:
            self._load()
        if self._selected is not None and not self._selected.is_dir():
            logger.warning("Selected workspace path is unavailable: %s", self._selected)
            return None
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
        self._selected = candidate
        if not candidate.is_dir():
            logger.warning("Persisted workspace path is unavailable: %s", candidate)

    def select(self, path: Path) -> None:
        resolved = path.expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"Selected path is not a directory: {resolved}")
        _write_json_atomic(
            self.settings_path,
            {"schemaVersion": 1, "workspacePath": str(resolved)},
        )
        self._selected = resolved
        self._loaded = True


class CadWorkspaceState(WorkspaceState):
    """The user-visible folder shared by WG and Fusion's WGLink add-in.

    Run exports and CAD exchange used to share ``WorkspaceState``. Keeping a
    separate persisted path prevents changing an export destination from
    silently disconnecting Fusion. Existing installations adopt their old
    selected workspace once so upgrades do not lose a working link.
    """

    SETTINGS_NAME = "cadlink_settings.json"
    SETTINGS_KEY = "cadLinkPath"
    #: Whether a return carries a copy of the CAD document it was taken from.
    #: It lives beside the folder because the Fusion add-in reads this file
    #: already: one setting, set in WG, read where the add-in was going to look
    #: anyway, rather than the same switch offered in two applications.
    CAPTURE_KEY = "captureDocument"
    #: Where WG files what the add-in captured. The boolean above stays the
    #: add-in's switch -- it only decides whether to carry the document -- so an
    #: add-in that predates this key keeps working unchanged.
    CAPTURE_MODE_KEY = "captureMode"

    def __init__(self, data_dir: Path, *, proposed_path: Path | None = None) -> None:
        super().__init__(data_dir, default_path=data_paths(data_dir).root / "cadlink")
        self.proposed_path = (
            Path(proposed_path).expanduser()
            if proposed_path is not None
            else proposed_cadlink_dir()
        )
        self._capture_mode: CaptureMode = "run"
        self.settings_path = (data_paths(data_dir).root / self.SETTINGS_NAME).resolve()
        self.legacy_settings_path = (
            data_paths(data_dir).root / "workspace_settings.json"
        ).resolve()
        self._migrate_legacy_selection()

    def _migrate_legacy_selection(self) -> None:
        """Adopt a proven legacy CAD exchange exactly once.

        An output-only selection must not silently become a CAD connection.
        Existing ``wglink`` or ``wgreturn`` content is the durable evidence
        that the old shared folder was actually used by WGLink.
        """

        if self.settings_path.exists():
            return
        try:
            payload = json.loads(self.legacy_settings_path.read_text(encoding="utf-8"))
            raw_path = str(payload.get("workspacePath") or "").strip()
            if not raw_path:
                return
            candidate = Path(raw_path).expanduser().resolve()
        except (OSError, ValueError, TypeError, json.JSONDecodeError, AttributeError):
            return
        if not candidate.is_dir() or not any(
            (candidate / child).is_dir() for child in ("wglink", "wgreturn")
        ):
            return
        _write_json_atomic(
            self.settings_path,
            {"schemaVersion": 1, self.SETTINGS_KEY: str(candidate)},
        )

    def create_proposed_if_requested(self, path: Path) -> None:
        """Create the folder this class proposed, and only that one.

        Accepting the suggested location must not be a two-step chore in Finder,
        but a select route that creates whatever path it is handed would turn a
        typo into a new empty CAD exchange the add-in then cannot find.
        """

        resolved = path.expanduser()
        if resolved.is_dir() or resolved != self.proposed_path:
            return
        resolved.mkdir(parents=True, exist_ok=True)

    def path(self) -> Path:
        selected = self.selected_path()
        if selected is None:
            raise ValueError("No WGLink folder has been selected.")
        return selected

    def _load(self) -> None:
        self._loaded = True
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        # One rule, so an existing install and a fresh one behave the same: the
        # stored mode wins, and a settings file that only ever knew the boolean
        # means "off" when it was switched off and the default otherwise.
        stored_mode = str(payload.get(self.CAPTURE_MODE_KEY) or "").strip()
        if stored_mode in CAPTURE_MODES:
            self._capture_mode = stored_mode  # type: ignore[assignment]
        else:
            self._capture_mode = "off" if payload.get(self.CAPTURE_KEY) is False else "run"
        raw_path = str(payload.get(self.SETTINGS_KEY) or "").strip()
        if not raw_path:
            return
        candidate = Path(raw_path).expanduser().resolve()
        if candidate.is_dir():
            self._selected = candidate
        else:
            logger.warning("Persisted WGLink path is unavailable: %s", candidate)

    def select(self, path: Path) -> None:
        resolved = path.expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"Selected path is not a directory: {resolved}")
        if not self._loaded:
            self._load()
        self._selected = resolved
        self._loaded = True
        self._persist()

    @property
    def capture_mode(self) -> CaptureMode:
        """Where a captured CAD document is filed, or ``off`` for not at all.

        Reading loads the settings file first: the stored value used to be
        readable only after something else happened to trigger the lazy load,
        so a fresh state answered with the default instead of the setting.
        """

        if not self._loaded:
            self._load()
        return self._capture_mode

    @property
    def capture_document(self) -> bool:
        """Whether a return carries a copy of the CAD document it came from.

        This is the add-in's half of the setting and stays a boolean: filing is
        WG's business, carrying the document is the add-in's.
        """

        return self.capture_mode != "off"

    def set_capture_mode(self, mode: CaptureMode) -> None:
        """Choose whether returns carry a CAD document, and where it is filed."""

        if mode not in CAPTURE_MODES:
            raise ValueError(f"Unknown capture mode: {mode}")
        if not self._loaded:
            self._load()
        self._capture_mode = mode
        self._persist()

    def _persist(self) -> None:
        """Write both settings together so neither erases the other.

        Choosing a folder used to rewrite this file wholesale, which would drop
        the capture choice the next time a folder was picked.
        """

        payload: dict[str, Any] = {
            "schemaVersion": 1,
            # Written together and always: the add-in reads only the boolean,
            # so it must never be absent just because WG learned a third mode.
            self.CAPTURE_KEY: self._capture_mode != "off",
            self.CAPTURE_MODE_KEY: self._capture_mode,
        }
        if self._selected is not None:
            payload[self.SETTINGS_KEY] = str(self._selected)
        _write_json_atomic(self.settings_path, payload)


def create_cad_workspace_router(state: CadWorkspaceState) -> APIRouter:
    router = APIRouter(prefix="/api/cad-workspace", tags=["cadlink"])

    @router.get("/path")
    async def cad_workspace_path() -> dict[str, Any]:
        selected = state.selected_path()
        return {
            "path": str(selected) if selected is not None else None,
            "selected": selected is not None,
            # The proposal is not a fallback: nothing reads it until the user
            # accepts it, so an unselected CAD folder stays unselected.
            "proposed": str(state.proposed_path),
            "proposedExists": state.proposed_path.is_dir(),
            "captureDocument": state.capture_document,
            "captureMode": state.capture_mode,
        }

    @router.post("/capture-document")
    async def cad_workspace_capture_document(
        payload: CaptureDocumentRequest,
    ) -> dict[str, Any]:
        mode = payload.mode
        if mode is None:
            if payload.enabled is None:
                raise HTTPException(
                    status_code=422, detail="Provide a capture mode."
                )
            mode = "run" if payload.enabled else "off"
        try:
            state.set_capture_mode(mode)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Could not save the setting: {exc}"
            ) from exc
        return {
            "captureDocument": state.capture_document,
            "captureMode": state.capture_mode,
        }

    @router.post("/select")
    async def cad_workspace_select(
        payload: SelectCadWorkspaceRequest | None = None,
    ) -> dict[str, Any]:
        selected = (
            payload.path
            if payload is not None
            else await asyncio.to_thread(
                _select_workspace_folder, "Select WGLink folder", state.proposed_path
            )
        )
        if selected:
            try:
                state.create_proposed_if_requested(Path(selected))
            except OSError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not create the CAD Link folder: {exc}",
                ) from exc
        if not selected:
            current = state.selected_path()
            return {
                "selected": current is not None,
                "path": str(current) if current is not None else None,
            }
        try:
            state.select(Path(selected))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"selected": True, "path": str(state.path())}

    @router.post("/open")
    async def cad_workspace_open() -> dict[str, str]:
        try:
            path = state.path()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            subprocess.Popen(open_folder_command(path), **background_process_kwargs())
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Failed to open folder: {exc}"
            ) from exc
        return {"status": "opened", "path": str(path)}

    return router


def create_workspace_router(state: WorkspaceState) -> APIRouter:
    router = APIRouter(prefix="/api/workspace", tags=["workspace"])

    def available_path() -> Path | JSONResponse:
        try:
            return state.path()
        except WorkspaceUnavailableError as exc:
            return _workspace_unavailable_response(exc)

    def picker_start() -> Path | None:
        """Where to open the dialog: only ever a hint, never a requirement."""

        try:
            return state.path()
        except Exception:
            return state.selected_path()

    @router.get("/path")
    async def workspace_path() -> Any:
        path = available_path()
        if isinstance(path, JSONResponse):
            return path
        selected = state.selected_path()
        return {"path": str(path), "selected": selected is not None}

    @router.post("/select")
    async def workspace_select(payload: SelectWorkspaceRequest | None = None) -> Any:
        selected = (
            payload.path
            if payload is not None
            else await asyncio.to_thread(
                _select_workspace_folder, "Select output folder", picker_start()
            )
        )
        if not selected:
            path = available_path()
            if isinstance(path, JSONResponse):
                return path
            return {"selected": False, "path": str(path)}
        try:
            state.select(Path(selected))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        path = available_path()
        if isinstance(path, JSONResponse):
            return path
        return {"selected": True, "path": str(path)}

    @router.post("/open")
    async def workspace_open() -> Any:
        path = available_path()
        if isinstance(path, JSONResponse):
            return path
        try:
            subprocess.Popen(open_folder_command(path), **background_process_kwargs())
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to open folder: {exc}") from exc
        return {"status": "opened", "path": str(path)}

    @router.post("/write-export")
    async def workspace_write_export(request: WriteExportRequest) -> Any:
        # Automatic exports must work on first launch without a native folder
        # picker. Production supplies ``<checkout>/output`` as this fallback;
        # an explicit selection still overrides it.
        workspace_path = available_path()
        if isinstance(workspace_path, JSONResponse):
            return workspace_path
        workspace_root = workspace_path.resolve()
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
        if export_exists and request.existing == "merge_identical":
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
        if export_exists and request.existing == "overwrite":
            # Replacing a file is the point here; replacing a *directory* with a
            # file is not, and would surface as an opaque write failure below.
            for _segments, _encoded, destination in prepared:
                if destination.is_dir() and not destination.is_symlink():
                    raise HTTPException(
                        status_code=409,
                        detail=f"Export path is a directory, not a file: {destination}",
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
                overwrite = request.existing == "overwrite"
                for segments, _encoded, destination in prepared:
                    # merge_identical has already proven every existing file is
                    # byte-identical, so skipping it keeps the write a no-op
                    # rather than churning mtimes a file watcher would report.
                    if destination.exists() and not overwrite:
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
    application: FastAPI,
    *,
    default_path: Path | None = None,
    legacy_defaults: Sequence[Path] = (),
) -> WorkspaceState:
    state = WorkspaceState(
        Path(application.state.data_dir),
        default_path=default_path,
        legacy_defaults=legacy_defaults,
    )
    application.state.workspace = state
    application.include_router(create_workspace_router(state))
    cad_state = CadWorkspaceState(Path(application.state.data_dir))
    application.state.cad_workspace = cad_state
    application.include_router(create_cad_workspace_router(cad_state))
    return state


__all__ = [
    "WorkspaceState",
    "CadWorkspaceState",
    "CaptureDocumentRequest",
    "WriteExportRequest",
    "SelectCadWorkspaceRequest",
    "SelectWorkspaceRequest",
    "create_workspace_router",
    "create_cad_workspace_router",
    "mount_workspace",
]
