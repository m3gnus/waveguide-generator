"""The suite never reaches the WGLink add-in installed on the machine running it.

Every application start runs the add-in reconciliation
(``server/cadlink/addin_update.py``), and that resolves Fusion's AddIns
directory from the user's home. Before the sandbox, a test run on a developer's
Mac took the installer's operation lock inside the real AddIns directory on
every start, and a test started from the checkout that manages the installed
add-in would have replaced it with the checkout's pin.

The root ``conftest.py`` points ``WG2_FUSION_ADDINS_DIR`` at a sandbox and fails
any test that touches the real location. These tests hold both halves.
"""

from __future__ import annotations

from pathlib import Path
import importlib.util
import os
import sys
from types import ModuleType

import pytest

from server.cadlink import addin_update


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def root_conftest(pytestconfig: pytest.Config) -> ModuleType:
    """The repository-root ``conftest.py``, as pytest loaded it.

    Not ``import conftest``: pytest re-imports every non-package conftest
    under that one name, so the import would find ``server/tests/conftest.py``.
    """

    expected = (REPO_ROOT / "conftest.py").resolve()
    for plugin in pytestconfig.pluginmanager.get_plugins():
        location = getattr(plugin, "__file__", None)
        if location and Path(location).resolve() == expected:
            return plugin  # type: ignore[return-value]
    raise AssertionError("the root conftest.py is not loaded")


def _installer() -> ModuleType:
    path = REPO_ROOT / "scripts" / "install_wglink.py"
    spec = importlib.util.spec_from_file_location("wg_install_wglink_sandbox_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_suite_runs_with_a_sandboxed_addins_directory(root_conftest: ModuleType) -> None:
    sandbox = Path(os.environ["WG2_FUSION_ADDINS_DIR"])

    assert sandbox == root_conftest.SANDBOX_FUSION_ADDINS_DIR
    assert sandbox.is_relative_to(root_conftest.SANDBOX_DATA_DIR)
    for real in root_conftest.REAL_FUSION_ADDINS_DIRS:
        assert not sandbox.is_relative_to(real)


def test_the_installer_resolves_the_override_before_the_home_directory(tmp_path: Path) -> None:
    installer = _installer()
    chosen = tmp_path / "AddIns"

    for platform in ("macos", "windows", "linux"):
        assert (
            installer.default_addins_dir(
                platform, home=tmp_path / "home", environ={"WG2_FUSION_ADDINS_DIR": str(chosen)}
            )
            == chosen
        )
    # An empty value is no override.
    assert (
        installer.default_addins_dir(
            "linux", home=tmp_path / "home", environ={"WG2_FUSION_ADDINS_DIR": ""}
        )
        is None
    )


def test_the_startup_reconciliation_works_in_the_sandbox(root_conftest: ModuleType) -> None:
    """The positive proof: the lock appears in the sandbox, nothing reached home."""

    sandbox = root_conftest.SANDBOX_FUSION_ADDINS_DIR
    sandbox.mkdir(parents=True, exist_ok=True)
    lock = sandbox / ".WGLink-install.lock"
    lock.unlink(missing_ok=True)
    touched_before = len(root_conftest.REAL_ADDINS_TOUCHES)

    verdict, detail = addin_update.refresh_and_log()

    assert root_conftest.REAL_ADDINS_TOUCHES[touched_before:] == []
    assert verdict == "absent", detail
    assert lock.is_file(), "the reconciliation did not resolve the sandboxed directory"


@pytest.mark.parametrize(
    ("event", "args"),
    [
        ("open", ("{root}/WGLink/wglink_install.json", "r", 0)),
        ("open", ("{root}/.WGLink-install.lock", None, os.O_RDWR | os.O_CREAT)),
        ("os.rename", ("{root}/WGLink", "{root}/WGLink.previous", None, None)),
        ("os.replace", ("/elsewhere/staging", "{root}/WGLink", None, None)),
        ("shutil.rmtree", ("{root}/WGLink", None)),
        ("os.mkdir", ("{root}/WGLink", 0o777, None)),
        ("os.listdir", ("{root}",)),
        ("os.scandir", ("{root}/WGLink",)),
        ("os.remove", ("{root}/WGLink/WGLink.py", None)),
    ],
)
def test_the_guard_recognises_a_touch_of_the_protected_directory(
    root_conftest: ModuleType, tmp_path: Path, event: str, args: tuple[object, ...]
) -> None:
    root = tmp_path / "Autodesk" / "Autodesk Fusion" / "API" / "AddIns"
    concrete = tuple(
        arg.format(root=root.as_posix()) if isinstance(arg, str) else arg for arg in args
    )

    assert root_conftest.protected_addins_access(event, concrete, (root,)) is not None
    # The same event on a sibling path is not a touch.
    elsewhere = tuple(
        arg.replace("AddIns", "AddInsX") if isinstance(arg, str) else arg for arg in concrete
    )
    assert root_conftest.protected_addins_access(event, elsewhere, (root,)) is None


def test_the_guard_ignores_unrelated_events_and_arguments(
    root_conftest: ModuleType, tmp_path: Path
) -> None:
    root = tmp_path / "Autodesk" / "AddIns"
    check = root_conftest.protected_addins_access

    assert check("import", (str(root),), (root,)) is None
    assert check("open", (3, "r", 0), (root,)) is None
    assert check("open", (None, "r", 0), (root,)) is None
    assert check("open", (os.fsencode(str(root / "x")), "r", 0), (root,)) is not None


@pytest.mark.skipif(sys.platform not in {"darwin", "win32"}, reason="Fusion is macOS/Windows only")
def test_the_real_locations_are_the_ones_fusion_uses(root_conftest: ModuleType) -> None:
    home = Path.home()
    names = {path.as_posix() for path in root_conftest.REAL_FUSION_ADDINS_DIRS}
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support" / "Autodesk"
    else:
        appdata = os.environ.get("APPDATA")
        base = (Path(appdata) if appdata else home / "AppData" / "Roaming") / "Autodesk"
    assert (base / "Autodesk Fusion 360" / "API" / "AddIns").as_posix() in names
    assert (base / "Autodesk Fusion" / "API" / "AddIns").as_posix() in names
