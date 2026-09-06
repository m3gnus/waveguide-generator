"""Reaching recovery when the app layer is the directory that is missing.

The swap's last window -- killed between ``app`` -> ``app.previous`` and
``staged`` -> ``app`` -- used to be unrecoverable automatically on every
platform, because every launcher reached recovery *through* the app layer.
These tests drive the route that does not: the staged ``recovery`` directory
beside the layers, the real compiled macOS launcher, and the generated Linux
launcher script, each against a fabricated installation whose app layer has
been renamed aside exactly as an interrupted update leaves it.

**What is proven here and what is not.** The launcher tests run the real
entry-point binary and the real generated script, and the recovery they invoke
is the real ``apply_update.py`` reading a real journal. That is a proof of the
*entry path*: with no app layer, the launcher still reaches recovery, and the
installation is usable afterwards. It is not a proof of power-loss durability,
and it is not a proof about Windows: the Windows bridge is exercised as the
Python it is, not as a double-click on a renamed ``pythonw.exe``, which needs a
Windows machine this repository has never had.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from launchers import bundle_recovery
from launchers.apply_update import begin_update_transaction, plan_layer_swap
from scripts import build_bundle
from server.platform.paths import resolve_data_dir


REPOSITORY_ROOT = Path(bundle_recovery.__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# The data directory, resolved without the module that normally resolves it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "system, environ",
    [
        ("Darwin", {}),
        ("Linux", {}),
        ("Linux", {"XDG_DATA_HOME": "/xdg/data"}),
        ("Windows", {"APPDATA": "/appdata"}),
        ("Darwin", {"WG2_DATA_DIR": "/elsewhere"}),
    ],
)
def test_the_recovery_copy_of_the_data_directory_rule_agrees_with_the_app_layers(
    system: str, environ: dict[str, str], tmp_path: Path
) -> None:
    """A second implementation is only safe while it cannot drift.

    ``server.platform.paths`` lives in the app layer, which is missing in the
    state this module exists for, so the rule is written out again here. This
    is what keeps the copy honest.
    """

    assert bundle_recovery.resolve_data_dir(
        system=system, environ=environ, home=tmp_path
    ) == resolve_data_dir(system=system, environ=environ, home=tmp_path)


def test_an_explicit_data_directory_is_read_out_of_the_launcher_arguments() -> None:
    assert bundle_recovery.data_dir_override(["--no-gui"]) is None
    assert bundle_recovery.data_dir_override(["--data-dir", "/here"]) == "/here"
    assert bundle_recovery.data_dir_override(["--data-dir=/here"]) == "/here"
    # A trailing flag with no value names nothing, and must not read past the end.
    assert bundle_recovery.data_dir_override(["--data-dir"]) is None


# ---------------------------------------------------------------------------
# What it will and will not run
# ---------------------------------------------------------------------------


def _recovery_directory(root: Path, *, helper_body: str = "print('helper')\n") -> Path:
    recovery = root / "recovery"
    recovery.mkdir(parents=True)
    helper = recovery / bundle_recovery.HELPER_NAME
    helper.write_text(helper_body, encoding="utf-8")
    (recovery / bundle_recovery.MANIFEST_NAME).write_text(
        json.dumps({"schemaVersion": 1, "helperSha256": bundle_recovery.file_sha256(helper)}),
        encoding="utf-8",
    )
    return recovery


def test_a_substituted_or_truncated_helper_is_refused_rather_than_run(tmp_path: Path) -> None:
    """The digest is the reason this may run a program at all."""

    recovery = _recovery_directory(tmp_path)
    assert bundle_recovery.verified_helper(recovery).name == bundle_recovery.HELPER_NAME

    (recovery / bundle_recovery.HELPER_NAME).write_text("import os\n", encoding="utf-8")
    with pytest.raises(bundle_recovery.RecoveryUnavailable, match="does not match the digest"):
        bundle_recovery.verified_helper(recovery)

    (recovery / bundle_recovery.HELPER_NAME).unlink()
    with pytest.raises(bundle_recovery.RecoveryUnavailable, match="helper is missing"):
        bundle_recovery.verified_helper(recovery)

    (recovery / bundle_recovery.MANIFEST_NAME).write_text("{}", encoding="utf-8")
    with pytest.raises(bundle_recovery.RecoveryUnavailable, match="records no helper digest"):
        bundle_recovery.verified_helper(recovery)


def test_recovery_runs_the_staged_helper_with_the_running_interpreter_only(
    tmp_path: Path,
) -> None:
    """Nothing from PATH, nothing from the data directory, nothing from the journal."""

    resources = tmp_path / "Resources"
    resources.mkdir()
    recovery = _recovery_directory(resources)
    commands: list[list[str]] = []

    def runner(command, **kwargs):
        commands.append(list(command))
        (resources / "app").mkdir()
        return subprocess.CompletedProcess(command, 0)

    code = bundle_recovery.recover(
        resources=resources,
        arguments=["--no-gui", "--data-dir", str(tmp_path / "data")],
        platform_name="linux",
        runner=runner,
        interpreter="/bundle/runtime/bin/python3.13",
        attempts=1,
        delay=0.0,
        report=lambda _message: None,
    )

    assert code == bundle_recovery.EXIT_OK
    assert commands == [
        [
            "/bundle/runtime/bin/python3.13",
            str(recovery / bundle_recovery.HELPER_NAME),
            "--recover",
            "--bundle",
            str(resources),
            "--data-dir",
            str(tmp_path / "data"),
        ]
    ]


def test_a_live_update_is_waited_for_rather_than_raced(tmp_path: Path) -> None:
    """A missing app layer is what an update in progress looks like from outside.

    The dwell is the whole interlock: an updater mid-swap puts the layer back
    within it, and nothing is run. Only a layer that is still missing at the end
    is treated as interrupted.
    """

    resources = tmp_path / "Resources"
    resources.mkdir()
    _recovery_directory(resources)
    ran: list[object] = []
    slept: list[float] = []

    def sleep(delay: float) -> None:
        slept.append(delay)
        if len(slept) == 2:
            (resources / "app").mkdir()

    code = bundle_recovery.recover(
        resources=resources,
        runner=lambda *args, **kwargs: ran.append(args),
        attempts=6,
        delay=0.01,
        sleep=sleep,
        report=lambda _message: None,
    )

    assert code == bundle_recovery.EXIT_OK
    assert ran == []
    assert len(slept) == 2


def test_a_missing_recovery_directory_says_so_and_runs_nothing(tmp_path: Path) -> None:
    resources = tmp_path / "Resources"
    resources.mkdir()
    said: list[str] = []

    code = bundle_recovery.recover(
        resources=resources,
        runner=lambda *args, **kwargs: pytest.fail("nothing may be run"),
        attempts=1,
        delay=0.0,
        report=said.append,
    )

    assert code == bundle_recovery.EXIT_NO_HELPER
    assert any("recovery manifest" in message for message in said)


# ---------------------------------------------------------------------------
# The Windows bridge, as the Python it is
# ---------------------------------------------------------------------------


def test_the_windows_bridge_hands_back_to_the_app_layer_when_it_is_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The frozen shim owns nothing but the decision.

    Everything that changes with the application still lives in the app layer
    and still updates with it; this only chooses whether that layer can be
    reached at all.
    """

    root = tmp_path / "Waveguide Generator"
    (root / "app").mkdir(parents=True)
    (root / "app" / "wg_desktop_bootstrap.py").write_text(
        "started = True\n", encoding="utf-8"
    )
    executable = root / "Waveguide Generator.exe"
    executable.write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(root / "app"))

    bundle_recovery.windows_boot(
        environ={}, argv=[""], executable=str(executable)
    )

    assert sys.modules["wg_desktop_bootstrap"].started is True


def test_the_windows_bridge_recovers_when_the_app_layer_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "Waveguide Generator"
    root.mkdir(parents=True)
    executable = root / "Waveguide Generator.exe"
    executable.write_text("", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def recover(**kwargs: object) -> int:
        calls.append(kwargs)
        return 7

    monkeypatch.setattr(bundle_recovery, "recover", recover)
    with pytest.raises(SystemExit) as exit_code:
        bundle_recovery.windows_boot(
            environ={}, argv=["", "--no-gui"], executable=str(executable)
        )

    assert exit_code.value.code == 7
    assert calls[0]["resources"] == root.resolve()
    assert calls[0]["arguments"] == ["--no-gui"]


def test_the_windows_bridge_leaves_worker_subprocesses_alone(tmp_path: Path) -> None:
    """The same executable is deliberately usable as ``sys.executable``.

    A worker is started with ``-c`` or ``-m`` and never with an empty argv[0],
    which is the only thing that means "somebody double-clicked this".
    """

    executable = tmp_path / "Waveguide Generator.exe"
    executable.write_text("", encoding="utf-8")

    for argv in ([], ["-c"], ["script.py"], ["-m"]):
        bundle_recovery.windows_boot(
            environ={}, argv=argv, executable=str(executable)
        )
    bundle_recovery.windows_boot(
        environ={}, argv=[""], executable=str(tmp_path / "python.exe")
    )


# ---------------------------------------------------------------------------
# What the builder stages
# ---------------------------------------------------------------------------


def test_the_windows_import_path_reaches_recovery_before_the_app_layer() -> None:
    """The site hook has to be findable in the state where ``app`` is missing."""

    lines = build_bundle.windows_pth().splitlines()

    assert "recovery" in lines and "app" in lines
    assert lines.index("recovery") < lines.index("app")
    assert lines[-1] == "import site"


def test_the_builder_stages_a_verified_recovery_route_outside_both_layers(
    tmp_path: Path,
) -> None:
    resources = tmp_path / "Resources"
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True)
    (runtime_root / "RUNTIME-MANIFEST.json").write_text(
        json.dumps({"schemaVersion": 1, "runtimeId": "rt-1"}), encoding="utf-8"
    )
    resources.mkdir()

    recovery = build_bundle.write_recovery_layer(
        resources,
        repo_root=REPOSITORY_ROOT,
        runtime_root=runtime_root,
        platform_name=build_bundle.WINDOWS_PLATFORM,
    )

    assert recovery == resources / "recovery"
    helper = recovery / "apply_update.py"
    entry = recovery / "wg_bundle_recovery.py"
    # Byte copies: the recovery that runs from here is the one the updater's
    # own tests exercise in place.
    assert helper.read_bytes() == (REPOSITORY_ROOT / "launchers" / "apply_update.py").read_bytes()
    assert entry.read_bytes() == (REPOSITORY_ROOT / "launchers" / "bundle_recovery.py").read_bytes()
    manifest = json.loads((recovery / "RECOVERY-MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["runtimeId"] == "rt-1"
    assert manifest["helperSha256"] == bundle_recovery.file_sha256(helper)
    # The verifier the entry uses accepts exactly what the builder wrote.
    assert bundle_recovery.verified_helper(recovery) == helper
    # Windows alone needs the site hook, because it is the only platform whose
    # launcher is an interpreter rather than a program of ours.
    assert (recovery / "sitecustomize.py").is_file()
    assert "wg_bundle_recovery" in (recovery / "sitecustomize.py").read_text(encoding="utf-8")


def test_the_app_layer_no_longer_owns_the_windows_site_hook(tmp_path: Path) -> None:
    """It cannot: the site hook is what has to survive the app layer going away."""

    app_root = tmp_path / "app"
    app_root.mkdir()

    build_bundle.write_windows_bootstrap(app_root)

    assert (app_root / "wg_desktop_bootstrap.py").is_file()
    assert not (app_root / "sitecustomize.py").exists()


# ---------------------------------------------------------------------------
# The real entry points, against a real interrupted installation
# ---------------------------------------------------------------------------


def _interpreter_shim(path: Path) -> None:
    """A bundle interpreter that is really this one, reachable at a bundle path.

    The launchers exec ``<resources>/runtime/bin/python3.13`` by absolute path,
    which is the property under test. Copying a whole runtime into a temporary
    directory would prove nothing extra and cost seconds per case.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8"
    )
    path.chmod(0o755)


def _desktop_layer(app_root: Path, marker: str) -> None:
    """An app layer whose ``launchers.desktop`` says it ran, and exits."""

    package = app_root / "launchers"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "desktop.py").write_text(
        f"import sys\nprint({marker!r})\nsys.exit(0)\n", encoding="utf-8"
    )
    (app_root / "APP-MANIFEST.json").write_text(
        json.dumps({"schemaVersion": 1, "version": "9.9.9", "runtimeId": "rt-1"}),
        encoding="utf-8",
    )


def _interrupted_installation(resources: Path, data_dir: Path, platform_name: str) -> None:
    """The state a kill between the first two renames leaves behind.

    Built with the updater's own ``plan_layer_swap`` and
    ``begin_update_transaction`` so the journal is the real record, then the
    first rename is performed and nothing else -- which is exactly where a
    killed process stops.
    """

    _desktop_layer(resources / "app", "old app started")
    (resources / "runtime").mkdir(parents=True, exist_ok=True)
    (resources / "runtime" / "RUNTIME-MANIFEST.json").write_text(
        json.dumps({"schemaVersion": 1, "runtimeId": "rt-1"}), encoding="utf-8"
    )
    staged = data_dir / "updates" / "9.9.9" / "staged"
    _desktop_layer(staged / "app", "new app started")
    (staged / "runtime").mkdir(parents=True)
    (staged / "runtime" / "RUNTIME-MANIFEST.json").write_text(
        json.dumps({"schemaVersion": 1, "runtimeId": "rt-2"}), encoding="utf-8"
    )
    (data_dir / "workspace").mkdir(parents=True)
    (data_dir / "workspace" / "design.wg2").write_text("a design", encoding="utf-8")

    bundle = bundle_recovery.bundle_from_resources(resources, platform_name)
    begin_update_transaction(
        data_dir=data_dir,
        bundle=bundle,
        resources=resources,
        layers=plan_layer_swap(resources, staged / "app", staged / "runtime"),
        platform_name=platform_name,
    )
    # The kill point: the first rename happened, the second never did.
    (resources / "app").rename(resources / "app.previous")


def _staged_recovery(resources: Path) -> None:
    build_bundle.write_recovery_layer(
        resources,
        repo_root=REPOSITORY_ROOT,
        runtime_root=resources / "runtime",
        platform_name=build_bundle.LINUX_PLATFORM,
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS launcher is a Mach-O binary")
def test_the_compiled_macos_launcher_recovers_an_installation_with_no_app_layer(
    tmp_path: Path,
) -> None:
    """The real binary, the real journal, the real helper. No app layer at all.

    This is the window the plan called unrecoverable on every platform: killed
    between ``app`` -> ``app.previous`` and ``staged`` -> ``app``. The launcher
    used to ``chdir`` into a directory that was not there and stop.
    """

    if shutil.which("clang") is None:
        pytest.skip("clang is required to build the launcher under test")

    binary = tmp_path / "launcher"
    subprocess.run(
        [
            "clang",
            "-O1",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-o",
            str(binary),
            str(REPOSITORY_ROOT / "launchers" / "macos" / "launcher.c"),
        ],
        check=True,
    )

    bundle = tmp_path / "Waveguide Generator.app"
    resources = bundle / "Contents" / "Resources"
    macos_dir = bundle / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True)
    resources.mkdir(parents=True)
    data_dir = tmp_path / "data"
    _interrupted_installation(resources, data_dir, "darwin")
    _interpreter_shim(resources / "runtime" / "bin" / "python3.13")
    _staged_recovery(resources)
    shutil.copy2(binary, macos_dir / "Waveguide Generator")

    assert not (resources / "app").exists()

    completed = subprocess.run(
        [str(macos_dir / "Waveguide Generator"), "--no-gui", "--data-dir", str(data_dir)],
        capture_output=True,
        text=True,
        env={**os.environ, "WG2_DATA_DIR": str(data_dir)},
        timeout=180,
    )

    assert (resources / "app").is_dir(), completed.stderr
    assert completed.returncode == 0, completed.stderr
    # It did not stop at recovery: the start continued into the application.
    assert "app started" in completed.stdout
    # User data is untouched by any of it.
    assert (data_dir / "workspace" / "design.wg2").read_text(encoding="utf-8") == "a design"


@pytest.mark.skipif(sys.platform == "win32", reason="the Linux launcher is a POSIX shell script")
def test_the_generated_linux_launcher_reaches_recovery_and_then_the_application(
    tmp_path: Path,
) -> None:
    """The generated script, with a recorder standing in for the entry.

    The entry itself is exercised against the real helper by the macOS case
    above. What this proves is the part only the script can be wrong about:
    that a missing app layer reaches the recovery entry at all, with the
    bundle's own interpreter and the caller's arguments, and that the start
    continues into the application when the layer comes back.
    """

    resources = tmp_path / "waveguide-generator"
    resources.mkdir()
    _desktop_layer(resources / "app", "app started")
    _interpreter_shim(resources / "runtime" / "bin" / "python3.13")
    recovery = resources / "recovery"
    recovery.mkdir()
    record = tmp_path / "invocation.json"
    (recovery / "wg_bundle_recovery.py").write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(record)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        # What a rollback does: the previous layer goes back where it was.
        f"pathlib.Path({str(resources / 'app.previous')!r})"
        f".rename({str(resources / 'app')!r})\n",
        encoding="utf-8",
    )
    launcher = resources / "waveguide-generator"
    launcher.write_text(build_bundle.linux_launcher(), encoding="utf-8", newline="\n")
    launcher.chmod(0o755)

    # The kill point, as the macOS case builds it: no app layer.
    shutil.move(str(resources / "app"), str(resources / "app.previous"))
    shutil.copytree(resources / "app.previous", resources / "app.staged")

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/sh", str(launcher), "--no-gui"],
            capture_output=True,
            text=True,
            timeout=180,
        )

    completed = run()

    assert record.is_file(), completed.stderr
    invocation = json.loads(record.read_text(encoding="utf-8"))
    assert invocation[:2] == ["--resources", str(resources)]
    assert "--no-gui" in invocation
    assert completed.returncode == 0, completed.stderr
    assert "app started" in completed.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="the Linux launcher is a POSIX shell script")
def test_the_generated_linux_launcher_still_refuses_what_recovery_cannot_fix(
    tmp_path: Path,
) -> None:
    """Recovery that cannot run leaves the old refusal exactly where it was."""

    resources = tmp_path / "waveguide-generator"
    resources.mkdir()
    _interpreter_shim(resources / "runtime" / "bin" / "python3.13")
    _staged_recovery(resources)
    launcher = resources / "waveguide-generator"
    launcher.write_text(build_bundle.linux_launcher(), encoding="utf-8", newline="\n")
    launcher.chmod(0o755)

    completed = subprocess.run(
        ["/bin/sh", str(launcher), "--no-gui"],
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "WG2_DATA_DIR": str(tmp_path / "data")},
    )

    assert completed.returncode == 71
    assert "installation at" in completed.stderr
    assert "Reinstall it by running install.sh" in completed.stderr
