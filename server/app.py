"""
MWG Horn BEM Solver Backend
FastAPI application for running acoustic simulations
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Optional, Any
import uuid
import asyncio
import subprocess
from pathlib import Path
from datetime import datetime

# Import solver module (will be created)
try:
    from solver import BEMSolver
    SOLVER_AVAILABLE = True
except ImportError:
    SOLVER_AVAILABLE = False
    print("Warning: BEM solver not available. Install bempp-cl to enable simulations.")

try:
    from solver.gmsh_geo_mesher import (
        generate_msh_from_geo,
        gmsh_mesher_available,
        GmshMeshingError
    )
except ImportError:
    generate_msh_from_geo = None
    gmsh_mesher_available = lambda: False

    class GmshMeshingError(RuntimeError):
        pass

try:
    from solver.waveguide_builder import build_waveguide_mesh
    WAVEGUIDE_BUILDER_AVAILABLE = True
except ImportError:
    build_waveguide_mesh = None
    WAVEGUIDE_BUILDER_AVAILABLE = False

app = FastAPI(title="MWG Horn BEM Solver", version="1.0.0")

# Enable CORS for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Job storage (in production, use Redis or database)
jobs: Dict[str, Dict[str, Any]] = {}


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
        raise RuntimeError("Repository metadata not found (.git directory is missing).")

    try:
        _run_git(repo_root, "remote", "get-url", "origin")
    except RuntimeError as exc:
        raise RuntimeError("Git remote 'origin' is not configured.") from exc

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
    """
    angle_range: List[float] = [0, 180, 37]  # [start, end, num_points]
    norm_angle: float = 5.0  # Normalization angle in degrees
    distance: float = 2.0  # Measurement distance in meters
    inclination: float = 35.0  # Inclination angle in degrees


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
    verbose: bool = False  # Print detailed progress and validation


class JobStatus(BaseModel):
    status: str
    progress: float
    message: Optional[str] = None


class SimulationResults(BaseModel):
    frequencies: List[float]
    directivity: Dict[str, List[List[float]]]
    impedance: Optional[Dict[str, List[float]]] = None
    spl_on_axis: Optional[Dict[str, List[float]]] = None
    di: Optional[Dict[str, List[float]]] = None


class GmshMeshRequest(BaseModel):
    geoText: str
    mshVersion: str = "2.2"
    binary: bool = False


class WaveguideParamsRequest(BaseModel):
    """ATH-format waveguide parameters for the Python OCC mesh builder.

    Grid parameters (n_angular, n_length) control geometry sampling density.
    Resolution parameters (throat_res, mouth_res, rear_res) control Gmsh
    mesh element sizes, independently of the grid.
    """
    formula_type: str = "R-OSSE"

    # ── R-OSSE formula parameters ──────────────────────────────────────────
    R: Optional[str] = None         # Mouth radius expression [mm] (R-OSSE only)
    r: float = 0.4                  # R-OSSE r parameter
    b: float = 0.2                  # R-OSSE b parameter
    m: float = 0.85                 # R-OSSE m (apex shift)
    tmax: float = 1.0               # Truncation fraction of computed length

    # ── OSSE formula parameters ────────────────────────────────────────────
    L: Optional[str] = "120"        # Waveguide length expression [mm] (OSSE only)
    s: Optional[str] = "0.58"       # Termination shape expression (OSSE only)
    n: float = 4.158                # Termination curvature exponent (OSSE only)
    h: float = 0.0                  # Extra shape factor (OSSE only)

    # ── Shared formula parameters ──────────────────────────────────────────
    a: Optional[str] = None         # Coverage angle expression [deg]
    r0: float = 12.7               # Throat radius [mm]
    a0: float = 15.5               # Throat half-angle [deg]
    k: float = 2.0                  # Expansion factor / flare constant
    q: float = 3.4                  # Shape factor

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

    # ── Subdomain interfaces (accepted, no geometry effect yet) ───────────
    subdomain_slices: Optional[str] = None
    interface_offset: Optional[str] = None
    interface_draw: Optional[str] = None
    interface_resolution: Optional[str] = None

    # ── Enclosure (cabinet box geometry) ──────────────────────────────────
    # enc_depth > 0 → generate enclosure box (ignores wall_thickness)
    # enc_depth == 0 + wall_thickness > 0 → generate horn wall shell only
    enc_depth:   float = 0.0        # Enclosure depth [mm] (0 = no enclosure)
    enc_space_l: float = 25.0       # Extra space left of mouth bounding box [mm]
    enc_space_t: float = 25.0       # Extra space top of mouth bounding box [mm]
    enc_space_r: float = 25.0       # Extra space right of mouth bounding box [mm]
    enc_space_b: float = 25.0       # Extra space bottom of mouth bounding box [mm]
    enc_edge:    float = 18.0       # Edge rounding radius [mm] (reserved)

    # ── Simulation / output ────────────────────────────────────────────────
    sim_type: int = 2               # ABEC.SimType: 1=infinite baffle, 2=free standing
    msh_version: str = "2.2"        # Gmsh MSH format version


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
    return {
        "status": "ok",
        "solver": "bempp-cl" if SOLVER_AVAILABLE else "unavailable",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/updates/check")
async def check_updates():
    try:
        return get_update_status()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/mesh/generate-msh")
async def generate_mesh_with_gmsh(request: GmshMeshRequest):
    """
    Generate a .msh file from .geo input using Gmsh.

    This endpoint is intentionally strict: .msh content must be authored by Gmsh.
    """
    if not request.geoText or not request.geoText.strip():
        raise HTTPException(status_code=422, detail="geoText must be a non-empty string.")

    if request.mshVersion not in ("2.2", "4.1"):
        raise HTTPException(status_code=422, detail="mshVersion must be '2.2' or '4.1'.")

    if generate_msh_from_geo is None or not gmsh_mesher_available():
        raise HTTPException(
            status_code=503,
            detail="Gmsh meshing service unavailable. .msh export requires a working Gmsh backend."
        )

    try:
        # Run gmsh generation on the request thread; gmsh Python API may fail in worker threads.
        result = generate_msh_from_geo(
            request.geoText,
            request.mshVersion,
            request.binary
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except GmshMeshingError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Gmsh meshing failed: {exc}"
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error during Gmsh meshing: {exc}"
        ) from exc

    return {
        "msh": result["msh"],
        "generatedBy": "gmsh",
        "stats": result["stats"]
    }


@app.post("/api/mesh/build")
async def build_mesh_from_params(request: WaveguideParamsRequest):
    """
    Build .geo and .msh from ATH waveguide parameters using Gmsh OCC Python API.

    Supports both R-OSSE and OSSE formula types with full ATH geometry features:
    throat extension, slot, circular arc, profile rotation, guiding curves,
    and morph (rectangular/circular target shape).

    Builds geometry using Gmsh's OpenCASCADE kernel with BSpline curves and
    ThruSections surfaces, producing a mesh that accurately follows the curved
    waveguide geometry.

    Returns the Gmsh .geo script (OCC-format), the .msh mesh, and mesh statistics.
    Assigns ABEC-compatible physical group names: SD1G0, SD1D1001, SD2G0.

    Returns 503 if the Gmsh Python API is not available.
    """
    if not WAVEGUIDE_BUILDER_AVAILABLE or build_waveguide_mesh is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Python OCC mesh builder unavailable. "
                "Install gmsh Python API: pip install gmsh>=4.10.0"
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

    return {
        "msh": result["msh_text"],
        "generatedBy": "gmsh-occ",
        "stats": result["stats"]
    }


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

    if not SOLVER_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="BEM solver not available. Please install bempp-cl."
        )
    
    # Generate unique job ID
    job_id = str(uuid.uuid4())
    
    # Initialize job status
    jobs[job_id] = {
        "status": "queued",
        "progress": 0.0,
        "created_at": datetime.now().isoformat(),
        "request": request.model_dump(),
        "results": None,
        "error": None
    }
    
    # Start simulation in background
    asyncio.create_task(run_simulation(job_id, request))
    
    return {"job_id": job_id}


@app.post("/api/stop/{job_id}")
async def stop_simulation(job_id: str):
    """
    Stop a running simulation job
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    
    # Only allow stopping if the job is queued or running
    if job["status"] not in ["queued", "running"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot stop job with status: {job['status']}"
        )
    
    job["status"] = "cancelled"
    job["progress"] = 0.0
    job["error"] = "Simulation cancelled by user"
    job["stopped_at"] = datetime.now().isoformat()
    
    return {"message": f"Job {job_id} has been cancelled", "status": "cancelled"}


@app.get("/api/status/{job_id}")
async def get_job_status(job_id: str):
    """
    Get the status of a simulation job
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    return JobStatus(
        status=job["status"],
        progress=job["progress"],
        message=job.get("error")
    )


@app.get("/api/results/{job_id}")
async def get_results(job_id: str):
    """
    Retrieve simulation results
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    
    if job["status"] != "complete":
        raise HTTPException(
            status_code=400,
            detail=f"Job not complete. Current status: {job['status']}"
        )
    
    if job["results"] is None:
        raise HTTPException(status_code=500, detail="Results not available")
    
    return job["results"]


async def run_simulation(job_id: str, request: SimulationRequest):
    """
    Run BEM simulation in background
    """
    try:
        # Update status
        jobs[job_id]["status"] = "running"
        jobs[job_id]["progress"] = 0.1
        
        # Initialize solver
        solver = BEMSolver()
        
        # Extract mesh generation options
        mesh_opts = request.options.get("mesh", {})
        
        # Check if options are flat or nested
        if "use_gmsh" in request.options:
            use_gmsh = request.options.get("use_gmsh", False)
            target_freq = request.options.get("target_frequency", 
                                            max(request.frequency_range) if request.frequency_range else 1000.0)
        else:
            use_gmsh = mesh_opts.get("use_gmsh", False)
            target_freq = mesh_opts.get("target_frequency", 
                                        max(request.frequency_range) if request.frequency_range else 1000.0)

        # Convert mesh data with surface tags
        jobs[job_id]["progress"] = 0.2
        mesh = solver.prepare_mesh(
            request.mesh.vertices,
            request.mesh.indices,
            surface_tags=request.mesh.surfaceTags,
            boundary_conditions=request.mesh.boundaryConditions,
            use_gmsh=use_gmsh,
            target_frequency=target_freq
        )
        
        # Run simulation
        jobs[job_id]["progress"] = 0.3
        
        results = await asyncio.to_thread(
            solver.solve,
            mesh=mesh,
            frequency_range=request.frequency_range,
            num_frequencies=request.num_frequencies,
            sim_type=request.sim_type,
            polar_config=request.polar_config.model_dump() if request.polar_config else None,
            progress_callback=lambda p: update_progress(job_id, 0.3 + p * 0.6),
            use_optimized=request.use_optimized,
            enable_symmetry=request.enable_symmetry,
            verbose=request.verbose
        )
        
        # Store results
        jobs[job_id]["progress"] = 1.0
        jobs[job_id]["status"] = "complete"
        jobs[job_id]["results"] = results
        jobs[job_id]["completed_at"] = datetime.now().isoformat()
        
    except Exception as e:
        import traceback
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        jobs[job_id]["failed_at"] = datetime.now().isoformat()
        print(f"Simulation error for job {job_id}: {e}")
        print(f"Full traceback:")
        traceback.print_exc()


def update_progress(job_id: str, progress: float):
    """Update job progress"""
    if job_id in jobs:
        jobs[job_id]["progress"] = min(0.95, progress)


if __name__ == "__main__":
    import uvicorn
    print("Starting MWG Horn BEM Solver Backend...")
    print(f"Solver available: {SOLVER_AVAILABLE}")
    if not SOLVER_AVAILABLE:
        print("Warning: bempp-cl not installed. Install it to enable simulations.")
    uvicorn.run(app, host="0.0.0.0", port=8000)
