"""
MWG Horn BEM Solver Backend
FastAPI application for running acoustic simulations
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError, field_validator
from typing import Dict, List, Optional, Any, Union
import uuid
import asyncio
import subprocess
import threading
from collections import deque
from pathlib import Path
from datetime import datetime
from db import SimulationDB

# Import solver module (will be created)
try:
    from solver import BEMSolver
    from solver.contract import normalize_mesh_validation_mode
    from solver.deps import (
        BEMPP_RUNTIME_READY,
        GMSH_OCC_RUNTIME_READY,
        get_dependency_status
    )
    from solver.device_interface import normalize_device_mode
    SOLVER_AVAILABLE = BEMPP_RUNTIME_READY
    if SOLVER_AVAILABLE:
        print("[BEM] Device auto policy: opencl_gpu -> opencl_cpu -> numba")
except ImportError:
    SOLVER_AVAILABLE = False
    BEMPP_RUNTIME_READY = False
    GMSH_OCC_RUNTIME_READY = False

    def normalize_mesh_validation_mode(value: Any) -> str:
        mode = str(value or "warn").strip().lower()
        if mode not in {"strict", "warn", "off"}:
            raise ValueError("mesh_validation_mode must be one of: off, strict, warn.")
        return mode

    def normalize_device_mode(value: Any) -> str:
        mode = str(value or "auto").strip().lower()
        if mode not in {"auto", "opencl_cpu", "opencl_gpu", "numba"}:
            raise ValueError("device_mode must be one of: auto, opencl_cpu, opencl_gpu, numba.")
        return mode

    def get_dependency_status():
        return {
            "supportedMatrix": {},
            "runtime": {
                "python": {"version": None, "supported": False},
                "gmsh_python": {"available": False, "version": None, "supported": False, "ready": False},
                "bempp": {"available": False, "variant": None, "version": None, "supported": False, "ready": False}
            }
        }

    print("Warning: BEM solver not available. Install bempp-cl to enable simulations.")



try:
    from solver.waveguide_builder import build_waveguide_mesh
    WAVEGUIDE_BUILDER_AVAILABLE = True
except ImportError:
    build_waveguide_mesh = None
    WAVEGUIDE_BUILDER_AVAILABLE = False

try:
    from solver.gmsh_utils import gmsh_mesher_available
    from solver.gmsh_geo_mesher import generate_msh_from_geo
except ImportError:
    def gmsh_mesher_available() -> bool:
        return False

    def generate_msh_from_geo(*_args, **_kwargs):
        raise RuntimeError("Legacy .geo mesher backend is unavailable.")

app = FastAPI(title="MWG Horn BEM Solver", version="1.0.0")

# Enable CORS for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory runtime cache (kept for active scheduling and test compatibility).
jobs: Dict[str, Dict[str, Any]] = {}
job_queue: deque[str] = deque()
running_jobs: set[str] = set()
jobs_lock = threading.RLock()
scheduler_loop_running = False
max_concurrent_jobs = 1

db = SimulationDB(Path(__file__).resolve().parent / "data" / "simulations.db")
db_initialized = False


def ensure_db_ready() -> None:
    global db_initialized
    if db_initialized:
        return
    db.initialize()
    db_initialized = True


def _validate_occ_adaptive_bem_shell(enc_depth: float, wall_thickness: float) -> None:
    """Adaptive BEM requires either enclosure volume or wall shell thickness."""
    if float(enc_depth) <= 0.0 and float(wall_thickness) <= 0.0:
        raise ValueError(
            "Adaptive BEM simulation requires a closed shell. "
            "Increase enclosure depth or wall thickness."
        )


def _is_terminal_status(status: str) -> bool:
    return status in {"complete", "error", "cancelled"}


def _now_iso() -> str:
    return datetime.now().isoformat()


def _build_config_summary(request: "SimulationRequest") -> Dict[str, Any]:
    options = request.options if isinstance(request.options, dict) else {}
    mesh_opts = options.get("mesh", {}) if isinstance(options.get("mesh", {}), dict) else {}
    waveguide_params = mesh_opts.get("waveguide_params") if isinstance(mesh_opts.get("waveguide_params"), dict) else {}
    return {
        "formula_type": waveguide_params.get("formula_type"),
        "frequency_range": request.frequency_range,
        "num_frequencies": request.num_frequencies,
        "sim_type": str(request.sim_type),
    }


def _merge_job_cache_from_db(job_id: str) -> Optional[Dict[str, Any]]:
    ensure_db_ready()
    with jobs_lock:
        cached = jobs.get(job_id)
        if cached:
            return cached
    row = db.get_job_row(job_id)
    if not row:
        return None
    merged = {
        "id": row["id"],
        "status": row["status"],
        "progress": row["progress"],
        "stage": row.get("stage"),
        "stage_message": row.get("stage_message"),
        "created_at": row.get("created_at"),
        "queued_at": row.get("queued_at"),
        "started_at": row.get("started_at"),
        "completed_at": row.get("completed_at"),
        "error": row.get("error_message"),
        "error_message": row.get("error_message"),
        "has_results": row.get("has_results"),
        "has_mesh_artifact": row.get("has_mesh_artifact"),
        "label": row.get("label"),
        "cancellation_requested": row.get("cancellation_requested"),
        "config_summary": row.get("config_summary_json"),
    }
    config = row.get("config_json")
    if isinstance(config, dict):
        merged["request"] = config
        try:
            merged["request_obj"] = SimulationRequest(**config)
        except Exception:
            pass
    with jobs_lock:
        jobs[job_id] = merged
    return merged


def _set_job_fields(job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    ensure_db_ready()
    if not fields:
        return _merge_job_cache_from_db(job_id)

    if "progress" in fields:
        fields["progress"] = max(0.0, min(1.0, float(fields["progress"])))

    now = _now_iso()
    fields.setdefault("updated_at", now)
    mapped = dict(fields)
    if "error" in mapped and "error_message" not in mapped:
        mapped["error_message"] = mapped["error"]

    db_fields = {
        key: mapped[key]
        for key in [
            "status",
            "progress",
            "stage",
            "stage_message",
            "error_message",
            "started_at",
            "completed_at",
            "cancellation_requested",
            "has_results",
            "has_mesh_artifact",
            "label",
        ]
        if key in mapped
    }
    if db_fields:
        db.update_job(job_id, **db_fields)

    with jobs_lock:
        job = jobs.get(job_id)
        if job:
            job.update(mapped)
            if "error_message" in mapped:
                job["error"] = mapped["error_message"]
            return job
    return _merge_job_cache_from_db(job_id)


def update_progress(job_id: str, progress: float) -> None:
    job = _merge_job_cache_from_db(job_id)
    if not job:
        return
    if _is_terminal_status(job.get("status", "")):
        return
    _set_job_fields(job_id, progress=progress)


def update_job_stage(
    job_id: str,
    stage: str,
    *,
    progress: Optional[float] = None,
    stage_message: Optional[str] = None,
) -> None:
    """Update non-terminal job stage metadata."""
    job = _merge_job_cache_from_db(job_id)
    if not job:
        return
    if _is_terminal_status(job.get("status", "")):
        return
    payload: Dict[str, Any] = {"stage": stage}
    if stage_message is not None:
        payload["stage_message"] = stage_message
    if progress is not None:
        payload["progress"] = progress
    _set_job_fields(job_id, **payload)


def _remove_from_queue(job_id: str) -> None:
    with jobs_lock:
        if not job_queue:
            return
        remaining = [queued_id for queued_id in job_queue if queued_id != job_id]
        job_queue.clear()
        job_queue.extend(remaining)


async def _drain_scheduler_queue() -> None:
    global scheduler_loop_running
    if scheduler_loop_running:
        return

    scheduler_loop_running = True
    try:
        while True:
            with jobs_lock:
                can_start = len(running_jobs) < max_concurrent_jobs and len(job_queue) > 0
                if not can_start:
                    break
                job_id = job_queue.popleft()
                running_jobs.add(job_id)

            job = _merge_job_cache_from_db(job_id)
            if not job:
                with jobs_lock:
                    running_jobs.discard(job_id)
                continue
            if job.get("status") != "queued":
                with jobs_lock:
                    running_jobs.discard(job_id)
                continue

            started_at = _now_iso()
            _set_job_fields(
                job_id,
                status="running",
                started_at=started_at,
                stage="initializing",
                stage_message="Initializing BEM solver",
                progress=0.05,
            )

            request_obj = job.get("request_obj")
            if request_obj is None:
                raw = job.get("request")
                if not isinstance(raw, dict):
                    with jobs_lock:
                        running_jobs.discard(job_id)
                    _set_job_fields(
                        job_id,
                        status="error",
                        stage="error",
                        stage_message="Simulation failed",
                        error_message="Unable to restore queued simulation payload.",
                        completed_at=_now_iso(),
                    )
                    continue
                try:
                    request_obj = SimulationRequest(**raw)
                except Exception as exc:
                    with jobs_lock:
                        running_jobs.discard(job_id)
                    _set_job_fields(
                        job_id,
                        status="error",
                        stage="error",
                        stage_message="Simulation failed",
                        error_message=f"Unable to validate restored simulation payload: {exc}",
                        completed_at=_now_iso(),
                    )
                    continue
                _set_job_fields(job_id, request_obj=request_obj)

            asyncio.create_task(run_simulation(job_id, request_obj))
    finally:
        scheduler_loop_running = False


def _run_git(repo_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git {' '.join(args)} timed out.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip() or f"exit code {exc.returncode}"
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}") from exc


def get_update_status() -> Dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    if not (repo_root / ".git").exists():
        raise RuntimeError(
            "Git repository not found (.git directory is missing). "
            "If you downloaded the code as a ZIP file, please initialize a git repository or "
            "clone from https://github.com/m3gnus/waveguide-generator"
        )

    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise RuntimeError("Git is not installed or not in system PATH.")

    try:
        _run_git(repo_root, "remote", "get-url", "origin")
    except RuntimeError:
        raise RuntimeError(
            "Git remote 'origin' is not configured. "
            "Expected remote: https://github.com/m3gnus/waveguide-generator.git"
        )

    try:
        _run_git(repo_root, "fetch", "origin", "--quiet")
    except RuntimeError as exc:
        raise RuntimeError("Unable to fetch updates from origin. Check network and remote access.") from exc

    current_commit = _run_git(repo_root, "rev-parse", "HEAD")
    current_branch = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")

    try:
        origin_head_ref = _run_git(repo_root, "symbolic-ref", "refs/remotes/origin/HEAD")
    except RuntimeError:
        origin_head_ref = "refs/remotes/origin/main"

    default_branch = origin_head_ref.rsplit("/", 1)[-1]
    remote_ref = f"refs/remotes/origin/{default_branch}"
    remote_commit = _run_git(repo_root, "rev-parse", remote_ref)

    counts_raw = _run_git(repo_root, "rev-list", "--left-right", "--count", f"HEAD...{remote_ref}")
    counts = counts_raw.split()
    if len(counts) != 2:
        raise RuntimeError(f"Unexpected git rev-list output: '{counts_raw}'")

    ahead_count = int(counts[0])
    behind_count = int(counts[1])

    return {
        "updateAvailable": behind_count > 0,
        "aheadCount": ahead_count,
        "behindCount": behind_count,
        "currentBranch": current_branch,
        "defaultBranch": default_branch,
        "currentCommit": current_commit,
        "remoteCommit": remote_commit,
        "checkedAt": datetime.now().isoformat()
    }


class BoundaryCondition(BaseModel):
    type: str  # 'velocity', 'neumann', 'robin'
    surfaceTag: int
    value: Optional[float] = None
    impedance: Optional[str] = None


class MeshData(BaseModel):
    vertices: List[float]
    indices: List[int]
    surfaceTags: List[int]  # Per-triangle surface tags
    format: str = "msh"
    boundaryConditions: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class PolarConfig(BaseModel):
    """
    Configuration for ABEC.Polars directivity map

    Follows the ATH/ABEC format for polar observation configurations.
    Reference: Ath-4.8.2-UserGuide section 4.1.5 (ABEC.Polars)

    Example in ATH format:
        ABEC.Polars:SPL_D = {
            MapAngleRange = 0,180,37
            NormAngle = 5
            Distance = 2
            Inclination = 35
        }

    Attributes:
        angle_range: [start_deg, end_deg, num_points] for angular sweep
        norm_angle: Reference angle in degrees for normalization
        distance: Measurement distance from horn mouth in meters
        inclination: Inclination angle in degrees for measurement plane
        enabled_axes: Requested directivity planes (horizontal|vertical|diagonal)
    """
    angle_range: List[float] = [0, 180, 37]  # [start, end, num_points]
    norm_angle: float = 5.0  # Normalization angle in degrees
    distance: float = 2.0  # Measurement distance in meters
    inclination: float = 35.0  # Inclination angle in degrees
    enabled_axes: List[str] = ["horizontal", "vertical", "diagonal"]

    @field_validator("enabled_axes")
    @classmethod
    def validate_enabled_axes(cls, value: List[str]) -> List[str]:
        allowed = {"horizontal", "vertical", "diagonal"}
        if not isinstance(value, list) or len(value) == 0:
            raise ValueError("polar_config.enabled_axes must contain at least one axis.")

        normalized = []
        seen = set()
        for axis in value:
            name = str(axis).strip().lower()
            if name not in allowed:
                raise ValueError(
                    "polar_config.enabled_axes values must be one of: horizontal, vertical, diagonal."
                )
            if name in seen:
                continue
            seen.add(name)
            normalized.append(name)

        if len(normalized) == 0:
            raise ValueError("polar_config.enabled_axes must contain at least one axis.")
        return normalized


class SimulationRequest(BaseModel):
    mesh: MeshData
    frequency_range: List[float]
    num_frequencies: int
    sim_type: str
    options: Optional[Dict[str, Any]] = {}
    polar_config: Optional[PolarConfig] = None
    # New optimization options
    use_optimized: bool = True  # Enable optimized solver with symmetry, caching, correct polars
    enable_symmetry: bool = True  # Enable automatic symmetry detection and reduction
    verbose: bool = True  # Print detailed progress and validation
    mesh_validation_mode: str = "warn"  # strict | warn | off
    frequency_spacing: str = "log"  # linear | log
    device_mode: str = "auto"  # auto | opencl_cpu | opencl_gpu | numba

    @field_validator("device_mode")
    @classmethod
    def validate_device_mode(cls, value: str) -> str:
        raw = str(value or "auto").strip().lower()
        if raw not in {"auto", "opencl_cpu", "opencl_gpu", "numba", "opencl", "cpu_opencl", "gpu_opencl"}:
            raise ValueError("device_mode must be one of: auto, opencl_cpu, opencl_gpu, numba.")
        return normalize_device_mode(raw)


class JobStatus(BaseModel):
    status: str
    progress: float
    stage: Optional[str] = None
    stage_message: Optional[str] = None
    message: Optional[str] = None


class SimulationResults(BaseModel):
    frequencies: List[float]
    directivity: Dict[str, List[List[float]]]
    impedance: Optional[Dict[str, List[float]]] = None
    spl_on_axis: Optional[Dict[str, List[float]]] = None
    di: Optional[Dict[str, List[float]]] = None


class WaveguideParamsRequest(BaseModel):
    """ATH-format waveguide parameters for the Python OCC mesh builder.

    Grid parameters (n_angular, n_length) control geometry sampling density.
    Resolution parameters (throat_res, mouth_res, rear_res) control Gmsh
    mesh element sizes, independently of the grid.
    """
    formula_type: str = "R-OSSE"

    # ── R-OSSE formula parameters ──────────────────────────────────────────
    R: Optional[str] = None         # Mouth radius expression [mm] (R-OSSE only)
    r: Union[float, str] = 0.4     # R-OSSE apex radius (constant or expression in p)
    b: Union[float, str] = 0.2     # R-OSSE bending (constant or expression in p)
    m: Union[float, str] = 0.85    # R-OSSE apex shift (constant or expression in p)
    tmax: Union[float, str] = 1.0  # Truncation fraction (constant or expression in p)

    # ── OSSE formula parameters ────────────────────────────────────────────
    L: Optional[str] = "120"        # Waveguide length expression [mm] (OSSE only)
    s: Optional[str] = "0.58"       # Termination shape expression (OSSE only)
    n: Union[float, str] = 4.158   # Termination curvature (constant or expression in p)
    h: Union[float, str] = 0.0     # Extra shape factor (constant or expression in p)

    # ── Shared formula parameters ──────────────────────────────────────────
    a: Optional[str] = None         # Coverage angle expression [deg]
    r0: Union[float, str] = 12.7   # Throat radius [mm] (constant or expression in p)
    a0: Union[float, str] = 15.5   # Throat half-angle [deg] (constant or expression in p)
    k: Union[float, str] = 2.0     # Flare constant (constant or expression in p)
    q: Union[float, str] = 3.4     # Shape factor (constant or expression in p)

    # ── Throat geometry ────────────────────────────────────────────────────
    throat_profile: int = 1         # 1=OS-SE profile, 3=Circular Arc
    throat_ext_angle: float = 0.0   # Conical throat extension half-angle [deg]
    throat_ext_length: float = 0.0  # Conical throat extension axial length [mm]
    slot_length: float = 0.0        # Straight slot length [mm]
    rot: float = 0.0               # Profile rotation about [0, r0] [deg]

    # ── Circular arc profile (throat_profile == 3) ─────────────────────────
    circ_arc_term_angle: float = 1.0  # Mouth terminal tangent angle [deg]
    circ_arc_radius: float = 0.0     # Explicit arc radius [mm] (0 = auto)

    # ── Guiding curve ──────────────────────────────────────────────────────
    gcurve_type: int = 0            # 0=none, 1=superellipse, 2=superformula
    gcurve_dist: float = 0.5        # Distance from throat (fraction 0-1 or mm if >1)
    gcurve_width: float = 0.0       # Width along X [mm]
    gcurve_aspect_ratio: float = 1.0  # Height/width ratio
    gcurve_se_n: float = 3.0        # Superellipse exponent
    gcurve_sf: Optional[str] = None    # Superformula: packed "a,b,m,n1,n2,n3"
    gcurve_sf_a: Optional[str] = None
    gcurve_sf_b: Optional[str] = None
    gcurve_sf_m1: Optional[str] = None
    gcurve_sf_m2: Optional[str] = None
    gcurve_sf_n1: Optional[str] = None
    gcurve_sf_n2: Optional[str] = None
    gcurve_sf_n3: Optional[str] = None
    gcurve_rot: float = 0.0         # Guiding curve rotation [deg]

    # ── Morph ──────────────────────────────────────────────────────────────
    morph_target: int = 0           # 0=none, 1=rectangle, 2=circle
    morph_width: float = 0.0        # Target width [mm]
    morph_height: float = 0.0       # Target height [mm]
    morph_corner: float = 0.0       # Corner radius [mm]
    morph_rate: float = 3.0         # Morph rate exponent
    morph_fixed: float = 0.0        # Fixed part 0-1 (no morph before this fraction)
    morph_allow_shrinkage: int = 0  # 0=no, 1=yes

    # ── Geometry grid (shape fidelity, NOT mesh element density) ───────────
    n_angular: int = 100            # Mesh.AngularSegments (must be multiple of 4)
    n_length: int = 20              # Mesh.LengthSegments
    quadrants: int = 1234           # Mesh.Quadrants: 1, 12, 14, or 1234

    # ── BEM mesh element sizes (Gmsh triangle size) ────────────────────────
    throat_res: float = 5.0         # Mesh.ThroatResolution [mm]
    mouth_res: float = 8.0          # Mesh.MouthResolution [mm]
    rear_res: float = 25.0          # Mesh.RearResolution [mm] (free-standing only)
    wall_thickness: float = 6.0     # Mesh.WallThickness [mm] (free-standing only)

    # ── Enclosure (cabinet box geometry) ──────────────────────────────────
    # enc_depth > 0 → generate enclosure box (ignores wall_thickness)
    # enc_depth == 0 + wall_thickness > 0 → generate horn wall shell only
    enc_depth:   float = 0.0        # Enclosure depth [mm] (0 = no enclosure)
    enc_space_l: float = 25.0       # Extra space left of mouth bounding box [mm]
    enc_space_t: float = 25.0       # Extra space top of mouth bounding box [mm]
    enc_space_r: float = 25.0       # Extra space right of mouth bounding box [mm]
    enc_space_b: float = 25.0       # Extra space bottom of mouth bounding box [mm]
    enc_edge:    float = 18.0       # Edge rounding radius [mm]
    enc_edge_type: int = 1          # 1=rounded fillet, 2=chamfer
    corner_segments: int = 4        # Axial segments for edge rounding
    enc_front_resolution: Optional[str] = None  # Front baffle corner resolutions q1,q2,q3,q4 [mm]
    enc_back_resolution: Optional[str] = None   # Back baffle corner resolutions q1,q2,q3,q4 [mm]

    # ── Simulation / output ────────────────────────────────────────────────
    sim_type: int = 2               # ABEC.SimType: 1=infinite baffle, 2=free standing
    msh_version: str = "2.2"        # Gmsh MSH format version


class GmshMeshRequest(BaseModel):
    geoText: str
    mshVersion: str = "2.2"
    binary: bool = False


async def generate_mesh_with_gmsh(request: GmshMeshRequest):
    """Legacy `.geo -> .msh` compatibility shim used by server tests."""
    geo_text = str(request.geoText or "")
    if not geo_text.strip():
        raise HTTPException(status_code=422, detail="geoText must be a non-empty .geo script.")

    if not gmsh_mesher_available():
        raise HTTPException(
            status_code=503,
            detail="Legacy /api/mesh/generate-msh requires a working Gmsh backend.",
        )

    try:
        result = generate_msh_from_geo(
            geo_text,
            msh_version=request.mshVersion,
            binary=bool(request.binary),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f".geo meshing failed: {exc}") from exc

    return {
        "msh": result["msh"],
        "generatedBy": "gmsh",
        "stats": result["stats"],
    }


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "MWG Horn BEM Solver",
        "version": "1.0.0",
        "status": "running",
        "solver_available": SOLVER_AVAILABLE
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Health check requested", flush=True)
    dependency_status = get_dependency_status()

    # Include BEM device interface diagnostics when solver is available
    device_info = None
    if SOLVER_AVAILABLE:
        try:
            from solver.device_interface import selected_device_metadata
            device_info = selected_device_metadata("auto")
        except Exception:
            pass

    return {
        "status": "ok",
        "solver": "bempp-cl" if SOLVER_AVAILABLE else "unavailable",
        "solverReady": BEMPP_RUNTIME_READY,
        "occBuilderReady": WAVEGUIDE_BUILDER_AVAILABLE and GMSH_OCC_RUNTIME_READY,
        "dependencies": dependency_status,
        "deviceInterface": device_info,
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/updates/check")
async def check_updates():
    try:
        return get_update_status()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.on_event("startup")
async def startup_jobs_runtime():
    ensure_db_ready()
    db.prune_terminal_jobs(retention_days=30, max_terminal_jobs=1000)

    queued_rows = db.recover_on_startup("Server restarted during execution")
    with jobs_lock:
        for row in queued_rows:
            request_obj = None
            request_dump = row.get("config_json")
            if isinstance(request_dump, dict):
                try:
                    request_obj = SimulationRequest(**request_dump)
                except Exception:
                    request_obj = None
            jobs[row["id"]] = {
                "id": row["id"],
                "status": row["status"],
                "progress": row["progress"],
                "stage": row.get("stage"),
                "stage_message": row.get("stage_message"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "queued_at": row.get("queued_at"),
                "started_at": row.get("started_at"),
                "completed_at": row.get("completed_at"),
                "request": request_dump,
                "request_obj": request_obj,
                "error_message": row.get("error_message"),
                "error": row.get("error_message"),
                "results": None,
                "mesh_artifact": None,
                "cancellation_requested": row.get("cancellation_requested", False),
                "config_summary": row.get("config_summary_json", {}),
                "has_results": row.get("has_results", False),
                "has_mesh_artifact": row.get("has_mesh_artifact", False),
                "label": row.get("label"),
            }
            job_queue.append(row["id"])

    if queued_rows:
        asyncio.create_task(_drain_scheduler_queue())


@app.post("/api/mesh/build")
async def build_mesh_from_params(request: WaveguideParamsRequest):
    """
    Build a Gmsh-authored .msh from ATH waveguide parameters using the Gmsh OCC Python API.

    Supports both R-OSSE and OSSE formula types with full ATH geometry features:
    throat extension, slot, circular arc, profile rotation, guiding curves,
    and morph (rectangular/circular target shape).

    Builds geometry using Gmsh's OpenCASCADE kernel with BSpline curves and
    ThruSections surfaces, producing a mesh that accurately follows the curved
    waveguide geometry.

    Returns the generated .msh mesh and mesh statistics (and optional STL text).
    Assigns ABEC-compatible physical group names: SD1G0, SD1D1001, SD2G0.

    Returns 503 if the Gmsh Python API is not available.
    """
    if not WAVEGUIDE_BUILDER_AVAILABLE or build_waveguide_mesh is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Python OCC mesh builder unavailable. "
                "Install gmsh Python API: pip install gmsh>=4.15.0"
            )
        )

    if not GMSH_OCC_RUNTIME_READY:
        dep = get_dependency_status()
        gmsh_info = dep["runtime"]["gmsh_python"]
        py_info = dep["runtime"]["python"]
        gmsh_range = dep["supportedMatrix"].get("gmsh_python", {}).get("range", ">=4.15,<5.0")
        py_range = dep["supportedMatrix"].get("python", {}).get("range", ">=3.10,<3.14")
        raise HTTPException(
            status_code=503,
            detail=(
                "Python OCC mesh builder dependency check failed. "
                f"python={py_info.get('version')} supported={py_info.get('supported')}; "
                f"gmsh={gmsh_info.get('version')} supported={gmsh_info.get('supported')}. "
                f"Supported matrix: python {py_range}, gmsh {gmsh_range}."
            )
        )

    if request.msh_version not in ("2.2", "4.1"):
        raise HTTPException(status_code=422, detail="msh_version must be '2.2' or '4.1'.")

    if request.formula_type not in ("R-OSSE", "OSSE"):
        raise HTTPException(
            status_code=422,
            detail=(
                f"formula_type '{request.formula_type}' is not supported. "
                "Supported types: 'R-OSSE', 'OSSE'."
            )
        )

    try:
        # Run directly on the request thread — gmsh Python API fails in worker threads
        # (signal handlers can only be installed from the main thread).
        result = build_waveguide_mesh(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Waveguide build failed: {exc}"
        ) from exc

    response = {
        "msh": result["msh_text"],
        "generatedBy": "gmsh-occ",
        "stats": result["stats"]
    }
    if result.get("stl_text"):
        response["stl"] = result["stl_text"]
    return response


@app.post("/api/solve")
async def submit_simulation(request: SimulationRequest):
    """
    Submit a new BEM simulation job
    
    Returns a job ID for tracking progress
    """
    triangle_count = len(request.mesh.indices) // 3
    if len(request.mesh.vertices) % 3 != 0:
        raise HTTPException(status_code=422, detail="Mesh vertices length must be divisible by 3.")
    if len(request.mesh.indices) % 3 != 0:
        raise HTTPException(status_code=422, detail="Mesh indices length must be divisible by 3.")
    if len(request.mesh.surfaceTags) != triangle_count:
        raise HTTPException(
            status_code=422,
            detail=f"Mesh surfaceTags length ({len(request.mesh.surfaceTags)}) must match triangle count ({triangle_count})."
        )
    # sim_type is always 2 (free-standing); infinite baffle was removed.
    try:
        normalize_mesh_validation_mode(request.mesh_validation_mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    options = request.options if isinstance(request.options, dict) else {}
    mesh_opts = options.get("mesh", {}) if isinstance(options.get("mesh", {}), dict) else {}
    mesh_strategy = str(mesh_opts.get("strategy", "")).strip().lower()

    if mesh_strategy == "occ_adaptive":
        waveguide_params = mesh_opts.get("waveguide_params")
        if not isinstance(waveguide_params, dict):
            raise HTTPException(
                status_code=422,
                detail="options.mesh.waveguide_params must be an object when options.mesh.strategy='occ_adaptive'."
            )
        try:
            validated_waveguide = WaveguideParamsRequest(**waveguide_params)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid options.mesh.waveguide_params: {exc.errors()}"
            ) from exc
        try:
            _validate_occ_adaptive_bem_shell(
                validated_waveguide.enc_depth,
                validated_waveguide.wall_thickness,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # BEM solve path always builds full-domain geometry.
        # Backend symmetry optimization can still reduce internally when safe.
        if int(validated_waveguide.quadrants) != 1234:
            waveguide_params["quadrants"] = 1234

        if not WAVEGUIDE_BUILDER_AVAILABLE or build_waveguide_mesh is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Python OCC mesh builder unavailable. "
                    "Install gmsh Python API: pip install gmsh>=4.15.0"
                )
            )

        if not GMSH_OCC_RUNTIME_READY:
            dep = get_dependency_status()
            gmsh_info = dep["runtime"]["gmsh_python"]
            py_info = dep["runtime"]["python"]
            gmsh_range = dep["supportedMatrix"].get("gmsh_python", {}).get("range", ">=4.15,<5.0")
            py_range = dep["supportedMatrix"].get("python", {}).get("range", ">=3.10,<3.14")
            raise HTTPException(
                status_code=503,
                detail=(
                    "Adaptive OCC mesh builder dependency check failed. "
                    f"python={py_info.get('version')} supported={py_info.get('supported')}; "
                    f"gmsh={gmsh_info.get('version')} supported={gmsh_info.get('supported')}. "
                    f"Supported matrix: python {py_range}, gmsh {gmsh_range}."
                )
            )

    if not SOLVER_AVAILABLE:
        dep = get_dependency_status()
        bempp_info = dep["runtime"]["bempp"]
        py_info = dep["runtime"]["python"]
        bempp_cl_range = dep["supportedMatrix"].get("bempp_cl", {}).get("range", ">=0.4,<0.5")
        bempp_legacy_range = dep["supportedMatrix"].get("bempp_api_legacy", {}).get("range", ">=0.3,<0.4")
        raise HTTPException(
            status_code=503,
            detail=(
                "BEM solver not available. Please install bempp-cl. "
                f"python={py_info.get('version')} supported={py_info.get('supported')}; "
                f"bempp variant={bempp_info.get('variant')} version={bempp_info.get('version')} "
                f"supported={bempp_info.get('supported')}. "
                f"Supported matrix: bempp-cl {bempp_cl_range}, legacy bempp_api {bempp_legacy_range}."
            )
        )
    
    ensure_db_ready()
    job_id = str(uuid.uuid4())
    now = _now_iso()
    request_dump = request.model_dump()
    config_summary = _build_config_summary(request)

    job_record = {
        "id": job_id,
        "status": "queued",
        "progress": 0.0,
        "stage": "queued",
        "stage_message": "Job queued",
        "created_at": now,
        "updated_at": now,
        "queued_at": now,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "error_message": None,
        "request": request_dump,
        "request_obj": request,
        "results": None,
        "mesh_artifact": None,
        "cancellation_requested": False,
        "config_summary": config_summary,
        "has_results": False,
        "has_mesh_artifact": False,
        "label": None,
    }

    db.create_job({
        "id": job_id,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "queued_at": now,
        "progress": 0.0,
        "stage": "queued",
        "stage_message": "Job queued",
        "error_message": None,
        "cancellation_requested": False,
        "config_json": request_dump,
        "config_summary_json": config_summary,
        "has_results": False,
        "has_mesh_artifact": False,
        "label": None,
    })

    with jobs_lock:
        jobs[job_id] = job_record
        job_queue.append(job_id)

    asyncio.create_task(_drain_scheduler_queue())

    return {"job_id": job_id}


@app.post("/api/stop/{job_id}")
async def stop_simulation(job_id: str):
    """
    Stop a running simulation job
    """
    job = _merge_job_cache_from_db(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] not in ["queued", "running"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot stop job with status: {job['status']}"
        )

    if job["status"] == "queued":
        _remove_from_queue(job_id)
        _set_job_fields(
            job_id,
            status="cancelled",
            progress=0.0,
            stage="cancelled",
            stage_message="Simulation cancelled",
            error_message="Simulation cancelled by user",
            completed_at=_now_iso(),
            cancellation_requested=True,
        )
    else:
        _set_job_fields(
            job_id,
            status="cancelled",
            stage="cancelled",
            stage_message="Simulation cancelled",
            error_message="Simulation cancelled by user",
            completed_at=_now_iso(),
            cancellation_requested=True,
        )

    asyncio.create_task(_drain_scheduler_queue())
    return {"message": f"Job {job_id} has been cancelled", "status": "cancelled"}


@app.get("/api/status/{job_id}")
async def get_job_status(job_id: str):
    """
    Get the status of a simulation job
    """
    job = _merge_job_cache_from_db(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatus(
        status=job["status"],
        progress=float(job.get("progress", 0.0)),
        stage=job.get("stage"),
        stage_message=job.get("stage_message"),
        message=job.get("error_message") or job.get("error")
    )


@app.get("/api/results/{job_id}")
async def get_results(job_id: str):
    """
    Retrieve simulation results
    """
    job = _merge_job_cache_from_db(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] != "complete":
        raise HTTPException(
            status_code=400,
            detail=f"Job not complete. Current status: {job['status']}"
        )

    cached_results = job.get("results")
    if isinstance(cached_results, dict):
        return cached_results

    stored = db.get_results(job_id)
    if stored is None:
        raise HTTPException(status_code=500, detail="Results not available")
    _set_job_fields(job_id, results=stored)
    return stored


@app.get("/api/mesh-artifact/{job_id}")
async def get_mesh_artifact(job_id: str):
    """
    Download the simulation mesh artifact (.msh text) for a given job.
    Returns plain text suitable for saving as a .msh file.
    """
    job = _merge_job_cache_from_db(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    msh_text = job.get("mesh_artifact")
    if not msh_text:
        msh_text = db.get_mesh_artifact(job_id)
        if msh_text:
            _set_job_fields(job_id, mesh_artifact=msh_text, has_mesh_artifact=True)
    if not msh_text:
        raise HTTPException(status_code=404, detail="No mesh artifact available for this job")

    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(content=msh_text, media_type="text/plain")


def _serialize_job_item(job: Dict[str, Any]) -> Dict[str, Any]:
    summary = job.get("config_summary")
    if summary is None:
        summary = job.get("config_summary_json", {})

    return {
        "id": job.get("id"),
        "status": job.get("status"),
        "progress": float(job.get("progress", 0.0)),
        "stage": job.get("stage"),
        "stage_message": job.get("stage_message"),
        "created_at": job.get("created_at"),
        "queued_at": job.get("queued_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "config_summary": summary or {},
        "has_results": bool(job.get("has_results")),
        "has_mesh_artifact": bool(job.get("has_mesh_artifact")),
        "label": job.get("label"),
        "error_message": job.get("error_message"),
    }


def _parse_status_filters(raw: Optional[str]) -> Optional[List[str]]:
    if raw is None:
        return None
    values = [token.strip() for token in str(raw).split(",") if token.strip()]
    if not values:
        return None
    allowed = {"queued", "running", "complete", "error", "cancelled"}
    bad = [value for value in values if value not in allowed]
    if bad:
        raise HTTPException(
            status_code=422,
            detail=f"status filter contains unsupported values: {', '.join(bad)}",
        )
    dedup: List[str] = []
    for value in values:
        if value not in dedup:
            dedup.append(value)
    return dedup


@app.get("/api/jobs")
async def list_jobs(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    ensure_db_ready()
    statuses = _parse_status_filters(status)
    rows, total = db.list_jobs(statuses=statuses, limit=limit, offset=offset)
    items: List[Dict[str, Any]] = []
    for row in rows:
        merged = _merge_job_cache_from_db(row["id"]) or row
        items.append(_serialize_job_item(merged))

    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.delete("/api/jobs/clear-failed")
async def clear_failed_jobs():
    ensure_db_ready()
    deleted_ids = db.delete_jobs_by_status(["error"])
    with jobs_lock:
        for job_id in deleted_ids:
            jobs.pop(job_id, None)
            running_jobs.discard(job_id)
    for job_id in deleted_ids:
        _remove_from_queue(job_id)
    return {
        "deleted": len(deleted_ids) > 0,
        "deleted_count": len(deleted_ids),
        "deleted_ids": deleted_ids,
    }


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    ensure_db_ready()
    job = _merge_job_cache_from_db(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Cannot delete active job")

    deleted = db.delete_job(job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Job not found")

    with jobs_lock:
        jobs.pop(job_id, None)
        running_jobs.discard(job_id)
    _remove_from_queue(job_id)
    return {"deleted": True, "job_id": job_id}


class ChartsRenderRequest(BaseModel):
    frequencies: List[float] = []
    spl: List[float] = []
    di: List[float] = []
    di_frequencies: List[float] = []
    impedance_frequencies: List[float] = []
    impedance_real: List[float] = []
    impedance_imaginary: List[float] = []
    directivity: Dict[str, Any] = {}


@app.post("/api/render-charts")
async def render_charts(request: ChartsRenderRequest):
    """
    Render all result charts as PNG images using Matplotlib.
    Returns base64-encoded PNGs for each chart type.
    """
    try:
        from solver.charts import render_all_charts
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Chart renderer not available: {e}"
        )

    try:
        charts = render_all_charts(request.model_dump())
        result = {}
        for key, b64 in charts.items():
            if b64 is not None:
                result[key] = f"data:image/png;base64,{b64}"
        return {"charts": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chart rendering failed: {e}")


class DirectivityRenderRequest(BaseModel):
    frequencies: List[float]
    directivity: Dict[str, Any]
    reference_level: float = -6.0


@app.post("/api/render-directivity")
async def render_directivity(request: DirectivityRenderRequest):
    """
    Render directivity heatmap as a PNG image using Matplotlib.
    Returns base64-encoded PNG.
    """
    try:
        from solver.directivity_plot import render_directivity_plot
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Matplotlib not available: {e}"
        )

    if not request.frequencies or not request.directivity:
        raise HTTPException(status_code=400, detail="Missing frequencies or directivity data")

    try:
        image_b64 = render_directivity_plot(request.frequencies, request.directivity,
                                                   reference_level=request.reference_level)
        if image_b64 is None:
            raise HTTPException(status_code=400, detail="No directivity patterns to render")
        return {"image": f"data:image/png;base64,{image_b64}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Rendering failed: {e}")


async def run_simulation(job_id: str, request: SimulationRequest):
    """
    Run BEM simulation in background
    """
    try:
        job = _merge_job_cache_from_db(job_id)
        if not job:
            return
        if job.get("status") == "queued":
            _set_job_fields(
                job_id,
                status="running",
                started_at=_now_iso(),
                stage="initializing",
                stage_message="Initializing BEM solver",
                progress=0.05,
            )
        update_job_stage(job_id, "initializing", progress=0.05, stage_message="Initializing BEM solver")
        
        # Initialize solver
        solver = BEMSolver()
        
        # Extract mesh generation options
        options = request.options if isinstance(request.options, dict) else {}
        mesh_opts = options.get("mesh", {}) if isinstance(options.get("mesh", {}), dict) else {}
        mesh_strategy = str(mesh_opts.get("strategy", "")).strip().lower()

        # Check if options are flat or nested
        if "use_gmsh" in options:
            use_gmsh = options.get("use_gmsh", False)
            target_freq = options.get("target_frequency", 
                                            max(request.frequency_range) if request.frequency_range else 1000.0)
        else:
            use_gmsh = mesh_opts.get("use_gmsh", False)
            target_freq = mesh_opts.get("target_frequency", 
                                        max(request.frequency_range) if request.frequency_range else 1000.0)

        if mesh_strategy == "occ_adaptive":
            waveguide_params = mesh_opts.get("waveguide_params")
            if not isinstance(waveguide_params, dict):
                raise ValueError(
                    "options.mesh.waveguide_params must be provided for options.mesh.strategy='occ_adaptive'."
                )
            if not WAVEGUIDE_BUILDER_AVAILABLE or build_waveguide_mesh is None or not GMSH_OCC_RUNTIME_READY:
                raise RuntimeError("Adaptive OCC mesh builder is unavailable.")

            update_job_stage(job_id, "mesh_prepare", progress=0.15, stage_message="Building adaptive OCC mesh")
            validated = WaveguideParamsRequest(**waveguide_params)
            _validate_occ_adaptive_bem_shell(validated.enc_depth, validated.wall_thickness)
            validated_payload = validated.model_dump()
            requested_quadrants = int(validated_payload.get("quadrants", 1234))
            # Ensure OCC adaptive simulation mesh is always generated as full domain.
            validated_payload["quadrants"] = 1234
            occ_result = build_waveguide_mesh(validated_payload, include_canonical=True)
            canonical = occ_result.get("canonical_mesh") or {}

            vertices = canonical.get("vertices")
            indices = canonical.get("indices")
            surface_tags = canonical.get("surfaceTags")
            if not isinstance(vertices, list) or not isinstance(indices, list) or not isinstance(surface_tags, list):
                raise RuntimeError("Adaptive OCC mesh generation did not return canonical mesh arrays.")
            if len(indices) % 3 != 0:
                raise RuntimeError("Adaptive OCC mesh returned invalid triangle index data.")
            if len(surface_tags) != len(indices) // 3:
                raise RuntimeError("Adaptive OCC mesh returned mismatched surface tag count.")
            # BEM solve semantics: every non-source surface is rigid wall.
            # Keep tag 2 (source) and normalize all other tags to 1.
            surface_tags = [2 if int(tag) == 2 else 1 for tag in surface_tags]

            # Store mesh artifact for optional download via /api/mesh-artifact/{job_id}
            msh_artifact = occ_result.get("msh_text")
            _set_job_fields(job_id, mesh_artifact=msh_artifact, has_mesh_artifact=bool(msh_artifact))
            if msh_artifact:
                db.store_mesh_artifact(job_id, msh_artifact)

            mesh_metadata = dict(request.mesh.metadata or {})
            mesh_metadata.update({
                "units": "mm",
                "unitScaleToMeter": 0.001,
                "meshStrategy": "occ_adaptive",
                "generatedBy": "gmsh-occ",
                "requestedQuadrants": requested_quadrants,
                "effectiveQuadrants": 1234,
                "occStats": occ_result.get("stats") or {},
            })

            mesh = solver.prepare_mesh(
                vertices,
                indices,
                surface_tags=surface_tags,
                boundary_conditions=request.mesh.boundaryConditions,
                mesh_metadata=mesh_metadata,
                use_gmsh=False,
                target_frequency=target_freq
            )
        else:
            # Convert mesh data with surface tags (legacy canonical path)
            update_job_stage(job_id, "mesh_prepare", progress=0.15, stage_message="Preparing canonical mesh")
            mesh = solver.prepare_mesh(
                request.mesh.vertices,
                request.mesh.indices,
                surface_tags=request.mesh.surfaceTags,
                boundary_conditions=request.mesh.boundaryConditions,
                mesh_metadata=request.mesh.metadata,
                use_gmsh=use_gmsh,
                target_frequency=target_freq
            )
        
        # Run simulation
        update_job_stage(job_id, "solver_setup", progress=0.30, stage_message="Configuring BEM solve")

        def _solver_stage_callback(stage: str, progress: Optional[float] = None, message: Optional[str] = None) -> None:
            normalized_progress = 0.0 if progress is None else max(0.0, min(1.0, float(progress)))

            if stage in {"setup", "solver_setup"}:
                update_job_stage(
                    job_id,
                    "solver_setup",
                    progress=0.30 + (normalized_progress * 0.05),
                    stage_message=message or "Configuring BEM solve",
                )
                return

            if stage == "frequency_solve":
                update_job_stage(
                    job_id,
                    "bem_solve",
                    progress=0.35 + (normalized_progress * 0.50),
                    stage_message=message or "Solving BEM frequencies",
                )
                return

            if stage == "directivity":
                update_job_stage(
                    job_id,
                    "directivity",
                    progress=0.85 + (normalized_progress * 0.13),
                    stage_message=message or (
                        "Generating polar maps (horizontal/vertical/diagonal) and deriving DI from solved frequencies"
                    ),
                )
                return

            if stage == "finalizing":
                update_job_stage(
                    job_id,
                    "finalizing",
                    progress=0.98 + (normalized_progress * 0.01),
                    stage_message=message or "Finalizing results",
                )
                return

            update_job_stage(
                job_id,
                str(stage),
                stage_message=message,
            )
        
        results = await asyncio.to_thread(
            solver.solve,
            mesh=mesh,
            frequency_range=request.frequency_range,
            num_frequencies=request.num_frequencies,
            sim_type=request.sim_type,
            polar_config=request.polar_config.model_dump() if request.polar_config else None,
            progress_callback=lambda p: _solver_stage_callback("frequency_solve", progress=p),
            stage_callback=_solver_stage_callback,
            use_optimized=request.use_optimized,
            enable_symmetry=request.enable_symmetry,
            verbose=request.verbose,
            mesh_validation_mode=request.mesh_validation_mode,
            frequency_spacing=request.frequency_spacing,
            device_mode=request.device_mode,
        )
        
        # Store results
        latest = _merge_job_cache_from_db(job_id)
        if latest and latest.get("status") == "cancelled":
            _set_job_fields(job_id, stage="cancelled", stage_message="Simulation cancelled")
            return

        completed_at = _now_iso()
        _set_job_fields(
            job_id,
            stage="complete",
            stage_message="Simulation complete",
            progress=1.0,
            status="complete",
            results=results,
            has_results=True,
            completed_at=completed_at,
            cancellation_requested=False,
            error_message=None,
        )
        db.store_results(job_id, results)

    except Exception as e:
        import traceback
        _set_job_fields(
            job_id,
            status="error",
            stage="error",
            stage_message="Simulation failed",
            error_message=str(e),
            completed_at=_now_iso(),
        )
        print(f"Simulation error for job {job_id}: {e}")
        print(f"Full traceback:")
        traceback.print_exc()
    finally:
        with jobs_lock:
            running_jobs.discard(job_id)
        db.prune_terminal_jobs(retention_days=30, max_terminal_jobs=1000)
        asyncio.create_task(_drain_scheduler_queue())

if __name__ == "__main__":
    import uvicorn
    print("Starting MWG Horn BEM Solver Backend...")
    print(f"Solver available: {SOLVER_AVAILABLE}")
    print(f"OCC builder ready: {WAVEGUIDE_BUILDER_AVAILABLE and GMSH_OCC_RUNTIME_READY}")
    if not SOLVER_AVAILABLE:
        print("Warning: bempp-cl not installed. Install it to enable simulations.")
    uvicorn.run(app, host="0.0.0.0", port=8000)
