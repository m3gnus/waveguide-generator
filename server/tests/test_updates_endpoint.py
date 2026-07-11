import asyncio
import subprocess
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routes_misc import check_updates
from services.update_service import get_update_status


class UpdatesEndpointTest(unittest.TestCase):
    @patch("services.update_service._run_git")
    def test_get_update_status_reports_behind_remote(self, mock_run_git):
        mock_run_git.side_effect = [
            "git@github.com:m3gnus/waveguide-generator.git",
            "",
            "1111111111111111111111111111111111111111",
            "feature/check-updates",
            "refs/remotes/origin/main",
            "2222222222222222222222222222222222222222",
            "0 3"
        ]

        status = get_update_status()
        self.assertTrue(status["updateAvailable"])
        self.assertEqual(status["behindCount"], 3)
        self.assertEqual(status["aheadCount"], 0)
        self.assertEqual(status["defaultBranch"], "main")

    @patch("services.update_service._run_git")
    def test_get_update_status_uses_main_when_origin_head_missing(self, mock_run_git):
        def run_git_side_effect(_repo_root, *args):
            command = tuple(args)
            if command == ("remote", "get-url", "origin"):
                return "git@github.com:m3gnus/waveguide-generator.git"
            if command == ("fetch", "origin", "--quiet"):
                return ""
            if command == ("rev-parse", "HEAD"):
                return "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            if command == ("rev-parse", "--abbrev-ref", "HEAD"):
                return "main"
            if command == ("symbolic-ref", "refs/remotes/origin/HEAD"):
                raise RuntimeError("missing symbolic-ref")
            if command == ("rev-parse", "refs/remotes/origin/main"):
                return "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            if command == ("rev-list", "--left-right", "--count", "HEAD...refs/remotes/origin/main"):
                return "0 0"
            raise AssertionError(f"Unexpected git command: {command}")

        mock_run_git.side_effect = run_git_side_effect

        status = get_update_status()
        self.assertFalse(status["updateAvailable"])
        self.assertEqual(status["defaultBranch"], "main")
        self.assertEqual(status["behindCount"], 0)
        self.assertEqual(status["aheadCount"], 0)

    def test_check_updates_maps_runtime_error_to_http_503(self):
        with patch("api.routes_misc.get_update_status", side_effect=RuntimeError("network unavailable")):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(check_updates())

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("network unavailable", str(ctx.exception.detail))

    def test_check_updates_runs_git_work_in_a_worker_thread(self):
        to_thread = AsyncMock(return_value={"updateAvailable": False})
        with patch("api.routes_misc.asyncio.to_thread", to_thread):
            result = asyncio.run(check_updates())

        self.assertEqual(result, {"updateAvailable": False})
        to_thread.assert_awaited_once_with(get_update_status)

    @patch("services.update_service._run_git")
    @patch("services.update_service.subprocess.run")
    def test_git_version_probe_has_a_timeout(self, mock_run, mock_run_git):
        mock_run.return_value = None
        mock_run_git.side_effect = [
            "git@github.com:m3gnus/waveguide-generator.git",
            "",
            "1111111111111111111111111111111111111111",
            "main",
            "refs/remotes/origin/main",
            "1111111111111111111111111111111111111111",
            "0 0",
        ]

        get_update_status()

        mock_run.assert_called_once_with(
            ["git", "--version"], check=True, capture_output=True, timeout=10
        )

    @patch("services.update_service.subprocess.run")
    def test_git_version_probe_timeout_maps_to_runtime_error(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(["git", "--version"], 10)

        with self.assertRaisesRegex(RuntimeError, "Git version check timed out"):
            get_update_status()


if __name__ == "__main__":
    unittest.main()
