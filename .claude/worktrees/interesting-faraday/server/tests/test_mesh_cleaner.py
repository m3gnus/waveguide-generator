"""
Tests for solver/mesh_cleaner.py
"""
import unittest
import numpy as np
import meshio

from solver.mesh_cleaner import (
    clean_mesh,
    extract_physical_tags,
    mesh_stats,
    MeshStats,
)


def _make_simple_mesh(points, triangles, tags=None):
    """Helper: create a meshio.Mesh for testing."""
    cells = [("triangle", np.array(triangles, dtype=np.int64))]
    cell_data = {}
    if tags is not None:
        cell_data = {"gmsh:physical": [np.array(tags, dtype=np.int32)]}
    return meshio.Mesh(points=np.array(points, dtype=float), cells=cells, cell_data=cell_data)


class TestMeshStats(unittest.TestCase):
    def test_single_triangle_stats(self):
        pts = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        tris = [[0, 1, 2]]
        s = mesh_stats(np.array(pts), np.array(tris))
        self.assertEqual(s.vertices, 3)
        self.assertEqual(s.triangles, 1)
        self.assertEqual(s.boundary_edges, 3)  # All edges are open on a single triangle
        self.assertEqual(s.nonmanifold_edges, 0)
        self.assertEqual(s.duplicate_faces, 0)
        self.assertEqual(s.degenerate_faces, 0)
        self.assertEqual(s.components, 1)

    def test_degenerate_face_detected(self):
        pts = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        # Collapsed triangle: two identical vertex indices
        tris = [[0, 0, 1]]
        s = mesh_stats(np.array(pts), np.array(tris))
        self.assertEqual(s.degenerate_faces, 1)

    def test_duplicate_faces_detected(self):
        pts = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        tris = [[0, 1, 2], [0, 1, 2]]  # same face twice
        s = mesh_stats(np.array(pts), np.array(tris))
        self.assertEqual(s.duplicate_faces, 1)


class TestCleanMesh(unittest.TestCase):
    def test_no_changes_needed_on_clean_mesh(self):
        pts = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]]
        tris = [[0, 1, 2], [1, 3, 2]]
        mesh = _make_simple_mesh(pts, tris)
        cleaned, changes, before, after = clean_mesh(mesh, merge_tol=1e-9, area_tol=0.0)
        self.assertEqual(changes["merged_vertices"], 0)
        self.assertEqual(changes["removed_degenerate_faces"], 0)
        self.assertEqual(changes["removed_duplicate_faces"], 0)
        self.assertEqual(after.triangles, 2)

    def test_duplicate_face_removed(self):
        pts = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        tris = [[0, 1, 2], [0, 1, 2]]
        mesh = _make_simple_mesh(pts, tris)
        cleaned, changes, before, after = clean_mesh(mesh, merge_tol=1e-9)
        self.assertEqual(changes["removed_duplicate_faces"], 1)
        self.assertEqual(after.triangles, 1)

    def test_degenerate_face_removed(self):
        pts = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        # First triangle is valid, second is collapsed
        tris = [[0, 1, 2], [0, 0, 1]]
        mesh = _make_simple_mesh(pts, tris)
        cleaned, changes, before, after = clean_mesh(mesh, merge_tol=1e-9)
        self.assertEqual(changes["removed_degenerate_faces"], 1)
        self.assertEqual(after.triangles, 1)

    def test_near_coincident_vertices_merged(self):
        eps = 1e-12
        pts = [
            [0, 0, 0], [1, 0, 0], [0, 1, 0],
            [eps, eps, eps],  # nearly same as vertex 0
        ]
        # Two triangles sharing (almost) vertex 0/3
        tris = [[0, 1, 2], [3, 2, 1]]
        mesh = _make_simple_mesh(pts, tris)
        cleaned, changes, before, after = clean_mesh(mesh, merge_tol=1e-9)
        # Vertex 3 should merge into vertex 0
        self.assertGreater(changes["merged_vertices"], 0)

    def test_physical_tags_preserved_through_cleaning(self):
        pts = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]]
        tris = [[0, 1, 2], [1, 3, 2]]
        tags = [1, 2]
        mesh = _make_simple_mesh(pts, tris, tags)
        cleaned, _, _, _ = clean_mesh(mesh, merge_tol=1e-9)
        extracted = extract_physical_tags(cleaned)
        self.assertIsNotNone(extracted)
        self.assertEqual(len(extracted), 2)
        self.assertIn(1, extracted)
        self.assertIn(2, extracted)

    def test_extract_physical_tags_returns_none_when_absent(self):
        pts = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        tris = [[0, 1, 2]]
        mesh = _make_simple_mesh(pts, tris, tags=None)
        result = extract_physical_tags(mesh)
        self.assertIsNone(result)


class TestLoadMshForBemValidation(unittest.TestCase):
    """Tests for load_msh_for_bem physical tag validation.

    Note: meshio's .msh write/read cycle doesn't preserve gmsh:physical data
    in a round-trip manner. These tests use mocking to verify the validation
    logic in load_msh_for_bem() works correctly when physical tags ARE present.
    """

    def test_load_msh_for_bem_raises_on_missing_physical_tags(self):
        """load_msh_for_bem must raise ValueError when .msh lacks physical groups."""
        import tempfile
        import os
        from solver.mesh import load_msh_for_bem

        # Create a simple mesh without physical groups (no cell_data)
        pts = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]]
        tris = [[0, 1, 2], [1, 3, 2]]
        mesh = _make_simple_mesh(pts, tris, tags=None)

        with tempfile.TemporaryDirectory() as tmpdir:
            msh_path = os.path.join(tmpdir, "test_no_tags.msh")
            meshio.write(msh_path, mesh)

            with self.assertRaises(ValueError) as ctx:
                load_msh_for_bem(msh_path)

            self.assertIn("no physical groups", str(ctx.exception).lower())
            self.assertIn("gmsh:physical", str(ctx.exception))

    def test_load_msh_for_bem_raises_on_missing_source_tag(self):
        """load_msh_for_bem must raise ValueError when no source (tag 2) elements exist."""
        import tempfile
        import os
        from unittest.mock import patch
        import numpy as np
        from solver.mesh import load_msh_for_bem

        # Create a mesh that would have only wall tags (tag 1)
        pts = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]]
        tris = [[0, 1, 2], [1, 3, 2]]
        mesh = _make_simple_mesh(pts, tris, tags=None)

        with tempfile.TemporaryDirectory() as tmpdir:
            msh_path = os.path.join(tmpdir, "test_no_source.msh")
            meshio.write(msh_path, mesh)

            # Mock extract_physical_tags to return tags without source
            with patch(
                "solver.mesh.extract_physical_tags",
                return_value=np.array([1, 1], dtype=np.int32),  # All walls, no source
            ):
                with self.assertRaises(ValueError) as ctx:
                    load_msh_for_bem(msh_path)

                self.assertIn("no source-tagged elements", str(ctx.exception).lower())
                self.assertIn("tag 2", str(ctx.exception))

    def test_load_msh_for_bem_succeeds_with_valid_tags(self):
        """load_msh_for_bem must succeed when physical tags include source (tag 2)."""
        import tempfile
        import os
        from unittest.mock import patch, MagicMock
        import numpy as np
        from solver.mesh import load_msh_for_bem

        # Create a mesh that would have source (tag 2) and wall (tag 1)
        pts = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]]
        tris = [[0, 1, 2], [1, 3, 2]]
        mesh = _make_simple_mesh(pts, tris, tags=None)

        with tempfile.TemporaryDirectory() as tmpdir:
            msh_path = os.path.join(tmpdir, "test_valid.msh")
            meshio.write(msh_path, mesh)

            # Mock extract_physical_tags to return valid tags including source
            # Also mock bempp_api.Grid since bempp is not installed in test environment
            mock_grid = MagicMock()
            mock_grid.vertices = np.array([[0.0, 1.0, 0.0, 1.0], [0.0, 0.0, 1.0, 1.0], [0.0, 0.0, 0.0, 0.0]])
            mock_grid.elements = np.array([[0, 1], [1, 3], [2, 2]], dtype=np.int32)
            mock_grid.domain_indices = np.array([2, 1], dtype=np.int32)

            with patch(
                "solver.mesh.extract_physical_tags",
                return_value=np.array([2, 1], dtype=np.int32),  # Source and wall
            ), patch("solver.mesh.bempp_api") as mock_bempp:
                mock_bempp.Grid.return_value = mock_grid
                result = load_msh_for_bem(msh_path)
                self.assertIn("grid", result)
                self.assertIn("surface_tags", result)
                self.assertIn(2, result["surface_tags"])


if __name__ == "__main__":
    unittest.main()
