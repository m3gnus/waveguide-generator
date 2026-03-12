import types
import unittest
from unittest.mock import patch

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


class SymmetryBenchmarkTest(unittest.TestCase):
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
            "solver.solve_optimized.solve_frequency_cached",
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
            "solver.solve_optimized.solve_frequency_cached",
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
