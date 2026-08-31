"""The native-PATH guard around every Windows Gmsh call.

``gmsh.initialize()`` truncates the process's native PATH on Windows. Measured
on this host with gmsh 4.15.2, PATH went from 1486 characters to 316, cut in
the middle of an entry so the last survivor was the fragment
``C:\\Program Files (x86)\\Common F``. ``os.environ`` is untouched, so the
damage is invisible from Python and only shows through
``GetEnvironmentVariableW``: anything afterwards that resolves an executable or
lazily loads a DLL through PATH fails for no visible reason.

``_preserve_native_windows_path`` snapshots PATH through kernel32 and restores
it. Nothing exercised it directly, on either half of its contract.
"""

from __future__ import annotations

import sys

import pytest

from server.mesh import gmsh_worker


pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="the guard is a no-op off Windows"
)


def _fail_the_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make only the PATH write fail, leaving the read side real.

    The real handle is captured before the patch: delegating through the
    module attribute would resolve back to the double and recurse.
    """

    real = gmsh_worker._kernel32

    class _FailingRestore:
        def __getattr__(self, name: str) -> object:
            return getattr(real, name)

        @staticmethod
        def SetEnvironmentVariableW(name: str, value: str | None) -> int:
            return 0

    monkeypatch.setattr(gmsh_worker, "_kernel32", _FailingRestore())


def test_restores_a_path_the_body_truncated() -> None:
    """The body's PATH damage does not outlive the guard."""

    before = gmsh_worker._read_native_windows_path()
    assert before is not None

    with gmsh_worker._preserve_native_windows_path():
        gmsh_worker._kernel32.SetEnvironmentVariableW("PATH", "C:/truncated")
        assert gmsh_worker._read_native_windows_path() == "C:/truncated"

    assert gmsh_worker._read_native_windows_path() == before


def test_a_failed_restore_does_not_mask_the_body_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The body's exception survives a restore that could not write PATH.

    The restore runs in a ``finally``, so raising there would replace the
    exception that actually explains the failed mesh or export -- the caller
    would see a WinError about an environment variable instead of the geometry
    error that stopped the run.
    """

    _fail_the_restore(monkeypatch)

    with pytest.raises(RuntimeError, match="geometry failed"):
        with gmsh_worker._preserve_native_windows_path():
            raise RuntimeError("geometry failed")


def test_a_failed_restore_still_raises_when_nothing_is_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A damaged PATH is reported on its own when the body succeeded."""

    _fail_the_restore(monkeypatch)

    with pytest.raises(OSError):
        with gmsh_worker._preserve_native_windows_path():
            pass
