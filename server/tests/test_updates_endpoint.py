import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app import check_updates, get_update_status


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


if __name__ == "__main__":
    unittest.main()
