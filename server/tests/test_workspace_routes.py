import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.datastructures import UploadFile

import api.routes_misc as routes_misc
from api.routes_misc import export_file, set_workspace_path, workspace_open, workspace_path, workspace_reset


def make_upload_file(name: str, content: bytes) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content))


class WorkspaceRoutesTest(unittest.TestCase):
    def tearDown(self):
        routes_misc._custom_workspace_path = None
        routes_misc._workspace_path_loaded = False

    def test_export_file_writes_to_workspace_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_root = Path(tmpdir).resolve()
            with patch("api.routes_misc._get_default_output_path", return_value=workspace_root):
                result = asyncio.run(
                    export_file(
                        file=make_upload_file("manual.txt", b"hello workspace"),
                        workspace_subdir="",
                    )
                )

            output_file = workspace_root / "manual.txt"
            self.assertTrue(output_file.exists())
            self.assertEqual(output_file.read_bytes(), b"hello workspace")
            self.assertEqual(result["workspaceRoot"], str(workspace_root))
            self.assertEqual(result["workspaceSubdir"], "")

    def test_export_file_writes_to_workspace_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_root = Path(tmpdir).resolve()
            with patch("api.routes_misc._get_default_output_path", return_value=workspace_root):
                result = asyncio.run(
                    export_file(
                        file=make_upload_file("bundle.csv", b"freq,spl\n100,90\n"),
                        workspace_subdir="jobs/horn_12",
                    )
                )

            output_file = workspace_root / "jobs" / "horn_12" / "bundle.csv"
            self.assertTrue(output_file.exists())
            self.assertEqual(output_file.read_text(), "freq,spl\n100,90\n")
            self.assertEqual(result["workspaceSubdir"], "jobs/horn_12")

    def test_export_file_rejects_path_traversal_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_root = Path(tmpdir).resolve()
            with patch("api.routes_misc._get_default_output_path", return_value=workspace_root):
                with self.assertRaises(HTTPException) as ctx:
                    asyncio.run(
                        export_file(
                            file=make_upload_file("bad.txt", b"bad"),
                            workspace_subdir="../outside",
                        )
                    )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("workspace_subdir", str(ctx.exception.detail))

    def test_workspace_path_returns_backend_workspace_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_root = Path(tmpdir).resolve()
            with patch("api.routes_misc._get_default_output_path", return_value=workspace_root):
                result = asyncio.run(workspace_path())

        self.assertEqual(result["path"], str(workspace_root))

    def test_workspace_path_restores_persisted_custom_folder_after_restart(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir).resolve()
            settings_path = tmp_root / "data" / "workspace_settings.json"
            default_root = tmp_root / "default-output"
            selected_root = tmp_root / "selected-output"
            selected_root.mkdir()

            with patch("api.routes_misc._WORKSPACE_SETTINGS_PATH", settings_path), patch(
                "api.routes_misc._REPO_OUTPUT_PATH", default_root
            ):
                routes_misc._custom_workspace_path = None
                routes_misc._workspace_path_loaded = False

                result = asyncio.run(set_workspace_path(str(selected_root)))
                self.assertEqual(result, {"path": str(selected_root), "custom": True})
                self.assertTrue(settings_path.exists())

                routes_misc._custom_workspace_path = None
                routes_misc._workspace_path_loaded = False
                restored = asyncio.run(workspace_path())

        self.assertEqual(restored["path"], str(selected_root))

    def test_workspace_reset_persists_default_folder_choice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir).resolve()
            settings_path = tmp_root / "data" / "workspace_settings.json"
            default_root = tmp_root / "default-output"
            selected_root = tmp_root / "selected-output"
            selected_root.mkdir()

            with patch("api.routes_misc._WORKSPACE_SETTINGS_PATH", settings_path), patch(
                "api.routes_misc._REPO_OUTPUT_PATH", default_root
            ):
                routes_misc._custom_workspace_path = None
                routes_misc._workspace_path_loaded = False

                asyncio.run(set_workspace_path(str(selected_root)))
                reset_result = asyncio.run(workspace_reset())
                self.assertEqual(reset_result, {"path": str(default_root), "custom": False})

                routes_misc._custom_workspace_path = None
                routes_misc._workspace_path_loaded = False
                restored = asyncio.run(workspace_path())

        self.assertEqual(restored["path"], str(default_root))

    def test_workspace_open_creates_and_opens_workspace_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace_root = (Path(tmpdir) / "output").resolve()
            with patch("api.routes_misc._get_default_output_path", return_value=workspace_root), patch(
                "api.routes_misc.platform.system", return_value="Darwin"
            ), patch("api.routes_misc.subprocess.Popen") as popen:
                result = asyncio.run(workspace_open())

            self.assertTrue(workspace_root.exists())
            popen.assert_called_once_with(["open", str(workspace_root)])
            self.assertEqual(result["status"], "opened")
            self.assertEqual(result["path"], str(workspace_root))


if __name__ == "__main__":
    unittest.main()
