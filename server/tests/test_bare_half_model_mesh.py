"""Bare (wall-less) half-model meshes: the cut cross-section must stay open.

The Metal solver's open-edge guard is disabled for bare meshes (the mouth rim
is a legitimate free edge after mirroring), so a regression to the
pre-585a9f8 mesher behaviour — capping the cut cross-section with triangles
lying in the symmetry plane — produces NO open-edge signal at all. This test
covers the bare reduced-domain gap the enclosure-closure and true-IB image
tests cannot see.

Also asserts the include_canonical=False stats path (tag counts without the
full canonical list payload).
"""

import unittest

import numpy as np

try:
    from solver.mesher_adapter import build_waveguide_mesh
except Exception:  # pragma: no cover - adapter import guarded below
    build_waveguide_mesh = None

from contracts import WaveguideParamsRequest

_BARE_HALF = {
    "formula_type": "OSSE",
    "L": "80",
    "r0": 12.7,
    "a": "40",
    "a0": 10.0,
    "k": 1.0,
    "n": 4.0,
    "q": 0.99,
    "s": "0.6",
    "n_angular": 32,
    "n_length": 8,
    "throat_res": 8.0,
    "mouth_res": 18.0,
    "rear_res": 24.0,
    "wall_thickness": 0.0,
    "enc_depth": 0.0,
    "source_shape": 2,
}


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


def _open_boundary_edges(triangles: np.ndarray) -> np.ndarray:
    counts: dict[tuple[int, int], int] = {}
    for tri in triangles:
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            counts[(min(a, b), max(a, b))] = counts.get((min(a, b), max(a, b)), 0) + 1
    return np.array([edge for edge, c in counts.items() if c == 1], dtype=np.int64)


@unittest.skipUnless(_mesher_runtime_ready(), "hornlab-waveguide-mesher runtime not available")
class BareHalfModelMeshTest(unittest.TestCase):
    def _build(self, quadrants: str):
        payload = WaveguideParamsRequest(**_BARE_HALF).model_dump()
        payload["quadrants"] = quadrants
        return build_waveguide_mesh(payload, include_canonical=True)

    def test_bare_half_models_keep_the_cut_cross_section_open(self):
        for quadrants, axis in (("12", 1), ("14", 0)):
            with self.subTest(quadrants=quadrants):
                result = self._build(quadrants)
                canonical = result["canonical_mesh"]
                verts = np.asarray(canonical["vertices"], dtype=float).reshape(-1, 3)
                tris = np.asarray(canonical["indices"], dtype=np.int64).reshape(-1, 3)

                tol = 1e-6
                # No triangle may lie entirely in the cut plane: that is the
                # capped cross-section defect, which is invisible to open-edge
                # checks (the cap closes the rim).
                in_plane = np.all(np.abs(verts[tris][:, :, axis]) <= tol, axis=1)
                self.assertEqual(
                    int(np.count_nonzero(in_plane)),
                    0,
                    f"{int(np.count_nonzero(in_plane))} triangle(s) lie in the "
                    f"cut plane of the bare {quadrants} half model (capped "
                    "cross-section regression).",
                )

                # The mirror cut itself must exist: some open edges on the
                # cut plane...
                edges = _open_boundary_edges(tris)
                self.assertGreater(len(edges), 0)
                on_plane = np.all(np.abs(verts[edges][:, :, axis]) <= tol, axis=1)
                self.assertGreater(
                    int(np.count_nonzero(on_plane)),
                    0,
                    "expected cut-plane open edges on the bare half model",
                )
                # ...and the mouth rim is a legitimate off-plane open edge.
                self.assertGreater(
                    int(np.count_nonzero(~on_plane)),
                    0,
                    "expected the bare mouth rim to stay an open edge",
                )

    def test_include_canonical_false_still_reports_tag_counts(self):
        payload = WaveguideParamsRequest(**_BARE_HALF).model_dump()
        payload["quadrants"] = "12"
        result = build_waveguide_mesh(payload, include_canonical=False)
        self.assertNotIn("canonical_mesh", result)
        tag_counts = result["stats"]["tagCounts"]
        self.assertGreater(int(tag_counts["1"]), 0)
        self.assertGreater(int(tag_counts["2"]), 0)


if __name__ == "__main__":
    unittest.main()
