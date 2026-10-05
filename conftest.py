"""Point application data and Documents at a sandbox for the whole test run.

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

Runtime state: the same rule covers the BEAT and Julia runtime of the machine
running the tests (native Metal and OpenCL discovery are not disabled: the
variables below do not reach them, so a test that needs those marks itself
``real_runtime``). The ``_no_prepared_runtime`` fixture below points
``HORNLAB_BEAT_RUNTIME_DIR`` (where ``state-<backend>.json`` lives),
``HORNLAB_BEAT_JULIA``, ``HORNLAB_BEAT_WORKER_DIR`` and ``JULIA_DEPOT_PATH`` at
per-test empty locations and clears the force-CPU switch and the ROCm/HIP
variables, so every ordinary test starts from "no prepared runtime" whether it
runs on a Mac with a provisioned BEAT or on a bare hosted runner. A test that
needs a runtime states one explicitly (a fake, or a state file it writes into
the redirected directory). A test whose purpose is to qualify the runtime really
installed on this machine opts out with ``@pytest.mark.real_runtime`` (or a
module-level ``pytestmark``); such a test keeps the ambient environment and must
skip itself when no runtime is present. Mutating tests that need a *real*
sandbox rather than none are not opted out: they set their own directory.

This file sits at the repository root so it covers ``server/tests`` and
``scripts/tests`` whether pytest invokes them together or as separate CI jobs.
"""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import shutil
import sys
import tempfile

import pytest

# A worker is a process, not a core budget: native libraries otherwise start
# their own full-machine thread pools in every worker (and in both broker lanes).
for _thread_env in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS"):
    os.environ[_thread_env] = "1"

from server.platform.paths import DATA_DIR_ENV  # noqa: E402 - bound native pools before application imports

# Deliberately unconditional. An ambient WG2_DATA_DIR is as likely to be the
# developer's real directory as a scratch one, and the suite cannot tell the
# two apart.
SANDBOX_DATA_DIR = Path(tempfile.mkdtemp(prefix="wg-test-data-"))
os.environ[DATA_DIR_ENV] = str(SANDBOX_DATA_DIR)
# Server startup brings Fusion's WGLink up to this build and installs it where
# it is missing (server/cadlink/addin_update.py). A test run must never touch
# the add-in of the person running it, so the startup refresh is off here;
# tests of the refresh call it with an explicit add-ins folder.
os.environ["WG2_WGLINK_REFRESH"] = "0"
# Nor may it collect the solve commands a real Fusion delivered: the backend's
# consumer loop is off here; tests drive one delivery pass at a time instead.
os.environ["WG2_CAD_DELIVERY"] = "0"


def pytest_configure(config: pytest.Config) -> None:
    workers = config.getoption("numprocesses", default=0)
    if workers not in (None, 0) and (
        not isinstance(workers, int) or not 1 <= workers <= 6
    ):
        raise pytest.UsageError("Use a bounded worker count: -n 0 through -n 6; never -n auto.")
    if workers and config.getoption("dist", default="no") != "loadgroup":
        raise pytest.UsageError("Parallel tests require --dist=loadgroup to honour serial groups.")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.get_closest_marker("serial") is not None:
            item.add_marker(pytest.mark.xdist_group("serial"))


def pytest_unconfigure(config: pytest.Config) -> None:
    # The xdist controller collects no tests, so its session fixture never runs.
    # Clean its import-time sandbox as well as the workers' sandboxes.
    shutil.rmtree(SANDBOX_DATA_DIR, ignore_errors=True)


# -- The WGLink add-in installed on this machine ------------------------------
#
# The add-in reconciliation (``server/cadlink/addin_update.py``) resolves
# Fusion's AddIns directory from the user's home, takes the installer's
# operation lock inside it, and updates or installs WGLink there. The switch
# above turns it off for starts in this process, but not everything that runs
# it inherits the switch: a test calls the refresh itself, and a harness that
# starts a real server builds that child's environment from scratch.
#
# ``WG2_FUSION_ADDINS_DIR`` (``scripts/install_wglink.py``) moves that
# directory into the sandbox for this process and every child that inherits
# its environment. A harness that builds a child environment from scratch sets
# it itself (``server/tests/test_bounded_server_shutdown.py``).
#
# The guard below is what makes a miss visible: an audit hook refuses, and
# records, any file operation this process makes under the real directories,
# and the fixture after it fails the test in which that happened, or in which
# they changed at all -- which also catches a child process that wrote there.

FUSION_ADDINS_DIR_ENV = "WG2_FUSION_ADDINS_DIR"
SANDBOX_FUSION_ADDINS_DIR = SANDBOX_DATA_DIR / "fusion-addins"
REAL_HOME_DIR = Path.home()


def _real_fusion_addins_dirs() -> tuple[Path, ...]:
    """Both of Fusion's AddIns locations on this platform, legacy and current."""

    home = REAL_HOME_DIR
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support" / "Autodesk"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = (Path(appdata) if appdata else home / "AppData" / "Roaming") / "Autodesk"
    else:
        return ()
    return tuple(
        base / name / "API" / "AddIns" for name in ("Autodesk Fusion 360", "Autodesk Fusion")
    )


REAL_FUSION_ADDINS_DIRS = _real_fusion_addins_dirs()
os.environ[FUSION_ADDINS_DIR_ENV] = str(SANDBOX_FUSION_ADDINS_DIR)

# Capture the real add-in locations above before redirecting the home used by
# Documents defaults. WG2_DATA_DIR alone does not cover default_runs_dir().
SANDBOX_HOME_DIR = SANDBOX_DATA_DIR / "home"
os.environ["USERPROFILE" if sys.platform == "win32" else "HOME"] = str(SANDBOX_HOME_DIR)
# An ambient XDG override would otherwise defeat the redirected HOME.
os.environ["XDG_DOCUMENTS_DIR"] = str(SANDBOX_HOME_DIR / "Documents")

#: Every refused touch of a real AddIns directory, in order.
REAL_ADDINS_TOUCHES: list[str] = []

#: Only events that name a filesystem path: ``open`` and the ``os.*`` and
#: ``shutil.*`` families (``os.listdir``, ``os.rename``, ``shutil.rmtree``, ...).
_AUDITED_EVENT_PREFIXES = ("os.", "shutil.")


def _comparable(path: str) -> str:
    normalized = os.path.normcase(os.path.abspath(path))
    # macOS and Windows file systems are case-insensitive by default.
    return normalized.casefold() if sys.platform in {"darwin", "win32"} else normalized


def protected_addins_access(
    event: str, args: tuple[object, ...], roots: tuple[Path, ...]
) -> str | None:
    """Describe the touch if audit ``event`` names a path under one of ``roots``."""

    if event != "open" and not event.startswith(_AUDITED_EVENT_PREFIXES):
        return None
    for arg in args:
        if not isinstance(arg, (str, bytes, os.PathLike)):
            continue
        try:
            text = os.fsdecode(os.fspath(arg))
        except (TypeError, ValueError):
            continue
        # Cheap before exact: every protected root ends in "AddIns".
        if "addins" not in text.casefold():
            continue
        candidate = _comparable(text)
        for root in roots:
            protected = _comparable(str(root))
            if candidate == protected or candidate.startswith(protected + os.sep):
                return f"{event} {text}"
    return None


def _refuse_real_addins_access(event: str, args: tuple[object, ...]) -> None:
    touch = protected_addins_access(event, args, REAL_FUSION_ADDINS_DIRS)
    if touch is not None:
        REAL_ADDINS_TOUCHES.append(touch)
        raise PermissionError(
            f"test isolation: {touch} reaches the WGLink add-in installed on this machine"
        )


if REAL_FUSION_ADDINS_DIRS:
    sys.addaudithook(_refuse_real_addins_access)


def _real_addins_snapshot() -> tuple[object, ...]:
    """What an install, a replacement or a new lock file would change.

    ``os.stat`` is not an audited event, so taking this never trips the hook.
    """

    state: list[object] = []
    for root in REAL_FUSION_ADDINS_DIRS:
        for path in (
            root,
            root / ".WGLink-install.lock",
            root / "WGLink",
            root / "WGLink" / "wglink_install.json",
            root / "WGLink.previous",
        ):
            try:
                stat = path.stat()
            except OSError:
                state.append((str(path), None))
            else:
                state.append((str(path), stat.st_ino, stat.st_mtime_ns, stat.st_size))
    return tuple(state)


@pytest.fixture(autouse=True)
def _no_test_touches_the_installed_addin(request: pytest.FixtureRequest) -> Iterator[None]:
    """Fail the test that reached the real AddIns directory, from any process."""

    touched = len(REAL_ADDINS_TOUCHES)
    before = _real_addins_snapshot()
    yield
    touches = REAL_ADDINS_TOUCHES[touched:]
    changed = _real_addins_snapshot() != before
    if touches or changed:
        detail = "; ".join(touches) if touches else "its files changed (a child process?)"
        pytest.fail(
            f"{request.node.nodeid} reached the WGLink add-in installed on this machine: "
            f"{detail}. Resolve Fusion's AddIns directory through "
            f"{FUSION_ADDINS_DIR_ENV}, which the suite points at a sandbox.",
            pytrace=False,
        )


#: Variables through which a BEAT/Julia/OpenCL runtime prepared on the host is
#: found. Name -> value for the test (``{dir}`` is a per-test empty folder), or
#: ``None`` to remove it. HORNLAB_BEAT_JULIA names a file that does not exist:
#: the package treats a configured-but-missing path as "no Julia" and does not
#: fall through to PATH or the provisioned runtime.
_RUNTIME_ISOLATION: dict[str, str | None] = {
    "HORNLAB_BEAT_RUNTIME_DIR": "{dir}/beat-runtime",
    "HORNLAB_BEAT_JULIA": "{dir}/no-julia",
    "HORNLAB_BEAT_WORKER_DIR": "{dir}/beat-workers",
    "HORNLAB_BEAT_PERSISTENT_HOST": "0",
    "JULIA_DEPOT_PATH": "{dir}/julia-depot",
    "JULIA_PROJECT": None,
    "JULIA_LOAD_PATH": None,
    "HORNLAB_BEAT_FORCE_CPU": None,
    "HORNLAB_BEAT_PROBE_ENGINE_DIR": None,
    "BLAB_ROCM_PATH": None,
    "ROCM_PATH": None,
    "HIP_PATH": None,
    "ROCM_HOME": None,
    "XDG_DATA_HOME": "{dir}/xdg-data",
    "XDG_RUNTIME_DIR": None,
}
_runtime_isolation_counter = 0


@pytest.fixture(autouse=True)
def _no_prepared_runtime(request: pytest.FixtureRequest) -> Iterator[None]:
    """Start every ordinary test with no prepared BEAT or Julia runtime.

    Only the BEAT/Julia lookup (state directory, Julia path, worker and depot
    directories, force-CPU and ROCm/HIP variables) is redirected; native Metal and
    OpenCL discovery still sees the host, so tests that rely on those are marked.

    A test that passes only because this machine has a provisioned runtime fails
    on every hosted runner (the BEAT OpenCL endpoint tests did, 2026-10-04).
    ``@pytest.mark.real_runtime`` opts out. No ``monkeypatch``: see
    ``_no_test_leaks_application_environment`` for why a suite-wide fixture
    avoids it.
    """

    if request.node.get_closest_marker("real_runtime") is not None:
        yield
        return
    global _runtime_isolation_counter
    _runtime_isolation_counter += 1
    base = SANDBOX_DATA_DIR / "runtime-isolation" / f"{os.getpid()}-{_runtime_isolation_counter}"
    saved = {name: os.environ.get(name) for name in _RUNTIME_ISOLATION}
    for name, template in _RUNTIME_ISOLATION.items():
        if template is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = template.format(dir=base)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


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
