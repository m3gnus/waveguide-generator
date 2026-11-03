"""Archive helpers must not initialize the workspace HTTP transport."""

import subprocess
import sys


def test_archive_import_defers_http_transport_and_preserves_public_exports():
    result = subprocess.run(
        [sys.executable, "-c", """
import sys
from server.workspace.archive import archive_folder_slug
assert 'server.workspace.api' not in sys.modules
assert 'fastapi' not in sys.modules
assert archive_folder_slug('A / B') == 'A_B'
import server.workspace as workspace
assert workspace.__all__ == ['mount_workspace']
assert 'mount_workspace' in dir(workspace)
from server.workspace.api import mount_workspace
assert workspace.mount_workspace is mount_workspace
assert workspace.mount_workspace is workspace.mount_workspace
try:
    workspace.absent
except AttributeError:
    pass
else:
    raise AssertionError('unknown export must fail')
"""],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
