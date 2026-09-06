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
from launchers import update_lock
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


def test_a_live_updater_holding_the_claim_stops_recovery_dead(tmp_path: Path) -> None:
    """The interlock the dwell is not.

    An updater slower than any wait is still mid-swap, so the wait cannot be the
    guard. This takes the same exclusive claim the updater CLI takes and refuses
    when it cannot get it -- changing nothing, and saying so.
    """

    resources = tmp_path / "Resources"
    resources.mkdir()
    _recovery_directory(resources)
    data_dir = tmp_path / "data"
    said: list[str] = []

    with update_lock.claim_update(data_dir):
        code = bundle_recovery.recover(
            resources=resources,
            arguments=["--data-dir", str(data_dir)],
            platform_name="linux",
            runner=lambda *args, **kwargs: pytest.fail("nothing may run under the claim"),
            attempts=1,
            delay=0.0,
            report=said.append,
        )

    assert code == bundle_recovery.EXIT_UPDATE_IN_PROGRESS
    assert any("still running" in message for message in said)
    # Nothing was moved: the installation is exactly as the updater left it.
    assert not (resources / "app").exists()


def test_the_claim_is_exclusive_and_is_released_when_its_holder_dies(
    tmp_path: Path,
) -> None:
    """A killed updater must not leave an installation unrecoverable forever.

    That is why this is an OS lock on a descriptor and not a pid written into a
    file: the kernel drops it when the holder dies, which is exactly the case
    the whole recovery route exists for.
    """

    data_dir = tmp_path / "data"

    with update_lock.claim_update(data_dir):
        with pytest.raises(update_lock.UpdateInProgress):
            with update_lock.claim_update(data_dir):
                pytest.fail("the claim is not exclusive")

    # Released on exit, so the next caller gets it.
    with update_lock.claim_update(data_dir):
        pass

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            f"sys.path.insert(0, {str(REPOSITORY_ROOT)!r})\n"
            "from launchers.update_lock import claim_update\n"
            f"with claim_update({str(data_dir)!r}):\n"
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
            with update_lock.claim_update(data_dir):
                pytest.fail("a live holder must exclude this process")
    finally:
        holder.kill()
        holder.wait(timeout=30)
    # The holder was killed, not asked to release. The claim is free anyway.
    with update_lock.claim_update(data_dir):
        pass


def test_the_updater_cli_takes_the_same_claim_for_a_real_transaction() -> None:
    """Both sides have to take the same lock or neither is excluded.

    Asserted on the CLI entry rather than by driving a swap, because that is the
    single place every real transaction passes through and the property under
    test is that it is taken at all.
    """

    source = (REPOSITORY_ROOT / "launchers" / "apply_update.py").read_text(encoding="utf-8")

    assert "with _claim_update(args.data_dir):" in source
    assert "return _run_transaction(args)" in source
    # Every argument refusal happens before the claim, so an unusable command
    # line still creates nothing on disk.
    assert source.index('parser.error("--staged-app-dir is required') < source.index(
        "with _claim_update(args.data_dir):"
    )
    # --recover must not take it: the bootstrap holds it across that subprocess.
    recover_branch = source.index("if args.recover:")
    claim_line = source.index("with _claim_update(args.data_dir):")
    assert recover_branch < claim_line
    assert "return recover_bundle(" in source[recover_branch:claim_line]


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
