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


class OccBemMeshTopologyTest(unittest.TestCase):
    """Verify that the OCC mesh with wall_thickness > 0 has correct BEM topology.

    Regression guard for the disconnected-outer-wall bug and tag assignment:
    - All surfaces (inner horn + outer shell + rear disc + mouth rim) must be tag 1 (SD1G0).
    - Tag 3 (SD2G0) must NOT appear — it is reserved for enclosure box exports only.
    - Tag 1 and tag 2 must share throat-ring nodes (connected mesh).
    - The mesh must be one connected component (no isolated outer shell).
    """

    # Asymmetric R-OSSE config from 260218asro.txt — chosen because it produces a stable
    # Gmsh mesh both with and without the outer wall shell.
    _BASE_PARAMS = {
        "formula_type": "R-OSSE",
        "R": "185 * (abs(cos(p)/1.8)**3 + abs(sin(p)/0.8)**4)**(-1/6)",
        "r0": 12.7,
        "a0": 15.5,
        "a": "25 * (abs(cos(p)/1.2)**4  + abs(sin(p)/1)**3)**(-1/3)",
        "k": 0.6,
        "r": 0.4,
        "b": "0.25 * (abs(cos(p)/1.2)**4  + abs(sin(p)/1)**3)**(-1/3)",
        "m": 0.86,
        "q": 3.5,
        "n_angular": 50,
        "n_length": 20,
        "throat_res": 5.0,
        "mouth_res": 15.0,
        "rear_res": 25.0,
        "quadrants": 1234,
        "sim_type": 2,
        "msh_version": "2.2",
    }

    @staticmethod
    def _iter_msh_triangles(msh_text):
        import re

        elems_m = re.search(r"\$Elements\n(\d+)\n(.*?)\$EndElements", msh_text, re.DOTALL)
        if elems_m is None:
            return []

        triangles = []
        for line in elems_m.group(2).strip().split("\n"):
            parts = line.split()
            if len(parts) < 3:
                continue
            if int(parts[1]) != 2:
                continue
            ntags = int(parts[2])
            phys_tag = int(parts[3]) if ntags >= 1 else -1
            n0 = int(parts[3 + ntags])
            n1 = int(parts[4 + ntags])
            n2 = int(parts[5 + ntags])
            triangles.append((phys_tag, (n0, n1, n2)))
        return triangles

    @staticmethod
    def _edge_counts_from_triangles(triangles):
        from collections import defaultdict

        edge_uses = defaultdict(list)

        def edge_key(a, b):
            return (a, b) if a < b else (b, a)

        for tri_idx, (phys_tag, (n0, n1, n2)) in enumerate(triangles):
            for a, b in ((n0, n1), (n1, n2), (n2, n0)):
                edge_uses[edge_key(a, b)].append((tri_idx, phys_tag))
        return edge_uses

    @unittest.skipUnless(
        GMSH_OCC_RUNTIME_READY,
        "Requires supported gmsh Python runtime for OCC meshing integration test.",
    )
    def test_freestanding_wall_mesh_has_only_tag1_and_tag2(self):
        """Free-standing wall mesh (wall_thickness=6) must have exactly tags 1 and 2.

        All rigid-wall surfaces (inner horn + outer shell + rear disc + mouth rim)
        must be in tag 1 (SD1G0).  Tag 3 (SD2G0) is reserved for enclosure-box exports
        only (enc_depth > 0) and must not appear here.
        """
        from collections import Counter
        from solver.waveguide_builder import build_waveguide_mesh

        params = dict(self._BASE_PARAMS)
        params["wall_thickness"] = 6.0
        params["enc_depth"] = 0.0

        result = build_waveguide_mesh(params, include_canonical=True)
        canonical = result["canonical_mesh"]
        tag_counts = Counter(canonical["surfaceTags"])

        self.assertNotIn(
            3, tag_counts,
            "Free-standing wall mesh must not contain tag 3 (SD2G0 is for enclosure exports only).",
        )
        self.assertIn(1, tag_counts, "Mesh must have tag 1 (SD1G0: all rigid wall surfaces).")
        self.assertIn(2, tag_counts, "Mesh must have tag 2 (SD1D1001: throat source disc).")

    @unittest.skipUnless(
        GMSH_OCC_RUNTIME_READY,
        "Requires supported gmsh Python runtime for OCC meshing integration test.",
    )
    def test_freestanding_wall_tag1_and_tag2_share_throat_boundary_nodes(self):
        """Inner horn surface (tag 1) and throat disc (tag 2) must share throat-ring nodes."""
        import re
        import numpy as np
        from solver.waveguide_builder import build_waveguide_mesh

        params = dict(self._BASE_PARAMS)
        params["wall_thickness"] = 6.0
        params["enc_depth"] = 0.0

        result = build_waveguide_mesh(params, include_canonical=True)
        msh = result["msh_text"]

        nodes_m = re.search(r"\$Nodes\n(\d+)\n(.*?)\$EndNodes", msh, re.DOTALL)
        nodes = {}
        for line in nodes_m.group(2).strip().split("\n"):
            parts = line.split()
            nodes[int(parts[0])] = np.array([float(parts[1]), float(parts[2]), float(parts[3])])

        elems_m = re.search(r"\$Elements\n(\d+)\n(.*?)\$EndElements", msh, re.DOTALL)
        tris_by_tag: dict = {}
        for line in elems_m.group(2).strip().split("\n"):
            parts = line.split()
            if len(parts) < 3:
                continue
            if int(parts[1]) == 2:  # triangle
                ntags = int(parts[2])
                phys_tag = int(parts[3])
                n0, n1, n2 = int(parts[3 + ntags]), int(parts[4 + ntags]), int(parts[5 + ntags])
                tris_by_tag.setdefault(phys_tag, []).append((n0, n1, n2))

        nodes_tag1 = {n for tri in tris_by_tag.get(1, []) for n in tri}
        nodes_tag2 = {n for tri in tris_by_tag.get(2, []) for n in tri}
        shared = nodes_tag1 & nodes_tag2

        self.assertGreater(
            len(shared),
            0,
            "Tag 1 (rigid wall) and tag 2 (throat disc) must share throat-ring boundary nodes.",
        )

    @unittest.skipUnless(
        GMSH_OCC_RUNTIME_READY,
        "Requires supported gmsh Python runtime for OCC meshing integration test.",
    )
    def test_freestanding_wall_mesh_is_one_connected_component(self):
        """All tag-1 triangles must form a single connected component.

        Regression guard: the outer shell must NOT be disconnected from the inner horn.
        """
        import re
        from solver.waveguide_builder import build_waveguide_mesh

        params = dict(self._BASE_PARAMS)
        params["wall_thickness"] = 6.0
        params["enc_depth"] = 0.0

        result = build_waveguide_mesh(params, include_canonical=True)
        msh = result["msh_text"]

        elems_m = re.search(r"\$Elements\n(\d+)\n(.*?)\$EndElements", msh, re.DOTALL)
        tris_tag1 = []
        for line in elems_m.group(2).strip().split("\n"):
            parts = line.split()
            if len(parts) < 3:
                continue
            if int(parts[1]) == 2:  # triangle
                ntags = int(parts[2])
                phys_tag = int(parts[3])
                if phys_tag == 1:
                    n0, n1, n2 = int(parts[3 + ntags]), int(parts[4 + ntags]), int(parts[5 + ntags])
                    tris_tag1.append((n0, n1, n2))

        # Build adjacency graph: nodes share an edge → same component.
        from collections import defaultdict
        adj: dict = defaultdict(set)
        for n0, n1, n2 in tris_tag1:
            for a, b in [(n0, n1), (n1, n2), (n2, n0)]:
                adj[a].add(b)
                adj[b].add(a)

        nodes_all = set(adj.keys())
        if not nodes_all:
            self.fail("No tag-1 triangles found in mesh.")

        # BFS from any node.
        visited = set()
        queue = [next(iter(nodes_all))]
        while queue:
            node = queue.pop()
            if node in visited:
                continue
            visited.add(node)
            queue.extend(adj[node] - visited)

        self.assertEqual(
            visited, nodes_all,
            f"Tag-1 mesh has more than one connected component: "
            f"{len(nodes_all)} nodes total, {len(visited)} reachable.",
        )

    @unittest.skipUnless(
        GMSH_OCC_RUNTIME_READY,
        "Requires supported gmsh Python runtime for OCC meshing integration test.",
    )
    def test_freestanding_wall_source_edges_are_two_triangle_manifold(self):
        """Every source-disc edge must have exactly two adjacent triangles.

        This guards against the rear annular throat connector creating 3-way edge
        sharing around the source boundary.
        """
        from solver.waveguide_builder import build_waveguide_mesh

        params = dict(self._BASE_PARAMS)
        params["wall_thickness"] = 6.0
        params["enc_depth"] = 0.0

        result = build_waveguide_mesh(params, include_canonical=True)
        triangles = self._iter_msh_triangles(result["msh_text"])
        edge_uses = self._edge_counts_from_triangles(triangles)

        # Collect all undirected edges touched by source triangles (tag 2).
        source_edges = set()
        for phys_tag, (n0, n1, n2) in triangles:
            if phys_tag != 2:
                continue
            source_edges.add((n0, n1) if n0 < n1 else (n1, n0))
            source_edges.add((n1, n2) if n1 < n2 else (n2, n1))
            source_edges.add((n2, n0) if n2 < n0 else (n0, n2))

        self.assertGreater(len(source_edges), 0, "Expected source-disc edges in OCC mesh.")

        for edge in source_edges:
            uses = edge_uses.get(edge, [])
            self.assertEqual(
                len(uses),
                2,
                f"Source edge {edge} must be shared by exactly 2 triangles, got {len(uses)}.",
            )

    @unittest.skipUnless(
        GMSH_OCC_RUNTIME_READY,
        "Requires supported gmsh Python runtime for OCC meshing integration test.",
    )
    def test_freestanding_wall_canonical_mesh_has_no_non_manifold_edges(self):
        """Canonical OCC mesh must be manifold in freestanding wall mode."""
        from collections import defaultdict
        from solver.waveguide_builder import build_waveguide_mesh

        params = dict(self._BASE_PARAMS)
        params["wall_thickness"] = 6.0
        params["enc_depth"] = 0.0

        result = build_waveguide_mesh(params, include_canonical=True)
        indices = result["canonical_mesh"]["indices"]

        def edge_key(a, b):
            return (a, b) if a < b else (b, a)

        edge_counts = defaultdict(int)
        for i in range(0, len(indices), 3):
            n0, n1, n2 = int(indices[i]), int(indices[i + 1]), int(indices[i + 2])
            edge_counts[edge_key(n0, n1)] += 1
            edge_counts[edge_key(n1, n2)] += 1
            edge_counts[edge_key(n2, n0)] += 1

        non_manifold_edges = [edge for edge, count in edge_counts.items() if count > 2]
        self.assertEqual(
            len(non_manifold_edges),
            0,
            f"Canonical OCC mesh has non-manifold edges in freestanding wall mode: {non_manifold_edges[:8]}",
        )


if __name__ == "__main__":
    unittest.main()
