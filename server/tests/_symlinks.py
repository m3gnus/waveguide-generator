"""Skip a symlink test on a host where this process cannot create a symlink.

Windows lets an unelevated process create a symbolic link only with Developer
Mode on (or the SeCreateSymbolicLinkPrivilege); elsewhere ``os.symlink`` fails
with WinError 1314. A test that needs a real link cannot say anything on such a
host, so it skips and names the reason instead of failing.

The check tries to create a link once per process rather than guessing from
the platform or from elevation, so a host that can create links always runs
these tests in full.
"""

from __future__ import annotations

import functools
import os
import tempfile

import pytest


@functools.cache
def symlink_refusal() -> str | None:
    """Why this process cannot create a symlink, or None when it can."""

    with tempfile.TemporaryDirectory(prefix="wg-symlink-probe-") as directory:
        target = os.path.join(directory, "target")
        with open(target, "w", encoding="utf-8"):
            pass
        try:
            os.symlink(target, os.path.join(directory, "link"))
        except OSError as exc:
            return f"this process cannot create symbolic links: {exc}"
    return None


requires_symlinks = pytest.mark.skipif(
    symlink_refusal() is not None, reason=symlink_refusal() or "symlinks available"
)
