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


#: Every variable the application reads is spelled ``WG2_*``.
APPLICATION_ENV_PREFIX = "WG2_"


def _application_environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if name.startswith(APPLICATION_ENV_PREFIX)
    }


@pytest.fixture(autouse=True)
def _no_test_leaks_application_environment(
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    """Fail the test that leaves a ``WG2_*`` variable changed for later tests.

    Production code may write the process environment: ``launch/serve.py``
    stores ``--data-dir`` in ``WG2_DATA_DIR`` so every later accessor agrees
    with it, which is right for a launcher that owns its process and wrong for
    a test that shares one. A test that calls such an entry point in-process
    without registering the variable with ``monkeypatch`` hands its own
    ``tmp_path`` to every test after it, and the test that then fails is a
    stranger, in another file, whose log paths name the leaker's directory.

    ``monkeypatch`` restores before this teardown runs (this autouse fixture is
    set up first, so it is torn down last), so only an unrestored write is
    reported. The environment is put back before failing, so one leak costs one
    error rather than every test that follows it.

    No ``monkeypatch`` here, for the reason ``server/tests/conftest.py`` gives
    at ``_no_test_opens_a_dialog``: requesting it from a suite-wide autouse
    fixture reorders teardown for every test.
    """

    before = _application_environment()
    yield
    after = _application_environment()
    if after == before:
        return
    changed = sorted(
        name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
    )
    for name in after.keys() - before.keys():
        del os.environ[name]
    os.environ.update(before)
    details = "; ".join(
        f"{name}: {before.get(name)!r} -> {after.get(name)!r}" for name in changed
    )
    pytest.fail(
        f"{request.node.nodeid} left the application environment changed "
        f"({details}). Register the variable with monkeypatch.setenv or "
        "monkeypatch.delenv before calling code that writes os.environ.",
        pytrace=False,
    )
