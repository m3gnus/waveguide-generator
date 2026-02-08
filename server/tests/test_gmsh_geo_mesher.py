import unittest

from solver.gmsh_geo_mesher import parse_msh_stats, generate_msh_from_geo


class GmshGeoMesherTest(unittest.TestCase):
    def test_parse_msh2_stats(self):
        msh = '\n'.join([
            '$MeshFormat',
            '2.2 0 8',
            '$EndMeshFormat',
            '$Nodes',
            '5',
            '$EndNodes',
            '$Elements',
            '7',
            '$EndElements'
        ])
        stats = parse_msh_stats(msh)
        self.assertEqual(stats['nodeCount'], 5)
        self.assertEqual(stats['elementCount'], 7)

    def test_invalid_version_is_rejected(self):
        with self.assertRaises(ValueError):
            generate_msh_from_geo('Point(1) = {0,0,0,1};\nMesh 2;\n', msh_version='9.9')


if __name__ == '__main__':
    unittest.main()
