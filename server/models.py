"""
Shared Pydantic request/response models for the MWG BEM Solver API.
"""

from pydantic import BaseModel, field_validator
from typing import Dict, List, Optional, Any, Union


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
    Configuration for ABEC.Polars directivity map.

    Follows the ATH/ABEC format for polar observation configurations.
    Reference: Ath-4.8.2-UserGuide section 4.1.5 (ABEC.Polars)
    """
    angle_range: List[float] = [0, 180, 37]  # [start, end, num_points]
    norm_angle: float = 5.0                   # Normalization angle in degrees
    distance: float = 2.0                     # Measurement distance in meters
    inclination: float = 35.0                 # Inclination angle in degrees
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


def _normalize_device_mode(value: Any) -> str:
    """Canonical device-mode normalization (mirrors solver.device_interface)."""
    mode = str(value or "auto").strip().lower()
    if mode not in {"auto", "opencl_cpu", "opencl_gpu", "numba"}:
        raise ValueError("device_mode must be one of: auto, opencl_cpu, opencl_gpu, numba.")
    return mode


class SimulationRequest(BaseModel):
    mesh: MeshData
    frequency_range: List[float]
    num_frequencies: int
    sim_type: str
    options: Optional[Dict[str, Any]] = {}
    polar_config: Optional[PolarConfig] = None
    use_optimized: bool = True
    enable_symmetry: bool = True
    verbose: bool = True
    mesh_validation_mode: str = "warn"  # strict | warn | off
    frequency_spacing: str = "log"      # linear | log
    device_mode: str = "auto"           # auto | opencl_cpu | opencl_gpu | numba

    @field_validator("device_mode")
    @classmethod
    def validate_device_mode(cls, value: str) -> str:
        raw = str(value or "auto").strip().lower()
        if raw not in {"auto", "opencl_cpu", "opencl_gpu", "numba", "opencl", "cpu_opencl", "gpu_opencl"}:
            raise ValueError("device_mode must be one of: auto, opencl_cpu, opencl_gpu, numba.")
        # Import real normalizer if available; fall back to local version.
        try:
            from solver.device_interface import normalize_device_mode
        except ImportError:
            normalize_device_mode = _normalize_device_mode
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
    r: Union[float, str] = 0.4     # R-OSSE apex radius
    b: Union[float, str] = 0.2     # R-OSSE bending
    m: Union[float, str] = 0.85    # R-OSSE apex shift
    tmax: Union[float, str] = 1.0  # Truncation fraction

    # ── OSSE formula parameters ────────────────────────────────────────────
    L: Optional[str] = "120"        # Waveguide length expression [mm] (OSSE only)
    s: Optional[str] = "0.58"       # Termination shape expression (OSSE only)
    n: Union[float, str] = 4.158   # Termination curvature
    h: Union[float, str] = 0.0     # Extra shape factor

    # ── Shared formula parameters ──────────────────────────────────────────
    a: Optional[str] = None         # Coverage angle expression [deg]
    r0: Union[float, str] = 12.7   # Throat radius [mm]
    a0: Union[float, str] = 15.5   # Throat half-angle [deg]
    k: Union[float, str] = 2.0     # Flare constant
    q: Union[float, str] = 3.4     # Shape factor

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
    gcurve_dist: float = 0.5
    gcurve_width: float = 0.0
    gcurve_aspect_ratio: float = 1.0
    gcurve_se_n: float = 3.0
    gcurve_sf: Optional[str] = None
    gcurve_sf_a: Optional[str] = None
    gcurve_sf_b: Optional[str] = None
    gcurve_sf_m1: Optional[str] = None
    gcurve_sf_m2: Optional[str] = None
    gcurve_sf_n1: Optional[str] = None
    gcurve_sf_n2: Optional[str] = None
    gcurve_sf_n3: Optional[str] = None
    gcurve_rot: float = 0.0

    # ── Morph ──────────────────────────────────────────────────────────────
    morph_target: int = 0
    morph_width: float = 0.0
    morph_height: float = 0.0
    morph_corner: float = 0.0
    morph_rate: float = 3.0
    morph_fixed: float = 0.0
    morph_allow_shrinkage: int = 0

    # ── Geometry grid (shape fidelity, NOT mesh element density) ───────────
    n_angular: int = 100
    n_length: int = 20
    quadrants: int = 1234

    # ── BEM mesh element sizes (Gmsh triangle size) ────────────────────────
    throat_res: float = 5.0
    mouth_res: float = 8.0
    rear_res: float = 25.0
    wall_thickness: float = 6.0

    # ── Enclosure (cabinet box geometry) ──────────────────────────────────
    enc_depth:   float = 0.0
    enc_space_l: float = 25.0
    enc_space_t: float = 25.0
    enc_space_r: float = 25.0
    enc_space_b: float = 25.0
    enc_edge:    float = 18.0
    enc_edge_type: int = 1
    corner_segments: int = 4
    enc_front_resolution: Optional[str] = None
    enc_back_resolution: Optional[str] = None

    # ── Simulation / output ────────────────────────────────────────────────
    sim_type: int = 2
    msh_version: str = "2.2"


class GmshMeshRequest(BaseModel):
    geoText: str
    mshVersion: str = "2.2"
    binary: bool = False


class ChartsRenderRequest(BaseModel):
    frequencies: List[float] = []
    spl: List[float] = []
    di: List[float] = []
    di_frequencies: List[float] = []
    impedance_frequencies: List[float] = []
    impedance_real: List[float] = []
    impedance_imaginary: List[float] = []
    directivity: Dict[str, Any] = {}


class DirectivityRenderRequest(BaseModel):
    frequencies: List[float]
    directivity: Dict[str, Any]
    reference_level: float = -6.0
