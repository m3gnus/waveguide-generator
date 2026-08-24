"""What the installers must keep doing, ported from v1's installer contract suite.

v1 carries these as `tests/installer-contract.test.js` and
`tests/installer-env-contract.test.js`. Almost every assertion there is a
tombstone for a real failure on somebody's machine, and none of those causes
went away when v2 was written -- cmd.exe still parses blocks before it runs
them, `git pull` still rewrites the script that is executing it, and Windows
still ships a Store stub called `python.exe`. So they are ported rather than
copied: v2's layout differs, its installer has no Node half at all, and two of
v1's assertions are about machinery v2 does not have.

Where a v1 test had no v2 counterpart it was replaced by the v2 invariant that
protects the same property, not dropped. The mapping is in each test's comment.

These are static checks on installer *text*. They are cheap, they run on every
platform including the one that cannot execute the other installer, and they
are the only automated coverage that notices an installer regression at all.
Behaviour that can be executed is tested in `test_fetch_spa.py`.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]

SHELL_INSTALLER = ROOT / "scripts" / "install.sh"
BATCH_INSTALLER = ROOT / "scripts" / "install.bat"
BATCH_ENTRY_POINT = ROOT / "installers" / "windows" / "install-and-update.bat"
SHELL_ENTRY_POINT = ROOT / "installers" / "macos" / "install-wg.command"
SHELL_UNINSTALLER = ROOT / "scripts" / "uninstall.sh"
BATCH_UNINSTALLER = ROOT / "scripts" / "uninstall.bat"
PUBLIC_SHELL_UNINSTALLERS = (
    ROOT / "installers" / "macos" / "uninstall.sh",
    ROOT / "installers" / "linux" / "uninstall.sh",
)
PUBLIC_BATCH_UNINSTALLER = ROOT / "installers" / "windows" / "uninstall.bat"
LINUX_INSTALL_ENTRY = ROOT / "installers" / "linux" / "install.sh"
LAUNCHER_COMMAND = ROOT / "launchers" / "macos" / "launch-wg.command"
LAUNCHER_BATCH = ROOT / "launchers" / "windows" / "launch-wg.bat"
LAUNCHER_LINUX = ROOT / "launchers" / "linux" / "launch-wg.sh"
MACOS_APP_EXECUTABLE = (
    ROOT
    / "launchers"
    / "macos"
    / "Waveguide Generator.app"
    / "Contents"
    / "MacOS"
    / "Waveguide Generator"
)

ALL_BATCH_FILES = (
    BATCH_INSTALLER,
    BATCH_ENTRY_POINT,
    BATCH_UNINSTALLER,
    PUBLIC_BATCH_UNINSTALLER,
    LAUNCHER_BATCH,
)
BOTH_INSTALLERS = (SHELL_INSTALLER, BATCH_INSTALLER)

VENV_REFERENCE = re.compile(r"\.venv(?:\b|[/\\])", re.IGNORECASE)
DESTRUCTIVE_COMMAND = re.compile(
    r"\b(?:rm|mv|rd|rmdir|del|move|ren|rename|rmtree|remove_tree|delete_tree)\b|"
    r"\bremove-item\b",
    re.IGNORECASE,
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def destroys_virtual_environment(source: str) -> bool:
    code_lines = [
        "" if re.match(r"\s*(?:#|rem\b|::)", line, re.IGNORECASE) else line
        for line in source.splitlines()
    ]
    if any(
        VENV_REFERENCE.search(line) and DESTRUCTIVE_COMMAND.search(line)
        for line in code_lines
    ):
        return True

    # uninstall.sh registers concrete paths and deletes them through one generic
    # target loop. Recognise that actual in-repository idiom as a positive control
    # without making every unrelated `rm` elsewhere in a script suspicious.
    code = "\n".join(code_lines)
    registers_venv_target = re.search(
        r"^\s*[^\n]*TARGETS\+=\([^\n]*\.venv",
        code,
        re.IGNORECASE | re.MULTILINE,
    )
    recursively_deletes_target = re.search(
        r"^\s*rm\s+-\S*[rf]\S*\s+[\"']?\$\{?target(?:\}|\b)",
        code,
        re.IGNORECASE | re.MULTILINE,
    )
    return registers_venv_target is not None and recursively_deletes_target is not None


def batch_code(source: str) -> str:
    """The batch file with `rem`/`::` comment lines blanked out.

    Several checks below look for text that is *also* discussed in the comments
    explaining why it is there, and a check that its own explanation satisfies
    is no check at all.
    """

    return "\n".join(
        "" if re.match(r"\s*(rem|::)", line, re.IGNORECASE) else line
        for line in source.splitlines()
    )


def invocation(source: str, script: str) -> int:
    """Where a helper script is actually *run*, not merely mentioned.

    Both installers list `scripts/bootstrap.py` among the files that prove the
    checkout is complete, long before they run it. Ordering assertions have to
    look at the call site.
    """

    # A call site passes the script as one quoted argument, because the path may
    # contain spaces. The checkout-completeness lists and the prose both name it
    # bare, so requiring the quotes is what separates the two.
    match = re.search(rf'"[^"\n]*{re.escape(script)}"', source)
    assert match is not None, f"no quoted invocation of {script} found"
    return match.start()


def normal_installation(source: str) -> str:
    """Return the main install path, excluding transaction-repair helpers."""

    marker = (
        '# ── The prebuilt interface '
        if source.startswith("#!/usr/bin/env bash")
        else "echo Installing the prebuilt interface..."
    )
    return source.split(marker, 1)[1]


def test_every_installer_file_exists_and_is_executable_where_that_matters():
    for path in (
        SHELL_INSTALLER,
        BATCH_INSTALLER,
        BATCH_ENTRY_POINT,
        SHELL_ENTRY_POINT,
        SHELL_UNINSTALLER,
        BATCH_UNINSTALLER,
        LINUX_INSTALL_ENTRY,
        *PUBLIC_SHELL_UNINSTALLERS,
        PUBLIC_BATCH_UNINSTALLER,
        LAUNCHER_COMMAND,
        LAUNCHER_BATCH,
        LAUNCHER_LINUX,
        MACOS_APP_EXECUTABLE,
    ):
        assert path.is_file(), f"{path.name} is missing"
    # Finder will not run a .command that is not executable, and the failure is
    # a permission dialog rather than anything that names the cause. Ask git for
    # the mode rather than the filesystem: Windows has no POSIX execute bit, so
    # a checkout there always reports 0o666 and a filesystem check fails for a
    # file that is committed correctly. What has to be right is the mode git
    # records, because that is what a macOS clone gets.
    for path in (
        SHELL_ENTRY_POINT,
        LINUX_INSTALL_ENTRY,
        *PUBLIC_SHELL_UNINSTALLERS,
        LAUNCHER_COMMAND,
        LAUNCHER_LINUX,
        MACOS_APP_EXECUTABLE,
    ):
        staged = subprocess.run(
            ["git", "ls-files", "--stage", "--", str(path.relative_to(ROOT))],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        mode = staged.split(maxsplit=1)[0] if staged else f"{path.stat().st_mode & 0o777:o}"
        expected = "100755" if staged else "755"
        assert mode == expected, (
            f"{path.relative_to(ROOT)} has mode {mode}, not {expected}; "
            "the OS will refuse to run it"
        )


def test_reorganized_entries_have_no_root_or_scripts_duplicates():
    for old_path in (
        "install-wg.command",
        "launch-wg.command",
        "launch-wg.bat",
        "scripts/install-and-update.bat",
    ):
        assert not (ROOT / old_path).exists(), f"obsolete public entry remains: {old_path}"


def test_public_entries_resolve_the_repository_two_levels_up():
    for path in (SHELL_ENTRY_POINT, LINUX_INSTALL_ENTRY, *PUBLIC_SHELL_UNINSTALLERS):
        assert "/../.." in read(path), f"{path} no longer resolves the reorganized root"
    for path in (BATCH_ENTRY_POINT, PUBLIC_BATCH_UNINSTALLER, LAUNCHER_BATCH):
        assert "..\\.." in read(path), f"{path} no longer resolves the reorganized root"
    assert "/../../../../.." in read(MACOS_APP_EXECUTABLE)


# ---------------------------------------------------------------------------
# v1: "installers enforce the runtime prerequisites they consume"
# ---------------------------------------------------------------------------


def test_installers_enforce_the_prerequisites_they_consume():
    for path in BOTH_INSTALLERS:
        source = read(path)
        assert "3.13" in source, f"{path.name} must state the required Python series"
        assert re.search(r"Git .*required|Git is required", source), (
            f"{path.name} must explain that Git is needed for the pinned modules"
        )


def test_installer_uses_the_selected_python_without_base_executable_indirection() -> None:
    for path in BOTH_INSTALLERS:
        assert "sys._base_executable" not in read(path)


def test_both_installers_enforce_the_same_git_version_floor():
    """The two scripts state the floor in their own syntax; they must agree.

    Drift between the installers is the failure mode that matters here -- one
    machine is turned away and an identical one is not, and nobody can reproduce
    it because they are on the other OS.
    """

    shell = read(SHELL_INSTALLER)
    major = re.search(r"^GIT_MIN_MAJOR=(\d+)$", shell, re.MULTILINE)
    minor = re.search(r"^GIT_MIN_MINOR=(\d+)$", shell, re.MULTILINE)
    assert major and minor, "install.sh must declare GIT_MIN_MAJOR / GIT_MIN_MINOR"

    batch = batch_code(read(BATCH_INSTALLER))
    assert re.search(rf"if %GIT_MAJOR% LSS {major.group(1)}\b", batch), (
        f"install.bat must reject Git below major {major.group(1)}"
    )
    assert re.search(rf"if %GIT_MINOR% LSS {minor.group(1)}\b", batch), (
        f"install.bat must reject Git below minor {minor.group(1)}"
    )
    assert f"{major.group(1)}.{minor.group(1)} or newer" in batch, (
        "install.bat's error message must name the same floor it enforces"
    )


def test_explicit_tag_requires_a_real_git_checkout_before_it_can_succeed():
    """A requested release must never degrade to a successful no-op."""

    shell = read(SHELL_INSTALLER)
    shell_tag = shell.index('if [[ -n "$TAG" ]]')
    shell_normal_skip = shell.index('say "  Skipped: this folder is not a Git clone')
    assert shell_tag < shell_normal_skip
    assert "Cannot install $TAG because this folder is not a Git checkout" in shell
    assert "git rev-parse --is-inside-work-tree" in shell

    batch = batch_code(read(BATCH_INSTALLER))
    batch_tag = batch.index(":checkout_tag")
    batch_tag_failure = batch.index(":tag_requires_git")
    assert batch_tag < batch_tag_failure
    assert "Cannot install %TAG% because this folder is not a Git checkout" in batch
    assert batch.count("git rev-parse --is-inside-work-tree") >= 2


def test_the_windows_installer_checks_the_visual_cpp_runtime():
    # v1 shipped an installer that reported "Bempp ready" on a box with no
    # redistributable, and every solve then died on "Numba could not be
    # imported". bempp is the only solve backend on Windows.
    source = read(BATCH_INSTALLER)
    assert "vcruntime140.dll" in source
    assert "vcruntime140_1.dll" in source
    assert "msvcp140.dll" in source
    assert "VCRedist" in source, "the error must name the thing to install"


def test_the_macos_installer_checks_for_the_xcode_command_line_tools():
    # The Apple Silicon Metal solver is a Swift package built on first use.
    source = read(SHELL_INSTALLER)
    assert "xcode-select -p" in source
    assert "xcode-select --install" in source, "the message must say how to fix it"


def test_no_installer_requires_node():
    """Goal 6 of the rebuild plan, as an assertion rather than an intention.

    This is the inversion of v1's test, which *required* `npm ci`. v2 downloads
    a prebuilt SPA precisely so that end users need no Node runtime; an
    installer that quietly grew an `npm ci` would take that back without anyone
    noticing until a user without Node tried to install.
    """

    for path in (*BOTH_INSTALLERS, SHELL_ENTRY_POINT, BATCH_ENTRY_POINT):
        source = read(path)
        for line in source.splitlines():
            code = line.split("#", 1)[0] if path.suffix in (".sh", ".command") else line
            if re.match(r"\s*(rem|::)", code, re.IGNORECASE):
                continue
            assert not re.search(r"^\s*(call\s+)?npm(\.cmd)?\s", code), (
                f"{path.name} runs npm: {line.strip()}"
            )


# ---------------------------------------------------------------------------
# v1: "installers preserve and replace an invalid virtual environment" and
#     "both installers keep at most one incompatible venv backup"
#
# scripts/bootstrap.py normally owns the environment. Updates must leave it in
# place so its manifest fingerprint keeps routine repair incremental. A cheap
# POSIX clone may be restored after a failure; otherwise bootstrap repairs the
# live environment against the restored checkout.
# ---------------------------------------------------------------------------


def test_environment_creation_is_delegated_to_bootstrap():
    for path in BOTH_INSTALLERS:
        source = read(path)
        assert "bootstrap.py" in source, f"{path.name} must delegate to scripts/bootstrap.py"


def test_no_installer_moves_the_environment_out_of_the_way_for_an_update():
    for path in (*BOTH_INSTALLERS, BATCH_ENTRY_POINT, SHELL_ENTRY_POINT):
        source = read(path)
        assert not re.search(r'\bmv\s+[^\n]*["\']?[^\s"\']*\.venv', source)
        assert not re.search(r'\bmove\s+[^\n]*["\']?[^\s"\']*\.venv', source, re.I)


def test_only_rollback_may_remove_an_installer_environment():
    for path in (BATCH_ENTRY_POINT, SHELL_ENTRY_POINT):
        assert not destroys_virtual_environment(read(path)), (
            f"{path.name} must leave environment replacement to the guarded installer."
        )
    shell = read(SHELL_INSTALLER)
    shell_transaction = shell.split("rollback_update() {", 1)[1].split(
        "fail() {", 1
    )[0]
    assert destroys_virtual_environment(shell_transaction)
    assert not destroys_virtual_environment(shell.replace(shell_transaction, ""))

    batch = read(BATCH_INSTALLER)
    batch_transaction = batch.split(":set_update_transaction_path", 1)[1].split(
        ":update_from_git", 1
    )[0]
    assert destroys_virtual_environment(batch_transaction)
    assert not destroys_virtual_environment(batch.replace(batch_transaction, ""))

    # The uninstallers are the ones allowed to. Requiring the detector to find
    # both makes this an actual positive control rather than a spelling check.
    assert destroys_virtual_environment(read(SHELL_UNINSTALLER))
    assert destroys_virtual_environment(read(BATCH_UNINSTALLER))


@pytest.mark.parametrize(
    "command",
    (
        'rd /s /q "%WG_ROOT%\\.venv"',
        'rmdir /s /q "%WG_ROOT%\\.venv"',
        'rm -fr "$ROOT/.venv"',
        'move /y ".venv" ".venv.bak"',
        'shutil.rmtree(root / ".venv")',
    ),
)
def test_the_environment_destruction_check_recognises_real_idioms(command: str):
    assert destroys_virtual_environment(command)


# ---------------------------------------------------------------------------
# v1: "both installers verify a solve can run, not just that imports succeed"
#     "a solver readiness check is reachable without an installer run"
# ---------------------------------------------------------------------------


def test_both_installers_verify_a_solve_can_run():
    for path in BOTH_INSTALLERS:
        assert "check_backends.py" in read(path)


def test_the_backend_check_asks_the_capability_probes_not_an_import():
    # Counting OpenCL devices reported "ready" on hosts where every solve then
    # failed; importing the wrapper proves even less, because it is pure Python.
    source = read(ROOT / "scripts" / "check_backends.py")
    assert "circsym_status" in source
    assert "bempp_status" in source
    assert "beat_status" in source
    assert "metal_status" in source
    assert "import hornlab_bempp_bem" not in source


def test_the_backend_check_runs_standalone():
    # v1 exposed this as an npm script. v2 has no package.json at the root, so
    # the equivalent promise is that the file is directly runnable and the
    # README says so.
    source = read(ROOT / "scripts" / "check_backends.py")
    assert source.startswith("#!/usr/bin/env python3")
    assert 'if __name__ == "__main__":' in source
    assert "check_backends.py" in read(ROOT / "README.md")


# ---------------------------------------------------------------------------
# v1: "Metal helper build runs before solve-backend selection"
#
# v2 builds no helper. The ordering that matters here is different and was
# chosen for the same reason -- do the cheap thing that can fail first.
# ---------------------------------------------------------------------------


def test_the_interface_is_fetched_before_the_multi_minute_dependency_install():
    for path in BOTH_INSTALLERS:
        source = normal_installation(read(path))
        assert invocation(source, "fetch_spa.py") < invocation(source, "bootstrap.py"), (
            f"{path.name} must install the SPA before bootstrapping: fetch_spa.py is "
            "standard library only so it runs on the interpreter already found, and a "
            "missing or corrupt release asset then fails in seconds rather than after "
            "a multi-minute pip install."
        )


def test_the_solve_check_comes_after_the_environment_exists():
    for path in BOTH_INSTALLERS:
        source = normal_installation(read(path))
        assert invocation(source, "bootstrap.py") < invocation(source, "check_backends.py")


def test_both_platform_installers_install_wglink_from_wgs_existing_environment():
    for path in BOTH_INSTALLERS:
        source = read(path)
        install_body = normal_installation(source)
        assert invocation(install_body, "bootstrap.py") < invocation(
            install_body, "install_wglink.py"
        )
        assert "--skip-wglink" in source
        assert "--replace-wglink" in source
        assert "--wglink-archive" in source
        assert re.search(r'\.venv[/\\](?:bin[/\\]python|Scripts[/\\]python\.exe)', source)


def test_uninstallers_remove_only_the_wg_managed_fusion_registration():
    for path in (SHELL_UNINSTALLER, BATCH_UNINSTALLER):
        source = read(path)
        assert "--print-managed-target" in source
        assert "integrations" in source and "wglink" in source and "runtime" in source


# ---------------------------------------------------------------------------
# v1: "strict preflight failures are fatal before the completion banner"
# ---------------------------------------------------------------------------


def test_an_unusable_solve_backend_is_fatal_and_never_reaches_the_banner():
    shell = read(SHELL_INSTALLER)
    assert re.search(r"No solve backend is usable[\s\S]*?exit 1", shell) or re.search(
        r"check_backends\.py[\s\S]{0,400}?fail ", shell
    ), "install.sh must abort when no backend works"
    assert shell.index("check_backends.py") < shell.index("Install complete.")

    batch = read(BATCH_INSTALLER)
    assert re.search(r":no_solve_backend[\s\S]*?exit /b 1", batch)
    assert batch.index("check_backends.py") < batch.index("Install complete.")


def test_a_release_archive_that_fails_verification_never_reaches_the_banner():
    for path in BOTH_INSTALLERS:
        source = read(path)
        assert source.index("fetch_spa.py") < source.index("Install complete.")
    # ...and the refusal itself is enforced where it can be executed.
    assert "REFUSING TO EXTRACT" in read(ROOT / "scripts" / "fetch_spa.py")


# ---------------------------------------------------------------------------
# v1: "updated code restarts through the freshly pulled installer"
# ---------------------------------------------------------------------------


def test_the_shell_installer_restarts_through_the_freshly_pulled_copy():
    source = read(SHELL_INSTALLER)
    assert re.search(r"exec bash \"\$ROOT/scripts/install\.sh\" --after-pull", source)
    assert "--after-pull" in source


def test_the_windows_installer_never_calls_itself_after_a_pull():
    # `git pull` rewrites install.bat, and cmd.exe re-reads a running batch file
    # by byte offset, so the old `call install\install.bat --after-pull` resumed
    # at a meaningless offset and executed fragments of unrelated lines.
    source = read(BATCH_INSTALLER)
    assert not re.search(r"call\s+.*install\.bat", source), (
        "install.bat must not call itself after a pull; it has already been "
        "overwritten and cmd.exe has lost its place in the file."
    )
    assert "exit /b 10" in source, (
        "install.bat should exit 10 to request a relaunch by install-and-update.bat."
    )
    assert "--after-pull" in source, "install.bat must still accept --after-pull."
    assert '"10"' in read(BATCH_ENTRY_POINT), "the entry point must handle the relaunch code"


def test_the_windows_installer_keeps_its_own_path_while_parsing_arguments():
    """Top-level argument parsing must not shift the batch file out of ``%0``.

    Plain ``shift`` moves ``%1`` into ``%0``. After parsing ``--root ROOT
    --no-launch``, the later ``%~f0`` self-relaunch would therefore resolve
    ``--no-launch`` relative to ROOT instead of naming the staged installer.
    ``shift /1`` deliberately leaves ``%0`` unchanged.
    """

    source = batch_code(read(BATCH_INSTALLER))
    parser = source.split(":parse_args", 1)[1].split(":show_usage", 1)[0]
    assert re.search(r"^shift /1$", parser, re.IGNORECASE | re.MULTILINE)
    assert not re.search(r"^shift$", parser, re.IGNORECASE | re.MULTILINE)
    assert 'call "%~f0" %*' in source


def test_the_git_update_explains_both_states_that_stop_it():
    # A branch with no upstream and a dirty tree are both normal. v1 only handled
    # the first and let `git pull --ff-only` discover the second, whose message
    # is about merging and sends people to the wrong fix.
    for path in BOTH_INSTALLERS:
        source = read(path)
        assert "no upstream" in source
        assert "uncommitted changes" in source
        assert "--ff-only" in source, f"{path.name} must only ever fast-forward"


def test_a_release_tag_can_be_installed_directly():
    for path in BOTH_INSTALLERS:
        source = read(path)
        assert "--tag" in source
        assert "git fetch --tags" in source
        assert "git checkout" in source


def test_updates_preserve_code_and_runtime_until_post_switch_validation_passes():
    shell = read(SHELL_INSTALLER)
    shell_tag = shell.split('if [[ -n "$TAG" ]]', 1)[1].split(
        "if ! git symbolic-ref", 1
    )[0]
    shell_branch = shell.split('before="$(git rev-parse HEAD)"', 1)[1].split(
        'exec bash "$ROOT/scripts/install.sh"', 1
    )[0]
    assert shell_tag.index("begin_update_transaction") < shell_tag.index(
        'git checkout --quiet "$TAG"'
    )
    assert shell_branch.index("begin_update_transaction") < shell_branch.index(
        'git merge --ff-only "$upstream"'
    )
    shell_begin = shell.split("begin_update_transaction() {", 1)[1].split(
        "commit_update_transaction() {", 1
    )[0]
    shell_rollback = shell.split("rollback_update() {", 1)[1].split(
        "begin_update_transaction() {", 1
    )[0]
    assert 'cp -a -c "$ROOT/.venv" "$UPDATE_TRANSACTION/venv"' in shell_begin
    assert 'cp --reflink=always "$ROOT/.venv/pyvenv.cfg"' in shell_begin
    assert 'cp -a --reflink=auto "$ROOT/.venv" "$UPDATE_TRANSACTION/venv"' in shell_begin
    assert '"$UPDATE_TRANSACTION/venv-repair"' in shell_begin
    assert 'rm -rf "$UPDATE_TRANSACTION/venv"' in shell_begin
    assert '"$BOOTSTRAP_PYTHON" "$ROOT/scripts/bootstrap.py"' in shell_rollback
    assert 'cp -a "$ROOT/frontend/dist" "$UPDATE_TRANSACTION/dist"' in shell_begin
    assert 'cp -a "$UPDATE_TRANSACTION/dist" "$ROOT/frontend/dist"' in shell_rollback
    assert 'mv "$ROOT/.venv"' not in shell
    assert 'mv "$ROOT/frontend/dist"' not in shell
    assert not re.search(r'\bmv\s+[^\n]*frontend/dist', shell)
    assert shell.index('"$ROOT/scripts/check_backends.py"') < shell.rindex(
        "commit_update_transaction"
    )
    fail_body = shell.split("fail() {", 1)[1].split("while [[ $#", 1)[0]
    assert "rollback_update" in fail_body
    for message in (
        "The requested SPA replacement could not be installed",
        "The Python environment could not be prepared",
        "No solve backend is usable",
    ):
        assert f'fail "{message}' in shell

    batch = read(BATCH_INSTALLER)
    batch_tag = batch.split(":checkout_tag", 1)[1].split(":tag_needs_clean_tree", 1)[0]
    batch_branch = batch.split('git fetch --quiet', 1)[1].split(":already_current", 1)[0]
    assert batch_tag.index("call :begin_update_transaction") < batch_tag.index(
        'git checkout --quiet "%TAG%"'
    )
    assert batch_branch.index("call :begin_update_transaction") < batch_branch.index(
        'git merge --ff-only "%AFTER_COMMIT%"'
    )
    batch_begin = batch.split("\n:begin_update_transaction\n", 1)[1].split(
        "\n:rollback_update\n", 1
    )[0]
    batch_rollback = batch.split("\n:rollback_update\n", 1)[1].split(
        "\n:commit_update_transaction\n", 1
    )[0]
    assert '"%UPDATE_TRANSACTION%\\venv-repair"' in batch_begin
    assert '"%BOOTSTRAP_PYTHON%" "%WG_ROOT%\\scripts\\bootstrap.py"' in batch_rollback
    assert re.search(
        r'robocopy "frontend\\dist" "%UPDATE_TRANSACTION%\\dist"[^\n]*/E',
        batch_begin,
        re.I,
    )
    assert re.search(
        r'robocopy "%UPDATE_TRANSACTION%\\dist" "frontend\\dist"[^\n]*/E',
        batch_rollback,
        re.I,
    )
    assert 'move /y ".venv"' not in batch.lower()
    assert 'move /y "frontend\\dist"' not in batch.lower()
    assert not re.search(r'\bmove\s+[^\n]*frontend\\dist', batch, re.I)
    assert batch.index('"%WG_ROOT%\\scripts\\check_backends.py"') < batch.index(
        "call :commit_update_transaction"
    )
    for label in (":spa_fatal", ":spa_replacement_failed", ":bootstrap_failed", ":no_solve_backend"):
        failure = batch.split(label, 1)[1].split("exit /b 1", 1)[0]
        assert "call :rollback_update" in failure


# ---------------------------------------------------------------------------
# v1: "windows entry point runs the installer from a copy outside the repo"
#     "windows relauncher preserves Unicode paths through the PowerShell tee"
# ---------------------------------------------------------------------------


def test_the_windows_entry_point_stages_the_installer_outside_the_repository():
    source = read(BATCH_ENTRY_POINT)
    assert "%TEMP%" in source, "the entry point must stage the installer in %TEMP%"
    assert re.search(r"copy /y", source, re.IGNORECASE), "it must copy before running"
    assert "--root" in source, "the staged copy needs the repository root passed explicitly"


def test_the_windows_entry_point_preserves_unicode_paths_through_the_powershell_tee():
    source = batch_code(read(BATCH_ENTRY_POINT))
    command = re.search(
        r"powershell\b[\s\S]*?(?=^set \"RUN_RESULT=)",
        source,
        re.IGNORECASE | re.MULTILINE,
    )
    assert command is not None, "the transcript PowerShell invocation is missing"
    invocation = command.group()
    assert "& $env:WG_TMP_INSTALLER" not in invocation, (
        "Windows PowerShell can corrupt a non-ASCII .bat path while handing it "
        "to cmd.exe when the ANSI and OEM code pages differ"
    )
    assert "& $env:ComSpec" in invocation
    assert "/v:off" in invocation, "caller arguments containing ! must not be expanded"
    for name in ("WG_TMP_INSTALLER", "WG_ROOT"):
        assert f"%%{name}%%" in invocation, (
            f"{name} must remain an ASCII environment reference until child cmd expands it"
        )
    assert "$env:WG_LOG" in invocation
    assert "$q = [char]34" in invocation, "runtime quotes keep paths with spaces grouped"
    # A plain cmd pipe takes ERRORLEVEL from the right-hand side, which would
    # destroy the exit code the exit-10 relaunch depends on.
    assert re.search(r"Tee-Object[\s\S]*exit \$LASTEXITCODE", invocation)


@pytest.mark.skipif(sys.platform != "win32", reason="exercises cmd.exe and Windows PowerShell")
def test_the_windows_entry_point_runs_from_non_ascii_user_paths(tmp_path: Path):
    """Regression for the error-3 report from a user whose name contains an accent.

    The real installer is replaced with a small probe, so this exercises the
    public staging, PowerShell tee and child-cmd handoff without touching Git,
    Python environments, the network, or the user's application data.
    """

    root = tmp_path / "André (installer test)" / "waveguide-generator"
    entry = root / "installers" / "windows" / BATCH_ENTRY_POINT.name
    entry.parent.mkdir(parents=True)
    shutil.copy2(BATCH_ENTRY_POINT, entry)

    scripts = root / "scripts"
    scripts.mkdir()
    sentinel = root / "unicode-handoff-ok"
    (scripts / "install.bat").write_text(
        "@echo off\n"
        'if not exist "%~2\\scripts\\install.bat" exit /b 91\n'
        'break > "%~2\\unicode-handoff-ok"\n'
        "exit /b 0\n",
        encoding="utf-8",
        newline="\r\n",
    )

    environment = os.environ.copy()
    environment["TEMP"] = str(tmp_path / "Têmp")
    environment["APPDATA"] = str(tmp_path / "Dâtà")
    Path(environment["TEMP"]).mkdir()
    Path(environment["APPDATA"]).mkdir()
    comspec = environment.get("COMSPEC", "cmd.exe")

    completed = subprocess.run(
        [comspec, "/d", "/c", str(entry), "--no-launch"],
        cwd=root,
        env=environment,
        input="\n",  # satisfy the double-click pause heuristic under cmd /c
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert sentinel.is_file(), "the staged installer did not receive the Unicode repository path"


def test_the_windows_entry_point_passes_forwarded_arguments_as_values():
    """Caller arguments must not become part of the PowerShell program text.

    A quoted cmd argument such as ``--spa-archive "C:\\My Files\\spa.tar.gz"``
    loses those grouping quotes when nested in the outer ``-Command`` string.
    ``;``, ``$`` and backticks are worse, because PowerShell parses them as
    syntax. Store each cmd argument in the environment, then make the child cmd
    expand only the resulting ASCII environment-variable references.
    """

    source = batch_code(read(BATCH_ENTRY_POINT))
    command = re.search(
        r"powershell\b[\s\S]*?(?=^set \"RUN_RESULT=)",
        source,
        re.IGNORECASE | re.MULTILINE,
    )
    assert command is not None, "the transcript PowerShell invocation is missing"
    assert "%*" not in command.group(), (
        "%* inside -Command turns caller-controlled argument text into PowerShell code"
    )
    assert 'set "WG_INSTALL_ARG_%WG_INSTALL_ARG_COUNT%=%~1"' in source, (
        "cmd must preserve each caller argument in a separate environment value"
    )
    assert "%%WG_INSTALL_ARG_" in command.group()
    assert "$cmdLine +=" in command.group(), (
        "the child cmd command must reconstruct every forwarded argument"
    )


def test_the_entry_points_keep_the_installers_exit_status_through_the_transcript():
    # The shell counterpart has the same hazard with `tee`: `$?` after a pipeline
    # is tee's status, not the installer's.
    source = read(SHELL_ENTRY_POINT)
    assert "PIPESTATUS" in source, "install-wg.command must read PIPESTATUS[0], not $?"
    assert "tee" in source


def test_the_entry_points_start_the_launcher_rather_than_reimplementing_it():
    assert "launchers/macos/launch-wg.command" in read(SHELL_ENTRY_POINT)
    assert re.search(
        r'call "%WG_ROOT%\\launchers\\windows\\launch-wg\.bat"',
        read(BATCH_ENTRY_POINT),
    ), (
        "without `call`, control transfers to the launcher permanently and the "
        "exit-code reporting below it never runs"
    )
    # And the transcript pipeline must not have the server inside it, or its
    # output is teed into a growing log for as long as it stays up.
    assert "--no-launch" in read(BATCH_ENTRY_POINT)
    assert "--no-launch" in read(SHELL_ENTRY_POINT)


# ---------------------------------------------------------------------------
# v1: "windows installer probes the venv with a script, not an inline -c"
#     "windows installer skips Microsoft Store python execution aliases"
# ---------------------------------------------------------------------------


def test_no_batch_file_runs_an_inline_python_probe_inside_a_block():
    # A `python -c "...(3,10) <= sys.version_info[:2] < (3,15)..."` probe inside a
    # parenthesised cmd block was mis-parsed: it reported failure while the same
    # command exited 0 at the prompt. v1's installer then discarded a healthy
    # .venv on every run and rebuilt it (~3 min). Indentation is the tell: every
    # in-block line in these files is indented, and every probe lives flat in a
    # subroutine.
    for path in ALL_BATCH_FILES:
        for line in read(path).splitlines():
            if not re.match(r"\s+\S", line):
                continue
            if re.match(r"\s*(rem|::)", line, re.IGNORECASE):
                continue
            assert ' -c "' not in line, (
                f"{path.name}: an inline python -c probe inside a block is mis-parsed "
                f"by cmd.exe. Move it to a subroutine.\n  {line.strip()}"
            )


def test_the_windows_installer_skips_microsoft_store_python_aliases():
    source = read(BATCH_INSTALLER)
    assert "WindowsApps" in source, "install.bat must recognise Store execution aliases"
    assert "STORE_ALIAS_SEEN" in source, "it should say when it skipped one"
    # The piped `echo %CANDIDATE% | find` form re-parses the candidate as command
    # text, so an interpreter path containing & runs its tail as a command.
    assert "CANDIDATE:\\WindowsApps\\=" in source


# ---------------------------------------------------------------------------
# v1: "windows installers never expand a path variable inside a block"
# R1-P1-7: installation under a parent path containing spaces
# ---------------------------------------------------------------------------

PATH_VARIABLES = ("WG_ROOT", "WG_LOG", "WG_LOG_DIR", "CD", "TEMP", "WG_TMP_INSTALLER",
                  "BOOTSTRAP_PYTHON", "DATA_DIR", "SPA_ARCHIVE", "WGLINK_ARCHIVE",
                  "WGLINK_TARGET", "CANDIDATE", "APPDATA")


def test_no_batch_file_expands_a_path_variable_bare_inside_a_block():
    # cmd.exe expands %VAR% when it PARSES a parenthesised block, before running
    # it, so a path containing ')' closes the block early. Verified in v1 with a
    # real install into "…\Hornlab - Workspace (test)\wg": the installer died in
    # 1.2 s with "\wg was unexpected at this time." before printing anything.
    for path in ALL_BATCH_FILES:
        for line in read(path).splitlines():
            if not re.match(r"\s+\S", line):
                continue
            if re.match(r"\s*(rem|::)", line, re.IGNORECASE):
                continue
            for name in PATH_VARIABLES:
                bare = re.compile(rf"(^|[^\"'%]){re.escape('%' + name + '%')}([^\"']|$)")
                assert not bare.search(line), (
                    f"{path.name}: unquoted %{name}% inside a block breaks on paths "
                    f"containing ')'. Quote it or restructure to a flat goto.\n  {line.strip()}"
                )


def test_no_batch_for_loop_runs_a_command_string_that_starts_with_a_quote():
    """`for /f ('"C:\\path\\python.exe" -c ...')` is not reliably parsed.

    cmd runs a `for /f` command string through `cmd /c`, which strips the
    outermost quotes under conditions that are famously hard to predict, so a
    quoted interpreter path in the first position can be mangled. Every command
    string in these files begins with a bare command name; where a specific
    interpreter is needed it is reached by a relative path, which cannot contain
    a space.
    """

    for path in ALL_BATCH_FILES:
        for line in batch_code(read(path)).splitlines():
            for match in re.finditer(r"\bin \('", line):
                assert not line[match.end():].startswith('"'), (
                    f"{path.name}: a for /f command string starts with a quoted path.\n"
                    f"  {line.strip()}"
                )


def test_batch_for_loops_expand_path_variables_only_inside_quotes():
    # `for ... in (...)` is a parenthesised context like any other, so a bare
    # %VAR% holding a path with ')' in it closes the list early. Quoting is what
    # makes `for %%i in ("%WG_ROOT%")` safe, and the same rule has to hold for
    # every command string a for /f runs.
    for path in ALL_BATCH_FILES:
        for line in batch_code(read(path)).splitlines():
            match = re.search(r"\bin \((.*)\) do\b", line)
            if match is None:
                continue
            body = match.group(1)
            for name in PATH_VARIABLES:
                bare = re.compile(rf"(^|[^\"%]){re.escape('%' + name + '%')}([^\"]|$)")
                assert not bare.search(body), (
                    f"{path.name}: %{name}% is expanded unquoted inside a for list, "
                    f"which breaks on a path containing ')'.\n  {line.strip()}"
                )


def test_batch_files_that_disable_delayed_expansion_never_use_it():
    # With delayed expansion ON, every expanded value is rescanned for ! and ^,
    # so a repository under "C:\My ! Projects" silently loses characters. These
    # files turn it off -- which also means a stray !VAR! is inert text rather
    # than a value, and would fail silently.
    for path in ALL_BATCH_FILES:
        source = read(path)
        if "DisableDelayedExpansion" not in source:
            continue
        for line in source.splitlines():
            if re.match(r"\s*(rem|::)", line, re.IGNORECASE):
                continue
            for name in PATH_VARIABLES:
                assert f"!{name}!" not in line, (
                    f"{path.name} disables delayed expansion, so !{name}! is literal "
                    f"text, not a value.\n  {line.strip()}"
                )


SHELL_PATH_VARIABLES = (
    "ROOT",
    "BOOTSTRAP_PYTHON",
    "VENV_PYTHON",
    "SPA_ARCHIVE",
    "WGLINK_ARCHIVE",
    "DATA_DIR",
    "LEGACY_DATA_DIR",
    "LOG",
    "LOG_DIR",
    "REPO_DIR",
    "LAUNCHER",
    "HERE",
    "PYTHON",
    "target",
    "WGLINK_TARGET",
    "interpreter",
)


def _blank_heredocs(source: str) -> str:
    lines = source.split("\n")
    result: list[str] = []
    terminator: str | None = None
    for line in lines:
        if terminator is not None:
            result.append("")
            if line.strip() == terminator:
                terminator = None
            continue
        result.append(line)
        opener = re.search(r"<<-?\s*['\"]?(\w+)['\"]?\s*$", line)
        if opener:
            terminator = opener.group(1)
    return "\n".join(result)


def unquoted_only(source: str) -> str:
    """Blank every byte that sits inside quotes, a comment, or a heredoc.

    A line-by-line stripper is not good enough: the installers' error messages
    are multi-line double-quoted strings, so ``$ROOT`` on a continuation line
    *is* quoted and flagging it would push the author towards a worse script to
    satisfy the test. Newlines are preserved so line numbers stay meaningful.
    """

    text = _blank_heredocs(source)
    out: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote == '"' and char == "\\" and index + 1 < len(text):
            out.append("  ")
            index += 2
            continue
        if quote is not None:
            if char == quote:
                quote = None
            out.append("\n" if char == "\n" else " ")
            index += 1
            continue
        if char in "\"'":
            quote = char
            out.append(" ")
            index += 1
            continue
        if char == "#" and (not out or out[-1] in " \t\n"):
            end = text.find("\n", index)
            end = len(text) if end == -1 else end
            out.append(" " * (end - index))
            index = end
            continue
        out.append(char)
        index += 1
    return "".join(out)


@pytest.mark.parametrize(
    "path",
    [
        SHELL_INSTALLER,
        SHELL_UNINSTALLER,
        SHELL_ENTRY_POINT,
        LINUX_INSTALL_ENTRY,
        *PUBLIC_SHELL_UNINSTALLERS,
        LAUNCHER_COMMAND,
        LAUNCHER_LINUX,
        MACOS_APP_EXECUTABLE,
    ],
    ids=lambda p: str(p.relative_to(ROOT)),
)
def test_shell_scripts_never_use_a_path_variable_unquoted(path):
    """R1-P1-7, checked rather than intended.

    The repository directory has no spaces. Users' parent folders do -- these
    scripts were installed and run for real under "Hornlab - Workspace (test)" --
    and every v1 bug of this shape was an unquoted expansion nobody looked at
    twice.
    """

    source = read(path)
    for number, line in enumerate(unquoted_only(source).split("\n"), start=1):
        for name in SHELL_PATH_VARIABLES:
            original = source.split("\n")[number - 1].strip()
            assert not re.search(rf"\${name}\b", line), (
                f"{path.name}:{number}: ${name} is used unquoted, which breaks on a "
                f"path containing spaces.\n  {original}"
            )
            assert not re.search(rf"\$\{{{name}\b[^}}]*\}}", line), (
                f"{path.name}:{number}: ${{{name}}} is used unquoted.\n  {original}"
            )


def test_the_quoting_check_would_actually_catch_an_unquoted_path():
    """Guard the guard. A scanner that blanks too much passes everything."""

    assert "$ROOT" in unquoted_only('cd $ROOT\n')
    assert "$ROOT" not in unquoted_only('cd "$ROOT"\n')
    assert "$ROOT" not in unquoted_only('fail "line one\n   folder: $ROOT"\n')
    assert "$ROOT" not in unquoted_only("# cd $ROOT\n")
    assert "$ROOT" not in unquoted_only("cat <<'USAGE'\ncd $ROOT\nUSAGE\n")
    assert "$LOG" in unquoted_only('tee -a $LOG\n')


# ---------------------------------------------------------------------------
# v2-only invariants
# ---------------------------------------------------------------------------


def test_batch_files_are_pinned_to_crlf():
    # cmd.exe is not reliably tolerant of LF-only line endings around labels and
    # goto, and this installer is nothing but labels and gotos. The rule has to
    # survive whatever the checking-out user's core.autocrlf says.
    attributes = read(ROOT / ".gitattributes")
    assert re.search(r"^\*\.bat\s+text\s+eol=crlf\s*$", attributes, re.MULTILINE), (
        ".gitattributes must pin *.bat to CRLF"
    )
    for path in ALL_BATCH_FILES:
        assert path.suffix == ".bat", f"{path} would not be covered by the *.bat rule"


def test_the_uninstall_is_documented_by_the_installers_and_the_readme():
    assert "uninstall.sh" in read(SHELL_INSTALLER)
    assert "uninstall.bat" in read(BATCH_INSTALLER)
    readme = read(ROOT / "README.md")
    assert "uninstall.sh" in readme
    assert "uninstall.bat" in readme


def test_the_uninstallers_refuse_to_delete_without_being_asked_twice():
    shell = read(SHELL_UNINSTALLER)
    assert "--yes" in shell
    assert "Refusing to delete without confirmation" in shell
    batch = read(BATCH_UNINSTALLER)
    assert "--yes" in batch
    assert "set /p" in batch


def test_batch_uninstaller_captures_its_root_before_shifting_arguments():
    batch = batch_code(read(BATCH_UNINSTALLER))
    capture = batch.index('set "SCRIPT_DIR=%~dp0"')
    first_shift = batch.index("shift")

    assert capture < first_shift
    assert 'cd /d "%SCRIPT_DIR%.."' in batch


@pytest.mark.skipif(os.name != "nt", reason="requires cmd.exe")
def test_batch_uninstaller_yes_removes_the_invoked_checkout(tmp_path: Path):
    checkout = tmp_path / "checkout with spaces"
    launch_directory = tmp_path / "started elsewhere"
    uninstaller = checkout / "scripts" / "uninstall.bat"
    uninstaller.parent.mkdir(parents=True)
    launch_directory.mkdir()
    uninstaller.write_bytes(BATCH_UNINSTALLER.read_bytes())
    (checkout / ".venv").mkdir()
    (checkout / ".venv" / "sentinel.txt").write_text("keep checkout", encoding="utf-8")
    (checkout / "frontend" / "dist").mkdir(parents=True)
    (checkout / "frontend" / "dist" / "index.html").write_text("built", encoding="utf-8")

    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", str(uninstaller), "--yes"],
        cwd=launch_directory,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert checkout.is_dir()
    assert not (checkout / ".venv").exists()
    assert not (checkout / "frontend" / "dist").exists()


def test_the_uninstallers_never_remove_the_checkout_or_v1():
    # v2 installs beside v1 with its own data directory. An uninstaller that
    # reached outside its own would make that promise a lie.
    for path in (SHELL_UNINSTALLER, BATCH_UNINSTALLER):
        source = read(path)
        assert "Waveguide Generator/" not in source
        assert "checkout itself is kept" in source
    # The data directory is asked for, not restated, so it cannot drift from
    # server/platform/paths.py.
    assert "resolve_data_dir" in read(SHELL_UNINSTALLER)
    assert "resolve_data_dir" in read(BATCH_UNINSTALLER)


def test_the_installers_finish_by_starting_the_launcher():
    assert "launch-wg.command" in read(SHELL_INSTALLER)
    assert "launch-wg.bat" in read(BATCH_INSTALLER)
    # Port selection and the browser open belong to the launcher and serve.py;
    # an installer that reimplemented either would be a second answer to drift.
    for path in BOTH_INSTALLERS:
        assert "3100" in read(path), f"{path.name} should say which ports are used"


def test_the_launchers_point_at_the_installer_now_that_one_exists():
    # Before P6.2 these told the user to download and extract a tarball by hand,
    # which is exactly the unverified extraction the installer refuses to do.
    # The hint now lives beside the freshness check that raises it, so that the
    # status window and the launchers' console path cannot drift apart.
    source = read(ROOT / "scripts" / "frontend_freshness.py")
    assert "installer_hint" in read(
        ROOT / "launchers" / "statusapp" / "controller.py"
    ), "the status window must still route its advice through that hint"
    for installer in (
        "installers/macos/install-wg.command",
        r"installers\windows\install-and-update.bat",
        "installers/linux/install.sh",
    ):
        assert installer in source
