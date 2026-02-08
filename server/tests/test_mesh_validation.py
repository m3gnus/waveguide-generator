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


if __name__ == "__main__":
    unittest.main()
