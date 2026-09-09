"""Point the application data directory at a sandbox for the whole test run.

Without this, a test that reaches any un-injected ``server.platform.paths``
accessor gets the user's own application directory. That is not hypothetical:
``DesktopWindow.run`` reports a frame-arming failure through
``launchers.statusapp.__main__._log_startup_failure``, which appends to
``data_paths().logs`` and swallows every error, and the desktop-launcher tests
drive that path with fake windows that have no ``shown``/``loaded`` event. A
suite run therefore writes into the real log while every test remains green.

The environment variable is the same knob an operator uses (``--data-dir``
writes it), so redirecting it covers every caller of ``resolve_data_dir``,
``data_paths``, or ``ensure_data_layout`` that does not already inject
``environ=``. Tests that need a different directory still set their own:
``monkeypatch.setenv`` restores this value afterwards, and an explicit argument
beats the environment anyway.

Set it at import, not in a fixture, because module-level code in a test file runs
during collection, before any fixture. A leak there must not slip under a
fixture-scoped guard.

This file sits at the repository root so it covers ``server/tests`` and
``scripts/tests`` whether pytest invokes them together or as separate CI jobs.
"""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import shutil
import tempfile

import pytest

from server.platform.paths import DATA_DIR_ENV

# Deliberately unconditional. An ambient WG2_DATA_DIR is as likely to be the
# developer's real directory as a scratch one, and the suite cannot tell the
# two apart.
SANDBOX_DATA_DIR = Path(tempfile.mkdtemp(prefix="wg-test-data-"))
os.environ[DATA_DIR_ENV] = str(SANDBOX_DATA_DIR)


@pytest.fixture(scope="session", autouse=True)
def sandbox_data_dir() -> Iterator[Path]:
    """Expose the suite sandbox and remove it when the run ends."""

    try:
        yield SANDBOX_DATA_DIR
    finally:
        shutil.rmtree(SANDBOX_DATA_DIR, ignore_errors=True)
