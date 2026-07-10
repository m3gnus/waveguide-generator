"""Integration coverage for true coupled infinite-baffle solves."""

from __future__ import annotations

import tempfile
import unittest

import numpy as np

try:
    from solver.mesher_adapter import build_waveguide_mesh
except Exception:  # pragma: no cover - guarded by runtime skip
    build_waveguide_mesh = None

from contracts import MeshData, PolarConfig, SimulationRequest
from solver.metal_solver import (
    metal_backend_status,
    solve_circsym_from_params,
    solve_metal_from_msh,
)


_BASE_PAYLOAD = {
    "formula_type": "OSSE",
    "L": 80.0,
    "r0": 12.7,
    "a": 35.0,
    "a0": 0.0,
    "k": 1.0,
    "n": 4.0,
    "q": 0.995,
    "s": 0.0,
    "n_angular": 32,
    "n_length": 10,
    "quadrants": 1234,
    "throat_res": 5.0,
    "mouth_res": 10.0,
    "rear_res": 10.0,
    "source_shape": 2,
    "source_velocity": 1,
}


def _payload(*, sim_type: int, quadrants: int = 1234, morph: bool = False) -> dict:
    out = dict(_BASE_PAYLOAD)
    out["sim_type"] = sim_type
    out["wall_thickness"] = 0.0 if sim_type == 1 else 6.0
    out["quadrants"] = quadrants
    if morph:
        out.update(
            {
                "morph_target": 1,
                "morph_width": 150.0,
                "morph_height": 90.0,
                "morph_corner": 12.0,
                "morph_rate": 0.7,
            }
        )
    return out


def _open_boundary_edges(triangles: np.ndarray) -> np.ndarray:
    counts: dict[tuple[int, int], int] = {}
    for tri in triangles:
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            key = (min(int(a), int(b)), max(int(a), int(b)))
            counts[key] = counts.get(key, 0) + 1
    return np.asarray([edge for edge, count in counts.items() if count == 1], dtype=np.int64)


def _mesher_runtime_ready() -> bool:
    if build_waveguide_mesh is None:
        return False
    try:
        from solver_bootstrap import HORNLAB_MESHER_AVAILABLE, HORNLAB_MESHER_RUNTIME_READY
    except Exception:
        return False
    return bool(HORNLAB_MESHER_AVAILABLE and HORNLAB_MESHER_RUNTIME_READY)


def _metal_runtime_ready() -> bool:
    try:
        status = metal_backend_status()
    except Exception:
        return False
    if not status.get("available"):
        return False
    try:
        from hornlab_metal_bem.sweep import _discover_runtime_smoke_cached
    except Exception:
        return False
    try:
        runtime = _discover_runtime_smoke_cached()
    except Exception:
        return False
    return bool(getattr(runtime, "available", False))


def _request(payload: dict, *, solver_mode: str = "auto") -> SimulationRequest:
    return SimulationRequest(
        mesh=MeshData(
            vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
            indices=[0, 1, 2],
            surfaceTags=[2],
            format="msh",
            boundaryConditions={},
            metadata={},
        ),
        frequency_range=[1000.0, 1000.0],
        num_frequencies=1,
        frequency_spacing="linear",
        sim_type=str(payload["sim_type"]),
        solver_mode=solver_mode,
        polar_config=PolarConfig(
            angle_range=[0.0, 180.0, 5],
            enabled_axes=["horizontal"],
            distance=2.0,
            observation_origin="mouth",
        ),
        solver_backend="metal",
        options={"mesh": {"strategy": "hornlab_mesher", "waveguide_params": dict(payload)}},
    )


def _solve_full_3d(payload: dict) -> dict:
    mesh_result = build_waveguide_mesh(payload, include_canonical=False)
    metadata = mesh_result.get("metadata") or mesh_result.get("stats", {}).get("metadata")
    with tempfile.NamedTemporaryFile(suffix=".msh", mode="w", encoding="utf-8") as msh_file:
        msh_file.write(mesh_result["msh_text"])
        msh_file.flush()
        return solve_metal_from_msh(
            msh_file.name,
            _request(payload, solver_mode="full_3d"),
            mesh_metadata=metadata,
        )


def _finite_spl(result: dict) -> np.ndarray:
    values = np.asarray(result["spl_on_axis"]["spl"], dtype=float)
    if not np.all(np.isfinite(values)):
        raise AssertionError(f"non-finite SPL values: {values!r}")
    return values


def _forward_directivity_db(result: dict) -> np.ndarray:
    row = result["directivity"]["horizontal"][0]
    values = [
        float(value)
        for angle, value in row
        if float(angle) <= 90.0 and value is not None and np.isfinite(float(value))
    ]
    if not values:
        raise AssertionError(f"no finite forward directivity values: {row!r}")
    return np.asarray(values, dtype=float)


@unittest.skipUnless(_mesher_runtime_ready(), "hornlab-waveguide-mesher runtime not available")
class InfiniteBaffleCoupledMeshIntegrationTest(unittest.TestCase):
    def test_wg_adapter_builds_ib_mesh_with_z0_aperture_tag(self):
        result = build_waveguide_mesh(_payload(sim_type=1), include_canonical=True)
        canonical = result["canonical_mesh"]
        verts = np.asarray(canonical["vertices"], dtype=float).reshape(-1, 3)
        tris = np.asarray(canonical["indices"], dtype=np.int64).reshape(-1, 3)
        tags = np.asarray(canonical["surfaceTags"], dtype=np.int32)

        self.assertIn(2, set(int(tag) for tag in tags))
        self.assertNotIn(4, set(int(tag) for tag in tags))
        self.assertEqual(result["metadata"]["apertureTag"], 12)
        self.assertIn(12, set(int(tag) for tag in tags))
        self.assertNotIn(4, set(int(tag) for tag in tags))
        # The coupled aperture mesh lives in z <= 0: source and inner wall form
        # the recessed interior, and tag 12 is the planar Rayleigh aperture cap.
        self.assertLessEqual(float(np.max(verts[:, 2])), 1.0e-9)
        self.assertLess(float(np.min(verts[:, 2])), -0.05)
        p0 = verts[tris[:, 0]]
        p1 = verts[tris[:, 1]]
        p2 = verts[tris[:, 2]]
        signed_volume = float(np.sum(p0 * np.cross(p1, p2)) / 6.0)
        self.assertLess(signed_volume, 0.0)
        source = tags == 2
        source_z_projection = float(
            np.sum(np.cross(p1[source] - p0[source], p2[source] - p0[source])[:, 2])
        )
        # Source cap sits at the throat; its normal points +z into the air column
        # toward the mouth on z=0.
        self.assertGreater(source_z_projection, 0.0)

        aperture = tags == 12
        self.assertGreater(int(np.count_nonzero(aperture)), 0)
        aperture_z = np.abs(verts[tris[aperture]][:, :, 2])
        self.assertLessEqual(float(np.max(aperture_z)), 1.0e-9)
        aperture_face_z = np.cross(
            p1[aperture] - p0[aperture],
            p2[aperture] - p0[aperture],
        )[:, 2]
        aperture_z_projection = float(np.sum(aperture_face_z))
        # Canonical coupled-IB meshes describe the interior cavity domain:
        # source normals point +Z toward the mouth, while every Rayleigh
        # aperture face points -Z into the cavity. Exterior half-space
        # evaluation is selected by tag 12, not by reversing that winding.
        self.assertTrue(np.all(aperture_face_z < 0.0))
        self.assertLess(aperture_z_projection, 0.0)

        edges = _open_boundary_edges(tris)
        self.assertEqual(len(edges), 0, "full-domain coupled aperture mesh should be closed")


@unittest.skipUnless(
    _mesher_runtime_ready() and _metal_runtime_ready(),
    "hornlab-waveguide-mesher and Metal runtime not available",
)
class InfiniteBaffleCoupledSolveIntegrationTest(unittest.TestCase):
    def test_ib_solve_is_forward_beam_via_coupled_circsym(self):
        ib_payload = _payload(sim_type=1)
        request = _request(ib_payload).model_copy(update={"solver_mode": "circsym"})

        ib = solve_circsym_from_params(ib_payload, request)

        self.assertEqual(ib["metadata"]["solver_mode"], "circsym")
        self.assertEqual(ib["metadata"]["metal"]["solver_mode"], "circsym")
        ib_spl = np.asarray(ib["spl_on_axis"]["spl"], dtype=float)
        self.assertTrue(np.all(np.isfinite(ib_spl)))

        native_diagnostics = ib["metadata"]["metal"]["native_diagnostics"]
        diagnostic_entries = [
            entry for entry in native_diagnostics if isinstance(entry, dict)
        ]
        self.assertTrue(any(entry.get("coupled_ib") is True for entry in diagnostic_entries))

        ib_horizontal = ib["directivity"]["horizontal"][0]
        front = next(
            float(value)
            for angle, value in ib_horizontal
            if np.isclose(float(angle), 0.0) and value is not None
        )
        rear = next(
            value
            for angle, value in ib_horizontal
            if np.isclose(float(angle), 180.0)
        )
        finite_horizontal = [
            float(value)
            for _, value in ib_horizontal
            if value is not None and np.isfinite(float(value))
        ]
        self.assertAlmostEqual(front, max(finite_horizontal), delta=1.0e-6)
        rear_db = float(rear) if rear is not None else -np.inf
        self.assertGreater(front - rear_db, 10.0)

    def test_circular_circsym_and_full_3d_coupled_ib_match_within_1_db(self):
        payload = _payload(sim_type=1)

        circsym = solve_circsym_from_params(
            payload,
            _request(payload, solver_mode="circsym"),
        )
        full_3d = _solve_full_3d(payload)

        self.assertEqual(circsym["metadata"]["infinite_baffle"]["backend"], "circsym_coupled")
        self.assertEqual(full_3d["metadata"]["infinite_baffle"]["backend"], "full_3d_coupled")
        delta_db = np.max(np.abs(_forward_directivity_db(circsym) - _forward_directivity_db(full_3d)))
        self.assertLessEqual(float(delta_db), 1.0)

    def test_tritonia_class_quarter_and_full_domain_coupled_ib_match(self):
        full_payload = _payload(sim_type=1, quadrants=1234, morph=True)
        quarter_payload = _payload(sim_type=1, quadrants=1, morph=True)

        full_domain = _solve_full_3d(full_payload)
        quarter = _solve_full_3d(quarter_payload)

        self.assertIsNone(full_domain["metadata"]["metal"]["native_symmetry_plane"])
        self.assertEqual(quarter["metadata"]["metal"]["native_symmetry_plane"], "yz+xz")
        self.assertEqual(full_domain["metadata"]["infinite_baffle"]["aperture_tag"], 12)
        self.assertEqual(quarter["metadata"]["infinite_baffle"]["aperture_tag"], 12)
        delta_db = np.max(np.abs(_finite_spl(quarter) - _finite_spl(full_domain)))
        self.assertLessEqual(float(delta_db), 0.1)


if __name__ == "__main__":
    unittest.main()
