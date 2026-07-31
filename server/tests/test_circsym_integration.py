from __future__ import annotations

import math
import unittest

from contracts import MeshData, PolarConfig, SimulationRequest
from solver.metal_solver import metal_backend_status, solve_circsym_from_params


def _metal_runtime_ready() -> bool:
    try:
        status = metal_backend_status()
    except Exception:
        return False
    if not status.get("available"):
        return False
    try:
        from hornlab_metal_bem.sweep import _discover_runtime_smoke_cached
    except Exception:
        return False
    try:
        runtime = _discover_runtime_smoke_cached()
    except Exception:
        return False
    return bool(getattr(runtime, "available", False))


def _payload(*, sim_type: int) -> dict:
    return {
        "formula_type": "OSSE",
        "L": 40.0,
        "r0": 8.0,
        "a": 25.0,
        "a0": 8.0,
        "k": 1.0,
        "n": 4.0,
        "q": 0.99,
        "s": 0.0,
        "n_angular": 24,
        "n_length": 8,
        "quadrants": 1234,
        "throat_res": 8.0,
        "mouth_res": 20.0,
        "rear_res": 24.0,
        "wall_thickness": 0.0 if sim_type == 1 else 4.0,
        "enc_depth": 0.0,
        "source_shape": 2,
        "source_velocity": 1,
        "sim_type": sim_type,
    }


def _request(payload: dict) -> SimulationRequest:
    return SimulationRequest(
        mesh=MeshData(
            vertices=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
            indices=[0, 1, 2],
            surfaceTags=[2],
            format="msh",
            boundaryConditions={},
            metadata={},
        ),
        frequency_range=[500.0, 500.0],
        num_frequencies=1,
        frequency_spacing="linear",
        sim_type=str(payload["sim_type"]),
        solver_mode="circsym",
        solver_backend="metal",
        polar_config=PolarConfig(
            angle_range=[0.0, 180.0, 5],
            enabled_axes=["horizontal"],
            distance=2.0,
            observation_origin="mouth",
        ),
        options={"mesh": {"strategy": "hornlab_mesher", "waveguide_params": dict(payload)}},
    )


# This module used pytest marks, but the project's runner is `unittest
# discover` (npm run test:server) and pytest is not a declared dependency, so
# the module failed to import on every machine without pytest. That surfaced as
# a collection ERROR rather than the intended skip. Expressed with unittest it
# runs under the harness that actually exists, and skips cleanly off Metal.
class CircsymIntegrationTest(unittest.TestCase):
    @unittest.skipUnless(_metal_runtime_ready(), "Metal runtime not available")
    def test_solve_circsym_from_params_unmocked_tiny_round_waveguide(self):
        for sim_type in (1, 2):
            with self.subTest(sim_type=sim_type):
                self._check_sim_type(sim_type)

    def _check_sim_type(self, sim_type: int) -> None:
        payload = _payload(sim_type=sim_type)
        request = _request(payload)

        result = solve_circsym_from_params(payload, request)

        self.assertEqual(result["frequencies"], [500.0])
        self.assertEqual(result["metadata"]["solver_mode"], "circsym")
        self.assertEqual(result["metadata"]["metal"]["solver_mode"], "circsym")
        meridian_metadata = result["metadata"]["metal"]["meridian"]
        self.assertNotIn("freqMaxHz", meridian_metadata)
        self.assertGreater(meridian_metadata["throatTargetSegmentM"], 0.0)
        self.assertEqual(len(result["spl_on_axis"]["spl"]), 1)
        spl_on_axis = result["spl_on_axis"]["spl"][0]
        self.assertIsNotNone(spl_on_axis)
        self.assertTrue(math.isfinite(float(spl_on_axis)))
        self.assertTrue(math.isfinite(float(result["impedance"]["real"][0])))
        self.assertIn("horizontal", result["directivity"])
        self.assertEqual(len(result["directivity"]["horizontal"][0]), 5)

        if sim_type == 1:
            native_diagnostics = result["metadata"]["metal"]["native_diagnostics"]
            diagnostic_entries = [
                entry for entry in native_diagnostics if isinstance(entry, dict)
            ]
            self.assertTrue(
                any(entry.get("coupled_ib") is True for entry in diagnostic_entries)
            )
            self.assertTrue(
                any(
                    int(entry.get("aperture_tag")) == 12
                    for entry in diagnostic_entries
                    if entry.get("aperture_tag") is not None
                )
            )
