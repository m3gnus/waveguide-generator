"""Small production requests for the PLAN §5 same-artifact agreement corpus."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import sys
from typing import Any, Callable

from server.jobs.models import SolveRequest

from .json_io import write_json

ROOT = Path(__file__).resolve().parents[2]

# The real-pipeline fixture's deliberately small OSSE, with its original mesh controls.
OSSE = {
    "formula": "OSSE", "L": 60, "a": 30, "a0": 10, "r0": 10,
    "k": 1, "n": 4, "q": 0.99, "s": 0.8,
    "mesh": {"angular_segments": 12, "length_segments": 4,
             "throat_resolution": 8, "mouth_resolution": 15,
             "quadrants": 1, "wall_thickness": 2},
    "source": {"shape": 2, "radius": -1, "curvature": 0, "velocity": 1},
    "simulation": {"sim_type": "freestanding"},
}
DRIVER = {"sd_cm2": math.pi * 2.5**2, "bl_t_m": 10.5, "re_ohm": 5.3, "le_mh": 0.5,
          "mmd_g": 12., "cms_m_per_n": 4e-4, "rms_kg_per_s": 1.2}


@dataclass(frozen=True)
class CorpusCase:
    name: str
    covers: str
    fixture: str
    geometry: str = "osse"
    quadrants: int = 1234
    coarse_hz: tuple[float, ...] = tuple(float(f) for f in range(500, 3501, 25))
    # coarse_hz is the legacy evidence-directory name; it now holds the DENSE axis.
    expect_no_features: bool = False
    no_features_reason: str | None = None
    require_narrow: bool = False
    require_cut_sensitivity: bool = False
    prominence_db: float = 1.
    max_vertices: int = 650
    coarse_minutes: tuple[int, int] = (2, 5)
    refine_minutes: tuple[int, int] = (4, 9)
    expected_pressure_columns: tuple[int, ...] = ()
    inclination_deg: float = 45.
    traces: bool = False
    precision: str = "float32"
    backend: str = "cpu"
    unsupported: str | None = None

    @property
    def dense_step_hz(self) -> float:
        steps = [b - a for a, b in zip(self.coarse_hz, self.coarse_hz[1:])]
        if not steps or min(steps) <= 0 or len(set(steps)) != 1:
            raise ValueError("Catalogue dense sweep must be uniform and increasing")
        return steps[0]

    def request(self, *, backend: str | None = None,
                frequencies: tuple[float, ...] | None = None,
                record: dict[str, Any] | None = None) -> SolveRequest:
        """Use WG's public request schema; imported identities come from ingestion."""
        options = {"engine": f"beat-{backend or self.backend}",
                   "frequencies_hz": list(frequencies or self.coarse_hz),
                   "frequency_spacing": "linear", "accuracy": "accurate",
                   "polar_config": {"angle_range": [0., 180., 13], "norm_angle": 0.,
                                    "inclination": self.inclination_deg, "distance": 2.,
                                    "spherical_sampling": True, "spherical_theta_count": 9,
                                    "spherical_phi_count": 16, "field_plane": self.traces}}
        if self.geometry.startswith("imported"):
            sources = [s["id"] for s in record["sources"]] if record else ["a", "b"]
            if self.geometry == "imported-curved" or self.name == "driver-loading":
                sources = sources[:1]
            channels = [{"id": f"drive-{s}", "source_ids": [s], "motion": "normal"} for s in sources]
            if self.name == "driver-loading":
                channels[0]["driver"] = deepcopy(DRIVER)
            geometry = {"type": "imported", "ingest_id": (record or {}).get("id", "wgi_" + "0" * 26),
                        "manifest_sha256": (record or {}).get("manifest_sha256", "sha256:" + "1" * 64),
                        "artifact_sha256": (record or {}).get("artifact_sha256", "sha256:" + "2" * 64),
                        "drive_channels": channels,
                        "mesh": {"rigid_size_mm": 20., "transition_mm": 30.,
                                 "source_size_mm": {s: 8. for s in sources}}}
            return SolveRequest.model_validate({"geometry": geometry, "options": options})
        design = deepcopy(OSSE)
        design["mesh"]["quadrants"] = self.quadrants
        if self.geometry == "rosse":
            design = {"formula": "R-OSSE", "R": 150, "r0": 12.7, "a": 60, "a0": 15.5,
                      "mesh": deepcopy(design["mesh"]), "simulation": design["simulation"]}
            design["mesh"].update(throat_resolution=25, mouth_resolution=50, rear_resolution=50)
        if self.geometry == "duct":
            # Driven closed end and a small open aperture: ka ~ 0.02 at the
            # quarter-wave mode (~276 Hz), rather than the old flared termination.
            design.update(L=10, a=2, a0=2, r0=3, throat_ext_length=300, throat_ext_angle=0)
            design["mesh"].update(length_segments=12, throat_resolution=3, mouth_resolution=3)
        return SolveRequest.model_validate({"design": design, "options": options})


CASES = {case.name: case for case in (
    CorpusCase("osse-full", "Full OSSE; no image symmetry", "server/tests/test_real_pipeline.py:PARAMETRIC_BODY"),
    CorpusCase("osse-quarter", "OSSE x0/y0 quarter and real symmetry copies",
               "server/tests/test_real_pipeline.py:PARAMETRIC_BODY", quadrants=1,
               coarse_hz=tuple(float(f) for f in range(500, 3501, 10)),
               coarse_minutes=(1, 8), refine_minutes=(2, 8)),
    CorpusCase("rosse-half", "R-OSSE rollback; yz half keeps the CPU matrix under 650 vertices",
               "server/tests/test_mesh_child.py", geometry="rosse", quadrants=14,
               coarse_hz=tuple(float(f) for f in range(500, 2501, 10)), coarse_minutes=(2, 6)),
    CorpusCase("narrow-resonance", "300 mm driven-end tube with 3 mm radius and small OSSE aperture; measured Q gate",
               "server/design/schema.py:DesignCommon.throat_ext_length", geometry="duct", quadrants=1,
               coarse_hz=tuple(float(f) for f in range(200, 401, 1)),
               require_narrow=True, coarse_minutes=(2, 6), refine_minutes=(4, 10)),
    CorpusCase("imported-two-sources", "Two independently driven CAD discs; channel order and source identity",
               "server/tests/test_cadlink_domain_automatic.py:_box/_bundle", geometry="imported-box",
               coarse_hz=tuple(float(f) for f in range(500, 2501, 10)),
               coarse_minutes=(2, 6), refine_minutes=(4, 10)),
    CorpusCase("imported-tilted-rear", "Curved normal drive, tilted net normal with a rear component",
               "server/tests/test_cadlink_domain_automatic.py:_curved_source_sheet/_curved_face",
               geometry="imported-curved", coarse_hz=tuple(float(f) for f in range(500, 2501, 10)),
               coarse_minutes=(2, 6)),
    CorpusCase("driver-loading", "Normal single-source CAD drive; P1 pressure loading and electrical driver coupling",
               "server/tests/test_cadlink_domain_automatic.py:_box/_bundle; server/tests/test_driver_lem.py:_spec",
               geometry="imported-box", coarse_hz=tuple(float(f) for f in range(20, 301, 2)),
               coarse_minutes=(2, 5)),
    CorpusCase("non-45-cut", "30 degree diagonal cut, horizontal and vertical controls",
               "server/tests/test_cadlink_domain_automatic.py:_box/_bundle", geometry="imported-box",
               inclination_deg=30., require_cut_sensitivity=True,
               coarse_hz=tuple(float(f) for f in range(500, 2501, 10)),
               coarse_minutes=(2, 8), refine_minutes=(2, 8)),
    CorpusCase("sphere-traces", "Full sphere and retained P1 pressure / DP0 Neumann traces",
               "server/tests/test_real_pipeline.py:PARAMETRIC_BODY", quadrants=1, traces=True,
               coarse_hz=tuple(float(f) for f in range(500, 3501, 10)),
               coarse_minutes=(1, 8), refine_minutes=(2, 8)),
)}


@dataclass(frozen=True)
class FrozenCase:
    case: CorpusCase
    request: dict[str, Any]
    mesh_bytes: bytes
    record: dict[str, Any] | None
    mesh_stats: dict[str, Any]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.mesh_bytes).hexdigest()


def _cad_fixture(case: CorpusCase, directory: Path) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    """Reuse WG's real CAD builders and ingestion, never the fake triangle surfaces."""
    tests = str(ROOT / "server/tests")
    sys.path.insert(0, tests)
    try:
        import test_cadlink_domain_automatic as fixtures
        from server.mesh.gmsh_worker import _run_in_gmsh_session
        if case.geometry == "imported-box":
            discs = [(0., 0., 25.)] if case.name == "driver-loading" else [(-28., -12., 10.), (19., 17., 12.)]
            sources = ["driver"] if case.name == "driver-loading" else ["a", "b"]
            bundle = fixtures._bundle(
                directory, case.name,
                lambda path: fixtures._box(path, x=(-60., 60.), discs=discs),
                fixtures._faces_near(*[(x, y, 0.) for x, y, _ in discs]), sources=sources, domain=None)
        else:
            def tilted(path: Path) -> None:
                fixtures._curved_source_sheet(path)
                def rotate() -> None:
                    gmsh = fixtures.gmsh
                    gmsh.clear()
                    gmsh.model.occ.importShapes(str(path), highestDimOnly=False)
                    gmsh.model.occ.synchronize()
                    volumes = gmsh.model.getEntities(3)
                    owned = set(gmsh.model.getBoundary(volumes, combined=False, oriented=False))
                    roots = volumes + [face for face in gmsh.model.getEntities(2) if face not in owned]
                    gmsh.model.occ.rotate(roots, 0., 0., 0., 0., 1., 0., math.pi / 4)
                    gmsh.model.occ.synchronize()
                    gmsh.write(str(path))
                    gmsh.clear()
                _run_in_gmsh_session(rotate)
            bundle = fixtures._bundle(directory, case.name, tilted, fixtures._curved_face,
                                      sources=["curved-rear"], domain=None)
        record = fixtures._ingest(bundle, directory / "ingestion", symmetry_mode="full")
        mesh = Path(record["mesh_store_path"]).read_bytes()
        return mesh, record, record["mesh"]["stats"]
    finally:
        sys.path.remove(tests)


def freeze_case(case: CorpusCase, directory: Path, *, backend: str = "cpu",
                mesher: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
                cad_builder: Callable[..., Any] | None = None) -> FrozenCase:
    """Mesh once, then share these exact bytes with every engine and refinement."""
    record = None
    if case.geometry.startswith("imported"):
        mesh, record, stats = (cad_builder or _cad_fixture)(case, directory)
        request = case.request(backend=backend, record=record)
    else:
        from server.mesh.builder import _build_sync
        from server.mesh.gmsh_worker import _run_in_gmsh_session
        request = case.request(backend=backend)
        design = request.design.model_dump(mode="json")
        built = mesher(design) if mesher else _run_in_gmsh_session(_build_sync, design)
        mesh, stats = built["msh_text"].encode(), built["stats"]
    if stats.get("vertex_count", 0) > case.max_vertices:
        raise ValueError(f"{case.name}: mesh exceeds predeclared {case.max_vertices}-vertex CPU budget; coarsen the catalogue")
    return FrozenCase(case, request.model_dump(mode="json"), mesh, record, stats)


def save_frozen(frozen: FrozenCase, directory: Path) -> None:
    (directory / "surface.msh").write_bytes(frozen.mesh_bytes)
    write_json(directory / "frozen.json", {"case": frozen.case.name, "request": frozen.request,
        "record": frozen.record, "mesh_stats": frozen.mesh_stats, "mesh_sha256": frozen.sha256})
