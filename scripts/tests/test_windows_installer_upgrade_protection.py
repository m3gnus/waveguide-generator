"""Static checks for the Windows installer's upgrade protection.

Inno Setup cannot run on the machines this suite runs on, so these tests read
``bundle-setup.iss`` and ``gates.ps1`` as text. They cannot prove the installer
behaves; they pin the properties that a careless edit would silently lose, and
catch the mistakes ISCC would only report at release-build time (a function
used before its definition, a comment that swallows code). The behavioural
proof is gates 13–16 in ``gates.ps1``, run on a Windows host.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

INSTALLERS = Path(__file__).resolve().parents[2] / "installers" / "windows"


@pytest.fixture(scope="module")
def script() -> str:
    return (INSTALLERS / "bundle-setup.iss").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code(script: str) -> str:
    return script.split("\n[Code]\n", 1)[1]


@pytest.fixture(scope="module")
def gates() -> str:
    return (INSTALLERS / "gates.ps1").read_text(encoding="utf-8")


def _body(code: str, header: str) -> str:
    """The text of one routine, from its header to the next top-level one."""

    start = code.index(header)
    following = re.search(r"\n(?:procedure|function) ", code[start + len(header) :])
    end = start + len(header) + following.start() if following else len(code)
    return code[start:end]


def _strip_comments_and_strings(code: str) -> str:
    """Remove ``{ ... }`` comments (nesting allowed, as ISCC accepts) and strings."""

    out: list[str] = []
    depth = 0
    in_string = False
    for char in code:
        if in_string:
            if char == "'":
                in_string = False
            continue
        if depth:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            continue
        if char == "{":
            depth = 1
        elif char == "'":
            in_string = True
        else:
            out.append(char)
    assert depth == 0, "unbalanced { } comment in [Code]"
    assert not in_string, "unterminated string in [Code]"
    return "".join(out)


def test_setup_mutex_and_relaunch_entry_are_declared(script: str) -> None:
    assert "\nSetupMutex=WaveguideGeneratorSetup\n" in script
    run = script.split("\n[Run]\n", 1)[1].split("\n[UninstallDelete]", 1)[0]
    entries = [line for line in run.splitlines() if line.startswith("Filename:")]
    assert len(entries) == 2
    interactive, relaunch = entries
    assert "skipifsilent" in interactive and "postinstall" in interactive
    # The silent updater start must not be skipped, must not need a checkbox,
    # and is only requested explicitly.
    assert "Check: RelaunchRequested" in relaunch
    assert "skipifsilent" not in relaunch and "postinstall" not in relaunch
    assert "nowait" in relaunch
    # Setup holds the mutex until it exits; the launcher refuses under it.
    assert "ping -n" in relaunch
    assert "function RelaunchRequested(): Boolean;" in script


def test_silent_upgrade_still_never_reuses_or_forces_wglink(script: str) -> None:
    assert "\nUsePreviousTasks=no\n" in script
    # No task is defaulted on by the installer for a silent run.
    assert "Flags: unchecked" in script.split('Name: "wglink"', 1)[1].splitlines()[0]
    # The Fusion-aware preselection is armed only for an interactive run that
    # named no task choice of its own; the only other selection setup makes is
    # the desktop shortcut the user already asked for (see
    # test_windows_installer_desktop_shortcut.py).
    init = _body(script, "procedure InitializeWizard();")
    assert "WgLinkPreselectPending := False;" in init
    assert "WgLinkPreselectPending := not WizardSilent() and not TaskChoiceOnCommandLine();" in init
    assert "WizardSelectTasks(" not in init
    assert script.count("WizardSelectTasks(WgLinkTaskName)") == 1
    assert script.count("WizardSelectTasks(") == 2


def test_fusion_preselection_runs_on_the_first_interactive_tasks_page(script: str) -> None:
    # Inno builds the tasks list only on the way to wpSelectTasks; selecting a
    # task in InitializeWizard finds an empty list and does nothing (observed
    # 2026-10-05 with Inno Setup 6.7.3). The selection belongs on that page.
    page = _body(script, "procedure CurPageChanged(CurPageID: Integer);")
    guard = "if (CurPageID = wpSelectTasks) and WgLinkPreselectPending and not WizardSilent() then"
    preselect = page.split(guard, 1)[1].split("\n  end;", 1)[0]
    assert "WgLinkPreselectPending := False;" in preselect
    assert "WizardSelectTasks(WgLinkTaskName);" in preselect
    assert preselect.index("WgLinkPreselectPending := False;") < preselect.index("WizardSelectTasks(")
    # A command-line task choice, interactive or not, is never overridden.
    choice = _body(script, "function TaskChoiceOnCommandLine(): Boolean;")
    assert "(Pos('/TASKS=', Argument) = 1) or (Pos('/MERGETASKS=', Argument) = 1)" in choice


def test_setup_choice_is_recorded_only_for_the_selected_task_before_install(code: str) -> None:
    step = _body(code, "procedure CurStepChanged(CurStep: TSetupStep);")
    post_install = step.split("if CurStep = ssPostInstall then", 1)[1]
    selected = post_install.split("if WizardIsTaskSelected(WgLinkTaskName) then", 1)[1].split("\n    else", 1)[0]
    assert selected.strip() == "begin\n      RecordWGLinkSetupChoice();\n      InstallWGLink();\n    end"
    recording = _body(code, "procedure RecordWGLinkSetupChoice();")
    assert code.count("RecordWGLinkSetupChoice();") == 2  # declaration and selected-task call
    assert code.count("--record-setup-choice") == 1 and "--record-setup-choice" in recording
    assert "FileExists(ExpandConstant('{app}\\runtime\\python.exe'))" in recording
    assert "SW_HIDE, ewWaitUntilTerminated, ExitCode, @WgLinkOutput" in recording
    assert "if not ExecAndLogOutput(" in recording and "else if ExitCode <> 0 then" in recording
    for name, value in (("WG2_BUNDLE", "'1'"), ("WG2_APP_ROOT", "ExpandConstant('{app}\\app')")):
        assert f"SetEnvironmentVariable('{name}', {value});" in recording
    finalizer = recording.split("  finally\n", 1)[1]
    assert "SetEnvironmentVariable('WG2_BUNDLE', PreviousBundleFlag);" in finalizer
    assert "SetEnvironmentVariable('WG2_APP_ROOT', PreviousAppRoot);" in finalizer
    assert recording.count("Log(") == 4
    assert not re.search(r'\b(?:MsgBox|Abort|RaiseException|FusionDetected|WgLinkAddInsDirectory)\b', recording)
    for header in ("procedure UninstallWGLink();", "procedure CurUninstallStepChanged("):
        uninstall = _body(code, header)
        assert "RecordWGLinkSetupChoice" not in uninstall and "--record-setup-choice" not in uninstall


def test_uninstall_reaches_locked_cleanup_when_only_an_orphan_lock_remains(code: str) -> None:
    uninstall = _body(code, "procedure UninstallWGLink();")
    # An absent target has no ownership marker. It must still reach Python,
    # which takes the OS lock before deleting an unused lock file on Windows.
    guard = (
        "if (DirExists(Target) or FileExists(Target)) and\n"
        "    (not WgLinkManagedByThisInstall(Target)) and not HasTransaction then"
    )
    assert guard in uninstall
    preserved = uninstall.split(guard, 1)[1].split("end;", 1)[0]
    assert "preserved non-owned target" in preserved and "exit;" in preserved
    assert uninstall.index(guard) < uninstall.index(" --uninstall --yes --root ")
    assert "SW_HIDE, ewWaitUntilTerminated, ExitCode, @WgLinkOutput" in uninstall
    # Cleanup runs before Inno deletes the bundled interpreter and script.
    step = _body(code, "procedure CurUninstallStepChanged(")
    assert "if CurUninstallStep = usUninstall then" in step
    assert "UninstallWGLink();" in step


def test_layers_are_renamed_aside_at_install_and_deleted_at_post_install(code: str) -> None:
    step = _body(code, "procedure CurStepChanged(CurStep: TSetupStep);")
    install = step.split("if CurStep = ssInstall then", 1)[1].split("if CurStep = ssPostInstall", 1)[0]
    assert install.index("WaitForApplicationExit()") < install.index("BeginProtectedReplace();")
    post = step.split("if CurStep = ssPostInstall then", 1)[1]
    # Committed before WGLink runs, which needs the new runtime.
    assert post.index("CommitProtectedReplace();") < post.index("InstallWGLink()")

    begin = _body(code, "procedure BeginProtectedReplace();")
    assert begin.index("--installer-entry") < begin.index("--installer-prepare")
    assert "EnsureNativeHelper()" in begin
    helper = _body(code, "function EnsureNativeHelper()")
    assert "ExtractTemporaryFile('wg-installer-helper.exe')" in helper
    assert begin.count("RaiseException(") >= 2


def test_commit_only_succeeds_after_native_durable_commit(code: str) -> None:
    commit = _body(code, "procedure CommitProtectedReplace();")
    assert commit.index("RunNative('--installer-commit')") < commit.index("ProtectionCommitted := True;")
    assert "RaiseException(" in commit


def test_rollback_happens_in_deinitialize_only_when_not_committed(code: str) -> None:
    deinit = _body(code, "procedure DeinitializeSetup();")
    assert (
        "if ProtectionStarted and not ProtectionCommitted then\n    RollBackProtectedReplace();"
        in deinit
    )
    # The record is written after the rollback, and says ok only if committed.
    assert deinit.index("RollBackProtectedReplace();") < deinit.index("WriteOutcome(")
    assert "if ProtectionCommitted then\n    WriteOutcome('ok')" in deinit
    assert "WriteOutcome('failed')" in deinit

    rollback = _body(code, "procedure RollBackProtectedReplace();")
    assert "RunNative('--installer-rollback')" in rollback
    assert "RollbackIncomplete := Code = 3;" in rollback
    assert "OutcomeWritten := True;" in rollback


def test_backups_are_never_install_delete_and_uninstall_names_the_owned_staging(script: str) -> None:
    assert not re.search(r"^\[InstallDelete\]", script, re.MULTILINE)
    for name in (".wg-install-old", ".wg-install-new", ".upgrade-in-progress"):
        assert f'Name: "{{app}}\\{name}"' in script
    files = script.split("[Files]", 1)[1].split("[Icons]", 1)[0]
    assert 'DestDir: "{app}\\.wg-install-new"' in files
    assert 'DestDir: "{app}\\runtime"' in files
    assert 'DestDir: "{app}\\app"' in files
    assert 'DestDir: "{app}\\recovery"' in files
    assert not re.search(r'DestDir: "\{app\}"', files)


def test_native_recovery_does_not_follow_junctions() -> None:
    source = (INSTALLERS.parents[1] / "launchers/windows/launcher.c").read_text()
    assert "FILE_FLAG_OPEN_REPARSE_POINT" in source
    assert "if (a & FILE_ATTRIBUTE_REPARSE_POINT) return 0;" in source
    assert "matches(root, &j->root_id)" in source


def test_wait_for_process_has_a_cap_and_fails_setup(code: str) -> None:
    assert "WaitForProcessLimitMs = 120000;" in code
    assert "{param:WAITPID|0}" in code
    wait = _body(code, "function WaitForApplicationExit(): Boolean;")
    assert "OpenProcess(SYNCHRONIZE, 0, Pid)" in wait
    assert "WaitForSingleObject(Handle, Slice) = WAIT_OBJECT_0" in wait
    assert "CloseHandle(Handle)" in wait
    init = _body(code, "function InitializeSetup(): Boolean;")
    assert "WaitForApplicationExit" not in _strip_comments_and_strings(init)
    prepare = _body(code, "function PrepareToInstall(var NeedsRestart: Boolean): String;")
    assert "WaitForApplicationExit" not in _strip_comments_and_strings(prepare)
    assert "MsgBox(" not in _strip_comments_and_strings(prepare)
    assert "BeginProtectedReplace" not in _strip_comments_and_strings(prepare)
    # No earlier event may start replacement.
    callers = re.findall(r"\bBeginProtectedReplace\(\);", _strip_comments_and_strings(code))
    assert len(callers) == 2  # its declaration and the ssInstall call
    step = _body(code, "procedure CurStepChanged(CurStep: TSetupStep);")
    install = step.split("if CurStep = ssInstall then", 1)[1].split("if CurStep = ssPostInstall", 1)[0]
    plain = _strip_comments_and_strings(install)
    assert re.match(r"\s*begin\s+if not WaitForApplicationExit\(\) or not WaitForRunningApplicationExit\(\) then", plain)
    failure, replacement = install.split("    BeginProtectedReplace();", 1)
    assert re.search(
        r"begin\s+WriteOutcome\('failed'\);\s+if not WizardSilent\(\) then\s+"
        r"MsgBox\('Waveguide Generator is still running\. Close it and run setup again\.', mbError, MB_OK\);\s+"
        r"Abort;\s+end;\s*$", failure,
    )
    assert replacement.strip() == "end;"
    assert "ProtectionStarted :=" not in plain
    # The sole flag assignment is inside replacement, never on wait failure.
    assert _strip_comments_and_strings(code).count("ProtectionStarted := True;") == 1
    assert "ProtectionStarted := True;" in _body(code, "procedure BeginProtectedReplace();")
    assert len(re.findall(r"\bWaitForApplicationExit\(\)", _strip_comments_and_strings(code))) == 2


def test_waitpid_waits_pump_the_wizard_message_queue(code: str) -> None:
    """Both /WAITPID waits run on the wizard's UI thread.

    One blocking wait of up to 120 s left the interactive wizard unpainted and
    marked "Not Responding". Each wait is now short slices with a message pump
    between them. The process cap is measured on the tick clock: counting
    100 ms slices stretched it to about 131 s, since each lasts about 109 ms.
    """

    plain_code = _strip_comments_and_strings(code)
    assert "WaitSliceMs = 100;" in code
    pump = _strip_comments_and_strings(_body(code, "procedure PumpMessages();"))
    assert "PeekMessageW(Msg, 0, 0, 0, PM_REMOVE)" in pump
    assert "TranslateMessage(Msg);" in pump and "DispatchMessageW(Msg);" in pump
    assert "PostQuitMessage(Msg.WParam);" in pump
    for name in ("PeekMessageW", "TranslateMessage", "DispatchMessageW", "PostQuitMessage"):
        assert f"external '{name}@user32.dll stdcall setuponly'" in code

    wait = _strip_comments_and_strings(_body(code, "function WaitForApplicationExit(): Boolean;"))
    assert "while not Exited and (Elapsed < WaitForProcessLimitMs) do" in wait
    assert "Slice := WaitForProcessLimitMs - Elapsed;" in wait
    assert "Elapsed := GetTickCount() - Start;" in wait
    assert "external 'GetTickCount@kernel32.dll stdcall setuponly'" in code
    assert "PumpMessages();" in wait
    # No single wait may block for the whole cap again.
    assert not re.search(r"WaitForSingleObject\(\w+, WaitForProcessLimitMs\)", plain_code)

    # A modal dialog inside the pump can outlast the deadline after the process
    # exited, so the timeout verdict must follow one final non-blocking look.
    after_loop = wait.split("Elapsed := GetTickCount() - Start;", 1)[1]
    assert re.search(
        r"if not Exited then\s+Exited := WaitForSingleObject\(Handle, 0\) = WAIT_OBJECT_0;\s+if Exited then",
        after_loop,
    )

    running = _strip_comments_and_strings(_body(code, "function WaitForRunningApplicationExit(): Boolean;"))
    # The mutex cap is on the clock too: 1201 counted 100 ms sleeps took ~131 s.
    assert "Attempt" not in running
    assert "Start := GetTickCount();" in running
    # The deadline is tested only right after a fresh mutex check, and the
    # sleep and pump come after that test, so a refusal never follows a pump.
    assert re.search(
        r"while True do\s+begin\s+Handle := OpenMutexW\(SYNCHRONIZE, 0, \)",
        running,
    )
    assert re.search(
        r"CloseHandle\(Handle\);\s+if not WaitRequested then\s+begin\s+WgLog\(\);\s+exit;\s+end;\s+"
        r"Elapsed := GetTickCount\(\) - Start;\s+if Elapsed >= WaitForProcessLimitMs then\s+break;\s+"
        r"Slice := WaitForProcessLimitMs - Elapsed;\s+if Slice > WaitSliceMs then\s+Slice := WaitSliceMs;\s+"
        r"Sleep\(Slice\);\s+PumpMessages\(\);\s+end;\s+WgLog\(\);",
        running,
    )
    # The pump is defined before either wait uses it.
    assert code.index("procedure PumpMessages();") < code.index("function WaitForApplicationExit(): Boolean;")


def test_prepare_to_install_keeps_head_behaviour_and_no_silent_message(code: str) -> None:
    prepare = _body(code, "function PrepareToInstall(var NeedsRestart: Boolean): String;")
    assert prepare.strip() == """function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if (not WizardSilent()) and (Length(WizardDirValue) > MaxRootLength()) then
    Result := TooLongMessage(WizardDirValue);
end;"""


def test_measured_silent_dialog_comment_is_preserved_verbatim(script: str) -> None:
    measured = """{ /DIR= skips the wizard's directory page, so a silent install would otherwise
  reach extraction with an unchecked root. This runs before the wizard exists
  and before any file is written.

  The WizardSilent split is not cosmetic. Returning a message from
  PrepareToInstall, or showing a MsgBox here, puts up a modal dialog that
  /SUPPRESSMSGBOXES does NOT cover -- measured 2026-08-27: a silent install with
  an over-long /DIR sat on a "Setup - Waveguide Generator" window indefinitely
  rather than failing. A silent run has nobody to answer a dialog, so it has to
  fail with an exit code instead. Setup that hangs is worse than setup that
  refuses: it takes a CI job's whole timeout with it and reports nothing. }
function InitializeSetup(): Boolean;"""
    assert measured in script


def test_outcome_record_has_the_agreed_fields_and_is_atomic(code: str) -> None:
    write = _body(code, "procedure WriteOutcome(")
    assert "{param:OUTCOME|}" in write
    for key in ("from", "to", "result", "when", "log"):
        assert f"'\"{key}\": '" in write.replace("{\"from\"", "\"from\"") or f'"{key}": ' in write
    assert "OutcomeLogPath()" in write
    selector = _body(code, "function OutcomeLogPath()")
    assert "WgLogPath" in selector and "{param:LOG|}" in selector
    assert "SaveStringsToUTF8FileWithoutBOM(Tmp, Lines, False)" in write
    assert write.index("SaveStringsToUTF8FileWithoutBOM") < write.index("RenameFile(Tmp, Path)")
    # Written at most once, so DeinitializeSetup cannot overwrite an earlier failure.
    assert "if OutcomeWritten then\n    exit;" in write
    # Paths carry backslashes and quotes; both are escaped.
    assert "StringChangeEx(Result, '\\', '\\\\', True);" in code
    assert "StringChangeEx(Result, '\"', '\\\"', True);" in code
    assert "GetDateTimeString('yyyy-mm-dd\"T\"hh:nn:ss', '-', ':')" in write


def test_fallback_outcome_cannot_claim_an_unverified_previous_install(code: str, gates: str) -> None:
    write = _body(code, "procedure WriteOutcome(")
    assert '"previousKept": ' in write
    # Fresh-root aborts and pre-protection timeouts have no native identity
    # evidence. Inno fallback must never assert that an old version was kept.
    assert re.findall(r"Kept\s*:=\s*'([^']+)';", write) == ["false"]
    assert len(re.findall(r"\bKept\s*:=", _strip_comments_and_strings(write))) == 1
    assert '$freshData.result -eq "failed"' in gates
    assert '$freshData.previousKept -eq $false' in gates
    assert '$freshReturned' in gates and '$freshExit -ne 0' in gates


def test_previous_version_comes_from_this_products_uninstall_key(script: str, code: str) -> None:
    app_id = re.search(r"^AppId=\{(\{[0-9A-F-]+\})", script, re.MULTILINE)
    assert app_id
    assert f"\\{app_id.group(1)}_is1'" in code
    assert "RegQueryStringValue(HKCU, UninstallRegistryKey, 'DisplayVersion', PreviousVersion)" in code


def test_every_routine_is_defined_before_it_is_called(code: str) -> None:
    """Pascal Script has no forward declarations here; ISCC rejects a use that
    precedes its definition, and only at release-build time."""

    plain = _strip_comments_and_strings(code)
    definitions = {
        match.group(2): match.start()
        for match in re.finditer(
            r"^(procedure|function)\s+(\w+)\s*[(:;]", plain, re.MULTILINE
        )
    }
    assert {"BeginProtectedReplace", "WriteOutcome", "IsReparsePoint"} <= definitions.keys()
    # Event functions and functions named from [Run]/[Setup] are entry points.
    entry_points = {
        "CurStepChanged", "CurUninstallStepChanged", "CurPageChanged", "InitializeWizard",
        "InitializeSetup", "DeinitializeSetup", "NextButtonClick", "PrepareToInstall",
        "RelaunchRequested", "ShouldSkipPage",
    }
    for name, position in definitions.items():
        if name in entry_points:
            continue
        earlier = re.search(rf"\b{name}\b", plain[:position])
        assert earlier is None, f"{name} is used before it is defined"


def test_gates_check_removed_files_outcome_and_waitpid(gates: str) -> None:
    assert "Gate 13 " in gates and "Gate 14 " in gates
    # Both a dropped module and a dropped runtime package are planted.
    assert 'app\\gate_removed_module.py' in gates
    assert 'runtime\\Lib\\site-packages\\gate_removed_package' in gates
    # The baseline is the package as freshly installed, taken before planting.
    assert gates.index("$layerBaseline =") < gates.index("$removedModule =")
    assert "$layerAfter -eq $layerBaseline" in gates
    assert '".app.old", ".runtime.old", ".wg-install-old", ".wg-install-new", ".upgrade-in-progress"' in gates
    assert '/OUTCOME=' in gates and '/WAITPID=' in gates
    # The silent upgrade in gate 13 passes no /TASKS.
    gate13 = gates.split("# --- Gate 13", 1)[1].split("# --- Gate 14", 1)[0]
    assert "/TASKS" not in gate13
    # Gates run before the developer/uninstall gates that dismantle the fixtures.
    assert gates.index("Gate 14 ") < gates.index("# --- Gate 11")


def test_gates_check_mutex_and_no_replacement_during_wait(gates: str) -> None:
    wait_gates = gates.split("# --- Gate 14", 1)[1].split("# --- Gate 11", 1)[0]
    assert "Gate 15 " in wait_gates
    assert "[Threading.Mutex]::OpenExisting" in wait_gates
    assert '"WaveguideGeneratorSetup"' in wait_gates
    assert '"/WAITPID: waiting up to"' in wait_gates
    assert "$waitingLogged" in wait_gates and "$mutexPresent" in wait_gates
    assert "$beforeWait -eq (LayerFingerprint $installRoot)" in wait_gates
    assert "-not (Test-Path $waitSentinel)" in wait_gates
    assert wait_gates.index("$contender = Start-SandboxedSetup -Executable $Setup") < wait_gates.index("Stop-Process -Id $standIn.Id")
    assert "-not (Test-Path $contenderRoot)" in wait_gates
    assert "$contender.ExitCode -ne 0" in wait_gates
    assert "$contenderBlocked -and $finishedAfter" in wait_gates


def test_timeout_gate_requires_self_exit_without_a_window(gates: str) -> None:
    timeout = gates.split("# --- Gate 16", 1)[1].split("# --- Gate 11", 1)[0]
    assert "[switch]$RunWaitPidTimeout" in gates
    assert "if ($RunWaitPidTimeout)" in timeout
    # Mandatory silent flags belong to the checked launch helper on this branch.
    helper = gates.split("function Start-SandboxedSetup {", 1)[1].split("\nfunction ", 1)[0]
    assert '$arguments = @("/VERYSILENT", "/SUPPRESSMSGBOXES") + $ExtraArguments' in helper
    assert "-ArgumentList $arguments" in helper
    assert "$timingOut = Start-SandboxedSetup -Executable $Setup" in timeout
    for switch in ("/WAITPID=", "/OUTCOME=", "/LOG=", "/RELAUNCH"):
        assert switch in timeout
    assert "$timingOut.WaitForExit(250)" in timeout
    assert "$timeoutClock.Elapsed.TotalSeconds -lt 150" in timeout
    assert "EnumWindows" in timeout and "IsWindowVisible" in timeout
    assert "private static extern bool IsWindowVisible(IntPtr window);" in timeout
    assert "ids.Contains((int)processId) && IsWindowVisible(window)" in timeout
    assert "GetWindowThreadProcessId" in timeout
    assert "Get-CimInstance Win32_Process" in timeout
    assert "$_.ParentProcessId -in $timeoutProcessIds" in timeout
    assert "HasVisibleWindow([int[]]$timeoutProcessIds)" in timeout
    assert "$noRelaunch = $timeoutText -notmatch '-- Run entry --'" in timeout
    # Forced cleanup cannot satisfy the self-exit condition.
    assert timeout.index("$timeoutReturned = $timingOut.WaitForExit") < timeout.index("Stop-Process")
    criteria = timeout.split("$timeoutOk =", 1)[1].split("\n", 1)[0]
    for required in (
        "$timeoutReturned", "$timeoutExit -ne 0", "-not $timeoutWindowSeen",
        "$standInSurvived", "was still running after 120 s",
        '$timeoutData.result -eq "failed"', "$failureWrites -eq 1",
        "$timeoutUntouched", "$noRelaunch",
    ):
        assert required in criteria
    assert "Gate 16 " in timeout
