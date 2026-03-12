"""Shared Pydantic API contracts for backend routes and services."""

import math
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, field_validator

VALID_DEVICE_MODES = {"auto", "opencl_cpu", "opencl_gpu"}
DEVICE_MODE_ALIASES = {
    "opencl": "opencl_cpu",
    "cpu_opencl": "opencl_cpu",
    "gpu_opencl": "opencl_gpu",
}


class BoundaryCondition(BaseModel):
    type: str
    surfaceTag: int
    value: Optional[float] = None
    impedance: Optional[str] = None


class MeshData(BaseModel):
    vertices: List[float]
    indices: List[int]
    surfaceTags: List[int]
    format: str = "msh"
    boundaryConditions: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class PolarConfig(BaseModel):
    """
    Configuration for ABEC.Polars directivity map.

    Follows the ATH/ABEC format for polar observation configurations.
    Reference: Ath-4.8.2-UserGuide section 4.1.5 (ABEC.Polars)
    """

    angle_range: List[float] = [0, 180, 37]
    norm_angle: float = 5.0
    distance: float = 2.0
    inclination: float = 35.0
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


class AdvancedSimulationSettings(BaseModel):
    enable_warmup: Optional[bool] = None
    use_burton_miller: Optional[bool] = None
    symmetry_tolerance: Optional[float] = None

    @field_validator("symmetry_tolerance")
    @classmethod
    def validate_symmetry_tolerance(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return value
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 0.0:
            raise ValueError("advanced_settings.symmetry_tolerance must be a positive finite number.")
        return numeric


def normalize_contract_device_mode(value: Any) -> str:
    raw = str(value or "auto").strip().lower()
    normalized = DEVICE_MODE_ALIASES.get(raw, raw)
    if normalized not in VALID_DEVICE_MODES:
        raise ValueError("device_mode must be one of: auto, opencl_cpu, opencl_gpu.")
    return normalized


class SimulationRequest(BaseModel):
    mesh: MeshData
    frequency_range: List[float]
    num_frequencies: int
    sim_type: str
    options: Optional[Dict[str, Any]] = {}
    polar_config: Optional[PolarConfig] = None
    advanced_settings: Optional[AdvancedSimulationSettings] = None
    use_optimized: bool = True
    enable_symmetry: bool = True
    verbose: bool = True
    mesh_validation_mode: str = "warn"
    frequency_spacing: str = "log"
    device_mode: str = "auto"

    @field_validator("device_mode")
    @classmethod
    def validate_device_mode(cls, value: str) -> str:
        return normalize_contract_device_mode(value)


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

    R: Optional[str] = None
    r: Union[float, str] = 0.4
    b: Union[float, str] = 0.2
    m: Union[float, str] = 0.85
    tmax: Union[float, str] = 1.0

    L: Optional[str] = "120"
    s: Optional[str] = "0.58"
    n: Union[float, str] = 4.158
    h: Union[float, str] = 0.0

    a: Optional[str] = None
    r0: Union[float, str] = 12.7
    a0: Union[float, str] = 15.5
    k: Union[float, str] = 2.0
    q: Union[float, str] = 3.4

    throat_profile: int = 1
    throat_ext_angle: float = 0.0
    throat_ext_length: float = 0.0
    slot_length: float = 0.0
    rot: float = 0.0

    circ_arc_term_angle: float = 1.0
    circ_arc_radius: float = 0.0

    gcurve_type: int = 0
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

    morph_target: int = 0
    morph_width: float = 0.0
    morph_height: float = 0.0
    morph_corner: float = 0.0
    morph_rate: float = 3.0
    morph_fixed: float = 0.0
    morph_allow_shrinkage: int = 0

    n_angular: int = 100
    n_length: int = 20
    quadrants: int = 1234

    throat_res: float = 6.0
    mouth_res: float = 15.0
    rear_res: float = 40.0
    wall_thickness: float = 6.0

    enc_depth: float = 0.0
    enc_space_l: float = 25.0
    enc_space_t: float = 25.0
    enc_space_r: float = 25.0
    enc_space_b: float = 25.0
    enc_edge: float = 18.0
    enc_edge_type: int = 1
    corner_segments: int = 4
    enc_front_resolution: Optional[str] = "25,25,25,25"
    enc_back_resolution: Optional[str] = "40,40,40,40"

    sim_type: int = 2
    msh_version: str = "2.2"


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


__all__ = [
    "AdvancedSimulationSettings",
    "BoundaryCondition",
    "ChartsRenderRequest",
    "DirectivityRenderRequest",
    "JobStatus",
    "MeshData",
    "PolarConfig",
    "SimulationRequest",
    "SimulationResults",
    "VALID_DEVICE_MODES",
    "WaveguideParamsRequest",
    "normalize_contract_device_mode",
]
