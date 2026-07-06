import json
import math
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from contracts import MeshData, PolarConfig, SimulationRequest
from solver import bempp_solver


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


class BemppSolverAdapterTest(unittest.TestCase):
    def _request(self, quadrants=1234):
        return SimulationRequest(
            mesh=MeshData(
                vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                indices=[0, 1, 2],
                surfaceTags=[2],
                format="msh",
                boundaryConditions={},
                metadata={},
            ),
            frequency_range=[500.0, 2000.0],
            num_frequencies=3,
            sim_type="2",
            frequency_spacing="linear",
            polar_config=PolarConfig(
                angle_range=[-45.0, 45.0, 3],
                enabled_axes=["horizontal"],
                distance=2.5,
                observation_origin="throat",
            ),
            solver_backend="bempp",
            options={
                "mesh": {
                    "strategy": "hornlab_mesher",
                    "waveguide_params": {"formula_type": "OSSE", "quadrants": quadrants},
                }
            },
        )

    def _fake_result(self):
        return SimpleNamespace(
            frequencies_hz=np.array([500.0, 1000.0, 2000.0], dtype=float),
            observation_angles_deg=np.array([-45.0, 0.0, 45.0], dtype=float),
            observation_planes=["horizontal"],
            pressure_complex=np.array(
                [
                    [[1.0e-5 + 0j, 2.0e-5 + 0j, 1.0e-5 + 0j]],
                    [[1.0e-4 + 0j, 2.0e-4 + 0j, 1.0e-4 + 0j]],
                    [[1.0e-3 + 0j, 2.0e-3 + 0j, 1.0e-3 + 0j]],
                ],
                dtype=np.complex128,
            ),
            directivity_db=np.array(
                [
                    [[-6.0, 0.0, -6.0]],
                    [[-6.0, 0.0, -6.0]],
                    [[-6.0, 0.0, -6.0]],
                ],
                dtype=float,
            ),
            impedance=np.array([1.0 + 0.5j, 2.0 + 1.5j, 3.0 + 2.5j], dtype=np.complex128),
            timings={"total_s": 0.2},
            solver_log=[
                {"frequency_hz": np.float64(500.0), "impedance": 1.0 + 0.5j},
            ],
        )

    def test_package_absent_status_and_solve_error_are_clear(self):
        with patch("solver.bempp_solver._load_bempp_api", return_value=False), patch(
            "solver.bempp_solver.opencl_runtime_status",
            return_value={"available": False, "reason": "pyopencl is unavailable"},
        ):
            status = bempp_solver.bempp_backend_status()
            self.assertFalse(status["available"])
            self.assertFalse(status["packageInstalled"])
            self.assertEqual(status["assemblyBackend"], "numba")
            self.assertIn("requirements-bempp.txt", status["reason"])

            with tempfile.NamedTemporaryFile(suffix=".msh") as msh_file:
                with self.assertRaises(bempp_solver.BemppBemUnavailable) as ctx:
                    bempp_solver.solve_bempp_from_msh(msh_file.name, self._request())

        self.assertIn("requirements-bempp.txt", str(ctx.exception))

    def test_fake_package_maps_config_and_response_contract(self):
        seen_configs = []

        def fake_solve(_mesh_path, config):
            seen_configs.append(config)
            return self._fake_result()

        with tempfile.NamedTemporaryFile(suffix=".msh") as msh_file, patch(
            "solver.bempp_solver._load_bempp_api", return_value=True
        ), patch("solver.bempp_solver.ObservationConfig", FakeObservationConfig), patch(
            "solver.bempp_solver.SolveConfig", FakeSolveConfig
        ), patch(
            "solver.bempp_solver.BIEFormulation",
            SimpleNamespace(STANDARD="standard"),
        ), patch(
            "solver.bempp_solver.bempp_solve", side_effect=fake_solve
        ), patch(
            "solver.bempp_solver.bempp_backend_status",
            return_value={
                "available": True,
                "packageInstalled": True,
                "openclAvailable": False,
                "assemblyBackend": "numba",
                "reason": "using numba fallback",
            },
        ):
            result = bempp_solver.solve_bempp_from_msh(msh_file.name, self._request())

        self.assertEqual(set(result.keys()), {"frequencies", "directivity", "spl_on_axis", "impedance", "di", "metadata"})
        self.assertEqual(result["frequencies"], [500.0, 1000.0, 2000.0])
        self.assertEqual(result["impedance"]["real"], [1.0, 2.0, 3.0])
        self.assertEqual(result["impedance"]["imaginary"], [0.5, 1.5, 2.5])
        self.assertEqual(result["spl_on_axis"]["phase_degrees"], [0.0, 0.0, 0.0])
        self.assertIn("horizontal", result["di"]["di"])
        self.assertTrue(all(math.isfinite(value) for value in result["di"]["di"]["horizontal"]))
        json.dumps(result)

        config = seen_configs[0]
        self.assertEqual(config.freq_min_hz, 500.0)
        self.assertEqual(config.freq_max_hz, 2000.0)
        self.assertEqual(config.freq_count, 3)
        self.assertEqual(config.freq_spacing, "linear")
        self.assertEqual(config.mesh_scale, 1.0)
        self.assertEqual(config.formulation, "complex_k")
        self.assertIsNone(config.native_symmetry_plane)
        self.assertEqual(config.assembly_backend, "numba")
        self.assertEqual(config.opencl_device, "cpu")
        self.assertEqual(config.precision, "single")
        self.assertEqual(config.observation.planes, ["horizontal"])
        self.assertEqual(config.observation.distance_m, 2.5)
        self.assertEqual(config.observation.angle_min_deg, -45.0)
        self.assertEqual(config.observation.angle_max_deg, 45.0)
        self.assertEqual(config.observation.angle_count, 3)
        self.assertEqual(config.observation.origin, "throat")

        metadata = result["metadata"]
        self.assertEqual(metadata["solver_backend"], "bempp")
        self.assertEqual(metadata["engine"], "hornlab-bempp-bem")
        self.assertEqual(metadata["phase_time_convention"], "exp(+ikr)")
        self.assertEqual(metadata["assemblyBackend"], "numba")
        self.assertEqual(metadata["device_interface"]["selected"], "bempp-cl-numba")
        self.assertNotIn(metadata["device_interface"]["selected"], {"bempp", "bempp-cl", "bemppcl"})
        self.assertIsNone(metadata["bempp"]["native_symmetry_plane"])
        self.assertEqual(
            metadata["bempp"]["solver_log"][0]["impedance"],
            {"real": 1.0, "imaginary": 0.5},
        )

    def test_reduced_domain_request_is_rejected_before_bempp_config(self):
        with tempfile.NamedTemporaryFile(suffix=".msh") as msh_file:
            with self.assertRaises(ValueError) as ctx:
                bempp_solver.solve_bempp_from_msh(msh_file.name, self._request(quadrants=1))

        self.assertIn("full-domain mesh", str(ctx.exception))
        self.assertIn("Mesh.Quadrants=1", str(ctx.exception))

    def test_infinite_baffle_request_gets_ib_specific_error(self):
        # IB maps to native_symmetry_plane="xy" (the Metal image plane), not a
        # quadrant cut, so the error must name infinite baffle and NOT tell the
        # user to use full azimuth (they already are).
        from tests import test_infinite_baffle_image_integration as ib

        with self.assertRaises(ValueError) as ctx:
            bempp_solver.solve_bempp_from_msh(
                "unused.msh", ib._request(ib._payload(sim_type=1))
            )
        msg = str(ctx.exception)
        self.assertIn("infinite-baffle", msg.lower())
        self.assertNotIn("Mesh.Quadrants=1234", msg)


if __name__ == "__main__":
    unittest.main()
