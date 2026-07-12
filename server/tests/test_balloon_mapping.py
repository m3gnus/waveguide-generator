import time
import unittest
from types import SimpleNamespace

import numpy as np

from contracts import PolarConfig, SimulationRequest
from solver.result_mapping import build_solver_response, observation_config


class _FakeObservationConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)


class _LegacyObservationConfig:
    """Mimics an older metal-bem without sphere support."""

    def __init__(self, planes, distance_m, angle_min_deg, angle_max_deg, angle_count, origin):
        self.planes = planes
        self.distance_m = distance_m


class _Unavailable(RuntimeError):
    pass


def _request(spherical=False, sim_type="2"):
    return SimulationRequest(
        frequency_range=[1000.0, 2000.0],
        num_frequencies=2,
        sim_type=str(sim_type),
        polar_config=PolarConfig(
            angle_range=[0.0, 180.0, 3],
            enabled_axes=["horizontal"],
            distance=2.0,
            observation_origin="mouth",
            spherical_sampling=spherical,
            spherical_theta_count=5,
            spherical_phi_count=8,
        ),
        options={"mesh": {"waveguide_params": {"quadrants": 1234}}},
    )


def _fake_result(with_sphere=True, theta_max=180.0):
    n_theta, n_phi = 5, 8
    theta_axis = np.linspace(0.0, theta_max, n_theta)
    theta = np.repeat(theta_axis, n_phi)
    phi = np.tile(np.arange(n_phi) * (360.0 / n_phi), n_theta)
    # Pressure falls off with theta so the pole is the loudest point.
    amplitude = 1.0e-3 * 10.0 ** (-(theta / 90.0) ** 2)
    sphere_pressure = np.stack([amplitude, amplitude * 0.5], axis=0).astype(np.complex128)

    result = SimpleNamespace(
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
        directivity_db=np.zeros((2, 1, 3)),
        impedance=np.array([-1.0 + 2.0j, -3.0 + 4.0j], dtype=np.complex128),
        timings={"total_s": 0.1},
        solver_log=[],
        native_diagnostics=[],
    )
    if with_sphere:
        result.sphere_pressure_complex = sphere_pressure
        result.sphere_theta_deg = theta
        result.sphere_phi_deg = phi
        result.sphere_points = np.zeros((theta.size, 3))
    return result


def _config():
    return SimpleNamespace(
        observation=SimpleNamespace(distance_m=2.0, origin="mouth")
    )


class ObservationConfigSphereTest(unittest.TestCase):
    def test_disabled_spherical_passes_no_sphere_kwargs(self):
        cfg = observation_config(_request(), _FakeObservationConfig, _Unavailable, "pkg")
        self.assertNotIn("sphere_grid", cfg.kwargs)
        self.assertNotIn("sphere_theta_max_deg", cfg.kwargs)

    def test_enabled_spherical_passes_grid(self):
        cfg = observation_config(
            _request(spherical=True), _FakeObservationConfig, _Unavailable, "pkg"
        )
        self.assertEqual(cfg.kwargs["sphere_grid"], (5, 8))
        self.assertNotIn("sphere_theta_max_deg", cfg.kwargs)

    def test_infinite_baffle_limits_balloon_to_hemisphere(self):
        cfg = observation_config(
            _request(spherical=True, sim_type="1"),
            _FakeObservationConfig,
            _Unavailable,
            "pkg",
        )
        self.assertEqual(cfg.kwargs["sphere_theta_max_deg"], 90.0)

    def test_legacy_backend_without_sphere_support_degrades_to_arcs(self):
        cfg = observation_config(
            _request(spherical=True), _LegacyObservationConfig, _Unavailable, "pkg"
        )
        self.assertEqual(cfg.distance_m, 2.0)
        self.assertFalse(hasattr(cfg, "sphere_grid"))

    def test_legacy_backend_still_works_without_spherical(self):
        cfg = observation_config(_request(), _LegacyObservationConfig, _Unavailable, "pkg")
        self.assertEqual(cfg.distance_m, 2.0)


class BalloonResponseTest(unittest.TestCase):
    def _response(self, result):
        return build_solver_response(
            result=result,
            config=_config(),
            request=_request(spherical=True),
            start_time=time.time(),
            metadata={},
        )

    def test_balloon_block_present_and_normalized(self):
        response = self._response(_fake_result())
        self.assertIn("balloon", response)
        balloon = response["balloon"]
        self.assertEqual(balloon["theta_deg"], [0.0, 45.0, 90.0, 135.0, 180.0])
        self.assertEqual(len(balloon["phi_deg"]), 8)
        grid = np.asarray(balloon["spl_norm_db"])
        self.assertEqual(grid.shape, (2, 5, 8))
        # Pole normalized to 0 dB for every frequency and phi column.
        np.testing.assert_allclose(grid[:, 0, :], 0.0, atol=1e-9)
        # Both frequencies share the same fall-off shape (amplitude scale
        # cancels in the normalization).
        np.testing.assert_allclose(grid[0], grid[1], atol=1e-6)
        self.assertFalse(balloon["hemisphere"])
        self.assertEqual(balloon["distance_m"], 2.0)

        self.assertIn("beam_shape", response)
        beam = response["beam_shape"]
        self.assertEqual(len(beam["spherical_di_db"]), 2)
        self.assertIsNotNone(beam["spherical_di_db"][0])

    def test_hemisphere_flag_from_theta_extent(self):
        response = self._response(_fake_result(theta_max=90.0))
        self.assertTrue(response["balloon"]["hemisphere"])
        self.assertEqual(response["beam_shape"]["di_domain"], "hemisphere")

    def test_no_sphere_fields_no_balloon_block(self):
        response = self._response(_fake_result(with_sphere=False))
        self.assertNotIn("balloon", response)
        self.assertNotIn("beam_shape", response)


if __name__ == "__main__":
    unittest.main()


class ResponseSolverLogTest(unittest.TestCase):
    def test_strips_raw_sphere_pressure_only(self):
        from solver.result_mapping import response_solver_log

        entries = [
            {"frequency_hz": 100.0, "observation_sphere_pressure_complex": np.ones(4)},
            {"frequency_hz": 200.0},
            "not-a-dict",
        ]
        sanitized = response_solver_log(entries)
        self.assertEqual(len(sanitized), 3)
        self.assertNotIn("observation_sphere_pressure_complex", sanitized[0])
        self.assertEqual(sanitized[0]["frequency_hz"], 100.0)
        self.assertEqual(sanitized[1], {"frequency_hz": 200.0})
        self.assertEqual(sanitized[2], "not-a-dict")
        # Original entries are not mutated.
        self.assertIn("observation_sphere_pressure_complex", entries[0])

    def test_empty_and_none_logs(self):
        from solver.result_mapping import response_solver_log

        self.assertEqual(response_solver_log(None), [])
        self.assertEqual(response_solver_log([]), [])
