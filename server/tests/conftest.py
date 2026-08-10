"""Suite-wide guards.

SIGPIPE: Python starts with it ignored, so a write to a closed pipe or socket
raises BrokenPipeError instead of killing the process. Native libraries the
suite loads (gmsh's C++ runtime is the known offender) can install their own
signal handlers and leave SIGPIPE at the default action -- after which the
FIRST late write to a dead fd, from any background thread, kills pytest with
exit 141 and no test named. ubuntu CI died exactly this way at a fixed-looking
position that was really just where the asynchronous signal landed. The
fixture restores the ignore after every test and names the test that flipped
the handler, so a regression is a visible warning instead of a silent
process kill.
"""

from __future__ import annotations

import os
import signal
import warnings

import pytest


@pytest.fixture(autouse=True)
def _sigpipe_stays_ignored(request: pytest.FixtureRequest):
    if os.name != "posix":
        yield
        return
    yield
    if signal.getsignal(signal.SIGPIPE) == signal.SIG_DFL:
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)
        warnings.warn(
            f"{request.node.nodeid} left SIGPIPE at the default action "
            "(a native library installed its own handlers); restored to "
            "ignored so a late broken-pipe write cannot kill the suite.",
            RuntimeWarning,
            stacklevel=1,
        )
