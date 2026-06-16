"""Integration guard: reduced-domain enclosure meshes stay watertight.

Regression for the WG "Superduper small" quarter-symmetry enclosure solve. A
bare (wall_thickness=0) OSSE mouth in a box whose ``enc_edge`` equals the
smallest enclosure margin (``enc_space_l = enc_space_r = enc_edge = 1``) used to
leave a ~0 mm flat-baffle ring that OCC dropped, tearing the front-baffle-to-
side-wall seam open off the symmetry planes. The Metal solver's open-edge guard
(correctly strict for enclosures) then rejected the leaking mesh with:

    native_symmetry_plane='yz+xz' requires every open boundary edge to lie on
    X=0 or Y=0; boundary edge ... is off the requested symmetry plane(s).

The mesher now clamps the edge roundover to keep a real flat-baffle clearance.
This test drives the exact failing geometry through the solver mesh adapter and
asserts every open boundary edge lies on a symmetry cut plane.
"""

import unittest

import numpy as np

try:
    from solver.mesher_adapter import build_waveguide_mesh
except Exception:  # pragma: no cover - adapter import guarded below
    build_waveguide_mesh = None

from contracts import WaveguideParamsRequest

# Full "Superduper small" quarter-symmetry enclosure parameters (the failing job).
_SUPERDUPER_SMALL = {
    "L": "160", "a": "45 - 10*cos(1*p)^2 -32*sin(p*1)^12", "a0": 15.5, "b": 0.2,
    "circ_arc_radius": 0.0, "circ_arc_term_angle": 1.0, "corner_segments": 4,
    "enc_back_resolution": "40,40,40,40", "enc_depth": 500.0, "enc_edge": 1.0,
    "enc_edge_type": 1, "enc_front_resolution": "40,40,40,40", "enc_space_b": 304.0,
    "enc_space_l": 1.0, "enc_space_r": 1.0, "enc_space_t": 304.0, "formula_type": "OSSE",
    "h": 0.0, "k": 0.5, "length_mode": "total", "m": 0.85, "morph_allow_shrinkage": 0,
    "morph_corner": 18.0, "morph_fixed": 0.0, "morph_height": 0.0, "morph_rate": 3.0,
    "morph_target": 1, "morph_width": 0.0, "mouth_res": 25.0, "msh_version": "2.2",
    "n": 5.0, "n_angular": 80, "n_length": 20, "q": 0.993, "quadrants": 1, "r": 0.4,
    "r0": 12.7, "rear_res": 40.0, "rot": 0.0, "s": "0.8", "slot_length": 0.0,
    "source_curv": 0, "source_radius": -1.0, "source_shape": 2, "source_velocity": 1,
    "step_body": "inner_surface", "throat_ext_angle": 0.0, "throat_ext_length": 0.0,
    "throat_profile": 1, "throat_res": 5.0, "tmax": 1.0, "vertical_offset": 80.0,
    "wall_thickness": 0.0,
}


def _open_boundary_edges(triangles: np.ndarray) -> np.ndarray:
    counts: dict[tuple[int, int], int] = {}
    for tri in triangles:
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            counts[(min(a, b), max(a, b))] = counts.get((min(a, b), max(a, b)), 0) + 1
    return np.array([edge for edge, c in counts.items() if c == 1], dtype=np.int64)


def _mesher_runtime_ready() -> bool:
    if build_waveguide_mesh is None:
        return False
    try:
        from solver_bootstrap import (
            HORNLAB_MESHER_AVAILABLE,
            HORNLAB_MESHER_RUNTIME_READY,
        )
    except Exception:
        return False
    return bool(HORNLAB_MESHER_AVAILABLE and HORNLAB_MESHER_RUNTIME_READY)


@unittest.skipUnless(_mesher_runtime_ready(), "hornlab-waveguide-mesher runtime not available")
class ReducedEnclosureMeshClosureTest(unittest.TestCase):
    def test_quarter_enclosure_edge_equals_space_has_no_off_plane_open_edges(self):
        payload = WaveguideParamsRequest(**_SUPERDUPER_SMALL).model_dump()
        result = build_waveguide_mesh(payload, include_canonical=True)
        canonical = result["canonical_mesh"]
        verts = np.asarray(canonical["vertices"], dtype=float).reshape(-1, 3)
        tris = np.asarray(canonical["indices"], dtype=np.int64).reshape(-1, 3)

        edges = _open_boundary_edges(tris)
        self.assertGreater(len(edges), 0, "expected open edges on the symmetry cut planes")

        tol = 1e-6
        on_x0 = np.all(np.abs(verts[edges][:, :, 0]) <= tol, axis=1)
        on_y0 = np.all(np.abs(verts[edges][:, :, 1]) <= tol, axis=1)
        off_plane = int(np.count_nonzero(~(on_x0 | on_y0)))
        self.assertEqual(
            off_plane,
            0,
            f"{off_plane} open boundary edge(s) lie off the X=0/Y=0 symmetry planes; "
            "the front-baffle-to-side-wall seam tore open (edge-clamp regression).",
        )


if __name__ == "__main__":
    unittest.main()
