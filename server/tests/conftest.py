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

BEMPP worker prewarm: ``create_app`` warms the native solver worker at startup
by default, which is a real 25-61 s native solve in a spawned child. The suite
builds hundreds of apps and several of its assertions are wall-clock bounds, so
it opts out through the same switch an operator would use. Tests that need the
prewarm re-enable it explicitly.
"""

from __future__ import annotations

import os

import pytest

from server.platform.signal_rearm import restore_sigpipe_ignore

os.environ.setdefault("WG2_SOLVER_WARMUP", "0")


@pytest.fixture(autouse=True)
def _sigpipe_stays_ignored():
    restore_sigpipe_ignore()
    try:
        yield
    finally:
        restore_sigpipe_ignore()
