import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app import GmshMeshRequest, generate_mesh_with_gmsh


class GmshEndpointTest(unittest.TestCase):
    def test_empty_geo_is_rejected(self):
        request = GmshMeshRequest(geoText='   ', mshVersion='2.2', binary=False)

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(generate_mesh_with_gmsh(request))

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn('geoText', str(ctx.exception.detail))

    def test_unavailable_mesher_returns_503(self):
        request = GmshMeshRequest(geoText='Point(1) = {0,0,0,1};\nMesh 2;\n', mshVersion='2.2', binary=False)

        with patch('api.routes_mesh.gmsh_mesher_available', return_value=False):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(generate_mesh_with_gmsh(request))

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn('requires a working Gmsh backend', str(ctx.exception.detail))

    def test_success_response_contains_gmsh_marker_and_stats(self):
        request = GmshMeshRequest(geoText='Point(1) = {0,0,0,1};\nMesh 2;\n', mshVersion='2.2', binary=False)
        mocked_msh = '\n'.join([
            '$MeshFormat',
            '2.2 0 8',
            '$EndMeshFormat',
            '$PhysicalNames',
            '4',
            '2 1 "SD1G0"',
            '2 2 "SD1D1001"',
            '2 3 "SD2G0"',
            '2 4 "I1-2"',
            '$EndPhysicalNames'
        ])

        with patch('api.routes_mesh.gmsh_mesher_available', return_value=True), patch(
            'api.routes_mesh.generate_msh_from_geo',
            return_value={
                'msh': mocked_msh,
                'stats': {'nodeCount': 42, 'elementCount': 64}
            }
        ):
            response = asyncio.run(generate_mesh_with_gmsh(request))

        self.assertEqual(response['generatedBy'], 'gmsh')
        self.assertEqual(response['stats']['nodeCount'], 42)
        self.assertIn('2 4 "I1-2"', response['msh'])


if __name__ == '__main__':
    unittest.main()
