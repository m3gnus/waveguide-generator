"""
Tests for the Tritonia-M benchmark/repro script.

These tests verify the mesh preparation, reporting, and CLI logic without
requiring an actual BEM solve (unless RUN_BEM_REFERENCE=1 is set).
"""

import json
import os
import sys
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

scripts_dir = str(Path(__file__).parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import benchmark_tritonia as bt


class TritoniaParamsTest(unittest.TestCase):
    def test_tritonia_params_has_required_fields(self):
        required_fields = [
            "formula_type",
            "L",
            "a",
            "r0",
            "a0",
            "k",
            "q",
            "n",
            "s",
            "quadrants",
            "enc_depth",
            "enc_edge",
            "n_angular",
            "n_length",
            "throat_res",
            "mouth_res",
        ]
        for field in required_fields:
            self.assertIn(field, bt.TRITONIA_PARAMS, f"Missing required field: {field}")

    def test_tritonia_params_formula_type_is_osse(self):
        self.assertEqual(bt.TRITONIA_PARAMS["formula_type"], "OSSE")

    def test_tritonia_params_has_enclosure(self):
        self.assertGreater(bt.TRITONIA_PARAMS["enc_depth"], 0)
        self.assertGreater(bt.TRITONIA_PARAMS["enc_edge"], 0)

    def test_tritonia_params_full_domain(self):
        self.assertEqual(bt.TRITONIA_PARAMS["quadrants"], 1234)


class MeshPrepResultTest(unittest.TestCase):
    def test_default_values(self):
        result = bt.MeshPrepResult(success=True)
        self.assertTrue(result.success)
        self.assertIsNone(result.error)
        self.assertEqual(result.vertex_count, 0)
        self.assertEqual(result.triangle_count, 0)
        self.assertEqual(result.tag_counts, {})
        self.assertEqual(result.elapsed_seconds, 0.0)

    def test_error_result(self):
        result = bt.MeshPrepResult(success=False, error="Test error")
        self.assertFalse(result.success)
        self.assertEqual(result.error, "Test error")

    def test_successful_result(self):
        result = bt.MeshPrepResult(
            success=True,
            vertex_count=1000,
            triangle_count=2000,
            tag_counts={1: 1900, 2: 100},
            elapsed_seconds=1.5,
        )
        self.assertEqual(result.vertex_count, 1000)
        self.assertEqual(result.triangle_count, 2000)
        self.assertEqual(result.tag_counts[1], 1900)
        self.assertEqual(result.tag_counts[2], 100)


class PrecisionTestResultTest(unittest.TestCase):
    def test_default_values(self):
        result = bt.PrecisionTestResult(precision="single")
        self.assertEqual(result.precision, "single")
        self.assertFalse(result.attempted)
        self.assertFalse(result.success)
        self.assertIsNone(result.error)
        self.assertEqual(result.elapsed_seconds, 0.0)
        self.assertIsNone(result.gmres_iterations)
        self.assertIsNone(result.spl_value)

    def test_success_result(self):
        result = bt.PrecisionTestResult(
            precision="single",
            attempted=True,
            success=True,
            elapsed_seconds=5.2,
            gmres_iterations=42,
            spl_value=94.5,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.gmres_iterations, 42)
        self.assertEqual(result.spl_value, 94.5)


class BenchmarkResultTest(unittest.TestCase):
    def test_to_dict(self):
        mesh_prep = bt.MeshPrepResult(success=True, vertex_count=100, triangle_count=200)
        precision_results = [
            bt.PrecisionTestResult(precision="single", attempted=True, success=True)
        ]
        result = bt.BenchmarkResult(
            runtime_available=True,
            mesh_prep=mesh_prep,
            device_metadata={"selected_mode": "opencl_cpu"},
            precision_results=precision_results,
            host_info={"python_version": "3.11.0"},
            unsupported_precision_modes=[],
            total_elapsed_seconds=10.0,
        )
        d = result.to_dict()
        self.assertTrue(d["runtime_available"])
        self.assertEqual(d["mesh_prep"]["vertex_count"], 100)
        self.assertEqual(d["precision_results"][0]["precision"], "single")
        self.assertEqual(d["device_metadata"]["selected_mode"], "opencl_cpu")


class GetHostInfoTest(unittest.TestCase):
    def test_returns_expected_keys(self):
        info = bt.get_host_info()
        self.assertIn("python_version", info)
        self.assertIn("python_supported", info)
        self.assertIn("platform", info)
        self.assertIn("machine", info)
        self.assertIn("gmsh_version", info)
        self.assertIn("gmsh_ready", info)
        self.assertIn("bempp_version", info)
        self.assertIn("bempp_ready", info)


class BuildTritoniaMeshTest(unittest.TestCase):
    @unittest.skipUnless(
        os.getenv("RUN_BEM_REFERENCE") == "1",
        "Set RUN_BEM_REFERENCE=1 to run mesh build tests.",
    )
    def test_build_mesh_succeeds_with_runtime(self):
        result = bt.build_tritonia_mesh()
        self.assertTrue(result.success, f"Mesh build failed: {result.error}")
        self.assertGreater(result.vertex_count, 0)
        self.assertGreater(result.triangle_count, 0)
        self.assertIn(1, result.tag_counts)
        self.assertIn(2, result.tag_counts)
        self.assertGreater(result.tag_counts[2], 0, "No source-tagged triangles")

    def test_build_mesh_reports_unavailable_runtime(self):
        with patch("benchmark_tritonia.GMSH_OCC_RUNTIME_READY", False):
            result = bt.build_tritonia_mesh()
            self.assertFalse(result.success)
            self.assertIn("unavailable", result.error.lower())


class RunBenchmarkTest(unittest.TestCase):
    def test_benchmark_returns_correct_structure(self):
        args = MagicMock()
        args.device = "auto"
        args.freq = 1000.0
        args.sweep = False
        args.precision = "single"
        args.no_solve = True
        args.timeout = 120.0

        with patch("benchmark_tritonia.GMSH_OCC_RUNTIME_READY", True), patch(
            "benchmark_tritonia.BEMPP_RUNTIME_READY", False
        ), patch(
            "benchmark_tritonia.build_tritonia_mesh",
            return_value=bt.MeshPrepResult(
                success=True, vertex_count=100, triangle_count=200, tag_counts={1: 190, 2: 10}
            ),
        ):
            result = bt.run_benchmark(args)

        self.assertIsInstance(result, bt.BenchmarkResult)
        self.assertFalse(result.runtime_available)
        self.assertTrue(result.mesh_prep.success)

    def test_benchmark_no_solve_mode(self):
        args = MagicMock()
        args.device = "auto"
        args.freq = 1000.0
        args.sweep = False
        args.precision = "single"
        args.no_solve = True
        args.timeout = 120.0

        with patch("benchmark_tritonia.GMSH_OCC_RUNTIME_READY", True), patch(
            "benchmark_tritonia.BEMPP_RUNTIME_READY", True
        ), patch(
            "benchmark_tritonia.build_tritonia_mesh",
            return_value=bt.MeshPrepResult(success=True, vertex_count=100, triangle_count=200),
        ):
            result = bt.run_benchmark(args)

        self.assertEqual(len(result.precision_results), 0)

    def test_benchmark_reports_unsupported_precision(self):
        args = MagicMock()
        args.device = "auto"
        args.freq = 1000.0
        args.sweep = False
        args.precision = "both"
        args.no_solve = False
        args.timeout = 120.0

        with patch("benchmark_tritonia.GMSH_OCC_RUNTIME_READY", True), patch(
            "benchmark_tritonia.BEMPP_RUNTIME_READY", True
        ), patch(
            "benchmark_tritonia.build_tritonia_mesh",
            return_value=bt.MeshPrepResult(success=True, vertex_count=100, triangle_count=200),
        ), patch(
            "benchmark_tritonia.prepare_solver_mesh",
            return_value={"grid": MagicMock(), "throat_elements": [0]},
        ), patch(
            "benchmark_tritonia.test_single_solve",
            side_effect=[
                bt.PrecisionTestResult(precision="single", attempted=True, success=True),
                bt.PrecisionTestResult(
                    precision="double", attempted=True, success=False, error="OpenCL error"
                ),
            ],
        ):
            result = bt.run_benchmark(args)

        self.assertIn("double", result.unsupported_precision_modes)
        self.assertNotIn("single", result.unsupported_precision_modes)


class CLITest(unittest.TestCase):
    def test_cli_json_output(self):
        with patch(
            "sys.argv",
            ["benchmark_tritonia.py", "--json", "--no-solve"],
        ), patch(
            "benchmark_tritonia.run_benchmark"
        ) as mock_run, patch(
            "sys.stdout", new_callable=StringIO
        ) as mock_stdout:
            mock_run.return_value = bt.BenchmarkResult(
                runtime_available=False,
                mesh_prep=bt.MeshPrepResult(success=True),
                device_metadata={},
                precision_results=[],
                host_info={"python_version": "3.11.0"},
                unsupported_precision_modes=[],
                total_elapsed_seconds=0.5,
            )

            try:
                bt.main()
            except SystemExit:
                pass

            output = mock_stdout.getvalue()
            parsed = json.loads(output)
            self.assertIn("runtime_available", parsed)
            self.assertIn("mesh_prep", parsed)


if __name__ == "__main__":
    unittest.main()
