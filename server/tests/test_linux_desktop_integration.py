"""The Linux desktop contracts that live in more than one file.

Three files have to agree before the installed launcher icon means anything:
``launchers/desktop.py`` names the running application to Qt, ``build_bundle``
writes the desktop entry that claims that name, and the installer renders the
entry's ``Exec`` line by matching it as literal text. None of the three fails
loudly when it drifts -- the window simply stops associating with its launcher,
which is exactly the symptom the 0.3.1 Linux bundle shipped and which nobody
noticed until a user reported the taskbar showing a browser.

Static checks on text, deliberately: they cost nothing, and they run on macOS
and Windows where the thing they describe cannot be executed at all.
"""

from __future__ import annotations

from pathlib import Path
import re

from launchers import desktop
from scripts.build_bundle import (
    LINUX_DESKTOP_ENTRY_NAME,
    LINUX_LAUNCHER_NAME,
    BundleBuilder,
    linux_desktop_entry,
)


ROOT = Path(__file__).resolve().parents[2]
BUNDLE_INSTALLER = ROOT / "installers" / "linux" / "bundle-install.sh"


def _entry_value(key: str) -> str:
    match = re.search(rf"^{key}=(.*)$", linux_desktop_entry(), re.MULTILINE)
    assert match is not None, f"the desktop entry has no {key}"
    return match.group(1)


def test_startup_wm_class_is_the_name_the_window_will_actually_carry() -> None:
    """X11 takes WM_CLASS's class from ``QCoreApplication::applicationName``.

    ``QXcbIntegration::wmClass`` in the selected Qt backend reads it there, so
    this key is true only while it equals what ``_name_linux_application``
    sets. When it was not -- 0.3.1 opened a browser, whose window carries the
    browser's class -- the launcher matched nothing, the taskbar entry did not
    group, and ``StartupNotify`` had nothing to stop its spinner on.
    """

    assert _entry_value("StartupWMClass") == desktop.LINUX_APPLICATION_NAME


def test_the_wayland_app_id_is_this_desktop_entrys_own_basename() -> None:
    """Wayland matches ``app_id`` against the entry's file name, not a class.

    ``QWaylandWindow`` sets ``app_id`` from ``QGuiApplication::desktopFileName``,
    and a compositor resolves it by looking for ``<app_id>.desktop``. So the
    string the launcher hands Qt has to be this file's name minus its suffix,
    or a Wayland session -- which is the default on current Fedora, Ubuntu and
    KDE -- shows an unnamed, iconless window.
    """

    assert f"{desktop.LINUX_DESKTOP_FILE_NAME}.desktop" == LINUX_DESKTOP_ENTRY_NAME


def test_the_installer_renders_the_exec_line_the_bundle_actually_ships() -> None:
    """The installer matches ``Exec`` as literal text, so drift is a hard stop.

    It refuses the install with "does not contain the expected Exec template"
    rather than writing a menu entry that cannot launch -- correct, and a
    release-time failure. This makes it a test-time one.
    """

    template = _entry_value("Exec")
    installer = BUNDLE_INSTALLER.read_text(encoding="utf-8")

    assert template == f"@INSTALL_DIR@/{LINUX_LAUNCHER_NAME}"
    assert '"Exec=@INSTALL_DIR@/$LAUNCHER_NAME")' in installer
    # Nothing may be appended while rendering either: a field code added on
    # this side alone would reintroduce the argument the entry promises not to
    # pass, and the entry's own test could not see it.
    assert """printf 'Exec="%s"\\n' "$EXECUTABLE\"""" in installer


def test_the_missing_library_message_names_packages_for_three_distributions() -> None:
    """A blocked user cannot translate "the matching libX* packages" into a fix.

    That is what the message said, and the reporter on Fedora 44 had to
    resolve ten package names with ``rpm -qf`` while stopped. The three lists
    below were checked against each distribution's own package index on
    2026-09-05.
    """

    installer = BUNDLE_INSTALLER.read_text(encoding="utf-8")

    assert "sudo apt install libglu1-mesa libgl1 libgomp1 libfontconfig1" in installer
    assert "sudo dnf install mesa-libGLU libglvnd-glx libgomp fontconfig" in installer
    assert "sudo pacman -S --needed glu libglvnd gcc-libs fontconfig" in installer
    # The one name a reader would otherwise guess wrong: current Fedora serves
    # libGL.so.1 from libglvnd, and mesa-libGL does not resolve.
    assert "libglvnd-glx, not mesa-libGL" in installer


def _requirements(name: str) -> dict[str, tuple[str, str]]:
    """Map each pinned distribution in a requirements file to (version, marker)."""

    pinned: dict[str, tuple[str, str]] = {}
    for raw in (ROOT / "server" / name).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        requirement, _, marker = line.partition(";")
        # ``uvicorn[standard]`` is one distribution with an extra; the lock
        # pins the distribution.
        distribution, _, version = requirement.strip().partition("==")
        pinned[distribution.split("[")[0].strip().casefold()] = (
            version.strip(),
            marker.strip(),
        )
    return pinned


def test_the_linux_window_backend_is_declared_and_locked() -> None:
    """pywebview leaves every Linux backend to an extra, so we name one.

    Windows and macOS get theirs from unconditional platform markers
    (pythonnet, the pyobjc set); Linux gets nothing unless asked, which is how
    0.3.1 came to ship the window library with no way to open a window. These
    two lines are what ``pywebview[pyside6]`` resolves to, spelled out because
    the platform markers and exact dependency versions stay explicit.
    """

    runtime = _requirements("requirements-runtime.txt")
    lock = _requirements("requirements-lock.txt")

    assert runtime["pywebview"] == ("6.2.1", ""), "the window itself is every platform's"
    for distribution in ("pyside6", "qtpy"):
        version, marker = runtime[distribution]
        assert marker == 'sys_platform == "linux"'
        assert lock[distribution] == (version, marker)
    # QtWebEngine -- what the interface is actually drawn in -- ships in
    # PySide6-Addons, so the metapackage alone would not be a complete lock.
    assert "pyside6-addons" in lock and "shiboken6" in lock


def test_the_pyside6_pieces_are_locked_at_one_version() -> None:
    """PySide6 requires its own parts at ``==`` its own version.

    ``PySide6==X`` depends on ``shiboken6==X``, ``PySide6_Essentials==X`` and
    ``PySide6_Addons==X``. A lock that pinned them apart would be unsatisfiable
    -- and would fail at install time on Linux only, where nothing else in this
    suite runs.
    """

    lock = _requirements("requirements-lock.txt")
    versions = {
        name: lock[name][0]
        for name in ("pyside6", "pyside6-addons", "pyside6-essentials", "shiboken6")
    }

    assert len(set(versions.values())) == 1, versions


def test_the_qt_backend_stays_on_the_wider_glibc_floor() -> None:
    """6.10 moved PySide6's Linux wheels from manylinux_2_28 to 2_34.

    glibc 2.28 reaches Ubuntu 20.04, Debian 10 and RHEL 8; 2.34 starts at
    Ubuntu 22.04 and Fedora 35. The bundle targets 24.04 and would not notice,
    but a source checkout on an older distribution would find no wheel, fall
    through to an sdist that cannot build, and fail its whole environment over
    a window it never asked for. Raising this pin is a decision about who can
    still install, so it should not happen by tidying.
    """

    version = _requirements("requirements-lock.txt")["pyside6"][0]
    major, minor, _rest = version.split(".", 2)

    assert (int(major), int(minor)) <= (6, 9), (
        f"PySide6 {version} ships manylinux_2_34 wheels; see requirements-runtime.txt"
    )


def test_every_runtime_pin_is_repeated_in_the_lock_at_the_same_version() -> None:
    """The lock is what bootstrap validates an environment against.

    A runtime requirement absent from it installs whatever pip resolves that
    day, in a bundle whose whole claim is reproducibility -- and bootstrap's
    check would not notice, because it only demands the entries the lock names.
    """

    lock = _requirements("requirements-lock.txt")
    for distribution, (version, marker) in _requirements("requirements-runtime.txt").items():
        assert distribution in lock, f"{distribution} is pinned but not locked"
        assert lock[distribution] == (version, marker), distribution


def test_the_tarball_instructions_no_longer_deny_the_native_window() -> None:
    """The bundle's own README presented the browser as the settled answer.

    "That is the documented behaviour, not a failure" read as a decision
    rather than the deferral it was, and it is now simply untrue.
    """

    builder = BundleBuilder(ROOT, system=lambda: "Linux")
    instructions = builder.linux_readme()

    assert "rather than the single native" not in instructions
    assert "--no-gui" in instructions, "the headless answer has to be somewhere"
