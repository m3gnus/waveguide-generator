"""Suite-wide guards.

SIGPIPE: Python starts with it ignored, so a write to a closed pipe or socket
raises BrokenPipeError instead of killing the process. Native libraries the
suite loads (gmsh's C++ runtime is the known offender) can install their own
signal handlers and leave SIGPIPE at the default action -- after which the
FIRST late write to a dead fd, from any background thread, kills pytest with
exit 141 and no test named. ubuntu CI died exactly this way at a fixed-looking
position that was really just where the asynchronous signal landed. Native
changes bypass Python's cached ``signal.getsignal`` value, so the fixture
unconditionally restores the ignore before and after every test.

Modal dialogs: three launcher paths report a failure with a blocking Win32
MessageBoxW, which on a runner has nobody to select OK. A test that reaches one
does not fail -- it stops, and the job holds the runner until something else
cancels it. Server (windows-latest) burned two 2h+ runs that way on 2026-08-24
while Linux and macOS stayed green, because ctypes.windll does not exist there
and the best-effort except around each call swallowed the AttributeError.
Injecting the reporter at each call site is the real fix; this is the net that
catches the next one that forgets.

BEMPP worker prewarm: ``create_app`` warms the native solver worker at startup
by default, which is a real 25-61 s native solve in a spawned child. The suite
builds hundreds of apps and several of its assertions are wall-clock bounds, so
it opts out through the same switch an operator would use. Tests that need the
prewarm re-enable it explicitly.
"""

from __future__ import annotations

import os
import sys

import pytest

from server.platform.signal_rearm import restore_sigpipe_ignore

os.environ.setdefault("WG2_SOLVER_WARMUP", "0")


@pytest.fixture(autouse=True)
def _no_test_inherits_a_claimed_session():
    """Stop one test's session claim from letting a later test SIGKILL the run.

    ``adopt_process_group`` records the claiming pid in a module global, and
    ``kill_own_process_group`` fires only when that global equals the *current*
    pid. In production the pair is safe: both run in the BEMPP worker child, and
    nothing else ever claims a session.

    In the suite they are not paired. ``test_bempp_process.py`` drives
    ``_bempp_worker_main`` in-process -- that is how it tests the loop -- and its
    first act is ``adopt_process_group()``. Under pytest that ``setsid()``
    succeeds, because pytest is a child of the shell and so not a group leader.
    So the pytest process becomes its own session leader and records *its own*
    pid as the claimant. Any later ``kill_own_process_group()`` then passes the
    guard and SIGKILLs the whole run: measured as ``EXIT=137`` at 3% with
    ``test_bempp_process.py`` and ``test_process_tree_posix.py`` in one run, and
    it does not reproduce when either runs alone.

    It also hides under ``setsid``: a runner that is already a session leader
    makes the in-process ``setsid()`` fail, the global stays unset, and the suite
    passes -- which is why this survived a green local run. CI invokes pytest as
    a plain child, which is the failing shape.

    Restoring the global cannot undo the ``setsid``, and does not need to. What
    matters is that no test inherits a claim it did not make.
    """

    from server.platform import process_tree

    claimed = process_tree._adopted_session_pid
    try:
        yield
    finally:
        process_tree._adopted_session_pid = claimed


@pytest.fixture(autouse=True)
def _sigpipe_stays_ignored():
    restore_sigpipe_ignore()
    try:
        yield
    finally:
        restore_sigpipe_ignore()


@pytest.fixture(autouse=True)
def _modal_dialogs_fail_instead_of_blocking():
    """Turn a blocking Win32 dialog into a named failure.

    ``pytest.fail`` raises ``Failed``, which derives from BaseException rather
    than Exception -- so it travels straight out through the deliberate
    best-effort ``except Exception`` around every one of these call sites, which
    an AssertionError would not.
    """

    if sys.platform != "win32":
        yield
        return

    import ctypes

    user32 = ctypes.windll.user32
    original = user32.MessageBoxW

    def _refuse(*_arguments: object) -> int:
        pytest.fail(
            "a test reached a blocking Win32 MessageBoxW, which would hang the "
            "run until the job is cancelled. Inject the seam the call site "
            "offers -- failure_reporter= for apply_update, browser_fallback= "
            "for DesktopWindow -- rather than letting the default dialog run.",
            pytrace=False,
        )

    user32.MessageBoxW = _refuse
    try:
        yield
    finally:
        user32.MessageBoxW = original
