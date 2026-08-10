"""The installer must not leak an interpreter override into the launcher.

Ported from v1's `tests/installer-env-contract.test.js`, whose subject was a
clean-machine Windows failure:

    install/install.bat did `set "PYTHON_BIN=py"` to remember which launcher it
    had picked. In cmd.exe every `set` creates an *environment* variable, so
    PYTHON_BIN was inherited by the child processes the installer spawns at the
    end. scripts/backend-python.js honors env.PYTHON_BIN ahead of the
    .waveguide/backend-python.path marker, so the final preflight audited the
    system Python instead of the .venv the installer had just built, reported
    every dependency MISSING, and aborted a fully working install with exit 1.

v2 has no backend-python.js and no marker file, but it has the same shape of
hazard and a larger blast radius: the launchers under `launchers/<platform>/`
honour **WG2_PYTHON** ahead of the repository environment, and the installer
now *ends by invoking the launcher*. An installer that stored its chosen
bootstrap interpreter in WG2_PYTHON would therefore hand the launcher a system
Python with none of v2's dependencies in it -- and on Windows it would do so
invisibly, because `set` exports.

install.sh is not affected by the export half: a bare `VAR=value` in bash is
shell-local. It is still checked, because the installer `exec`s the launcher
and an accidental `export` would carry just as far.
"""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]

LAUNCHER_BATCH = ROOT / "launchers" / "windows" / "launch-wg.bat"
LAUNCHER_COMMAND = ROOT / "launchers" / "macos" / "launch-wg.command"
INSTALLERS = (
    ROOT / "scripts" / "install.sh",
    ROOT / "scripts" / "install.bat",
    ROOT / "installers" / "windows" / "install-and-update.bat",
    ROOT / "installers" / "macos" / "install-wg.command",
)

#: Environment variables that redirect which interpreter ends up serving.
INTERPRETER_OVERRIDE_ENV_VARS = ("WG2_PYTHON",)

#: Every other WG2_* knob the launchers read. Listed so that adding a new one
#: forces a decision about whether it belongs above.
OTHER_LAUNCHER_ENV_VARS = ("WG2_NO_PAUSE",)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _launcher_env_vars() -> set[str]:
    names: set[str] = set()
    for path in (LAUNCHER_BATCH, LAUNCHER_COMMAND):
        for line in read(path).splitlines():
            if re.match(r"\s*(rem|::|#)", line):
                continue
            names.update(re.findall(r"WG2_[A-Z0-9_]+", line))
    return names


def test_the_launchers_honour_exactly_the_documented_override_vars():
    """If a new WG2_* knob appears in a launcher, classify it here.

    That is the whole point of the list: v1's bug was possible because nobody
    had written down which environment variables changed interpreter selection,
    so nobody checked the installer against them.
    """

    assert _launcher_env_vars() == set(INTERPRETER_OVERRIDE_ENV_VARS) | set(OTHER_LAUNCHER_ENV_VARS)


def test_no_installer_assigns_an_interpreter_override_variable():
    for path in INSTALLERS:
        source = read(path)
        for name in INTERPRETER_OVERRIDE_ENV_VARS:
            # cmd: `set NAME=` / `set "NAME=`. sh: `NAME=` / `export NAME=`.
            assignment = re.compile(
                rf'^\s*(set\s+"?{name}=|(export\s+)?{name}=)', re.IGNORECASE | re.MULTILINE
            )
            assert not assignment.search(source), (
                f"{path.name} assigns {name}. On Windows cmd.exe exports every `set` to "
                f"child processes, so the launcher this installer ends by invoking would "
                f"be pinned to the installer's bootstrap interpreter instead of the "
                f".venv it just built. Use a differently named variable."
            )


def test_the_installers_track_their_bootstrap_interpreter_under_a_name_the_launcher_ignores():
    for path in (ROOT / "scripts" / "install.sh", ROOT / "scripts" / "install.bat"):
        source = read(path)
        assert "BOOTSTRAP_PYTHON" in source, (
            f"{path.name} should hold its chosen interpreter in BOOTSTRAP_PYTHON, "
            "a name the launchers deliberately do not read."
        )
    assert "BOOTSTRAP_PYTHON" not in _launcher_env_vars()


def test_the_launchers_still_honour_the_override_they_document():
    # The mirror of the test above: an installer is only safe *because* the
    # launcher prefers WG2_PYTHON. If that stopped being true this whole suite
    # would be guarding nothing, and should be deleted rather than left green.
    for path in (LAUNCHER_BATCH, LAUNCHER_COMMAND):
        assert "WG2_PYTHON" in read(path)


def test_the_windows_installer_does_not_export_a_data_directory_override():
    """WG2_DATA_DIR decides where designs and job history live.

    An installer that set it would silently relocate the user's data for the
    launcher it starts, and only for that run -- so the app would come up empty
    once and be fine afterwards, which is close to the worst possible bug.
    """

    for path in INSTALLERS:
        assert not re.search(
            r'^\s*(set\s+"?WG2_DATA_DIR=|(export\s+)?WG2_DATA_DIR=)',
            read(path),
            re.IGNORECASE | re.MULTILINE,
        ), f"{path.name} must not set WG2_DATA_DIR"
