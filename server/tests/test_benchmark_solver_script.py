import argparse
import importlib.util
import unittest
from pathlib import Path


def _load_benchmark_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_solver.py"
    spec = importlib.util.spec_from_file_location("benchmark_solver_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load benchmark script module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


benchmark_solver = _load_benchmark_module()


class BenchmarkSolverScriptTest(unittest.TestCase):
    def test_parse_precision_modes_dedupes_and_collects_invalid(self):
        modes, invalid = benchmark_solver.parse_precision_modes(
            "single, double, SINGLE, fp16, ,triple"
        )
        self.assertEqual(modes, ["single", "double"])
        self.assertEqual(invalid, ["fp16", "triple"])

    def test_parse_precision_modes_defaults_to_single_when_empty(self):
        modes, invalid = benchmark_solver.parse_precision_modes(" , ")
        self.assertEqual(modes, ["single"])
        self.assertEqual(invalid, [])

    def test_resolve_frequency_plan_uses_reference_horn_defaults(self):
        args = argparse.Namespace(
            preset="reference-horn",
            freq_min=None,
            freq_max=None,
            num_freq=None,
            spacing=None,
        )
        plan = benchmark_solver.resolve_frequency_plan(args)
        self.assertEqual(plan["freq_min"], 1000.0)
        self.assertEqual(plan["freq_max"], 1000.0)
        self.assertEqual(plan["num_freq"], 1)
        self.assertEqual(plan["spacing"], "linear")

    def test_resolve_frequency_plan_applies_explicit_overrides(self):
        args = argparse.Namespace(
            preset="reference-horn",
            freq_min=1200.0,
            freq_max=1800.0,
            num_freq=4,
            spacing="log",
        )
        plan = benchmark_solver.resolve_frequency_plan(args)
        self.assertEqual(plan["freq_min"], 1200.0)
        self.assertEqual(plan["freq_max"], 1800.0)
        self.assertEqual(plan["num_freq"], 4)
        self.assertEqual(plan["spacing"], "log")

    def test_classify_precision_outcome_marks_runtime_error_unsupported(self):
        status = benchmark_solver.classify_precision_outcome("opencl failure", None)
        self.assertEqual(status, "unsupported")

    def test_classify_precision_outcome_marks_all_failures_unsupported(self):
        status = benchmark_solver.classify_precision_outcome(
            None,
            {
                "frequencies": [1000.0],
                "metadata": {"failure_count": 1},
            },
        )
        self.assertEqual(status, "unsupported")

    def test_classify_precision_outcome_marks_partial_or_zero_failures_supported(self):
        partial = benchmark_solver.classify_precision_outcome(
            None,
            {
                "frequencies": [500.0, 1000.0],
                "metadata": {"failure_count": 1},
            },
        )
        clean = benchmark_solver.classify_precision_outcome(
            None,
            {
                "frequencies": [1000.0],
                "metadata": {"failure_count": 0},
            },
        )
        self.assertEqual(partial, "supported")
        self.assertEqual(clean, "supported")

    def test_validate_args_requires_mesh_or_preset(self):
        args = argparse.Namespace(
            preset=None,
            mesh=None,
            precision_modes=None,
        )
        with self.assertRaises(ValueError):
            benchmark_solver.validate_args(args)

    def test_validate_args_rejects_preset_and_mesh_together(self):
        args = argparse.Namespace(
            preset="reference-horn",
            mesh="mesh.msh",
            precision_modes=None,
        )
        with self.assertRaises(ValueError):
            benchmark_solver.validate_args(args)

    def test_validate_args_applies_default_precision_matrix_for_reference_horn(self):
        args = argparse.Namespace(
            preset="reference-horn",
            mesh=None,
            precision_modes=None,
        )
        benchmark_solver.validate_args(args)
        self.assertEqual(args.precision_modes, "single,double")


if __name__ == "__main__":
    unittest.main()
