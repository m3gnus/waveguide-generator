"""The suite must never write into the user's own application directory.

The regression: ``DesktopWindow.run`` logs a frame-arming failure through
``_log_startup_failure``, which appends to ``data_paths().logs`` and swallows
every error. ``test_desktop_launcher.py`` drives that path with fake windows
whose events carry no ``shown`` or ``loaded`` hook. A run therefore creates and
appends to the user's real ``statusapp.log`` silently, because the writer is
best-effort by design and the tests pass.

The guard is the root ``conftest.py``. These tests prove it is armed.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from server.platform.paths import (
    APP_DIRECTORY,
    DATA_DIR_ENV,
    DOCUMENTS_DIRECTORY,
    data_paths,
    resolve_data_dir,
)

# Resolve from this file rather than through app_root(), which honors
# WG2_APP_ROOT and could aim the nested run at another checkout.
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_data_directory_is_the_sandbox_and_not_the_users_own(
    sandbox_data_dir: Path,
) -> None:
    """Every un-injected accessor resolves inside the sandbox."""

    assert data_paths().root == sandbox_data_dir
    for path in (data_paths().db, data_paths().logs, data_paths().locks):
        assert sandbox_data_dir in path.parents

    # Keep the rest of the environment: Windows needs APPDATA to resolve its
    # default. Dropping only the override reveals the current platform default.
    without_override = {
        name: value for name, value in os.environ.items() if name != DATA_DIR_ENV
    }
    users_own = resolve_data_dir(environ=without_override, home=Path.home())
    assert data_paths().root != users_own


def test_a_swallowed_startup_failure_lands_in_the_sandbox(
    sandbox_data_dir: Path,
) -> None:
    """Drive the exact best-effort writer that leaked."""

    from launchers.statusapp.__main__ import LOG_FILENAME, _log_startup_failure

    _log_startup_failure("probe: the suite is writing where it was told to")

    written = sandbox_data_dir / "logs" / LOG_FILENAME
    assert written.is_file()
    assert "probe: the suite is writing where it was told to" in written.read_text(
        encoding="utf-8"
    )


def test_a_nested_run_writes_nothing_under_the_home_it_is_given(tmp_path: Path) -> None:
    """Run the offending test file with a throwaway HOME and inspect it."""

    home = tmp_path / "home"
    home.mkdir()

    environment = dict(os.environ)
    # The child builds its own sandbox from the root conftest. Clearing the
    # inherited value prevents it from merely sharing this process's sandbox.
    # Without the guard, the child falls back to the platform default under
    # HOME, which is the leak this assertion catches.
    environment.pop(DATA_DIR_ENV, None)
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    environment["XDG_DATA_HOME"] = str(home / ".local" / "share")
    environment["XDG_DOCUMENTS_DIR"] = str(home / "Documents")
    environment["APPDATA"] = str(home / "AppData" / "Roaming")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "server/tests/test_desktop_launcher.py",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr

    strays = [
        path
        for path in home.rglob("*")
        if path.name in {APP_DIRECTORY, DOCUMENTS_DIRECTORY}
    ]
    assert strays == [], (
        "the suite created application directories under a throwaway HOME, so "
        f"a real run writes into the user's own: {strays}"
    )
