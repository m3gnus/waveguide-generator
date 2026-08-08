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

from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[2]

SHELL_INSTALLER = ROOT / "scripts" / "install.sh"
BATCH_INSTALLER = ROOT / "scripts" / "install.bat"
BATCH_ENTRY_POINT = ROOT / "scripts" / "install-and-update.bat"
SHELL_ENTRY_POINT = ROOT / "install-wg2.command"
SHELL_UNINSTALLER = ROOT / "scripts" / "uninstall.sh"
BATCH_UNINSTALLER = ROOT / "scripts" / "uninstall.bat"
LAUNCHER_COMMAND = ROOT / "launch-wg2.command"
LAUNCHER_BATCH = ROOT / "launch-wg2.bat"

ALL_BATCH_FILES = (BATCH_INSTALLER, BATCH_ENTRY_POINT, BATCH_UNINSTALLER, LAUNCHER_BATCH)
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


def test_every_installer_file_exists_and_is_executable_where_that_matters():
    for path in (
        SHELL_INSTALLER,
        BATCH_INSTALLER,
        BATCH_ENTRY_POINT,
        SHELL_ENTRY_POINT,
        SHELL_UNINSTALLER,
        BATCH_UNINSTALLER,
    ):
        assert path.is_file(), f"{path.name} is missing"
    # Finder will not run a .command that is not executable, and the failure is
    # a permission dialog rather than anything that names the cause.
    assert SHELL_ENTRY_POINT.stat().st_mode & 0o111, "install-wg2.command must be executable"


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
# v2 has neither behaviour, because scripts/bootstrap.py owns the environment:
# it fingerprints the dependency manifests, reinstalls when they move, and
# refuses a directory that is not a usable venv rather than renaming it. The
# property worth protecting is therefore the opposite one -- that no installer
# takes it upon itself to delete or rename a 500 MB environment.
# ---------------------------------------------------------------------------


def test_environment_creation_is_delegated_to_bootstrap():
    for path in BOTH_INSTALLERS:
        source = read(path)
        assert "bootstrap.py" in source, f"{path.name} must delegate to scripts/bootstrap.py"


def test_no_installer_deletes_or_renames_the_environment_behind_the_user():
    for path in (*BOTH_INSTALLERS, BATCH_ENTRY_POINT, SHELL_ENTRY_POINT):
        assert not destroys_virtual_environment(read(path)), (
            f"{path.name} destroys .venv. bootstrap.py decides what to do with an "
            "environment; an installer that removes one silently discards a "
            "multi-hundred-MB download the user may not be able to repeat."
        )
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
    assert "bempp_status" in source
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
        source = read(path)
        assert invocation(source, "fetch_spa.py") < invocation(source, "bootstrap.py"), (
            f"{path.name} must install the SPA before bootstrapping: fetch_spa.py is "
            "standard library only so it runs on the interpreter already found, and a "
            "missing or corrupt release asset then fails in seconds rather than after "
            "a multi-minute pip install."
        )


def test_the_solve_check_comes_after_the_environment_exists():
    for path in BOTH_INSTALLERS:
        source = read(path)
        assert invocation(source, "bootstrap.py") < invocation(source, "check_backends.py")


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


# ---------------------------------------------------------------------------
# v1: "windows entry point runs the installer from a copy outside the repo"
#     "windows relauncher passes paths to PowerShell through the environment"
# ---------------------------------------------------------------------------


def test_the_windows_entry_point_stages_the_installer_outside_the_repository():
    source = read(BATCH_ENTRY_POINT)
    assert "%TEMP%" in source, "the entry point must stage the installer in %TEMP%"
    assert re.search(r"copy /y", source, re.IGNORECASE), "it must copy before running"
    assert "--root" in source, "the staged copy needs the repository root passed explicitly"


def test_the_windows_entry_point_passes_paths_to_powershell_through_the_environment():
    source = batch_code(read(BATCH_ENTRY_POINT))
    for name in ("WG_TMP_INSTALLER", "WG_ROOT", "WG_LOG"):
        assert f"$env:{name}" in source, f"{name} must reach PowerShell as $env:{name}"
        assert f"'%{name}%'" not in source, (
            f"%{name}% would be substituted by cmd before PowerShell parsed the line"
        )
    # A plain cmd pipe takes ERRORLEVEL from the right-hand side, which would
    # destroy the exit code the exit-10 relaunch depends on.
    assert re.search(r"Tee-Object[\s\S]*exit \$LASTEXITCODE", source)


def test_the_windows_entry_point_passes_forwarded_arguments_as_values():
    """Caller arguments must not become part of the PowerShell program text.

    A quoted cmd argument such as ``--spa-archive "C:\\My Files\\spa.tar.gz"``
    loses those grouping quotes when nested in the outer ``-Command`` string.
    PowerShell then sees two arguments; ``;``, ``$`` and backticks are worse,
    because they are parsed as PowerShell syntax.  Store each cmd argument in
    the environment and splat the reconstructed array instead.
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
    assert "GetEnvironmentVariable('WG_INSTALL_ARG_' + $i)" in command.group()
    assert "@wgArgs" in command.group(), (
        "the reconstructed caller arguments must be splatted as an array"
    )


def test_the_entry_points_keep_the_installers_exit_status_through_the_transcript():
    # The shell counterpart has the same hazard with `tee`: `$?` after a pipeline
    # is tee's status, not the installer's.
    source = read(SHELL_ENTRY_POINT)
    assert "PIPESTATUS" in source, "install-wg2.command must read PIPESTATUS[0], not $?"
    assert "tee" in source


def test_the_entry_points_start_the_launcher_rather_than_reimplementing_it():
    assert "launch-wg2.command" in read(SHELL_ENTRY_POINT)
    assert re.search(r'call "%WG_ROOT%\\launch-wg2\.bat"', read(BATCH_ENTRY_POINT)), (
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
                  "BOOTSTRAP_PYTHON", "DATA_DIR", "SPA_ARCHIVE", "CANDIDATE", "APPDATA")


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


SHELL_PATH_VARIABLES = ("ROOT", "BOOTSTRAP_PYTHON", "VENV_PYTHON", "SPA_ARCHIVE",
                        "DATA_DIR", "LOG", "LOG_DIR", "REPO_DIR", "target", "interpreter")


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
    "path", [SHELL_INSTALLER, SHELL_UNINSTALLER, SHELL_ENTRY_POINT], ids=lambda p: p.name
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
    assert "launch-wg2.command" in read(SHELL_INSTALLER)
    assert "launch-wg2.bat" in read(BATCH_INSTALLER)
    # Port selection and the browser open belong to the launcher and serve.py;
    # an installer that reimplemented either would be a second answer to drift.
    for path in BOTH_INSTALLERS:
        assert "3100" in read(path), f"{path.name} should say which ports are used"


def test_the_launchers_point_at_the_installer_now_that_one_exists():
    # Before P6.2 these told the user to download and extract a tarball by hand,
    # which is exactly the unverified extraction the installer refuses to do.
    for path in (LAUNCHER_COMMAND, LAUNCHER_BATCH):
        source = read(path)
        assert "install" in source.lower(), f"{path.name} must name the installer"
