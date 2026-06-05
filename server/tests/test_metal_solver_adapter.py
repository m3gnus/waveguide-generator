import json
import math
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from contracts import MeshData, PolarConfig, SimulationRequest
from solver import metal_solver


class FakeObservationConfig:
    def __init__(
        self,
        *,
        planes,
        distance_m,
        angle_min_deg,
        angle_max_deg,
        angle_count,
        origin,
    ):
        self.planes = planes
        self.distance_m = distance_m
        self.angle_min_deg = angle_min_deg
        self.angle_max_deg = angle_max_deg
        self.angle_count = angle_count
        self.origin = origin


class FakeSolveConfig:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class MetalSolverAdapterTest(unittest.TestCase):
    def _request(self, quadrants=1):
        return SimulationRequest(
            mesh=MeshData(
                vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 2],
                surfaceTags=[2],
                format="msh",
                boundaryConditions={},
                metadata={},
            ),
            frequency_range=[1000.0, 2000.0],
            num_frequencies=2,
            sim_type="2",
            frequency_spacing="linear",
            polar_config=PolarConfig(
                angle_range=[0.0, 180.0, 3],
                enabled_axes=["horizontal"],
                distance=2.0,
                observation_origin="mouth",
            ),
            solver_backend="metal",
            options={
                "mesh": {
                    "strategy": "hornlab_mesher",
                    "waveguide_params": {"formula_type": "OSSE", "quadrants": quadrants},
                }
            },
        )

    def _fake_result(self):
        return SimpleNamespace(
            frequencies_hz=np.array([1000.0, 2000.0], dtype=float),
            observation_angles_deg=np.array([0.0, 90.0, 180.0], dtype=float),
            observation_planes=["horizontal"],
            pressure_complex=np.array(
                [
                    [[2.0e-5 + 0j, 1.0e-5 + 0j, 5.0e-6 + 0j]],
                    [[2.0e-4 + 0j, 1.0e-4 + 0j, 5.0e-5 + 0j]],
                ],
                dtype=np.complex128,
            ),
            directivity_db=np.array(
                [
                    [[0.0, -6.0, -12.0]],
                    [[0.0, -6.0, -12.0]],
                ],
                dtype=float,
            ),
            impedance=np.array([1.0 + 2.0j, 3.0 + 4.0j], dtype=np.complex128),
            timings={"total_s": 0.1},
            solver_log=[
                {"frequency_hz": np.float64(1000.0), "impedance": 1.0 + 2.0j},
            ],
            native_diagnostics=[],
        )

    def test_reduced_quadrants_set_native_symmetry_plane(self):
        seen_configs = []

        def fake_native_config(**kwargs):
            cfg = FakeSolveConfig(**kwargs)
            seen_configs.append(cfg)
            return cfg

        with tempfile.NamedTemporaryFile(suffix=".msh") as msh_file, patch(
            "solver.metal_solver.ObservationConfig", FakeObservationConfig
        ), patch("solver.metal_solver.native_config", side_effect=fake_native_config), patch(
            "solver.metal_solver.solve", return_value=self._fake_result()
        ), patch(
            "solver.metal_solver.metal_backend_status",
            return_value={"available": True, "supportedPlatform": True},
        ):
            result = metal_solver.solve_metal_from_msh(msh_file.name, self._request(quadrants=1))

        self.assertEqual(seen_configs[0].native_symmetry_plane, "yz+xz")
        self.assertEqual(result["metadata"]["metal"]["native_symmetry_plane"], "yz+xz")

    def test_half_domain_quadrants_map_to_expected_symmetry_planes(self):
        self.assertEqual(metal_solver._native_symmetry_plane(self._request(quadrants=14)), "yz")
        self.assertEqual(metal_solver._native_symmetry_plane(self._request(quadrants=12)), "xz")
        self.assertIsNone(metal_solver._native_symmetry_plane(self._request(quadrants=1234)))

    def test_result_packaging_uses_actual_on_axis_spl_and_di(self):
        with tempfile.NamedTemporaryFile(suffix=".msh") as msh_file, patch(
            "solver.metal_solver.ObservationConfig", FakeObservationConfig
        ), patch("solver.metal_solver.native_config", side_effect=lambda **kwargs: FakeSolveConfig(**kwargs)), patch(
            "solver.metal_solver.solve", return_value=self._fake_result()
        ), patch(
            "solver.metal_solver.metal_backend_status",
            return_value={"available": True, "supportedPlatform": True},
        ):
            result = metal_solver.solve_metal_from_msh(msh_file.name, self._request(quadrants=1234))

        spl = result["spl_on_axis"]["spl"]
        self.assertAlmostEqual(spl[0], 0.0, places=6)
        self.assertAlmostEqual(spl[1], 20.0, places=6)
        self.assertNotEqual(spl, [0.0, 0.0])
        self.assertEqual(result["spl_on_axis"]["phase_degrees"], [0.0, 0.0])
        self.assertIn("horizontal", result["di"]["di"])
        self.assertEqual(len(result["di"]["di"]["horizontal"]), 2)
        self.assertTrue(all(math.isfinite(value) for value in result["di"]["di"]["horizontal"]))
        json.dumps(result)
        self.assertEqual(
            result["metadata"]["metal"]["solver_log"][0]["impedance"],
            {"real": 1.0, "imaginary": 2.0},
        )


if __name__ == "__main__":
    unittest.main()
