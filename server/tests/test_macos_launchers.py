from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MACOS = ROOT / "launchers" / "macos"
COMMAND = MACOS / "launch-wg.command"
APP_EXECUTABLE = (
    MACOS / "Waveguide Generator.app" / "Contents" / "MacOS" / "Waveguide Generator"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_finder_app_delegates_to_the_validated_command_launcher() -> None:
    source = _read(APP_EXECUTABLE)
    assert "launchers/macos/launch-wg.command" in source
    assert "exec \"$LAUNCHER\"" in source
    assert "-m launchers.statusapp" not in source
    assert "scripts/bootstrap.py" not in source
    assert "WG2_FINDER_APP=1" in source


def test_there_is_exactly_one_macos_launcher() -> None:
    """The dev variant is retired; its rebuild lives in the single launcher.

    Two launchers was the whole complaint: the normal one told the user to quit
    and run the other whenever the frontend was stale.
    """

    commands = sorted(path.name for path in MACOS.glob("*.command"))
    assert commands == ["launch-wg.command"]


def test_the_launcher_builds_stale_local_sources_without_changing_git() -> None:
    source = _read(COMMAND)
    assert "scripts/frontend_freshness.py" in source
    assert "run build" in source
    assert "--mark" in source, "a successful build must stamp the sources"
    assert "git pull" not in source
    assert "git fetch" not in source
    # The retired launcher must not be delegated to or advised. It may still be
    # named in a comment explaining where this build step came from, so only
    # executable lines are checked.
    code = [
        line for line in source.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not [line for line in code if "launch-wg-dev" in line]


def test_a_checkout_that_cannot_build_still_starts() -> None:
    """No Node, no node_modules, or a failed build must not cost the app.

    An installed copy has no frontend sources at all, and a user without Node
    should lose the rebuild, not the application.
    """

    source = _read(COMMAND)
    # Every branch that cannot build warns and returns rather than calling fail.
    build_section = source.split("build_frontend_if_stale() {", 1)[1].split("\n}", 1)[0]
    assert "fail " not in build_section, "the build path must never hard-fail the launcher"
    assert build_section.count("return 0") >= 4
    assert "WG2_SKIP_FRONTEND_BUILD" in source


def test_a_missing_frontend_build_is_named_rather_than_raised() -> None:
    """Without dist the server dies in starlette, several frames deep.

    "Directory '.../frontend/dist' does not exist" reaching a user as a Python
    traceback is exactly the "it can't start" this launcher exists to prevent.
    """

    source = _read(COMMAND)
    assert 'frontend/dist/index.html' in source
    guard = source.split('if [[ ! -f "$REPO_DIR/frontend/dist/index.html" ]]', 1)[1][:400]
    assert "fail " in guard
    assert "npm run build" in guard
    # The guard must sit after the rebuild attempt, or a checkout that could
    # have built itself would be turned away.
    assert source.index("build_frontend_if_stale\n\n") < source.index(
        'if [[ ! -f "$REPO_DIR/frontend/dist/index.html" ]]'
    )


def test_macos_launchers_are_executable() -> None:
    for path in (COMMAND, APP_EXECUTABLE):
        assert path.stat().st_mode & 0o111, f"{path.relative_to(ROOT)} is not executable"
