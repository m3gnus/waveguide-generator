"""Integration coverage for true infinite-baffle image-plane solves."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

try:
    from solver.mesher_adapter import build_waveguide_mesh
except Exception:  # pragma: no cover - guarded by runtime skip
    build_waveguide_mesh = None

from contracts import MeshData, PolarConfig, SimulationRequest
from solver.metal_solver import metal_backend_status, solve_metal_from_msh


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
    "n_angular": 20,
    "n_length": 6,
    "quadrants": 1234,
    "throat_res": 8.0,
    "mouth_res": 20.0,
    "rear_res": 25.0,
    "source_shape": 2,
    "source_velocity": 1,
}


def _payload(*, sim_type: int) -> dict:
    out = dict(_BASE_PAYLOAD)
    out["sim_type"] = sim_type
    out["wall_thickness"] = 0.0 if sim_type == 1 else 6.0
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


def _request(payload: dict) -> SimulationRequest:
    return SimulationRequest(
        mesh=MeshData(
            vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
            indices=[0, 1, 2],
            surfaceTags=[2],
            format="msh",
            boundaryConditions={},
            metadata={},
        ),
        frequency_range=[200.0, 400.0],
        num_frequencies=2,
        frequency_spacing="linear",
        sim_type=str(payload["sim_type"]),
        polar_config=PolarConfig(
            angle_range=[0.0, 180.0, 5],
            enabled_axes=["horizontal"],
            distance=2.0,
            observation_origin="mouth",
        ),
        solver_backend="metal",
        options={"mesh": {"strategy": "hornlab_mesher", "waveguide_params": dict(payload)}},
    )


@unittest.skipUnless(_mesher_runtime_ready(), "hornlab-waveguide-mesher runtime not available")
class InfiniteBaffleImageMeshIntegrationTest(unittest.TestCase):
    def test_wg_adapter_builds_ib_mesh_with_only_z0_open_edges(self):
        result = build_waveguide_mesh(_payload(sim_type=1), include_canonical=True)
        canonical = result["canonical_mesh"]
        verts = np.asarray(canonical["vertices"], dtype=float).reshape(-1, 3)
        tris = np.asarray(canonical["indices"], dtype=np.int64).reshape(-1, 3)
        tags = np.asarray(canonical["surfaceTags"], dtype=np.int32)

        self.assertIn(2, set(int(tag) for tag in tags))
        self.assertNotIn(4, set(int(tag) for tag in tags))
        # The xy image shell lives in z >= 0 with the mouth rim on z=0 and the
        # throat/body extending into +z (hornlab-metal-bem's positive-z reduced
        # domain); the rigid-plane image across z=0 completes the double horn.
        self.assertGreaterEqual(float(np.min(verts[:, 2])), -1.0e-9)
        self.assertGreater(float(np.max(verts[:, 2])), 0.05)
        p0 = verts[tris[:, 0]]
        p1 = verts[tris[:, 1]]
        p2 = verts[tris[:, 2]]
        signed_volume = float(np.sum(p0 * np.cross(p1, p2)) / 6.0)
        self.assertGreater(signed_volume, 0.0)
        source = tags == 2
        source_z_projection = float(
            np.sum(np.cross(p1[source] - p0[source], p2[source] - p0[source])[:, 2])
        )
        # Source cap sits at the +z throat; its outward normal points -z into the
        # air column toward the mouth on z=0, so its z-projected area is negative.
        self.assertLess(source_z_projection, 0.0)

        edges = _open_boundary_edges(tris)
        self.assertGreater(len(edges), 0, "expected the mouth rim to remain open")
        z_on_edges = np.abs(verts[edges][:, :, 2])
        self.assertLessEqual(float(np.max(z_on_edges)), 1.0e-9)


@unittest.skipUnless(
    _mesher_runtime_ready() and _metal_runtime_ready(),
    "hornlab-waveguide-mesher and Metal runtime not available",
)
class InfiniteBaffleImageSolveIntegrationTest(unittest.TestCase):
    def test_ib_solve_has_half_space_signature_vs_freestanding(self):
        ib_payload = _payload(sim_type=1)
        fs_payload = _payload(sim_type=2)

        with tempfile.TemporaryDirectory(prefix="wg-ib-image-test-") as tmp_dir:
            tmp = Path(tmp_dir)
            ib_path = tmp / "ib.msh"
            fs_path = tmp / "freestanding.msh"
            ib_path.write_text(
                build_waveguide_mesh(ib_payload, include_canonical=False)["msh_text"],
                encoding="utf-8",
            )
            fs_path.write_text(
                build_waveguide_mesh(fs_payload, include_canonical=False)["msh_text"],
                encoding="utf-8",
            )

            ib = solve_metal_from_msh(ib_path, _request(ib_payload))
            fs = solve_metal_from_msh(fs_path, _request(fs_payload))

        # Infinite-baffle signature = on-axis half-space GAIN over free-standing.
        # A rigid baffle radiates into 2*pi instead of 4*pi, so on-axis SPL rises
        # by up to +6 dB at low frequency (the source is acoustically small there).
        ib_spl = np.asarray(ib["spl_on_axis"]["spl"], dtype=float)
        fs_spl = np.asarray(fs["spl_on_axis"]["spl"], dtype=float)
        delta_db = float(ib_spl[0] - fs_spl[0])
        self.assertGreater(delta_db, 2.0)
        self.assertLess(delta_db, 6.5)  # bounded by the 2*pi/4*pi (+6 dB) limit

        # The xy image models the rigid baffle by MIRRORING the horn across z=0,
        # so the far field is symmetric about the plane: 180 deg is the mirror of
        # the 0 deg front and MUST equal it. It is not a physical rear lobe, so we
        # assert the image symmetry rather than rear suppression (the meaningful
        # infinite-baffle test is the on-axis gain above).
        ib_horizontal = ib["directivity"]["horizontal"][0]
        ib_front = float(ib_horizontal[0][1])
        ib_rear = ib_horizontal[-1][1]
        ib_rear_db = float(ib_rear) if ib_rear is not None else -np.inf
        self.assertAlmostEqual(ib_rear_db, ib_front, delta=0.5)


if __name__ == "__main__":
    unittest.main()
