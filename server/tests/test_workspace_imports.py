"""Archive helpers must not initialize the workspace HTTP transport."""

import subprocess
import sys


def test_artifact_lookup_does_not_import_the_export_layer():
    result = subprocess.run(
        [sys.executable, "-c", """
import sys
from server.workspace.api import ExportMember, _member_bytes
try:
    _member_bytes(ExportMember(relative_path='horn.stl', artifact_id='missing'))
except ValueError as exc:
    assert 'Export again' in str(exc)
else:
    raise AssertionError('missing artifact must fail')
assert not any(name == 'server.exports' or name.startswith('server.exports.')
               for name in sys.modules), 'workspace imported the export layer'

# Production assembly must publish from the same store, without test patches.
from server.exports.api import STAGED_EXPORTS
data = b'stl-exact\\x00\\xff'
artifact_id = STAGED_EXPORTS.stage(data)
try:
    assert _member_bytes(ExportMember(relative_path='horn.stl', artifact_id=artifact_id)) == data
finally:
    STAGED_EXPORTS.discard(artifact_id)
"""],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr


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
