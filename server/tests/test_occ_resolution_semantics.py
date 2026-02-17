import unittest
from typing import Dict
from unittest.mock import patch

import numpy as np

from solver.deps import GMSH_OCC_RUNTIME_READY, gmsh
from solver.waveguide_builder import (
    _build_mouth_rim,
    _build_surface_from_points,
    _build_throat_disc_from_ring,
    _collect_boundary_curves,
    _compute_point_grids,
    _configure_mesh_size,
    gmsh_lock,
    _axial_interpolated_size,
    _panel_corner_points_by_quadrant,
    _parse_quadrant_resolutions,
    _rear_resolution_active,
)


class OccResolutionSemanticsTest(unittest.TestCase):
    def test_axial_interpolation_between_throat_and_mouth(self):
        throat = 4.0
        mouth = 10.0
        self.assertAlmostEqual(_axial_interpolated_size(0.0, 0.0, 120.0, throat, mouth), throat)
        self.assertAlmostEqual(_axial_interpolated_size(120.0, 0.0, 120.0, throat, mouth), mouth)
        self.assertAlmostEqual(_axial_interpolated_size(60.0, 0.0, 120.0, throat, mouth), 7.0)

    def test_rear_resolution_only_applies_to_freestanding_wall_mode(self):
        self.assertTrue(_rear_resolution_active(enc_depth=0.0, wall_thickness=6.0))
        self.assertFalse(_rear_resolution_active(enc_depth=200.0, wall_thickness=6.0))
        self.assertFalse(_rear_resolution_active(enc_depth=0.0, wall_thickness=0.0))

    def test_quadrant_resolution_parsing_supports_scalar_and_lists(self):
        self.assertEqual(_parse_quadrant_resolutions("5", 9.0), [5.0, 5.0, 5.0, 5.0])
        self.assertEqual(_parse_quadrant_resolutions("6,7,8,9", 9.0), [6.0, 7.0, 8.0, 9.0])
        self.assertEqual(_parse_quadrant_resolutions("6,7", 9.0), [6.0, 7.0, 9.0, 9.0])
        self.assertEqual(_parse_quadrant_resolutions("", 9.0), [9.0, 9.0, 9.0, 9.0])

    def test_quadrant_corner_mapping_matches_contract(self):
        # Q1(+x,+y), Q2(-x,+y), Q3(-x,-y), Q4(+x,-y)
        corners = _panel_corner_points_by_quadrant(-2.0, 3.0, -4.0, 5.0, 100.0)
        self.assertEqual(corners[0], (3.0, 5.0, 100.0))
        self.assertEqual(corners[1], (-2.0, 5.0, 100.0))
        self.assertEqual(corners[2], (-2.0, -4.0, 100.0))
        self.assertEqual(corners[3], (3.0, -4.0, 100.0))

    def test_collect_boundary_curves_returns_empty_for_empty_input(self):
        self.assertEqual(_collect_boundary_curves([]), [])

    def test_collect_boundary_curves_deduplicates_curves_across_surfaces(self):
        with patch("solver.waveguide_builder.gmsh") as gmsh_mock:
            gmsh_mock.model.getBoundary.side_effect = [
                [(1, 11), (1, 12)],
                [(1, 12), (1, 13), (2, 99)],
            ]
            out = _collect_boundary_curves([101, 202])

        self.assertEqual(out, [11, 12, 13])

    def test_outer_throat_ring_is_larger_and_wall_thickness_from_inner(self):
        params = {
            "formula_type": "R-OSSE",
            "R": "160",
            "a": "60",
            "r0": 12.7,
            "a0": 15.5,
            "k": 2.0,
            "r": 0.4,
            "b": 0.2,
            "m": 0.85,
            "q": 3.4,
            "tmax": 1.0,
            "quadrants": 1234,
            "enc_depth": 0.0,
            "wall_thickness": 6.0,
            "n_angular": 100,
            "n_length": 20,
        }
        inner_points, outer_points = _compute_point_grids(params)
        self.assertIsNotNone(outer_points)
        self.assertIsNotNone(inner_points)

        inner_ring = inner_points[:, 0, :]
        outer_ring = outer_points[:, 0, :]
        wall = float(params["wall_thickness"])

        radial_inner = np.sqrt(inner_ring[:, 0] ** 2 + inner_ring[:, 1] ** 2)
        radial_outer = np.sqrt(outer_ring[:, 0] ** 2 + outer_ring[:, 1] ** 2)
        self.assertTrue(np.all(radial_outer > radial_inner))

        dist = np.sqrt(np.sum((outer_ring - inner_ring) ** 2, axis=1))
        self.assertTrue(np.allclose(dist, wall, atol=1e-9))

    @unittest.skipUnless(
        GMSH_OCC_RUNTIME_READY,
        "Requires supported gmsh Python runtime for OCC meshing integration test.",
    )
    def test_rear_resolution_coarsens_outer_without_changing_inner_or_source(self):
        fine = self._mesh_group_triangle_counts(rear_res=2.0)
        coarse = self._mesh_group_triangle_counts(rear_res=25.0)

        self.assertGreater(fine["outer"], coarse["outer"])
        self.assertEqual(fine["inner"], coarse["inner"])
        self.assertEqual(fine["throat_disc"], coarse["throat_disc"])

    @staticmethod
    def _mesh_group_triangle_counts(rear_res: float) -> Dict[str, int]:
        params = {
            "formula_type": "R-OSSE",
            "R": "160",
            "a": "60",
            "r0": 12.7,
            "a0": 15.5,
            "k": 2.0,
            "r": 0.4,
            "b": 0.2,
            "m": 0.85,
            "q": 3.4,
            "tmax": 1.0,
            "quadrants": 1234,
            "enc_depth": 0.0,
            "wall_thickness": 6.0,
            "n_angular": 100,
            "n_length": 20,
            "throat_res": 5.0,
            "mouth_res": 8.0,
            "rear_res": float(rear_res),
        }

        with gmsh_lock:
            initialized_here = False
            try:
                if not gmsh.isInitialized():
                    gmsh.initialize()
                    initialized_here = True

                gmsh.option.setNumber("General.Terminal", 0)
                gmsh.clear()
                gmsh.model.add("OccRearResolutionSemantics")

                inner_points, outer_points = _compute_point_grids(params)
                if outer_points is None:
                    raise AssertionError("Expected outer_points for free-standing wall mode.")

                inner_dimtags = _build_surface_from_points(inner_points, closed=True)
                throat_disc_dimtags = _build_throat_disc_from_ring(inner_points[:, 0, :], closed=True)
                outer_dimtags = _build_surface_from_points(outer_points, closed=True)
                mouth_dimtags = _build_mouth_rim(inner_points, outer_points, closed=True)

                gmsh.model.occ.synchronize()

                surface_groups = {
                    "inner": [tag for _, tag in inner_dimtags],
                    "throat_disc": [tag for _, tag in throat_disc_dimtags],
                    "outer": [tag for dim, tag in outer_dimtags if dim == 2],
                    "mouth": [tag for dim, tag in mouth_dimtags if dim == 2],
                }

                _configure_mesh_size(
                    inner_points,
                    surface_groups,
                    throat_res=float(params["throat_res"]),
                    mouth_res=float(params["mouth_res"]),
                    rear_res=float(params["rear_res"]),
                )
                gmsh.option.setNumber("Mesh.Algorithm", 1)
                gmsh.model.mesh.generate(2)

                return {
                    group_key: OccResolutionSemanticsTest._triangle_count(surface_tags)
                    for group_key, surface_tags in surface_groups.items()
                }
            finally:
                if gmsh.isInitialized():
                    gmsh.clear()
                if initialized_here and gmsh.isInitialized():
                    gmsh.finalize()

    @staticmethod
    def _triangle_count(surface_tags):
        total = 0
        for tag in surface_tags:
            elem_tags, _ = gmsh.model.mesh.getElementsByType(2, int(tag))
            total += len(elem_tags)
        return int(total)


if __name__ == "__main__":
    unittest.main()
