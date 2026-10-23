"""V1-compatible workspace path, native selection, and open routes."""

from __future__ import annotations

from server.platform.json_file import write_json_atomic as _write_json_atomic

import asyncio
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
import base64
import binascii
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import secrets
import shutil
import subprocess
import time
from typing import Any, Callable, Literal, TypeVar
import unicodedata

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.datastructures import UploadFile

from server.platform.paths import data_paths, proposed_cadlink_dir, default_folder_root
from server.workspace.portable_paths import path_segments as _path_segments
from server.workspace import archive_identity
from server.workspace.folders import FolderRoot, read_folder_settings, read_settings, stored_path
from server.platform.process import background_process_kwargs
from server.platform.acl_repair import Outcome, repair_path
from server.platform.staging import publish_staging_directory


logger = logging.getLogger(__name__)

_T = TypeVar("_T")

MAX_EXPORT_MEMBERS = 100
# Automatic bundles can include tessellated STL/STEP geometry and rendered
# plots, so the old text-export ceiling was too small for otherwise valid runs.
MAX_EXPORT_BYTES = 256 * 1024 * 1024
# Legacy JSON clients use base64 (4/3 expansion), while current clients send
# multipart binary parts. Keep the larger route-only envelope for compatibility.
MAX_EXPORT_REQUEST_BODY_BYTES = 384 * 1024 * 1024
#: How long a chosen export destination stays usable, and how many are kept.
#:
#: A handle is **not** single-use. One export legitimately writes through it more
#: than once: a profile export writes two files with two calls, and an
#: ``existing=confirm`` refusal is repeated as ``overwrite`` once the user has
#: answered. It expires by time instead, and the bounds exist so a long-lived
#: server does not accumulate handles to folders whose user moved on.
EXPORT_DESTINATION_TTL_SECONDS = 30 * 60
MAX_EXPORT_DESTINATIONS = 8
EXISTING_FILE_POLICIES = frozenset({"reject", "merge_identical", "overwrite", "confirm"})


class ExportCollision(Exception):
    """An ``existing=confirm`` export would replace files that differ.

    Carries every one of them, not the first: the caller asks the user once
    about the whole export, and a question that named one file at a time would
    be the per-file prompt this exists to avoid. Nothing has been written when
    this is raised.
    """

    def __init__(self, directory: Path, paths: Sequence[Path]) -> None:
        self.directory = directory
        self.paths = [str(path) for path in paths]
        super().__init__(
            f"{len(self.paths)} file(s) in {directory} would be replaced with "
            "different content"
        )


def _export_collision_response(exc: ExportCollision) -> JSONResponse:
    """A refusal the client can turn into one question and one retry."""

    return JSONResponse(
        status_code=409,
        content={
            "code": "export_collision",
            "detail": str(exc),
            "directory": str(exc.directory),
            "paths": exc.paths,
        },
    )


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

    #: Where inside the destination the set lands. Required for the workspace,
    #: which is shared by every export and needs each one in its own folder.
    #: Empty means "the destination itself", which only a ``destination`` handle
    #: may ask for -- see ``ExportDestinationStore``.
    subdirectory: str = ""
    members: list[ExportMember] = Field(min_length=1, max_length=MAX_EXPORT_MEMBERS)
    #: ``reject`` refuses an existing directory outright. ``merge_identical``
    #: adds only what is missing and refuses to change a file that differs; it
    #: is what automatic post-run export uses, so a background write can never
    #: overwrite anything. ``overwrite`` replaces the members it is given and
    #: is for a user asking for an export a second time: several builders stamp
    #: the current time into their output, so a repeat export is *never* byte
    #: identical and ``merge_identical`` rejected the whole bundle.
    #:
    #: ``confirm`` is ``overwrite`` with the replacements shown first. It writes
    #: what is missing and skips what is byte identical, and if any member would
    #: change a file that differs it writes **nothing** and names every one of
    #: them, so the client can ask once and repeat the request as ``overwrite``.
    #: A manual export now goes to a folder the user chose, which may hold files
    #: WG never wrote; a basename collision there must not be settled by a
    #: report after the fact.
    existing: Literal["reject", "merge_identical", "overwrite", "confirm"] = "reject"
    #: A handle from ``POST /api/workspace/export-destination``, naming the
    #: folder the user chose for this one export. Absent means the workspace,
    #: which is what every automatic export uses.
    destination: str | None = None
    #: Run owner. Without a chosen destination, resolve beneath its recorded location.
    job_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def subdirectory_or_destination(self) -> "WriteExportRequest":
        if not self.subdirectory and self.destination is None:
            raise ValueError("subdirectory is required")
        return self


class SelectCadWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)


class ChooseExportDestinationRequest(BaseModel):
    """A folder typed instead of chosen from the native picker.

    Same reasoning as ``SelectWorkspaceRequest``: the picker runs on the machine
    hosting the server, so a browser on another machine needs a way to name a
    folder that does not open a dialog nobody is sitting in front of.
    """

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


class UseRootFolderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    useRoot: Literal["cadlink", "runs"]
    expectedPath: str | None = Field(default=None, min_length=1, max_length=4096)
    expectedCurrentPath: str | None = Field(default=None, min_length=1, max_length=4096)


#: Keep is mirrored as run for older WG; off captures nothing. The legacy
#: project input is accepted and normalized to run.
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



def _member_bytes(member: ExportMember, remaining: int = MAX_EXPORT_BYTES) -> bytes:
    if member.text is not None:
        content = member.text.encode("utf-8")
        if len(content) > remaining:
            raise ValueError("Export set exceeds the binary size limit")
        return content
    encoded = member.content_base64 or ""
    padding = 2 if encoded.endswith("==") else int(encoded.endswith("="))
    decoded_size = (len(encoded) // 4) * 3 - padding
    if decoded_size > remaining:
        raise ValueError("Export set exceeds the binary size limit")
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{member.relative_path!r} contains invalid base64 data") from exc



def _decode_json_export_request(
    payload: bytes | WriteExportRequest,
) -> tuple[str, str, list[tuple[str, bytes]], str | None, str | None]:
    request = (
        payload
        if isinstance(payload, WriteExportRequest)
        else WriteExportRequest.model_validate_json(payload)
    )
    members = []
    remaining = MAX_EXPORT_BYTES
    for member in request.members:
        content = _member_bytes(member, remaining)
        remaining -= len(content)
        members.append((member.relative_path, content))
    return (
        request.subdirectory,
        request.existing,
        members,
        request.destination,
        request.job_id,
    )


async def _decode_multipart_export_request(
    request: Request,
) -> tuple[str, str, list[tuple[str, bytes]], str | None, str | None]:
    form = await request.form()
    subdirectory = form.get("subdirectory", "")
    existing = form.get("existing", "reject")
    destination = form.get("destination") or None
    job_id = form.get("job_id") or None
    if job_id is not None and (not isinstance(job_id, str) or len(job_id) > 200):
        raise ValueError("job_id must identify a run")
    relative_paths = form.getlist("relative_path")
    files = form.getlist("file")
    if destination is not None and not isinstance(destination, str):
        raise ValueError("destination must be an export-destination handle")
    if not isinstance(subdirectory, str) or (not subdirectory and destination is None):
        raise ValueError("subdirectory is required")
    if existing not in EXISTING_FILE_POLICIES:
        raise ValueError(
            "existing must be one of " + ", ".join(sorted(EXISTING_FILE_POLICIES))
        )
    if not 1 <= len(files) <= MAX_EXPORT_MEMBERS:
        raise ValueError(f"file count must be between 1 and {MAX_EXPORT_MEMBERS}")
    if len(relative_paths) != len(files) or not all(
        isinstance(path, str) for path in relative_paths
    ):
        raise ValueError("each file requires one corresponding relative_path")

    members: list[tuple[str, bytes]] = []
    total_bytes = 0
    for relative_path, upload in zip(relative_paths, files, strict=True):
        if not isinstance(upload, UploadFile):
            raise ValueError("file fields must contain binary uploads")
        remaining = MAX_EXPORT_BYTES - total_bytes
        content = await upload.read(remaining + 1)
        total_bytes += len(content)
        if total_bytes > MAX_EXPORT_BYTES:
            raise ValueError(
                f"Export set exceeds the {MAX_EXPORT_BYTES}-byte binary size limit"
            )
        members.append((relative_path, content))
    return subdirectory, existing, members, destination, job_id


def _retry_after_acl_repair(
    destination: Path, read: Callable[[], _T], *, workspace_root: Path | None = None
) -> _T:
    """Run `read`; if it fails, try repairing a legacy ACL and run it once more.

    The app is what made these files unreadable -- a staging descriptor that
    named nobody but their writer, carried to the destination by `os.replace`
    (see `server/platform/acl_repair.py`). Repairing on encounter matters even
    with the boot sweep in place: the sweep is bounded and can be truncated, a
    workspace can be selected after it ran, and a file can arrive from a backup
    at any time.

    Only a descriptor matching the exact staging pattern is touched; anything
    else re-raises the original error, so a file a user restricted deliberately
    still produces a refusal rather than a silent widening of access.
    """

    try:
        return read()
    except OSError as first:
        if repair_path(destination, root=workspace_root) is not Outcome.REPAIRED:
            raise first
        logger.info(
            "Repaired a legacy staging ACL on %s while reading it back; "
            "the file was written by this application with permissions only "
            "its writer could use.",
            destination,
        )
        try:
            return read()
        except OSError:
            # Repaired and still unreadable is a different fault from the one
            # this function exists for. The caller's refusal is the right
            # answer, and the original error is the honest one to report.
            raise first


def _streaming_file_matches(path: Path, content: bytes) -> bool:
    """Compare an existing member without loading a second full copy into RAM."""

    if path.is_symlink() or not path.is_file() or path.stat().st_size != len(content):
        return False
    expected = hashlib.sha256(content).digest()
    observed = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            observed.update(chunk)
    return observed.digest() == expected


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
    return select_workspace_path(prompt, start_in=start_in).path


@dataclass(frozen=True)
class PathPickerResult:
    outcome: Literal["chosen", "cancelled", "unavailable"]
    path: str | None = None


def select_workspace_path(
    prompt: str, start_in: Path | None = None, *, file: bool = False,
) -> PathPickerResult:
    """Open a native path picker and return its selection, if any.

    ``start_in`` only positions the dialog. Opening it on the folder the
    application would suggest saves the user from navigating to a location they
    are about to accept, and costs nothing when the location does not exist.
    """

    # Prompts are supplied by application code, never by a request body.
    system = platform.system()
    if system not in {"Darwin", "Windows"} and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return PathPickerResult("unavailable")
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
                f"POSIX path of (choose {'file' if file else 'folder'} with prompt "
                f'"{prompt}"{location})',
            ]
        ]
    elif system == "Windows":
        selected_path = (
            f"$f.{'InitialDirectory' if file else 'SelectedPath'} = '{opening}'; " if opening is not None else ""
        )
        commands = [
            [
                "powershell",
                "-Command",
                "Add-Type -AssemblyName System.Windows.Forms; "
                f"$f = New-Object System.Windows.Forms.{'OpenFileDialog' if file else 'FolderBrowserDialog'}; "
                f"$f.{'Title' if file else 'Description'} = '{prompt}'; "
                f"{selected_path}"
                f"if ($f.ShowDialog() -eq 'OK') {{ $f.{'FileName' if file else 'SelectedPath'} }} "
                "else { Write-Output 'WG_PICKER_CANCELLED' }",
            ]
        ]
    else:
        zenity = ["zenity", "--file-selection", *([] if file else ["--directory"]), f"--title={prompt}"]
        if opening is not None:
            zenity.append(f"--filename={opening}/")
        commands = [
            zenity,
            ["kdialog", "--getopenfilename" if file else "--getexistingdirectory", str(opening or ".")],
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
        selected = result.stdout.strip()
        if result.returncode == 0 and selected and selected != "WG_PICKER_CANCELLED":
            return PathPickerResult("chosen", selected if file else selected.rstrip("/"))
        # Exit code 1 alone is ambiguous: display/utility failures use it too.
        # Only each utility's documented cancel signal ends the picker chain.
        cancelled = (
            (system == "Darwin" and result.returncode == 1 and "(-128)" in result.stderr)
            or (system == "Windows" and result.returncode == 0 and selected == "WG_PICKER_CANCELLED")
            or (system not in {"Darwin", "Windows"} and result.returncode == 1
                and not selected and not result.stderr.strip())
        )
        if cancelled:
            return PathPickerResult("cancelled")
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        select = filedialog.askopenfilename if file else filedialog.askdirectory
        try:
            selected = select(
                title=prompt,
                **({"initialdir": str(opening)} if opening is not None else {}),
            )
        finally:
            root.destroy()
        return PathPickerResult("chosen", str(selected)) if selected else PathPickerResult("cancelled")
    except Exception:
        return PathPickerResult("unavailable")


class WorkspaceState:
    def __init__(
        self,
        data_dir: Path,
        *,
        default_path: Path | None = None,
        legacy_defaults: Sequence[Path] = (),
        allow_adoption: bool = True,
    ) -> None:
        paths = data_paths(data_dir)
        self.default_path = (
            Path(default_path).expanduser().absolute()
            if default_path is not None
            else paths.workspace.resolve()
        )
        self.settings_path = (paths.root / "workspace_settings.json").resolve()
        self.legacy_defaults = tuple(
            Path(candidate).expanduser().resolve() for candidate in legacy_defaults
        )
        self.folders: FolderRoot | None = None
        self._selected: Path | None = None
        self._loaded = False
        if allow_adoption:
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
        if self.folders is not None:
            path = self.folders.effective("runs")
            if path == self.folders.implicit_runs_path:
                path.mkdir(parents=True, exist_ok=True)
            if not path.is_dir():
                raise WorkspaceUnavailableError(path)
            return path
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
        if self.folders is not None:
            if not isinstance(self, CadWorkspaceState) and not self.folders.runs_selected:
                self._selected = None
                return None
            path = self.folders.effective("cadlink" if isinstance(self, CadWorkspaceState) else "runs")
            self._selected = path
            return path if path.is_dir() else None
        if not self._loaded:
            self._load()
        if self._selected is not None and not self._selected.is_dir():
            # Said once per path, not on every poll that asks while it is gone.
            if getattr(self, "_reported_unavailable", None) != self._selected:
                logger.warning("Selected workspace path is unavailable: %s", self._selected)
                self._reported_unavailable = self._selected
            return None
        self._reported_unavailable = None
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
        candidate = Path(raw_path).expanduser().absolute()
        self._selected = candidate
        if not candidate.is_dir():
            logger.warning("Persisted workspace path is unavailable: %s", candidate)

    def select(self, path: Path) -> None:
        if self.folders is not None:
            self.folders.set_override("runs", path)
            return
        resolved = path.expanduser().absolute()
        if not resolved.is_dir():
            raise ValueError(f"Selected path is not a directory: {resolved}")
        _write_json_atomic(
            self.settings_path,
            {"schemaVersion": 1, "workspacePath": str(resolved)},
        )
        self._selected = resolved
        self._loaded = True


class ExportDestinationStore:
    """The folder a manual export was last sent to, and the handles for it.

    Deliberately separate from ``WorkspaceState``. Choosing where one export
    goes must not repoint the workspace: the workspace is where automatic
    exports, run archives and CAD projects live, and a user answering "put this
    STL on the Desktop" is not asking for their run history to move there.

    **The handle is a lifecycle, not a privilege boundary.** What keeps a page
    on the internet out of these endpoints is the loopback Host and Origin guard
    in ``server/app.py``; a client that passes it can name any directory by
    typing one, exactly as it already can for the workspace. What a handle does
    add is that ``write-export`` has no path parameter at all: the destination
    of a write is a folder resolved in an earlier, explicit request, so a
    mistyped or stale path fails when it is chosen rather than when files are
    landing, and the write endpoint cannot be aimed somewhere by a request that
    never asked for it.
    """

    SETTINGS_NAME = "export_settings.json"
    SETTINGS_KEY = "lastExportPath"

    def __init__(self, root: Path) -> None:
        self.settings_path = (Path(root) / self.SETTINGS_NAME).resolve()
        self._handles: "OrderedDict[str, tuple[Path, float]]" = OrderedDict()

    def remembered(self) -> Path | None:
        """The last folder an export was actually written to, if it still is one."""

        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        raw_path = str(payload.get(self.SETTINGS_KEY) or "").strip()
        if not raw_path:
            return None
        candidate = Path(raw_path).expanduser().resolve()
        return candidate if candidate.is_dir() else None

    def remember(self, path: Path) -> None:
        """Record where an export landed, so the next one opens there."""

        _write_json_atomic(
            self.settings_path,
            {"schemaVersion": 1, self.SETTINGS_KEY: str(path)},
        )

    def issue(self, path: Path, *, now: float | None = None) -> str:
        """Hand out one handle to a directory this server resolved.

        Reusable until it expires -- see ``EXPORT_DESTINATION_TTL_SECONDS``.
        """

        resolved = Path(path).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"Selected path is not a directory: {resolved}")
        token = secrets.token_urlsafe(18)
        self._handles[token] = (resolved, time.monotonic() if now is None else now)
        while len(self._handles) > MAX_EXPORT_DESTINATIONS:
            self._handles.popitem(last=False)
        return token

    def resolve(self, token: str, *, now: float | None = None) -> Path:
        """The directory a handle names, or a refusal a user can act on."""

        moment = time.monotonic() if now is None else now
        entry = self._handles.get(token)
        if entry is not None and moment - entry[1] > EXPORT_DESTINATION_TTL_SECONDS:
            del self._handles[token]
            entry = None
        if entry is None:
            raise KeyError(
                "That export destination is no longer available. "
                "Choose the folder again."
            )
        path = entry[0]
        if not path.is_dir():
            raise ValueError(f"The chosen export folder is unavailable: {path}")
        return path


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

    def __init__(self, data_dir: Path, *, proposed_path: Path | None = None, allow_adoption: bool = True) -> None:
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
        if allow_adoption:
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
            candidate = Path(raw_path).expanduser().absolute()
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

    def missing_path(self) -> Path | None:
        if self.selected_path() is not None:
            return None
        return self.folders.effective("cadlink") if self.folders is not None else self._selected

    def create_proposed_if_requested(self, path: Path) -> None:
        """Create the folder this class proposed, and only that one.

        Accepting the suggested location must not be a two-step chore in Finder,
        but a select route that creates whatever path it is handed would turn a
        typo into a new empty CAD exchange the add-in then cannot find.
        """

        resolved = path.expanduser()
        proposed = self.folders.root / "cadlink" if self.folders is not None else self.proposed_path
        if resolved.is_dir() or resolved != proposed:
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
            self._capture_mode = "off" if stored_mode == "off" else "run"
        else:
            self._capture_mode = "off" if payload.get(self.CAPTURE_KEY) is False else "run"
        raw_path = str(payload.get(self.SETTINGS_KEY) or "").strip()
        if raw_path:
            candidate = Path(raw_path).expanduser().absolute()
            self._selected = candidate
            if not candidate.is_dir():
                logger.warning("Persisted WGLink path is unavailable: %s", candidate)
        # Keep is mirrored as run for older WG; project is an accepted legacy input.
        if stored_mode == "project" and not (self.folders is not None and self.folders.read_only):
            try:
                self._persist()
            except OSError:
                logger.warning("Could not update the legacy Fusion capture setting", exc_info=True)

    def select(self, path: Path) -> None:
        if self.folders is not None:
            self.folders.set_override("cadlink", path)
            return
        resolved = path.expanduser().absolute()
        if not resolved.is_dir():
            raise ValueError(f"Selected path is not a directory: {resolved}")
        if not self._loaded:
            self._load()
        self._selected = resolved
        self._loaded = True
        self._persist()

    @property
    def capture_mode(self) -> CaptureMode:
        """Keep (``run``) or Don't keep (``off``), with lazy settings loading.

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

        if self.folders is not None and self.folders.read_only:
            raise ValueError("Folder settings are read-only; restore access before changing capture settings.")
        if mode not in CAPTURE_MODES:
            raise ValueError(f"Unknown capture mode: {mode}")
        if not self._loaded:
            self._load()
        self._capture_mode = "off" if mode == "off" else "run"
        self._persist()

    def _persist(self) -> None:
        """Write both settings together so neither erases the other.

        Choosing a folder used to rewrite this file wholesale, which would drop
        the capture choice the next time a folder was picked.
        """

        payload: dict[str, Any] = {
            **read_settings(self.settings_path),
            "schemaVersion": 1,
            # Written together and always: the add-in reads only the boolean,
            # so it must always agree with the two-way Keep setting.
            self.CAPTURE_KEY: self._capture_mode != "off",
            self.CAPTURE_MODE_KEY: self._capture_mode,
        }
        if self._selected is not None and (self.folders is None or self._selected.is_dir()
                                          or "cadlink" in self.folders.overrides
                                          or stored_path(payload.get(self.SETTINGS_KEY)) is not None):
            payload[self.SETTINGS_KEY] = str(self._selected)
        _write_json_atomic(self.settings_path, payload)


def create_cad_workspace_router(state: CadWorkspaceState) -> APIRouter:
    router = APIRouter(prefix="/api/cad-workspace", tags=["cadlink"])

    @router.get("/path")
    async def cad_workspace_path() -> dict[str, Any]:
        selected = state.selected_path()
        missing = state.missing_path()
        return {
            "path": str(selected) if selected is not None else None,
            "selected": selected is not None,
            "missingPath": str(missing) if missing is not None else None,
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
        # Compatibility entry point: new choices are WG roots, never overrides.
        if state.folders is not None:
            await _choose_folder_root(state.folders, payload.path if payload is not None else None)
            current = state.selected_path()
            return {"selected": current is not None, "path": str(current) if current is not None else None}
        selected = (
            payload.path
            if payload is not None
            else await asyncio.to_thread(
                _select_workspace_folder, "Select WGLink folder", state.selected_path() or state._selected or state.proposed_path
            )
        )
        if selected:
            try:
                state.create_proposed_if_requested(Path(selected))
            except OSError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not create the WGLink folder: {exc}",
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


def _write_export_sync(workspace_path: Path, subdirectory: str, existing: str,
                       members: list[tuple[str, bytes]]) -> dict[str, Any]:
    from server.workspace.run_moves import archive_write

    with archive_write(workspace_path / subdirectory):
        return _write_export_unlocked(workspace_path, subdirectory, existing, members)


def _write_export_unlocked(
    workspace_path: Path,
    subdirectory: str,
    existing: str,
    members: list[tuple[str, bytes]],
) -> dict[str, Any]:
    """Validate and publish an export set from a worker thread.

    An empty ``subdirectory`` writes into the root itself. Only a folder the
    user chose for this one export reaches that branch -- the route refuses it
    for the workspace -- because the whole point of choosing a destination is
    that the files land in it rather than in a folder invented underneath it.
    """

    workspace_root = workspace_path.resolve()
    try:
        subdirectory_segments = (
            _path_segments(subdirectory, "subdirectory") if subdirectory else []
        )
        if subdirectory_segments:
            export_directory = workspace_root.joinpath(*subdirectory_segments).resolve()
            _strictly_inside(export_directory, workspace_root, "subdirectory")
        else:
            if existing == "reject":
                # `reject` publishes by renaming a staging directory over the
                # export directory. Aimed at a folder the user already had, that
                # would replace its entire contents.
                raise ValueError(
                    "An export into the chosen folder itself cannot use "
                    "existing=reject"
                )
            export_directory = workspace_root

        prepared: list[tuple[list[str], bytes, Path]] = []
        total_bytes = 0
        seen: set[Path] = set()
        portable_seen: set[tuple[str, ...]] = set()
        for index, (relative_path, content) in enumerate(members):
            label = f"members[{index}].relative_path"
            segments = _path_segments(relative_path, label)
            if segments[-1].casefold() == "design.json":
                raise ValueError("design.json is reserved for the server archive owner")
            destination = export_directory.joinpath(*segments).resolve()
            _strictly_inside(destination, workspace_root, label)
            if destination == export_directory or export_directory not in destination.parents:
                raise ValueError(f"{label} resolves outside the export subdirectory")
            portable_key = _portable_path_key(segments)
            if destination in seen or portable_key in portable_seen:
                raise ValueError(f"{label} duplicates another member path")
            seen.add(destination)
            portable_seen.add(portable_key)
            total_bytes += len(content)
            if total_bytes > MAX_EXPORT_BYTES:
                raise ValueError(
                    f"Export set exceeds the {MAX_EXPORT_BYTES}-byte binary size limit"
                )
            prepared.append((segments, content, destination))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    export_exists = export_directory.exists() or export_directory.is_symlink()
    if export_exists and existing == "reject":
        raise HTTPException(
            status_code=409, detail=f"Export directory already exists: {export_directory}"
        )
    if export_exists:
        if export_directory.is_symlink() or not export_directory.is_dir():
            raise HTTPException(
                status_code=409,
                detail=f"Export path is not a directory: {export_directory}",
            )

    pending = prepared
    if export_exists and existing in {"merge_identical", "confirm"}:
        # One pass answers both policies: what is missing is written, what is
        # byte identical is a no-op, and what differs is either the refusal
        # (`merge_identical`) or the list the user is asked about once
        # (`confirm`).
        pending = []
        differing: list[Path] = []
        for segments, content, destination in prepared:
            if not (destination.exists() or destination.is_symlink()):
                pending.append((segments, content, destination))
                continue
            if existing == "confirm" and destination.is_dir() and not destination.is_symlink():
                raise HTTPException(
                    status_code=409,
                    detail=f"Export path is a directory, not a file: {destination}",
                )
            try:
                identical = _retry_after_acl_repair(
                    destination,
                    lambda: _streaming_file_matches(destination, content),
                    workspace_root=workspace_root,
                )
            except OSError as exc:
                # "Different content" would be a claim about bytes nobody read.
                # Say which it is, because the two need different answers: one
                # is a name collision, the other is a file this account cannot
                # open. See `server/platform/staging.py` for how the app used
                # to write files it could not read back.
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Cannot read the existing export file {destination} "
                        f"({exc.strerror or exc}); refusing to merge into it."
                    ),
                ) from exc
            if not identical:
                if existing == "confirm":
                    differing.append(destination)
                    pending.append((segments, content, destination))
                    continue
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Export file already exists with different content: "
                        f"{destination}"
                    ),
                )
        if differing:
            # Before anything is staged, so a declined question leaves the
            # folder exactly as it was.
            raise ExportCollision(export_directory, differing)
    if export_exists and existing == "overwrite":
        # Replacing a file is the point here; replacing a *directory* with a
        # file is not, and would surface as an opaque write failure below.
        for segments, content, destination in prepared:
            if destination.is_dir() and not destination.is_symlink():
                raise HTTPException(
                    status_code=409,
                    detail=f"Export path is a directory, not a file: {destination}",
                )

    response = {
        "directory": str(export_directory),
        "files": [str(destination) for _segments, _content, destination in prepared],
        # Named, not merely counted: a manual export into a folder the user
        # chose may sit next to files WG did not write, and "3 files written"
        # says nothing about which of them used to be something else.
        "replaced": [
            str(destination)
            for _segments, _content, destination in pending
            if destination.exists() or destination.is_symlink()
        ],
    }
    # A byte-identical merge retry is a genuine no-op: do not even create and
    # remove a staging directory, since that still generates watcher traffic.
    if not pending:
        return response

    # Staging lives beside the files it publishes, so `os.replace` stays on one
    # filesystem. For a chosen destination that is the folder itself: its parent
    # is the user's, not ours, and may not even be writable.
    staging_parent = (
        export_directory if export_directory == workspace_root else export_directory.parent
    )
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging_directory = publish_staging_directory(
        staging_parent, ".wg2-export-staging-"
    )
    try:
        for segments, content, _destination in pending:
            staged_file = staging_directory.joinpath(*segments)
            staged_file.parent.mkdir(parents=True, exist_ok=True)
            staged_file.write_bytes(content)
        if existing == "reject":
            os.replace(staging_directory, export_directory)
        else:
            export_directory.mkdir(exist_ok=True)
            for segments, _content, destination in pending:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging_directory.joinpath(*segments), destination)
            shutil.rmtree(staging_directory, ignore_errors=True)
    except Exception as exc:
        shutil.rmtree(staging_directory, ignore_errors=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to write export set: {exc}"
        ) from exc

    return response


_WRITE_EXPORT_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": WriteExportRequest.model_json_schema()},
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["subdirectory", "relative_path", "file"],
                    "properties": {
                        "subdirectory": {"type": "string"},
                        "job_id": {"type": "string", "description": "Run whose recorded location owns this write."},
                        "existing": {
                            "type": "string",
                            "enum": [
                                "reject",
                                "merge_identical",
                                "overwrite",
                                "confirm",
                            ],
                            "default": "reject",
                        },
                        "destination": {
                            "type": "string",
                            "description": (
                                "Handle from POST /api/workspace/export-destination. "
                                "Absent writes into the workspace."
                            ),
                        },
                        "relative_path": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "file": {
                            "type": "array",
                            "items": {"type": "string", "format": "binary"},
                        },
                    },
                }
            },
        },
    }
}


def create_workspace_router(
    state: WorkspaceState, destinations: ExportDestinationStore | None = None
) -> APIRouter:
    router = APIRouter(prefix="/api/workspace", tags=["workspace"])
    export_destinations = destinations or ExportDestinationStore(
        state.settings_path.parent
    )

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

    def destination_offer(selected: bool, *, with_token: bool = True) -> dict[str, Any]:
        """The folder the next manual export would default to, and its handle.

        The remembered folder wins; with none, the workspace is the suggestion,
        which keeps a first export landing where every export landed before this
        dialog existed. Only the handle is usable, so the path here is for the
        user to read.
        """

        remembered = export_destinations.remembered()
        path = remembered
        if path is None:
            try:
                path = state.path()
            except Exception:
                path = state.selected_path()
        token: str | None = None
        if path is not None and with_token:
            try:
                token = export_destinations.issue(path)
            except (OSError, ValueError):
                path, token = None, None
        return {
            "selected": selected,
            "path": str(path) if path is not None else None,
            "remembered": remembered is not None,
            "token": token,
        }

    @router.get("/export-destination")
    async def export_destination() -> Any:
        return destination_offer(False)

    @router.post("/export-destination")
    async def choose_export_destination(
        payload: ChooseExportDestinationRequest | None = None,
    ) -> Any:
        """Ask the user where this export goes, without moving the workspace.

        Cancelling answers ``selected: false`` and writes nothing -- neither a
        file nor the remembered folder, which only a completed export moves.
        """

        chosen = (
            payload.path
            if payload is not None
            else await asyncio.to_thread(
                _select_workspace_folder,
                "Choose export folder",
                export_destinations.remembered() or picker_start(),
            )
        )
        if not chosen:
            # No handle: cancelling answers "you chose nothing", and the client
            # keeps whatever folder it was already showing. Minting one here
            # would evict a live handle to hand back a folder nobody asked for.
            return destination_offer(False, with_token=False)
        try:
            token = export_destinations.issue(Path(chosen))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "selected": True,
            "path": str(Path(chosen).expanduser().resolve()),
            "remembered": False,
            "token": token,
        }

    @router.post(
        "/write-export",
        openapi_extra=_WRITE_EXPORT_OPENAPI,
        responses={
            422: {
                "description": "Validation Error",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/HTTPValidationError"}
                    }
                },
            }
        },
    )
    async def workspace_write_export(request: Request) -> Any:
        from server.workspace.run_moves import archive_write, MoveRefused

        try:
            # Root-wide guard also covers unowned old-client exports. Recorded
            # roots are additionally guarded in claim_run_write_path/the writer.
            # Guard the configured root even if offline. A one-off destination
            # can still work while the current Runs drive is disconnected.
            try:
                guarded_root = state.path()
            except WorkspaceUnavailableError as exc:
                guarded_root = exc.path
            with archive_write(guarded_root):
                return await workspace_write_export_guarded(request)
        except MoveRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorkspaceUnavailableError as exc:
            return _workspace_unavailable_response(exc)

    async def workspace_write_export_guarded(request: Request) -> Any:
        try:
            if isinstance(request, WriteExportRequest):
                # Direct endpoint calls in unit tests retain the legacy model
                # shape; actual ASGI requests always take one branch below.
                subdirectory, existing, members, destination, job_id = await asyncio.to_thread(
                    _decode_json_export_request, request
                )
            elif request.headers.get("content-type", "").lower().startswith(
                "multipart/form-data"
            ):
                subdirectory, existing, members, destination, job_id = (
                    await _decode_multipart_export_request(request)
                )
            else:
                raw = await request.body()
                subdirectory, existing, members, destination, job_id = await asyncio.to_thread(
                    _decode_json_export_request, raw
                )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if destination is None and job_id is not None:
            jobs = request.app.state.jobs_runtime
            row = await asyncio.to_thread(jobs.store.get_job_row, job_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Run not found")
            source = jobs._serialize_job(row, cadlink_store=request.app.state.cadlink_store).get("cad_source") or {}
            location = archive_identity.job_archive_location(row, source)
            prefix = f"{location.folder}/{location.run}"
            if subdirectory not in (prefix, prefix + "/exports"):
                raise HTTPException(status_code=422, detail="Run export must use its assigned folder")
            suffix = subdirectory[len(prefix):]
            try:
                path = await asyncio.to_thread(
                    archive_identity.claim_run_write_path, jobs.store, row, state.path, source
                )
            except OSError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            root = path.parent.parent
            subdirectory = f"{path.parent.name}/{path.name}{suffix}"
        elif destination is None:
            # Automatic exports must work on first launch without a native
            # folder picker. Production supplies ``<checkout>/output`` as this
            # fallback; an explicit selection still overrides it.
            root = available_path()
            if isinstance(root, JSONResponse):
                return root
            if not subdirectory:
                raise HTTPException(status_code=422, detail="subdirectory is required")
        else:
            try:
                root = export_destinations.resolve(destination)
            except KeyError as exc:
                raise HTTPException(status_code=409, detail=exc.args[0]) from exc
            except (OSError, ValueError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        try:
            written = await asyncio.to_thread(
                _write_export_sync,
                root,
                subdirectory,
                existing,
                members,
            )
        except ExportCollision as collision:
            return _export_collision_response(collision)
        if destination is not None:
            # "Last used", not "last chosen": a cancelled or refused export must
            # not move the folder the next one opens in.
            try:
                await asyncio.to_thread(export_destinations.remember, root)
            except OSError:
                logger.warning("Could not remember the export folder %s", root)
        return written

    return router


async def _choose_folder_root(folders: FolderRoot, path: str | None) -> dict[str, Any]:
    selected = path if path is not None else await asyncio.to_thread(
        _select_workspace_folder, "Select WG folder", folders.root
    )
    if selected:
        try:
            folders.select(Path(selected))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**folders.info(), "selected": bool(selected)}


def create_folder_root_router(folders: FolderRoot) -> APIRouter:
    router = APIRouter(prefix="/api/folders", tags=["workspace"])

    @router.get("/path")
    async def root_path() -> dict[str, Any]:
        return folders.info()

    @router.post("/select")
    async def root_select(payload: SelectWorkspaceRequest | UseRootFolderRequest | None = None) -> dict[str, Any]:
        if isinstance(payload, UseRootFolderRequest):
            try:
                folders.use_root(payload.useRoot,
                                 Path(payload.expectedPath) if payload.expectedPath is not None else None,
                                 Path(payload.expectedCurrentPath) if payload.expectedCurrentPath is not None else None)
            except (OSError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return folders.info()
        return await _choose_folder_root(folders, payload.path if payload is not None else None)

    @router.post("/open")
    async def root_open() -> dict[str, Any]:
        try:
            if not folders.root.is_dir():
                raise OSError(f"Selected root folder is unavailable: {folders.root}")
            subprocess.Popen(open_folder_command(folders.root), **background_process_kwargs())
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return folders.info()

    return router


def mount_workspace(
    application: FastAPI,
    *,
    default_path: Path | None = None,
    legacy_defaults: Sequence[Path] = (),
    default_root: Path | None = None,
    create_implicit_runs: bool = False,
    explicit_runs: bool = False,
) -> WorkspaceState:
    data_dir = Path(application.state.data_dir)
    known_settings: set[Path] = set()
    startup_read = read_folder_settings(data_dir, known_files=known_settings)
    settings = startup_read.settings
    version = settings["folders.json"].get("schemaVersion")
    unsupported = "schemaVersion" in settings["folders.json"] and (type(version) is not int or version != 1)
    root = settings["folders.json"].get("rootPath")
    recorded_root = isinstance(root, str) and Path(root).is_absolute()
    allow_adoption = not (startup_read.failed or unsupported or recorded_root)
    state = WorkspaceState(
        data_dir,
        default_path=default_path,
        legacy_defaults=legacy_defaults,
        allow_adoption=allow_adoption and data_dir / "workspace_settings.json" not in known_settings,
    )
    cad_state = CadWorkspaceState(
        data_dir, allow_adoption=allow_adoption and data_dir / "cadlink_settings.json" not in known_settings)
    folders = FolderRoot(
        Path(application.state.data_dir),
        default_root=default_root or default_folder_root(Path(application.state.data_dir)),
        legacy_runs=state.default_path,
        writer=_write_json_atomic,
        create_implicit_runs=create_implicit_runs,
        explicit_runs=explicit_runs,
        startup_read=startup_read,
        known_settings=known_settings,
    )

    def refresh() -> None:
        for name, target in (("runs", state), ("cadlink", cad_state)):
            target._loaded = False
            target._selected = None
            target.default_path = folders.effective(name)
            if isinstance(target, CadWorkspaceState):
                target.proposed_path = folders.effective(name)

    folders.on_change = refresh
    state.folders = cad_state.folders = folders
    refresh()
    application.state.folders = folders
    application.state.workspace = state
    application.state.cad_workspace = cad_state
    destinations = ExportDestinationStore(state.settings_path.parent)
    application.state.export_destinations = destinations
    application.include_router(create_folder_root_router(folders))
    application.include_router(create_workspace_router(state, destinations))
    application.include_router(create_cad_workspace_router(cad_state))
    return state


__all__ = [
    "WorkspaceState",
    "CadWorkspaceState",
    "ChooseExportDestinationRequest",
    "ExportCollision",
    "ExportDestinationStore",
    "CaptureDocumentRequest",
    "WriteExportRequest",
    "SelectCadWorkspaceRequest",
    "SelectWorkspaceRequest",
    "create_workspace_router",
    "create_cad_workspace_router",
    "mount_workspace",
]
