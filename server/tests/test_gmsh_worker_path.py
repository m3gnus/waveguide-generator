"""The native-PATH guard around every Windows Gmsh call.

``gmsh.initialize()`` truncates the process's native PATH on Windows. Measured
on this host with gmsh 4.15.2, PATH went from 1486 characters to 316, cut in
the middle of an entry so the last survivor was the fragment
``C:\\Program Files (x86)\\Common F``. ``os.environ`` is untouched, so the
damage is invisible from Python and only shows through
``GetEnvironmentVariableW``: anything afterwards that resolves an executable or
lazily loads a DLL through PATH fails for no visible reason.

The mesher owns the guard. WG imports that same function and applies it to
both ends of its worker-owned session.
"""

from __future__ import annotations

from contextlib import contextmanager
import sys
from types import SimpleNamespace

import pytest
from hornlab_mesher import native_env

from server.mesh import gmsh_worker


windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="the guard is a no-op off Windows"
)


def test_worker_reuses_the_mesher_path_guard_without_a_second_implementation() -> None:
    assert gmsh_worker._preserve_native_windows_path is native_env.preserve_native_windows_path
    assert not hasattr(gmsh_worker, "_read_native_windows_path")
    assert not hasattr(gmsh_worker, "_kernel32")


@pytest.mark.parametrize("failure", [None, "initialize", "body", "finalize"])
def test_worker_guards_session_boundaries_and_preserves_errors(
    monkeypatch: pytest.MonkeyPatch, failure: str | None,
) -> None:
    events = []
    initialized = False
    error = RuntimeError("native operation failed")

    @contextmanager
    def guard():
        events.append("enter")
        try:
            with native_env.preserve_native_windows_path():
                yield
        finally:
            events.append("exit")

    def initialize(**kwargs):
        nonlocal initialized
        assert kwargs == {"interruptible": False}
        events.append("initialize")
        if failure == "initialize":
            raise error
        initialized = True

    def finalize():
        nonlocal initialized
        events.append("finalize")
        initialized = False
        if failure == "finalize":
            raise error

    def build():
        events.append("body")
        if failure == "body":
            raise error
        return "mesh result"

    monkeypatch.setitem(sys.modules, "gmsh", SimpleNamespace(
        isInitialized=lambda: initialized, initialize=initialize, finalize=finalize,
    ))
    monkeypatch.setattr(gmsh_worker, "pin_gmsh_openmp_initial_thread", lambda: None)
    monkeypatch.setattr(gmsh_worker, "_preserve_native_windows_path", guard)

    if failure is None:
        assert gmsh_worker._run_in_gmsh_session(build) == "mesh result"
    else:
        with pytest.raises(RuntimeError) as raised:
            gmsh_worker._run_in_gmsh_session(build)
        assert raised.value is error

    expected = ["enter", "initialize", "exit"]
    if failure != "initialize":
        expected += ["body", "enter", "finalize", "exit"]
    assert events == expected
    assert not initialized


def _fail_the_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make only the PATH write fail, leaving the read side real.

    The real handle is captured before the patch: delegating through the
    module attribute would resolve back to the double and recurse.
    """

    real = native_env._kernel32

    class _FailingRestore:
        def __getattr__(self, name: str) -> object:
            return getattr(real, name)

        @staticmethod
        def SetEnvironmentVariableW(name: str, value: str | None) -> int:
            return 0

    monkeypatch.setattr(native_env, "_kernel32", _FailingRestore())


@windows_only
def test_restores_a_path_the_body_truncated() -> None:
    """The body's PATH damage does not outlive the guard."""

    before = native_env._read_native_windows_path()
    assert before is not None

    with gmsh_worker._preserve_native_windows_path():
        native_env._kernel32.SetEnvironmentVariableW("PATH", "C:/truncated")
        assert native_env._read_native_windows_path() == "C:/truncated"

    assert native_env._read_native_windows_path() == before


@windows_only
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


@windows_only
def test_a_failed_restore_still_raises_when_nothing_is_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A damaged PATH is reported on its own when the body succeeded."""

    _fail_the_restore(monkeypatch)

    with pytest.raises(OSError):
        with gmsh_worker._preserve_native_windows_path():
            pass
