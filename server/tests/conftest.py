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

BEAT CPU provisioning: same shape, different cost -- a background download of a
portable Julia. See the switch below.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from server.platform.signal_rearm import restore_sigpipe_ignore

os.environ.setdefault("WG2_SOLVER_WARMUP", "0")
# BEAT CPU runtime provisioning: a launched application provisions one in the
# background on a GPU-less Windows or Linux host, which downloads a portable
# Julia and precompiles it. A test run is not an install, so the suite opts out
# through the same switch an operator would use; the tests that are about the
# gate call ``start_cpu_provisioning`` with an explicit environment.
os.environ.setdefault("WG2_SKIP_BEAT_CPU_PROVISION", "1")


def pytest_sessionstart(session):
    """Refuse one missing prerequisite instead of cascading app-mount failures."""

    index = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "index.html"
    if not index.is_file():
        raise pytest.UsageError(
            "The server test suite requires the built frontend. From the repository "
            "root, run:\n  npm --prefix frontend ci\n"
            "  npm --prefix frontend run build\nThen rerun pytest."
        )


#: Where a launcher failure would be put on screen. Two modules, because
#: ``launchers.desktop`` binds the reporter into its own namespace at import
#: time, so patching the definition alone would leave that copy live.
_DIALOG_ENTRY_POINTS = (
    ("launchers.statusapp.__main__", "_show_startup_failure_dialog"),
    ("launchers.desktop", "_show_startup_failure_dialog"),
    ("launchers.desktop", "_show_bundle_failure_dialog"),
)

_startup_dialogs: list[str] = []


@pytest.fixture()
def real_startup_dialogs():
    """Opt out of the guard below.

    For the handful of tests whose subject *is* the dialog transport -- which
    command is chosen, what happens when none exists, whether the AppleScript
    parses. They stub the process layer themselves.
    """

    return None


@pytest.fixture(autouse=True)
def _no_test_opens_a_dialog(request):
    """No test may put a modal on the screen of whoever is running it.

    ``_report_startup_failure`` shows a dialog whenever nobody is reading
    stderr, and under pytest nobody is: the stream is captured, so
    ``isatty()`` is false. On macOS that dialog was a blocking ``osascript``,
    so the first test to report a startup failure held the whole run for the
    300 s faulthandler timeout and left the modal behind afterwards. Eight
    tests in this suite reach it, and none of them is about the dialog.

    The decision is left alone -- only the delivery is replaced -- so a test
    can still assert that a failure *would* have been shown, through
    :func:`startup_dialogs`.

    **Its own ``MonkeyPatch``, deliberately, and not the shared fixture.**
    Requesting ``monkeypatch`` here would make every test in the suite depend
    on it, and an autouse fixture at conftest scope is set up before the
    module-level ones -- so the shared undo, which finalises in reverse, would
    start running *after* module fixtures that clear an ``lru_cache`` in their
    teardown. Those fixtures would then find the plain function a test had
    patched in, and `cache_clear` is not an attribute of one. That is not a
    hypothetical: it is six teardown errors across
    ``test_beat_cpu_runtime``/``test_bempp_availability``/``test_solver_beat``
    in a full run, invisible to any subset that does not include them. A guard
    with no business in those tests must not reorder their fixtures, so this
    one owns its patches and undoes them itself.
    """

    if "real_startup_dialogs" in request.fixturenames:
        yield
        return
    _startup_dialogs.clear()
    patcher = pytest.MonkeyPatch()
    try:
        for module_name, attribute in _DIALOG_ENTRY_POINTS:
            module = sys.modules.get(module_name)
            if module is not None and hasattr(module, attribute):
                patcher.setattr(module, attribute, _startup_dialogs.append)
        yield
    finally:
        patcher.undo()


@pytest.fixture()
def startup_dialogs(_no_test_opens_a_dialog) -> list[str]:
    """Every message this test would have put on screen, in order."""

    return _startup_dialogs


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
