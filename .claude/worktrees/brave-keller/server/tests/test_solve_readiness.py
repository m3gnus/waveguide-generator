import json
import platform
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.solve_readiness import (
    READINESS_PROBE_ID,
    READINESS_SCHEMA_VERSION,
    read_bounded_solve_readiness,
)


class SolveReadinessTest(unittest.TestCase):
    def test_read_bounded_solve_readiness_reports_missing_record(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            record_path = Path(tmpdir) / "missing.json"
            with patch.dict("os.environ", {"WG_BOUNDED_SOLVE_RECORD_PATH": str(record_path)}):
                result = read_bounded_solve_readiness(preferred_mode="auto")

        self.assertEqual(result["status"], "missing")
        self.assertFalse(result["ready"])
        self.assertIn("No bounded solve validation record found", result["detail"])

    def test_read_bounded_solve_readiness_accepts_validated_record(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            record_path = Path(tmpdir) / "ready.json"
            payload = {
                "schemaVersion": READINESS_SCHEMA_VERSION,
                "probe": READINESS_PROBE_ID,
                "generatedAt": "2026-03-21T10:00:00+00:00",
                "host": {
                    "system": platform.system(),
                    "machine": platform.machine(),
                    "python_executable": sys.executable,
                },
                "requested_mode": "auto",
                "selected_mode": "opencl_cpu",
                "device_name": "Fake CPU",
                "attempted": True,
                "success": True,
                "runtime_available": True,
                "mesh_prep_success": True,
                "failure": None,
            }
            record_path.write_text(json.dumps(payload), encoding="utf-8")

            with patch.dict("os.environ", {"WG_BOUNDED_SOLVE_RECORD_PATH": str(record_path)}):
                result = read_bounded_solve_readiness(preferred_mode="auto")

        self.assertEqual(result["status"], "validated")
        self.assertTrue(result["ready"])
        self.assertEqual(result["selected_mode"], "opencl_cpu")

    def test_read_bounded_solve_readiness_reports_failed_record(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            record_path = Path(tmpdir) / "failed.json"
            payload = {
                "schemaVersion": READINESS_SCHEMA_VERSION,
                "probe": READINESS_PROBE_ID,
                "generatedAt": "2026-03-21T10:00:00+00:00",
                "host": {
                    "system": platform.system(),
                    "machine": platform.machine(),
                    "python_executable": sys.executable,
                },
                "requested_mode": "auto",
                "selected_mode": "opencl_cpu",
                "device_name": "Fake CPU",
                "attempted": True,
                "success": False,
                "runtime_available": True,
                "mesh_prep_success": True,
                "failure": "All 1 frequencies failed to solve. First failure(s): 2",
            }
            record_path.write_text(json.dumps(payload), encoding="utf-8")

            with patch.dict("os.environ", {"WG_BOUNDED_SOLVE_RECORD_PATH": str(record_path)}):
                result = read_bounded_solve_readiness(preferred_mode="auto")

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["ready"])
        self.assertIn("All 1 frequencies failed to solve", result["detail"])


if __name__ == "__main__":
    unittest.main()
