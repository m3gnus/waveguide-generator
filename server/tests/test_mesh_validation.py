import unittest

from solver.mesh import prepare_mesh


class MeshValidationTest(unittest.TestCase):
    def test_indices_out_of_range_raise(self):
        with self.assertRaises(ValueError) as ctx:
            prepare_mesh(
                vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 5],
                surface_tags=[2],
            )
        self.assertIn("out of bounds", str(ctx.exception))

    def test_no_source_tags_raise(self):
        with self.assertRaises(ValueError) as ctx:
            prepare_mesh(
                vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 2],
                surface_tags=[1],
            )
        self.assertIn("no source-tagged elements", str(ctx.exception))

    def test_unit_scale_uses_metadata_override(self):
        mesh = prepare_mesh(
            vertices=[0.0, 0.0, 0.0, 1000.0, 0.0, 0.0, 0.0, 1000.0, 0.0],
            indices=[0, 1, 2],
            surface_tags=[2],
            mesh_metadata={"unitScaleToMeter": 0.001},
        )
        self.assertAlmostEqual(mesh["unit_scale_to_meter"], 0.001)
        self.assertEqual(mesh["unit_detection"]["source"], "metadata.unitScaleToMeter")

    def test_unit_scale_uses_heuristic_with_warning_for_ambiguous_extent(self):
        mesh = prepare_mesh(
            vertices=[0.0, 0.0, 0.0, 3.0, 0.0, 0.0, 0.0, 3.0, 0.0],
            indices=[0, 1, 2],
            surface_tags=[2],
            mesh_metadata={},
        )
        self.assertAlmostEqual(mesh["unit_scale_to_meter"], 0.001)
        self.assertEqual(mesh["unit_detection"]["source"], "heuristic:ambiguous_default_mm")
        self.assertGreater(len(mesh["unit_detection"]["warnings"]), 0)


if __name__ == "__main__":
    unittest.main()
