"""Export helpers remain usable before HTTP and Fusion assembly."""

from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", [
    "server.exports.geometry_identity",
    "server.exports.core",
    "server.exports.wglink_service",
    "server.cadlink.onshape.api",
    "server.cadlink.intake.ingest",
])
def test_helper_import_does_not_assemble_export_or_fusion_routes(module: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", f"""
import importlib
import sys
importlib.import_module({module!r})
assert 'server.exports.api' not in sys.modules
assert 'server.cadlink.fusion.api' not in sys.modules

import server.exports as exports
assert exports.__all__ == ['mount_exports']
assert 'mount_exports' in dir(exports)
try:
    exports.unknown_export
except AttributeError:
    pass
else:
    raise AssertionError('unknown exports must raise AttributeError')

from server.exports import mount_exports
from server.exports.api import mount_exports as api_mount_exports
assert mount_exports is api_mount_exports
assert exports.mount_exports is mount_exports
from server.exports.api import ExportRequest, WgLinkExportRequest
from server.exports import wglink_service
assert ExportRequest is wglink_service.ExportRequest
assert WgLinkExportRequest is wglink_service.WgLinkExportRequest
assert ExportRequest.__module__ == WgLinkExportRequest.__module__ == 'server.exports.api'
from server.exports import api
from server.cadlink.onshape import api as onshape_api
assert api.export_wglink_sync is wglink_service.export_wglink_sync
assert onshape_api.export_wglink_sync is wglink_service.export_wglink_sync
assert api.normalize_base_name is onshape_api.normalize_base_name is wglink_service.normalize_base_name
"""],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
