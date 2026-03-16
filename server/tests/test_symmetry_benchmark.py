import types
import unittest
from unittest.mock import patch

import numpy as np

from solver.solve_optimized import solve_optimized
from solver.symmetry_benchmark import (
    benchmark_symmetry_cases,
    evaluate_symmetry_benchmark_case,
    get_symmetry_benchmark_cases,
)


class DummyGrid:
    def __init__(self, vertices, indices, domain_indices):
        self.vertices = vertices
        self.elements = indices.T if indices.shape[0] != 3 else indices
        self.domain_indices = domain_indices


def _directivity_stub():
    return {"horizontal": [], "vertical": [], "diagonal": []}


def _device_metadata_stub():
    return {
        "requested_mode": "auto",
        "selected_mode": "opencl_cpu",
        "interface": "opencl",
        "device_type": "cpu",
        "device_name": "Fake CPU",
        "fallback_reason": None,
        "available_modes": ["auto", "opencl_cpu", "opencl_gpu"],
        "requested": "auto",
        "selected": "opencl",
        "runtime_selected": "opencl",
        "runtime_retry_attempted": False,
        "runtime_retry_outcome": "not_needed",
        "runtime_profile": "default",
    }


_SOLVE_FREQ_TARGET = "solver.solve_optimized.HornBEMSolver._solve_single_frequency"
_HORN_INIT_TARGET = "solver.solve_optimized.HornBEMSolver.__init__"


def _stub_horn_init(self, grid, physical_tags, **kwargs):
    """Minimal HornBEMSolver.__init__ stub for unit tests."""
    self.grid = grid
    self.physical_tags = physical_tags
    self.c = kwargs.get("sound_speed", 343.0)
    self.rho = kwargs.get("rho", 1.21)
    self.tag_throat = kwargs.get("tag_throat", 2)
    self.boundary_interface = kwargs.get("boundary_interface", "opencl")
    self.potential_interface = kwargs.get("potential_interface", "opencl")
    self.bem_precision = kwargs.get("bem_precision", "double")
    self.use_burton_miller = kwargs.get("use_burton_miller", True)
    self.p1_space = None
    self.dp0_space = None
    self.lhs_identity = None
    self.rhs_identity = None
    self.driver_dofs = np.array([0], dtype=np.int32)
    self.enclosure_dofs = np.array([], dtype=np.int32)
    self.throat_element_areas = np.array([0.5], dtype=float)
    self.throat_p1_dofs = np.array([[0, 1, 2]], dtype=np.int32)
    self.unit_velocity_fun = None
    self.symmetry_info = None
    self.symmetry_planes = None
    self.mirror_grids = []
    self.mirror_spaces = []


class SymmetryBenchmarkTest(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._init_patcher = patch(_HORN_INIT_TARGET, _stub_horn_init)
        self._init_patcher.start()

    def tearDown(self):
        self._init_patcher.stop()
        super().tearDown()

    def test_benchmark_cases_cover_full_half_quarter_and_rejection(self):
        payload = benchmark_symmetry_cases(iterations=2)

        self.assertTrue(payload["all_passed"])
        self.assertEqual(
            sorted(case["name"] for case in payload["cases"]),
            [
                "full_reference",
                "half_yz",
                "quarter_xz",
                "quarter_xz_off_center_source",
            ],
        )

        half_case = next(case for case in payload["cases"] if case["name"] == "half_yz")
        self.assertEqual(half_case["policy"]["detected_symmetry_type"], "half_x")
        self.assertEqual(float(half_case["policy"]["reduction_factor"]), 2.0)
        self.assertLess(half_case["mesh"]["reduced_triangles"], half_case["mesh"]["triangles"])

    def test_off_center_case_reports_rejection_reason_and_throat_center(self):
        case = get_symmetry_benchmark_cases()["quarter_xz_off_center_source"]
        summary = evaluate_symmetry_benchmark_case(case)

        self.assertFalse(summary["policy"]["applied"])
        self.assertEqual(summary["policy"]["reason"], "excitation_off_center")
        self.assertEqual(summary["policy"]["detected_symmetry_type"], "quarter_xz")
        self.assertIsNotNone(summary["policy"]["throat_center"])
        self.assertFalse(summary["policy"]["excitation_centered"])

    def test_solve_optimized_surfaces_symmetry_policy_metadata(self):
        case = get_symmetry_benchmark_cases()["quarter_xz_off_center_source"]
        grid = DummyGrid(case.vertices, case.indices, case.surface_tags)
        mesh = {
            "grid": grid,
            "throat_elements": case.throat_elements,
            "original_vertices": case.vertices.copy(),
            "original_indices": case.indices.copy(),
            "original_surface_tags": case.surface_tags.copy(),
            "mesh_metadata": {"fullCircle": True},
            "unit_detection": {"source": "benchmark", "warnings": []},
        }

        with patch("solver.solve_optimized.boundary_device_interface", return_value="opencl"), patch(
            "solver.solve_optimized.potential_device_interface", return_value="opencl"
        ), patch(
            "solver.solve_optimized.selected_device_metadata",
            return_value=_device_metadata_stub(),
        ), patch(
            _SOLVE_FREQ_TARGET,
            return_value=(90.0, complex(1.0, 0.0), 6.0, ("p", "u", "sp", "su"), 15),
        ), patch(
            "solver.solve_optimized.calculate_directivity_patterns_correct",
            return_value=_directivity_stub(),
        ):
            results = solve_optimized(
                mesh=mesh,
                frequency_range=[1000.0, 1000.0],
                num_frequencies=1,
                sim_type="2",
                enable_symmetry=True,
                verbose=False,
                mesh_validation_mode="off",
                enable_warmup=False,
            )

        policy = results["metadata"]["symmetry_policy"]
        self.assertEqual(policy["reason"], "excitation_off_center")
        self.assertFalse(policy["applied"])
        self.assertEqual(policy["detected_symmetry_type"], "quarter_xz")
        self.assertEqual(results["metadata"]["symmetry"]["symmetry_type"], "full")

    def test_solve_optimized_reports_applied_symmetry_policy(self):
        case = get_symmetry_benchmark_cases()["half_yz"]
        grid = DummyGrid(case.vertices, case.indices, case.surface_tags)
        mesh = {
            "grid": grid,
            "throat_elements": case.throat_elements,
            "original_vertices": case.vertices.copy(),
            "original_indices": case.indices.copy(),
            "original_surface_tags": case.surface_tags.copy(),
            "mesh_metadata": {"fullCircle": True},
            "unit_detection": {"source": "benchmark", "warnings": []},
        }

        fake_bempp = types.SimpleNamespace(
            grid_from_element_data=lambda vertices, indices, tags: DummyGrid(vertices, indices, tags)
        )

        with patch("solver.solve_optimized.bempp_api", fake_bempp), patch(
            "solver.solve_optimized.boundary_device_interface",
            return_value="opencl",
        ), patch(
            "solver.solve_optimized.potential_device_interface",
            return_value="opencl",
        ), patch(
            "solver.solve_optimized.selected_device_metadata",
            return_value=_device_metadata_stub(),
        ), patch(
            _SOLVE_FREQ_TARGET,
            return_value=(91.0, complex(1.0, 0.0), 6.0, ("p", "u", "sp", "su"), 12),
        ), patch(
            "solver.solve_optimized.calculate_directivity_patterns_correct",
            return_value=_directivity_stub(),
        ):
            results = solve_optimized(
                mesh=mesh,
                frequency_range=[1000.0, 1000.0],
                num_frequencies=1,
                sim_type="2",
                enable_symmetry=True,
                verbose=False,
                mesh_validation_mode="off",
                enable_warmup=False,
            )

        policy = results["metadata"]["symmetry_policy"]
        self.assertTrue(policy["applied"])
        self.assertEqual(policy["reason"], "applied")
        self.assertEqual(policy["detected_symmetry_type"], "half_x")
        self.assertEqual(float(policy["reduction_factor"]), 2.0)
        self.assertEqual(results["metadata"]["symmetry"]["symmetry_type"], "half_x")


if __name__ == "__main__":
    unittest.main()
