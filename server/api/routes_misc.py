"""
Miscellaneous routes: health, updates, chart rendering, directivity rendering, file export,
workspace path/open.
"""

import asyncio
import hashlib
import json
import logging
import platform
import subprocess
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from contracts import ChartsRenderRequest, DirectivityRenderRequest
from services.runtime_preflight import collect_runtime_doctor_report
from services.solver_runtime import (
    BEMPP_SOLVER_READY,
    DEFAULT_CHART_THEME,
    SOLVER_AVAILABLE,
    HORNLAB_MESHER_AVAILABLE,
    HORNLAB_MESHER_RUNTIME_READY,
    bempp_backend_status,
    build_theme_montage_b64,
    get_dependency_status,
    get_settings_capabilities,
    is_metal_fast_solve_ready,
    list_available_themes,
    metal_backend_status,
    render_all_charts,
    render_directivity_plot,
)
from services.update_service import get_update_status

logger = logging.getLogger(__name__)

router = APIRouter()

_MATPLOTLIB_RENDER_LOCK = threading.Lock()
_RENDER_CACHE_MAX_ENTRIES = 32
_RENDER_CACHE: OrderedDict[str, Any] = OrderedDict()
_CACHE_MISS = object()


def _stable_render_cache_key(render_type: str, inputs: Dict[str, Any]) -> str:
    serialized = json.dumps(
        inputs,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"{render_type}:{hashlib.sha256(serialized).hexdigest()}"


def _render_cache_get(key: str) -> Any:
    try:
        value = _RENDER_CACHE.pop(key)
    except KeyError:
        return _CACHE_MISS
    _RENDER_CACHE[key] = value
    return value


def _render_cache_put(key: str, value: Any) -> None:
    _RENDER_CACHE.pop(key, None)
    _RENDER_CACHE[key] = value
    while len(_RENDER_CACHE) > _RENDER_CACHE_MAX_ENTRIES:
        _RENDER_CACHE.popitem(last=False)


def _render_with_matplotlib_lock(render_function: Any, *args: Any, **kwargs: Any) -> Any:
    with _MATPLOTLIB_RENDER_LOCK:
        return render_function(*args, **kwargs)


def _coerce_form_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


@router.get("/")
async def root() -> Dict[str, Any]:
    """Root endpoint."""
    return {
        "name": "MWG Horn BEM Solver",
        "version": "1.1.0",
        "status": "running",
        "solver_available": SOLVER_AVAILABLE,
    }


@router.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint."""
    logger.info("Health check requested")
    dependency_status = get_dependency_status()
    doctor_report = collect_runtime_doctor_report("auto")
    metal_status = metal_backend_status()
    metal_ready = is_metal_fast_solve_ready(metal_status)
    bempp_status = bempp_backend_status()
    solver_ready = bool(metal_ready or BEMPP_SOLVER_READY)
    solver_name = (
        "metal-bem"
        if metal_ready
        else "bempp-bem" if BEMPP_SOLVER_READY else "unavailable"
    )

    return {
        "status": "ok",
        "solver": solver_name,
        "solverReady": solver_ready,
        "solverBackends": {
            "metal": {"ready": metal_ready, "status": metal_status},
            "bempp": {"ready": bool(BEMPP_SOLVER_READY), "status": bempp_status},
        },
        "mesherReady": HORNLAB_MESHER_AVAILABLE and HORNLAB_MESHER_RUNTIME_READY,
        "dependencies": dependency_status,
        "dependencyDoctor": {
            "schemaVersion": doctor_report.get("schemaVersion"),
            "generatedAt": doctor_report.get("generatedAt"),
            "platform": doctor_report.get("platform"),
            "summary": doctor_report.get("summary"),
            "components": doctor_report.get("components"),
            "solveReadiness": doctor_report.get("solveReadiness"),
        },
        "capabilities": get_settings_capabilities(),
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/api/updates/check")
async def check_updates() -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(get_update_status)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/api/themes")
async def list_chart_themes() -> Dict[str, Any]:
    """
    List the chart themes the backend can render result charts in.

    Returns each theme's registry ``name`` plus a short human ``label`` (so the
    frontend does not hardcode the hornlab-plots registry) and marks the default.
    """
    themes = list_available_themes()
    return {"themes": themes, "default": DEFAULT_CHART_THEME}


@router.get("/api/theme-preview")
async def theme_preview(theme: Optional[str] = None) -> Dict[str, str]:
    """
    Render a 2x2 montage preview (directivity heatmap, frequency response,
    directivity index, impedance) for a theme from synthetic demo data.

    Returns a base64 PNG data URI. Results are cached per theme on the backend.
    """
    try:
        image_b64 = await asyncio.to_thread(
            _render_with_matplotlib_lock,
            build_theme_montage_b64,
            theme,
        )
        return {
            "theme": theme or DEFAULT_CHART_THEME,
            "image": f"data:image/png;base64,{image_b64}",
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Theme preview failed: {exc}") from exc


@router.post("/api/render-charts")
async def render_charts(request: ChartsRenderRequest) -> Dict[str, Any]:
    """
    Render all result charts as PNG images using Matplotlib.
    Returns base64-encoded PNGs for each chart type. ``request.theme`` selects
    the hornlab-plots theme (falls back to the backend default when omitted).
    """
    try:
        payload = request.model_dump()
        payload["theme"] = request.theme
        cache_key = _stable_render_cache_key("charts", payload)
        cached = _render_cache_get(cache_key)
        if cached is not _CACHE_MISS:
            return {"charts": dict(cached)}

        charts = await asyncio.to_thread(
            _render_with_matplotlib_lock,
            render_all_charts,
            payload,
        )
        result = {}
        for key, b64 in charts.items():
            if b64 is not None:
                result[key] = f"data:image/png;base64,{b64}"
        _render_cache_put(cache_key, dict(result))
        return {"charts": result}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chart rendering failed: {exc}") from exc


@router.post("/api/render-directivity")
async def render_directivity(request: DirectivityRenderRequest) -> Dict[str, str]:
    """
    Render directivity heatmap as a PNG image using Matplotlib.
    Returns base64-encoded PNG. ``request.theme`` selects the hornlab-plots
    theme (falls back to the backend default when omitted).
    """
    if not request.frequencies or not request.directivity:
        raise HTTPException(status_code=422, detail="Missing frequencies or directivity data")

    try:
        render_inputs = {
            "frequencies": request.frequencies,
            "directivity": request.directivity,
            "reference_level": request.reference_level,
            "theme": request.theme,
        }
        cache_key = _stable_render_cache_key("directivity", render_inputs)
        cached = _render_cache_get(cache_key)
        if cached is not _CACHE_MISS:
            return {"image": cached}

        image_b64 = await asyncio.to_thread(
            _render_with_matplotlib_lock,
            render_directivity_plot,
            request.frequencies,
            request.directivity,
            reference_level=request.reference_level,
            theme=request.theme,
        )
        if image_b64 is None:
            raise HTTPException(status_code=400, detail="No directivity patterns to render")
        image = f"data:image/png;base64,{image_b64}"
        _render_cache_put(cache_key, image)
        return {"image": image}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Rendering failed: {exc}") from exc


@router.post("/api/export-file")
async def export_file(
    file: UploadFile = File(...),
    workspace_subdir: str = Form(""),
    folder_path: Optional[str] = Form(None),
) -> Dict[str, str]:
    """
    Save an exported file to the backend-managed workspace root.
    Optional workspace_subdir writes into a nested folder under that root.
    """
    workspace_root = _get_default_output_path()

    # Backward-compatible alias while frontend migrates fully.
    requested_subdir = _coerce_form_string(workspace_subdir)
    if not requested_subdir:
        requested_subdir = _coerce_form_string(folder_path)

    if requested_subdir.startswith("/") or requested_subdir.startswith("\\"):
        raise HTTPException(status_code=400, detail="workspace_subdir must be relative to workspace root")

    subdir_parts = requested_subdir.replace("\\", "/").split("/")
    normalized_parts = [part.strip() for part in subdir_parts if part.strip()]
    if any(part in {".", ".."} for part in normalized_parts):
        raise HTTPException(status_code=400, detail="Invalid workspace_subdir")

    target_dir = (workspace_root / Path(*normalized_parts)).resolve()
    if workspace_root != target_dir and workspace_root not in target_dir.parents:
        raise HTTPException(status_code=400, detail="Invalid workspace_subdir")

    raw_filename = str(file.filename or "").strip()
    if (
        not raw_filename
        or raw_filename in {".", ".."}
        or "/" in raw_filename
        or "\\" in raw_filename
        or "\x00" in raw_filename
    ):
        raise HTTPException(status_code=400, detail="Invalid export filename")

    file_path = (target_dir / raw_filename).resolve()
    if file_path.parent != target_dir:
        raise HTTPException(status_code=400, detail="Invalid export filename")

    try:
        # Create folder if it doesn't exist
        target_dir.mkdir(parents=True, exist_ok=True)

        # Save file
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)

        logger.info(f"File exported: {file_path}")
        return {
            "status": "success",
            "path": str(file_path),
            "filename": raw_filename,
            "workspaceRoot": str(workspace_root),
            "workspaceSubdir": str(Path(*normalized_parts)) if normalized_parts else ""
        }
    except Exception as exc:
        logger.error(f"Export failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc


# ── Workspace path / open / select ─────────────────────────────────────────────

_REPO_OUTPUT_PATH: Path = (Path(__file__).parent.parent.parent / "output").resolve()
_WORKSPACE_SETTINGS_PATH: Path = (
    Path(__file__).resolve().parents[1] / "data" / "workspace_settings.json"
)
_custom_workspace_path: Optional[Path] = None
_workspace_path_loaded: bool = False


def _load_workspace_path_preference() -> None:
    """Hydrate the selected output folder from backend settings once per process."""
    global _custom_workspace_path, _workspace_path_loaded
    if _workspace_path_loaded:
        return
    _workspace_path_loaded = True

    try:
        if not _WORKSPACE_SETTINGS_PATH.exists():
            return
        with open(_WORKSPACE_SETTINGS_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        logger.warning("Could not load workspace settings: %s", exc)
        return

    raw_path = str(payload.get("workspacePath") or "").strip() if isinstance(payload, dict) else ""
    if not raw_path:
        return

    resolved = Path(raw_path).expanduser().resolve()
    if resolved.is_dir():
        _custom_workspace_path = resolved
    else:
        logger.warning("Persisted workspace path is unavailable: %s", resolved)


def _persist_workspace_path_preference(path: Optional[Path]) -> None:
    """Persist the selected output folder so backend restarts keep the same workspace."""
    try:
        _WORKSPACE_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_WORKSPACE_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "schemaVersion": 1,
                    "workspacePath": str(path) if path is not None else None,
                },
                f,
                indent=2,
            )
            f.write("\n")
    except Exception as exc:
        logger.warning("Could not persist workspace settings: %s", exc)


def _set_custom_workspace_path(path: Optional[Path]) -> None:
    global _custom_workspace_path, _workspace_path_loaded
    _workspace_path_loaded = True
    _custom_workspace_path = path
    _persist_workspace_path_preference(path)


def _get_default_output_path() -> Path:
    """Return the absolute path of the current output folder."""
    _load_workspace_path_preference()
    if _custom_workspace_path is not None:
        output_path = _custom_workspace_path
    else:
        output_path = _REPO_OUTPUT_PATH
    output_path.mkdir(parents=True, exist_ok=True)
    return output_path


@router.post("/api/workspace/reset")
async def workspace_reset() -> Dict[str, Any]:
    """Reset the workspace path back to the default."""
    _set_custom_workspace_path(None)
    default_path = _get_default_output_path()
    logger.info("Workspace path reset to default: %s", default_path)
    return {"path": str(default_path), "custom": False}


@router.get("/api/workspace/path")
async def workspace_path() -> Dict[str, str]:
    """Return the absolute path of the current output folder."""
    output_path = _get_default_output_path()
    return {"path": str(output_path)}


@router.post("/api/workspace/path")
async def set_workspace_path(path: str = Form(...)) -> Dict[str, str]:
    """Set a custom output folder path."""
    raw = path.strip()
    if not raw:
        _set_custom_workspace_path(None)
        logger.info("Workspace path reset to default: %s", _REPO_OUTPUT_PATH)
        return {"path": str(_REPO_OUTPUT_PATH), "custom": False}

    resolved = Path(raw).expanduser().resolve()
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory does not exist: {resolved}")

    _set_custom_workspace_path(resolved)
    logger.info("Workspace path set to: %s", resolved)
    return {"path": str(resolved), "custom": True}


def _select_workspace_folder() -> Optional[str]:
    """Open a native folder picker and return its selection, if any."""
    system = platform.system()
    selected: Optional[str] = None

    try:
        if system == "Darwin":
            try:
                result = subprocess.run(
                    [
                        "osascript", "-e",
                        'set theFolder to POSIX path of (choose folder with prompt "Select output folder")',
                    ],
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode == 0 and result.stdout.strip():
                    selected = result.stdout.strip().rstrip("/")
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
                logger.warning("macOS folder picker failed: %s", exc)

        elif system == "Windows":
            try:
                result = subprocess.run(
                    ["powershell", "-Command",
                     "Add-Type -AssemblyName System.Windows.Forms; "
                     "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                     "$f.Description = 'Select output folder'; "
                     "if ($f.ShowDialog() -eq 'OK') { $f.SelectedPath }"],
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode == 0 and result.stdout.strip():
                    selected = result.stdout.strip()
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
                logger.warning("Windows folder picker failed: %s", exc)

        else:
            # Linux / other: try zenity, then kdialog, then tkinter
            for cmd in [
                ["zenity", "--file-selection", "--directory", "--title=Select output folder"],
                ["kdialog", "--getexistingdirectory", "."],
            ]:
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                    if result.returncode == 0 and result.stdout.strip():
                        selected = result.stdout.strip()
                        break
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    continue

        # Fallback to tkinter on any platform if nothing selected yet
        if selected is None:
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                chosen = filedialog.askdirectory(title="Select output folder")
                root.destroy()
                if chosen:
                    selected = chosen
            except Exception:
                pass
    except Exception as exc:
        logger.warning("Folder picker failed unexpectedly: %s", exc)
    return selected


@router.post("/api/workspace/select")
async def workspace_select() -> Dict[str, Any]:
    """Open a native OS folder picker and set the result as the workspace path."""
    selected = await asyncio.to_thread(_select_workspace_folder)

    if not selected:
        return {"selected": False, "path": str(_get_default_output_path())}

    resolved = Path(selected).resolve()
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail=f"Selected path is not a directory: {resolved}")

    _set_custom_workspace_path(resolved)
    logger.info("Workspace folder selected via OS picker: %s", resolved)
    return {"selected": True, "path": str(resolved)}


class _WorkspaceOpenRequest(BaseModel):
    subdir: Optional[str] = None


@router.post("/api/workspace/open")
async def workspace_open(body: _WorkspaceOpenRequest = _WorkspaceOpenRequest()) -> Dict[str, str]:
    """Open the output folder (or a task subfolder) in the OS file manager."""
    output_path = _get_default_output_path()

    # If a task subfolder was requested, resolve it safely within the workspace
    if body.subdir:
        safe_name = Path(body.subdir).name  # prevent path traversal
        if safe_name:
            output_path = output_path / safe_name

    # Ensure the folder exists so the file manager can open it
    try:
        output_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Cannot create output folder: {exc}") from exc

    if not output_path.exists():
        raise HTTPException(status_code=404, detail=f"Output folder not found: {output_path}")

    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", str(output_path)])
        elif system == "Windows":
            subprocess.Popen(["explorer", str(output_path)])
        else:
            subprocess.Popen(["xdg-open", str(output_path)])
    except Exception as exc:
        logger.error(f"Failed to open folder in file manager: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to open folder: {exc}") from exc

    logger.info(f"Opened output folder in file manager: {output_path}")
    return {"status": "opened", "path": str(output_path)}
