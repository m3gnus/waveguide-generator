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
    def _request(self, quadrants=1, advanced_settings=None, waveguide_params=None):
        params = {"formula_type": "OSSE", "quadrants": quadrants}
        if waveguide_params:
            params.update(waveguide_params)
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
            advanced_settings=advanced_settings,
            options={
                "mesh": {
                    "strategy": "hornlab_mesher",
                    "waveguide_params": params,
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
        self.assertEqual(seen_configs[0].native_check_open_edges, False)
        self.assertEqual(seen_configs[0].formulation, "complex_k")
        self.assertEqual(seen_configs[0].complex_k_shift, 0.005)
        self.assertEqual(result["metadata"]["metal"]["native_symmetry_plane"], "yz+xz")
        self.assertEqual(result["metadata"]["metal"]["native_check_open_edges"], False)
        self.assertEqual(result["metadata"]["metal"]["formulation"], "complex_k")
        self.assertEqual(result["metadata"]["metal"]["complex_k_shift"], 0.005)

    def test_standard_formulation_override_is_forwarded(self):
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
            metal_solver.solve_metal_from_msh(
                msh_file.name,
                self._request(
                    quadrants=1,
                    advanced_settings={
                        "bem_formulation": "standard",
                        "complex_k_shift": 0.0125,
                    },
                ),
            )

        self.assertEqual(seen_configs[0].formulation, "standard")
        self.assertEqual(seen_configs[0].complex_k_shift, 0.0125)

    def test_burton_miller_is_rejected_for_metal(self):
        with tempfile.NamedTemporaryFile(suffix=".msh") as msh_file, patch(
            "solver.metal_solver.native_config", side_effect=lambda **kwargs: FakeSolveConfig(**kwargs)
        ), patch(
            "solver.metal_solver.solve", return_value=self._fake_result()
        ), patch(
            "solver.metal_solver.metal_backend_status",
            return_value={"available": True, "supportedPlatform": True},
        ):
            with self.assertRaises(ValueError):
                metal_solver.solve_metal_from_msh(
                    msh_file.name,
                    self._request(advanced_settings={"bem_formulation": "burton_miller"}),
                )

    def test_half_domain_quadrants_map_to_expected_symmetry_planes(self):
        self.assertEqual(metal_solver._native_symmetry_plane(self._request(quadrants=14)), "yz")
        self.assertEqual(metal_solver._native_symmetry_plane(self._request(quadrants=12)), "xz")
        self.assertIsNone(metal_solver._native_symmetry_plane(self._request(quadrants=1234)))

    def test_open_edge_guard_policy_follows_mesher_topology(self):
        self.assertFalse(
            metal_solver._native_check_open_edges(
                self._request(quadrants=1, waveguide_params={"enc_depth": 0, "wall_thickness": 0})
            )
        )
        self.assertTrue(
            metal_solver._native_check_open_edges(
                self._request(quadrants=1, waveguide_params={"enc_depth": 0, "wall_thickness": 6})
            )
        )
        # An enclosure is a sealed box (the mesher closes the baffle front), so a
        # reduced enclosure mesh has no off-plane open edges. Keep the strict guard
        # so a closure defect surfaces loudly instead of solving a leaking model.
        self.assertTrue(
            metal_solver._native_check_open_edges(
                self._request(quadrants=1, waveguide_params={"enc_depth": 200, "wall_thickness": 0})
            )
        )
        # The guard is topology-driven (enclosure / wall), not sim_type. An
        # infinite-baffle (sim_type=1) request is coerced to free-standing before
        # the solve, so a no-enclosure, no-wall open shell relaxes the guard the
        # same way a free-standing one does.
        self.assertFalse(
            metal_solver._native_check_open_edges(
                self._request(
                    quadrants=1,
                    waveguide_params={"sim_type": 1, "enc_depth": 0, "wall_thickness": 0},
                )
            )
        )
        self.assertTrue(
            metal_solver._native_check_open_edges(
                self._request(quadrants=1234, waveguide_params={"enc_depth": 0, "wall_thickness": 0})
            )
        )

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
