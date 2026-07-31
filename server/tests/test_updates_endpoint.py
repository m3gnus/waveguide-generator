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
            "1111111111111111111111111111111111111111",
            "feature/check-updates",
            "origin/feature/check-updates",
            "origin",
            "git@github.com:m3gnus/waveguide-generator.git",
            "",
            "2222222222222222222222222222222222222222",
            "0 3"
        ]

        status = get_update_status()
        self.assertTrue(status["updateAvailable"])
        self.assertEqual(status["behindCount"], 3)
        self.assertEqual(status["aheadCount"], 0)
        self.assertEqual(status["currentBranch"], "feature/check-updates")
        self.assertEqual(status["upstreamRef"], "origin/feature/check-updates")
        self.assertEqual(status["upstreamBranch"], "feature/check-updates")

    @patch("services.update_service._run_git")
    def test_get_update_status_uses_current_branch_upstream(self, mock_run_git):
        def run_git_side_effect(_repo_root, *args):
            command = tuple(args)
            if command == ("rev-parse", "HEAD"):
                return "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            if command == ("rev-parse", "--abbrev-ref", "HEAD"):
                return "release/1.1"
            if command == (
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{upstream}",
            ):
                return "upstream/release/1.1"
            if command == ("config", "branch.release/1.1.remote"):
                return "upstream"
            if command == ("remote", "get-url", "upstream"):
                return "https://github.com/m3gnus/waveguide-generator.git"
            if command == ("fetch", "upstream", "--quiet"):
                return ""
            if command == ("rev-parse", "upstream/release/1.1"):
                return "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            if command == (
                "rev-list",
                "--left-right",
                "--count",
                "HEAD...upstream/release/1.1",
            ):
                return "0 0"
            raise AssertionError(f"Unexpected git command: {command}")

        mock_run_git.side_effect = run_git_side_effect

        status = get_update_status()
        self.assertFalse(status["updateAvailable"])
        self.assertEqual(status["upstreamRemote"], "upstream")
        self.assertEqual(status["upstreamBranch"], "release/1.1")
        self.assertEqual(status["behindCount"], 0)
        self.assertEqual(status["aheadCount"], 0)

    @patch("services.update_service._run_git")
    def test_get_update_status_rejects_detached_head(self, mock_run_git):
        mock_run_git.side_effect = [
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "HEAD",
        ]

        with self.assertRaisesRegex(RuntimeError, "detached HEAD"):
            get_update_status()

    @patch("services.update_service._run_git")
    def test_get_update_status_rejects_branch_without_upstream(self, mock_run_git):
        mock_run_git.side_effect = [
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "local-work",
            RuntimeError("no upstream configured"),
        ]

        with self.assertRaisesRegex(RuntimeError, "has no upstream branch"):
            get_update_status()

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
    def test_git_availability_probe_spawns_no_subprocess(self, mock_run, mock_run_git):
        """The availability probe must not spawn a process, so it cannot hang.

        This replaces a pair of tests that asserted the probe passed
        timeout=10 to `subprocess.run(["git", "--version"])`. That guarded
        against a hang, but the spawn had a worse failure mode: CreateProcess
        raises FileNotFoundError([WinError 2]) when the *current working
        directory* has been deleted, not only when the executable is missing.
        A sibling test that chdir'd into a since-removed temporary directory
        made this report "Git is not installed" on a machine with git plainly
        on PATH. shutil.which needs no child process and no valid cwd, so the
        hang is now impossible by construction rather than bounded by a
        timeout. Pin that: nothing may be spawned.
        """
        mock_run_git.side_effect = [
            "1111111111111111111111111111111111111111",
            "main",
            "origin/main",
            "origin",
            "git@github.com:m3gnus/waveguide-generator.git",
            "",
            "1111111111111111111111111111111111111111",
            "0 0",
        ]

        with patch("services.update_service.shutil.which", return_value="C:\\git.exe"):
            get_update_status()

        mock_run.assert_not_called()

    def test_missing_git_maps_to_runtime_error(self):
        with patch("services.update_service.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Git is not installed"):
                get_update_status()


if __name__ == "__main__":
    unittest.main()
