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
import time

import pytest

from launchers import apply_update as apply_update_module
from launchers import bundle_recovery
from launchers import update_lock
from launchers.apply_update import begin_update_transaction, plan_layer_swap
from scripts import build_bundle
from server.platform.paths import resolve_data_dir


REPOSITORY_ROOT = Path(bundle_recovery.__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _claims_live_in_a_temporary_home(tmp_path_factory, monkeypatch: pytest.MonkeyPatch):
    """Keep every claim in this file out of the developer's real cache root.

    The claim is deliberately rooted in the per-user cache directory rather than
    in a data directory, so redirecting that root is how a test isolates it --
    and it has to reach spawned processes too, which is why these go into the
    environment rather than only into a call argument.
    """

    root = tmp_path_factory.mktemp("claim-home")
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("XDG_CACHE_HOME", str(root / ".cache"))
    monkeypatch.setenv("LOCALAPPDATA", str(root / "AppData" / "Local"))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: root))
    return root


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


def _complete_installation(resources: Path) -> None:
    """Both layers, each with the manifest that says it is a layer."""

    (resources / "app").mkdir(parents=True, exist_ok=True)
    (resources / "app" / "APP-MANIFEST.json").write_text("{}", encoding="utf-8")
    (resources / "runtime").mkdir(parents=True, exist_ok=True)
    (resources / "runtime" / "RUNTIME-MANIFEST.json").write_text("{}", encoding="utf-8")


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
        _complete_installation(resources)
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
            _complete_installation(resources)

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
    _complete_installation(root)
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
    # The claim both sides take travels with the pair, not from the app layer.
    lock = recovery / "update_lock.py"
    assert lock.read_bytes() == (REPOSITORY_ROOT / "launchers" / "update_lock.py").read_bytes()
    assert manifest["lockSha256"] == bundle_recovery.file_sha256(lock)


def test_the_app_layer_keeps_its_site_hook_for_installations_that_predate_recovery(
    tmp_path: Path,
) -> None:
    """An old install can receive this app layer, and must still start.

    The recovery shim is what has to survive the app layer going away, so it
    lives outside it. But an installation made before ``recovery`` existed has a
    ``._pth`` that lists only ``app``, and it can still be handed this layer by
    an in-app update. Removing the copy here would take that installation's only
    site hook away and leave its double-click doing nothing at all. On a bundle
    that does have ``recovery``, that directory precedes ``app`` on the import
    path, so the shim wins and this copy is never imported.
    """

    app_root = tmp_path / "app"
    app_root.mkdir()

    build_bundle.write_windows_bootstrap(app_root)

    assert (app_root / "wg_desktop_bootstrap.py").is_file()
    assert (app_root / "sitecustomize.py").read_text(encoding="utf-8") == (
        "import wg_desktop_bootstrap\n"
    )
    lines = build_bundle.windows_pth().splitlines()
    assert lines.index("recovery") < lines.index("app")


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


# ---------------------------------------------------------------------------
# What counts as recovered, and who owns the update while it happens
# ---------------------------------------------------------------------------


def test_a_helper_that_restores_the_layer_and_still_fails_does_not_start_the_app(
    tmp_path: Path,
) -> None:
    """The negative control. A directory back in place is not a finished recovery.

    ``recover_transaction`` reports failure when it could not re-seal the
    bundle, and leaves the transaction open on purpose so the next start tries
    again. Reading the restored directory as success would launch a bundle
    whose signature still describes the generation that was replaced.
    """

    resources = tmp_path / "Resources"
    resources.mkdir()
    _recovery_directory(resources)
    said: list[str] = []

    def runner(command, **kwargs):
        _complete_installation(resources)
        return subprocess.CompletedProcess(command, 9)

    code = bundle_recovery.recover(
        resources=resources,
        platform_name="linux",
        runner=runner,
        attempts=1,
        delay=0.0,
        report=said.append,
    )

    assert code == 9
    assert any("did not finish (it exited 9)" in message for message in said)
    assert not any("was recovered" in message for message in said)


def test_a_helper_that_succeeds_on_an_incomplete_installation_is_not_believed(
    tmp_path: Path,
) -> None:
    """A zero exit and half an installation is still half an installation."""

    resources = tmp_path / "Resources"
    resources.mkdir()
    _recovery_directory(resources)
    said: list[str] = []

    def runner(command, **kwargs):
        # The app layer is back, but the runtime it needs is not.
        (resources / "app").mkdir()
        (resources / "app" / "APP-MANIFEST.json").write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    code = bundle_recovery.recover(
        resources=resources,
        platform_name="linux",
        runner=runner,
        attempts=1,
        delay=0.0,
        report=said.append,
    )

    assert code == bundle_recovery.EXIT_UNRECOVERED
    assert any("still incomplete" in message for message in said)


def test_the_bootstrap_reports_a_live_updater_rather_than_starting(tmp_path: Path) -> None:
    """The bootstrap does not hold the claim; it reads the helper's answer.

    A parent holding a lock while it waits for a child that must acquire the
    same lock is a deadlock, so the helper takes the claim and this maps its
    exit code. Failing closed is the point: an installation somebody else owns
    is left exactly as it is.
    """

    resources = tmp_path / "Resources"
    resources.mkdir()
    _recovery_directory(resources)
    said: list[str] = []

    code = bundle_recovery.recover(
        resources=resources,
        arguments=["--data-dir", str(tmp_path / "data")],
        platform_name="linux",
        runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command, update_lock.EXIT_UPDATE_IN_PROGRESS
        ),
        attempts=1,
        delay=0.0,
        report=said.append,
    )

    assert code == update_lock.EXIT_UPDATE_IN_PROGRESS
    assert any("still running" in message for message in said)
    assert not (resources / "app").exists()


def test_the_real_helper_declines_while_the_claim_is_held_and_moves_nothing(
    tmp_path: Path,
) -> None:
    """End to end, with the real CLI: held claim in, refusal out, nothing moved."""

    # Shaped for the host, so the helper's own ``resources_directory`` lands on
    # the installation this test claims. A mismatch there would look like a
    # passing test and be no exclusion at all.
    bundle = tmp_path / ("Waveguide Generator.app" if sys.platform == "darwin" else "wg")
    resources = bundle_recovery.resources_for_platform(bundle, sys.platform)
    resources.mkdir(parents=True)
    data_dir = tmp_path / "data"
    _interrupted_installation(resources, data_dir, sys.platform)
    _staged_recovery(resources)
    before = sorted(entry.name for entry in resources.iterdir())

    with update_lock.claim_update(resources):
        completed = subprocess.run(
            [
                sys.executable,
                str(resources / "recovery" / "apply_update.py"),
                "--recover",
                "--bundle",
                str(bundle),
                "--data-dir",
                str(data_dir),
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )

    assert completed.returncode == update_lock.EXIT_UPDATE_IN_PROGRESS, completed.stderr
    assert sorted(entry.name for entry in resources.iterdir()) == before
    assert "Recovery declined" in (data_dir / "logs" / "update.log").read_text(encoding="utf-8")


def test_the_real_updater_cli_declines_a_transaction_while_the_claim_is_held(
    tmp_path: Path,
) -> None:
    """The other half of the same exclusion, on the path that installs."""

    # Shaped for the host, so the CLI's own ``resources_directory`` lands on the
    # same installation this test claims. A mismatch there would look like a
    # passing test and be no exclusion at all.
    bundle = tmp_path / ("Waveguide Generator.app" if sys.platform == "darwin" else "wg")
    resources = bundle_recovery.resources_for_platform(bundle, sys.platform)
    resources.mkdir(parents=True)
    data_dir = tmp_path / "data"
    _interrupted_installation(resources, data_dir, sys.platform)
    before = sorted(entry.name for entry in resources.iterdir())
    # A pid that has certainly exited, so a claim that failed to engage would
    # fall straight through to the transaction instead of hanging on a wait.
    dead = subprocess.Popen([sys.executable, "-c", ""])
    dead.wait(timeout=30)

    with update_lock.claim_update(resources):
        code = apply_update_module.main(
            [
                "--bundle",
                str(bundle),
                "--data-dir",
                str(data_dir),
                "--parent-pid",
                str(dead.pid),
                "--rollback",
            ]
        )

    assert code == update_lock.EXIT_UPDATE_IN_PROGRESS
    assert sorted(entry.name for entry in resources.iterdir()) == before
    assert "Update declined" in (data_dir / "logs" / "update.log").read_text(encoding="utf-8")


def test_the_claim_is_exclusive_and_is_released_when_its_holder_dies(
    tmp_path: Path,
) -> None:
    """A killed updater must not leave an installation unrecoverable forever.

    That is why this is an OS lock on a descriptor and not a pid written into a
    file: the kernel drops it when the holder dies, which is exactly the case
    the whole recovery route exists for.
    """

    resources = tmp_path / "Resources"

    with update_lock.claim_update(resources):
        with pytest.raises(update_lock.UpdateInProgress):
            with update_lock.claim_update(resources):
                pytest.fail("the claim is not exclusive")

    # Released on exit, so the next caller gets it.
    with update_lock.claim_update(resources):
        pass

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            f"sys.path.insert(0, {str(REPOSITORY_ROOT)!r})\n"
            "from launchers.update_lock import claim_update\n"
            f"with claim_update({str(resources)!r}):\n"
            "    print('held', flush=True)\n"
            "    time.sleep(120)\n",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(update_lock.UpdateInProgress):
            with update_lock.claim_update(resources):
                pytest.fail("a live holder must exclude this process")
    finally:
        holder.kill()
        holder.wait(timeout=30)
    # The holder was killed, not asked to release. The claim is free anyway.
    with update_lock.claim_update(resources):
        pass


def test_one_installation_has_one_claim_whatever_data_directory_is_named(
    tmp_path: Path,
) -> None:
    """The hole a data-directory-rooted lock left, closed.

    ``--data-dir`` is the caller's choice, so a claim rooted there is one a
    second process steps around by naming a different directory -- while both
    processes rename the same installation's layers. The claim is keyed on the
    installation instead, so the data directory cannot separate two owners of
    one bundle, and two bundles that happen to share a data directory stay
    independent.
    """

    installation = tmp_path / "copy-one" / "Resources"
    other = tmp_path / "copy-two" / "Resources"

    # The lock key is the *physical* installation, not the spelling. The
    # journal's key is deliberately left alone -- changing it would rename the
    # records of every installation in flight -- so the two agree only where the
    # input is already resolved, which is the whole reason this one resolves.
    assert update_lock.installation_key(installation) == (
        apply_update_module.installation_key(installation.resolve())
    )
    # One installation, two data directories: one claim, and it is not under
    # either of them.
    for data_dir in (tmp_path / "data-a", tmp_path / "data-b"):
        assert not update_lock.lock_path(installation).is_relative_to(data_dir)
    assert update_lock.lock_path(installation) != update_lock.lock_path(other)

    with update_lock.claim_update(installation):
        with pytest.raises(update_lock.UpdateInProgress):
            with update_lock.claim_update(installation):
                pytest.fail("two data directories must not buy two claims")
        # A different installation is a different world, as its journal is.
        with update_lock.claim_update(other):
            pass


def test_two_data_directories_cannot_update_one_installation_at_once(
    tmp_path: Path,
) -> None:
    """The interprocess control for the same thing, across a real boundary.

    Two updater CLIs, one bundle, two ``--data-dir`` values. Before the claim
    moved out of the data directory these were two independent owners renaming
    the same layers.
    """

    bundle = tmp_path / ("Waveguide Generator.app" if sys.platform == "darwin" else "wg")
    resources = bundle_recovery.resources_for_platform(bundle, sys.platform)
    resources.mkdir(parents=True)
    first_data = tmp_path / "data-a"
    second_data = tmp_path / "data-b"
    _interrupted_installation(resources, first_data, sys.platform)
    before = sorted(entry.name for entry in resources.iterdir())

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            f"sys.path.insert(0, {str(REPOSITORY_ROOT)!r})\n"
            "from launchers.update_lock import claim_update\n"
            f"with claim_update({str(resources)!r}):\n"
            "    print('held', flush=True)\n"
            "    time.sleep(120)\n",
        ],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ},
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        # A second updater naming a different data directory for the same
        # installation is refused, and moves nothing.
        code = apply_update_module.main(
            [
                "--bundle",
                str(bundle),
                "--data-dir",
                str(second_data),
                "--parent-pid",
                str(holder.pid),
                "--rollback",
            ]
        )
    finally:
        holder.kill()
        holder.wait(timeout=30)

    assert code == update_lock.EXIT_UPDATE_IN_PROGRESS
    assert sorted(entry.name for entry in resources.iterdir()) == before
def _interrupted_runtime_swap(resources: Path, data_dir: Path, platform_name: str) -> None:
    """Killed inside the *runtime* layer's turn, not the app layer's.

    ``swap_staged_layers`` takes one layer at a time: rename aside, rename in.
    So the state where ``app`` is already the new generation and there is no
    ``runtime`` at all is reachable, and it is the one where a launcher that
    checks only for ``app`` execs an interpreter that is not there.
    """

    _desktop_layer(resources / "app", "app started")
    (resources / "runtime").mkdir(parents=True, exist_ok=True)
    (resources / "runtime" / "RUNTIME-MANIFEST.json").write_text(
        json.dumps({"schemaVersion": 1, "runtimeId": "rt-1"}), encoding="utf-8"
    )
    _interpreter_shim(resources / "runtime" / "bin" / "python3.13")
    staged = data_dir / "updates" / "9.9.9" / "staged"
    _desktop_layer(staged / "app", "new app started")
    (staged / "runtime").mkdir(parents=True)
    (staged / "runtime" / "RUNTIME-MANIFEST.json").write_text(
        json.dumps({"schemaVersion": 1, "runtimeId": "rt-2"}), encoding="utf-8"
    )
    _interpreter_shim(staged / "runtime" / "bin" / "python3.13")
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
    # The app layer's turn completed; the runtime's was killed between its two
    # renames.
    (resources / "app").rename(resources / "app.previous")
    shutil.move(str(staged / "app"), str(resources / "app"))
    (resources / "runtime").rename(resources / "runtime.previous")


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS launcher is a Mach-O binary")
def test_the_compiled_macos_launcher_recovers_a_missing_runtime_with_no_python_on_path(
    tmp_path: Path,
) -> None:
    """No app-layer problem at all, and no interpreter either -- and no PATH.

    The interpreter this needs is inside the layer that is missing, so the
    launcher falls back to ``runtime.previous``. ``PATH`` is emptied to make the
    point measurable rather than assumed: nothing here may be found by searching
    for it.
    """

    if shutil.which("clang") is None:
        pytest.skip("clang is required to build the launcher under test")

    binary = tmp_path / "launcher"
    subprocess.run(
        [
            "clang", "-O1", "-Wall", "-Wextra", "-Werror", "-o", str(binary),
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
    _interrupted_runtime_swap(resources, data_dir, "darwin")
    _staged_recovery(resources)
    shutil.copy2(binary, macos_dir / "Waveguide Generator")

    assert (resources / "app").is_dir()
    assert not (resources / "runtime").exists()
    assert (resources / "runtime.previous" / "bin" / "python3.13").is_file()

    completed = subprocess.run(
        [str(macos_dir / "Waveguide Generator"), "--no-gui", "--data-dir", str(data_dir)],
        capture_output=True,
        text=True,
        env={
            "HOME": os.environ.get("HOME", str(tmp_path)),
            "PATH": "",
            "WG2_DATA_DIR": str(data_dir),
        },
        timeout=180,
    )

    assert (resources / "runtime" / "bin" / "python3.13").is_file(), completed.stderr
    assert completed.returncode == 0, completed.stderr
    assert "app started" in completed.stdout
    assert (data_dir / "workspace" / "design.wg2").read_text(encoding="utf-8") == "a design"


def test_the_windows_bridge_starts_the_application_after_a_successful_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recovered start continues; it does not ask the user to open it again.

    The import system cached the failure to find the app layer while it really
    was absent, so the caches are dropped before the same path is tried.
    """

    root = tmp_path / "Waveguide Generator"
    root.mkdir(parents=True)
    executable = root / "Waveguide Generator.exe"
    executable.write_text("", encoding="utf-8")
    app = root / "app"
    monkeypatch.syspath_prepend(str(app))
    monkeypatch.delitem(sys.modules, "wg_desktop_bootstrap", raising=False)

    def recover(**_kwargs: object) -> int:
        _complete_installation(root)
        (app / "wg_desktop_bootstrap.py").write_text("started = True\n", encoding="utf-8")
        return bundle_recovery.EXIT_OK

    monkeypatch.setattr(bundle_recovery, "recover", recover)

    bundle_recovery.windows_boot(environ={}, argv=[""], executable=str(executable))

    assert sys.modules["wg_desktop_bootstrap"].started is True


def test_the_windows_bridge_does_not_start_anything_it_could_not_recover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "Waveguide Generator"
    root.mkdir(parents=True)
    executable = root / "Waveguide Generator.exe"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        bundle_recovery, "recover", lambda **_kwargs: bundle_recovery.EXIT_UPDATE_IN_PROGRESS
    )

    with pytest.raises(SystemExit) as exit_code:
        bundle_recovery.windows_boot(environ={}, argv=[""], executable=str(executable))

    assert exit_code.value.code == bundle_recovery.EXIT_UPDATE_IN_PROGRESS


@pytest.mark.skipif(sys.platform != "darwin", reason="the reseal that fails here is codesign")
def test_the_real_helper_restoring_layers_but_failing_to_reseal_is_not_a_start(
    tmp_path: Path,
) -> None:
    """The negative control against the real helper, not a stand-in.

    A bundle with no main executable cannot be signed, so ``recover_transaction``
    restores both layers and then reports failure -- deliberately leaving the
    transaction open so the next start tries again. The directories are back;
    the recovery is not finished. Starting here would launch a bundle whose seal
    describes the generation that was replaced.
    """

    bundle = tmp_path / "Waveguide Generator.app"
    resources = bundle / "Contents" / "Resources"
    resources.mkdir(parents=True)
    data_dir = tmp_path / "data"
    _interrupted_installation(resources, data_dir, "darwin")
    _interpreter_shim(resources / "runtime" / "bin" / "python3.13")
    _staged_recovery(resources)
    said: list[str] = []

    code = bundle_recovery.recover(
        resources=resources,
        arguments=["--data-dir", str(data_dir)],
        attempts=1,
        delay=0.0,
        report=said.append,
    )

    # The layers really are back -- which is exactly why the exit code has to be
    # the verdict rather than the file system.
    assert (resources / "app" / "APP-MANIFEST.json").is_file()
    assert code != bundle_recovery.EXIT_OK
    assert any("did not finish" in message for message in said)
    assert not any("was recovered" in message for message in said)
    log = (data_dir / "logs" / "update.log").read_text(encoding="utf-8")
    assert "could not be signed and verified" in log


# ---------------------------------------------------------------------------
# Which installations get this, and which keep starting without it
# ---------------------------------------------------------------------------


def _resolved_sitecustomize(paths: list[Path]) -> Path | None:
    """Which ``sitecustomize.py`` ``site`` would import, for a given path order."""

    import importlib.machinery

    finder = importlib.machinery.PathFinder()
    spec = finder.find_spec("sitecustomize", [str(path) for path in paths])
    return None if spec is None or spec.origin is None else Path(spec.origin)


def test_an_installation_that_predates_recovery_still_boots_on_a_new_app_layer(
    tmp_path: Path,
) -> None:
    """The upgrade seam. An old install can receive this app layer in-app.

    Its ``._pth`` lists only ``app``, and no in-app update rewrites that file:
    ``refresh_launcher_files`` transports only what the runtime manifest's
    ``launcherFiles`` names, which is the renamed pythonw and its DLLs. So the
    site hook this layer carries is the only one that installation has, and
    removing it would leave a double-click doing nothing at all.
    """

    root = tmp_path / "old-install"
    app = root / "app"
    app.mkdir(parents=True)
    build_bundle.write_windows_bootstrap(app)

    # The old ``._pth`` order: runtime entries, then app. No recovery entry.
    resolved = _resolved_sitecustomize([root / "runtime" / "Lib", app])

    assert resolved == app / "sitecustomize.py"
    assert resolved.read_text(encoding="utf-8") == "import wg_desktop_bootstrap\n"


def test_an_installation_that_has_recovery_uses_the_shim_and_not_the_app_copy(
    tmp_path: Path,
) -> None:
    """And a new install keeps using the shim as later app layers arrive.

    ``recovery`` precedes ``app`` in the generated ``._pth``, so the shim is
    what ``site`` finds; the app-layer copy is carried for the old installs
    above and is never imported here. The shim delegates to the app layer's own
    bootstrap, so the half that changes with the application still changes.
    """

    root = tmp_path / "new-install"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "RUNTIME-MANIFEST.json").write_text(
        json.dumps({"schemaVersion": 1, "runtimeId": "rt-1"}), encoding="utf-8"
    )
    app = root / "app"
    app.mkdir()
    build_bundle.write_windows_bootstrap(app)
    build_bundle.write_recovery_layer(
        root,
        repo_root=REPOSITORY_ROOT,
        runtime_root=runtime,
        platform_name=build_bundle.WINDOWS_PLATFORM,
    )

    order = [
        root / entry.replace("\\", "/")
        for entry in build_bundle.windows_pth().splitlines()
        if not entry.startswith("import ")
    ]
    resolved = _resolved_sitecustomize(order)

    assert resolved == root / "recovery" / "sitecustomize.py"
    assert "windows_boot()" in resolved.read_text(encoding="utf-8")
    # The delegation target still ships with the application, so a later app
    # layer replaces it in the ordinary way.
    assert (app / "wg_desktop_bootstrap.py").is_file()


def test_the_layer_archives_carry_no_recovery_directory(tmp_path: Path) -> None:
    """Stated as a test because it is the limit of what an in-app update can do.

    ``recovery`` sits beside the layers precisely so a layer swap cannot move
    it. The same fact is why an in-app update cannot deliver it to an
    installation that has none: that needs the installer.
    """

    app = tmp_path / "app"
    app.mkdir()
    build_bundle.write_windows_bootstrap(app)

    assert not (app / "recovery").exists()
    assert not (app / bundle_recovery.MANIFEST_NAME).exists()


# ---------------------------------------------------------------------------
# Every production path that decides a transaction, and none of them deadlocked
# ---------------------------------------------------------------------------


def test_the_detached_rollback_helper_is_staged_with_the_module_it_imports(
    tmp_path: Path,
) -> None:
    """A helper without its claim is a repair route that cannot start.

    The handoff used to copy one file. This module refuses to import without
    the claim -- deliberately, because the alternative was a silent no-op
    exactly where the detached rollback needs the exclusion -- so both travel,
    and the copy is then run to prove it.
    """

    from launchers.statusapp import updater as statusapp_updater

    bundle = tmp_path / ("Waveguide Generator.app" if sys.platform == "darwin" else "wg")
    resources = bundle_recovery.resources_for_platform(bundle, sys.platform)
    resources.mkdir(parents=True)
    (resources / "runtime" / "bin").mkdir(parents=True)
    _interpreter_shim(resources / "runtime" / "bin" / "python3.13")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    commands: list[list[str]] = []

    statusapp_updater.launch_rollback_handoff(
        bundle,
        data_dir,
        parent_pid=os.getpid(),
        environ={},
        platform_name=sys.platform,
        process_factory=lambda command, **kwargs: commands.append(list(command)),
    )

    staged = data_dir / "rollback"
    assert (staged / "apply_update.py").is_file()
    assert (staged / "update_lock.py").read_bytes() == (
        REPOSITORY_ROOT / "launchers" / "update_lock.py"
    ).read_bytes()
    # The copy runs. Importing the claim from beside itself is the whole point.
    probe = subprocess.run(
        [sys.executable, str(staged / "apply_update.py"), "--help"],
        capture_output=True,
        text=True,
        cwd=str(staged),
        timeout=120,
    )
    assert probe.returncode == 0, probe.stderr
    assert "--recover" in probe.stdout


def test_the_staged_manual_helper_is_also_staged_with_its_claim(tmp_path: Path) -> None:
    """The same contract on the other staging path, which had it too."""

    data_dir = tmp_path / "data"

    staged = apply_update_module.stage_recovery_helper(data_dir, bundle=tmp_path / "b")

    assert staged is not None
    assert (staged.parent / "update_lock.py").is_file()
    probe = subprocess.run(
        [sys.executable, str(staged), "--help"],
        capture_output=True,
        text=True,
        cwd=str(staged.parent),
        timeout=120,
    )
    assert probe.returncode == 0, probe.stderr


def test_a_staged_helper_missing_its_claim_refuses_to_run_rather_than_skip_it(
    tmp_path: Path,
) -> None:
    """The finding, asserted. A missing module is a staging bug, not a licence.

    The import used to fall back to ``nullcontext``, which turned "this copy was
    staged wrong" into "this copy silently has no exclusion" -- on the detached
    rollback, which renames layers.
    """

    staged = tmp_path / "rollback"
    staged.mkdir()
    shutil.copyfile(
        REPOSITORY_ROOT / "launchers" / "apply_update.py", staged / "apply_update.py"
    )

    probe = subprocess.run(
        [sys.executable, str(staged / "apply_update.py"), "--help"],
        capture_output=True,
        text=True,
        cwd=str(staged),
        timeout=120,
    )

    assert probe.returncode != 0
    assert "update_lock" in probe.stderr
    assert "nullcontext" not in (
        REPOSITORY_ROOT / "launchers" / "apply_update.py"
    ).read_text(encoding="utf-8")


def _bundle_for_startup(tmp_path: Path, *, app_generation: str, runtime_generation: str):
    """An installed bundle whose two layers declare the given generations."""

    bundle = tmp_path / ("Waveguide Generator.app" if sys.platform == "darwin" else "wg")
    resources = bundle_recovery.resources_for_platform(bundle, sys.platform)
    (resources / "app").mkdir(parents=True)
    (resources / "app" / "APP-MANIFEST.json").write_text(
        json.dumps({"schemaVersion": 1, "runtimeId": app_generation}), encoding="utf-8"
    )
    (resources / "runtime").mkdir(parents=True)
    (resources / "runtime" / "RUNTIME-MANIFEST.json").write_text(
        json.dumps({"schemaVersion": 1, "runtimeId": runtime_generation}), encoding="utf-8"
    )
    return bundle, resources


def _startup_exit_code(data_dir: Path, app_layer: Path, monkeypatch) -> int | None:
    """Run the real startup entry path and report whether it would start.

    ``launchers.statusapp.__main__._recover_interrupted_bundle_update`` is the
    one place every start mode passes through. It returns an exit code when the
    installation must not be started and ``None`` when it may be, so this is the
    actual "did a server start" answer rather than a proxy for it.
    """

    from launchers.statusapp import __main__ as statusapp_main

    monkeypatch.setenv("WG2_BUNDLE", "1")
    monkeypatch.setenv("WG2_APP_ROOT", str(app_layer))
    delivered: list[str] = []
    return statusapp_main._recover_interrupted_bundle_update(
        ["--data-dir", str(data_dir)], report=delivered.append
    ), delivered


def test_an_ordinary_start_before_the_first_rename_does_not_start_a_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interleaving the manifests cannot see.

    The updater holds the claim and has not renamed anything yet, so both layers
    are present and agree -- and a start that read that agreement would load code
    out of the very directories about to be renamed. Nothing authorized this
    start, so it does not happen.
    """

    _bundle, resources = _bundle_for_startup(
        tmp_path, app_generation="old-0", runtime_generation="old-0"
    )
    data_dir = tmp_path / "data"

    with update_lock.claim_update(resources):
        code, delivered = _startup_exit_code(data_dir, resources / "app", monkeypatch)

    assert code == 1, "an unauthorized start under a live claim must not start"
    assert delivered and "did not start" in delivered[0]
    assert "nothing authorized this start" in delivered[0].casefold()


def test_a_start_after_the_swap_but_before_the_reseal_does_not_start_a_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other agreeing window, on the far side of the renames.

    Both layers are the new generation and agree, and the bundle has not been
    resealed yet. Agreement is true here too, which is exactly why it cannot be
    the authorization.
    """

    _bundle, resources = _bundle_for_startup(
        tmp_path, app_generation="new-1", runtime_generation="new-1"
    )
    data_dir = tmp_path / "data"

    with update_lock.claim_update(resources):
        code, delivered = _startup_exit_code(data_dir, resources / "app", monkeypatch)

    assert code == 1
    assert delivered and "did not start" in delivered[0]


def test_a_start_over_a_mixed_generation_installation_does_not_start_a_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And the visibly half-swapped case, which was never allowed either."""

    _bundle, resources = _bundle_for_startup(
        tmp_path, app_generation="new-1", runtime_generation="old-0"
    )
    data_dir = tmp_path / "data"

    with update_lock.claim_update(resources):
        code, delivered = _startup_exit_code(data_dir, resources / "app", monkeypatch)

    assert code == 1
    assert delivered and "did not start" in delivered[0]


def test_a_claim_that_cannot_be_taken_at_all_does_not_start_a_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed. Ownership unknown and an installation that may be mid-change.

    The claim's directory is occupied by a file, so creating the lock raises --
    the real failure, not a patched one. An earlier revision answered this with
    "nothing to recover" and started anyway.
    """

    _bundle, resources = _bundle_for_startup(
        tmp_path, app_generation="same", runtime_generation="same"
    )
    data_dir = tmp_path / "data"
    blocked = update_lock.lock_path(resources)
    blocked.parent.parent.mkdir(parents=True, exist_ok=True)
    blocked.parent.write_text("not a directory", encoding="utf-8")

    code, delivered = _startup_exit_code(data_dir, resources / "app", monkeypatch)

    assert code == 1, "an installation whose claim cannot be taken must not start"
    assert delivered and "could not be taken" in delivered[0]


def test_the_updaters_own_authorized_relaunch_starts_without_deadlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half that must keep working, and what actually proves it.

    The grant is minted only after the swap and the reseal, so holding one is
    evidence of where the granting transaction had got to -- not an inference
    from state a writer is about to change. The claim is still held, because a
    relaunch that does not stay running is rolled back, and the start is prompt
    because acquisition never blocks.
    """

    _bundle, resources = _bundle_for_startup(
        tmp_path, app_generation="new-1", runtime_generation="new-1"
    )
    data_dir = tmp_path / "data"

    with update_lock.claim_update(resources):
        nonce = update_lock.grant_relaunch(resources)
        monkeypatch.setenv(update_lock.RELAUNCH_ENVIRONMENT_VARIABLE, nonce)
        started = time.monotonic()
        code, delivered = _startup_exit_code(data_dir, resources / "app", monkeypatch)
        elapsed = time.monotonic() - started

    assert code is None, "the updater's authorized relaunch must start"
    assert delivered == []
    assert elapsed < 5.0
    assert "authorized this relaunch" in (
        data_dir / "logs" / "update.log"
    ).read_text(encoding="utf-8")


def test_a_start_with_no_authorization_does_not_destroy_the_one_that_has_it(
    tmp_path: Path,
) -> None:
    """The ordinary extra launch, which must cost the updater's child nothing.

    A user opening the application while the updater is relaunching it presents
    no nonce. An earlier revision read and unlinked the grant before looking at
    what was presented, so that launch deleted the authorization its sibling was
    about to spend -- the child then refused to start, and the updater rolled a
    healthy update back because the relaunch "did not stay running".
    """

    _bundle, resources = _bundle_for_startup(
        tmp_path, app_generation="new-1", runtime_generation="new-1"
    )

    nonce = update_lock.grant_relaunch(resources)

    assert update_lock.consume_relaunch_grant(resources, None) is False
    assert update_lock.consume_relaunch_grant(resources, "") is False
    assert update_lock.consume_relaunch_grant(resources, "not-a-nonce") is False
    assert update_lock.consume_relaunch_grant(resources, "0" * 32) is False
    # None of that touched the real one.
    assert update_lock.consume_relaunch_grant(resources, nonce) is True


def test_a_grant_is_single_use_expiring_and_installation_bound(tmp_path: Path) -> None:
    _bundle, resources = _bundle_for_startup(
        tmp_path, app_generation="new-1", runtime_generation="new-1"
    )
    _other_bundle, other = _bundle_for_startup(
        tmp_path / "second", app_generation="new-1", runtime_generation="new-1"
    )

    nonce = update_lock.grant_relaunch(resources)
    assert update_lock.consume_relaunch_grant(resources, nonce) is True
    assert update_lock.consume_relaunch_grant(resources, nonce) is False

    stale = update_lock.grant_relaunch(resources)
    assert (
        update_lock.consume_relaunch_grant(
            resources, stale, now=time.time() + update_lock.GRANT_LIFETIME_SECONDS + 1.0
        )
        is False
    )

    # A grant for one installation says nothing about another, and spending it
    # there leaves that installation's own grant alone.
    theirs = update_lock.grant_relaunch(other)
    mine = update_lock.grant_relaunch(resources)
    assert update_lock.consume_relaunch_grant(resources, theirs) is False
    assert update_lock.consume_relaunch_grant(other, theirs) is True
    assert update_lock.consume_relaunch_grant(resources, mine) is True


def test_only_one_of_several_simultaneous_consumers_spends_a_grant(
    tmp_path: Path,
) -> None:
    """The one-shot claim, raced by real processes rather than argued about.

    Consumption is a rename, so the loser's rename finds nothing there. A read
    followed by an unlink would let several readers see the same payload and
    every one of them answer yes, which is not a one-shot grant. Repeated
    because a race proven once is a race that happened to interleave once: each
    round mints a fresh grant, parks every consumer on the same barrier, and
    releases them together.
    """

    _bundle, resources = _bundle_for_startup(
        tmp_path, app_generation="new-1", runtime_generation="new-1"
    )
    consumers = 6
    rounds = 4

    for round_number in range(rounds):
        nonce = update_lock.grant_relaunch(resources)
        go = tmp_path / f"go-{round_number}"
        program = (
            "import os, sys, time\n"
            f"sys.path.insert(0, {str(REPOSITORY_ROOT)!r})\n"
            "from launchers.update_lock import consume_relaunch_grant\n"
            "print('ready', flush=True)\n"
            f"go = {str(go)!r}\n"
            "while not os.path.exists(go):\n"
            "    pass\n"
            f"print(consume_relaunch_grant({str(resources)!r}, {nonce!r}), flush=True)\n"
        )
        started = [
            subprocess.Popen(
                [sys.executable, "-c", program],
                stdout=subprocess.PIPE,
                text=True,
                env={**os.environ},
            )
            for _ in range(consumers)
        ]
        try:
            # Every consumer has imported and is spinning on the barrier, so the
            # release lands on all of them at once rather than on whichever
            # interpreter finished starting first.
            for process in started:
                assert process.stdout is not None
                assert process.stdout.readline().strip() == "ready"
            go.write_text("go", encoding="utf-8")
            answers = [
                process.communicate(timeout=120)[0].strip() for process in started
            ]
        finally:
            for process in started:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=30)

        assert sorted(answers) == ["False"] * (consumers - 1) + ["True"], (
            f"round {round_number}: {answers}"
        )


def test_the_in_app_startup_recovery_still_decides_when_nobody_owns_the_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordinary start, with the app layer present and no updater running."""

    from launchers.statusapp import updater as statusapp_updater

    bundle = tmp_path / ("Waveguide Generator.app" if sys.platform == "darwin" else "wg")
    resources = bundle_recovery.resources_for_platform(bundle, sys.platform)
    app_layer = resources / "app"
    app_layer.mkdir(parents=True)
    data_dir = tmp_path / "data"
    seen: list[dict[str, object]] = []

    def recover_transaction(**kwargs: object):
        seen.append(kwargs)
        # The claim is held for the duration, and only for the duration.
        with pytest.raises(update_lock.UpdateInProgress):
            with update_lock.claim_update(resources):
                pytest.fail("the startup recovery must hold the claim while deciding")
        return statusapp_updater.RecoveryOutcome("none", "nothing to do")

    monkeypatch.setattr(statusapp_updater, "recover_transaction", recover_transaction)

    outcome = statusapp_updater.recover_interrupted_bundle_update(
        ["--data-dir", str(data_dir)],
        environ={"WG2_BUNDLE": "1", "WG2_APP_ROOT": str(app_layer)},
        platform_name=sys.platform,
    )

    assert outcome is not None and outcome.action == "none"
    assert seen and seen[0]["resources"] == resources
    # Released afterwards, so an updater started next is not locked out.
    with update_lock.claim_update(resources):
        pass


def test_one_installation_spelled_three_ways_is_one_claim(tmp_path: Path) -> None:
    """A relative path, a symlink and an absolute path are one installation.

    The CLI takes ``--bundle`` as a raw path and claims before anything resolves
    it, so a key that only normalises maps three spellings of one bundle to
    three "exclusive" claims on one set of directories.
    """

    real = tmp_path / "real" / "Resources"
    real.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "real", target_is_directory=True)

    spellings = [real, Path(link) / "Resources", Path(os.path.relpath(real, tmp_path))]
    keys = set()
    for spelling in spellings:
        previous = Path.cwd()
        os.chdir(tmp_path)
        try:
            keys.add(update_lock.installation_key(spelling))
        finally:
            os.chdir(previous)
    assert len(keys) == 1, f"three spellings of one installation gave {keys}"


def test_an_alias_spelling_cannot_take_a_second_claim_on_one_installation(
    tmp_path: Path,
) -> None:
    """The interprocess control for that, through the real CLI.

    A live holder claims the installation by its real path; a second updater
    names the same bundle through a symlink. Before the key resolved, that was
    two owners renaming one set of directories.
    """

    real_root = tmp_path / "real"
    bundle = real_root / ("Waveguide Generator.app" if sys.platform == "darwin" else "wg")
    resources = bundle_recovery.resources_for_platform(bundle, sys.platform)
    resources.mkdir(parents=True)
    data_dir = tmp_path / "data"
    _interrupted_installation(resources, data_dir, sys.platform)
    before = sorted(entry.name for entry in resources.iterdir())
    link = tmp_path / "link"
    link.symlink_to(real_root, target_is_directory=True)
    aliased_bundle = link / bundle.name

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            f"sys.path.insert(0, {str(REPOSITORY_ROOT)!r})\n"
            "from launchers.update_lock import claim_update\n"
            f"with claim_update({str(resources)!r}):\n"
            "    print('held', flush=True)\n"
            "    time.sleep(120)\n",
        ],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ},
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        code = apply_update_module.main(
            [
                "--bundle",
                str(aliased_bundle),
                "--data-dir",
                str(data_dir),
                "--parent-pid",
                str(holder.pid),
                "--rollback",
            ]
        )
    finally:
        holder.kill()
        holder.wait(timeout=30)

    assert code == update_lock.EXIT_UPDATE_IN_PROGRESS
    assert sorted(entry.name for entry in resources.iterdir()) == before
