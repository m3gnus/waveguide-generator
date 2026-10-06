"""Static checks for how the Windows installer settles an existing desktop shortcut.

Every setup since 0.3.1 writes the same ``Waveguide Generator.lnk`` on the
desktop when the ``desktopicon`` task is ticked, and ``UsePreviousTasks=no``
means the silent in-app updater never ticks it. A shortcut left by an earlier
install therefore outlived every upgrade. Setup now removes a Waveguide
Generator desktop shortcut when the task is not ticked, except one a ticked run
recorded as the user's choice, which a silent run keeps and the wizard offers
again. A shortcut that points anywhere else, or cannot be read, is never
touched.

Inno Setup cannot run where this suite runs, so these tests read
``bundle-setup.iss`` as text, like the other installer tests. The behavioural
proof is a Windows run of the compiled setup over a real desktop shortcut.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def script() -> str:
    return (ROOT / "installers/windows/bundle-setup.iss").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code(script: str) -> str:
    return script.split("\n[Code]\n", 1)[1]


def _body(code: str, header: str) -> str:
    start = code.index(header)
    following = re.search(r"\n(?:procedure|function) ", code[start + len(header):])
    end = start + len(header) + following.start() if following else len(code)
    return code[start:end]


def test_the_choice_is_recorded_per_user(code: str) -> None:
    assert "DesktopShortcutKey = 'Software\\Hornlab\\Waveguide Generator';" in code
    assert "DesktopShortcutValue = 'DesktopShortcutChosen';" in code
    recorded = _body(code, "function DesktopShortcutRecorded(): Boolean;")
    assert "RegQueryDWordValue(HKCU, DesktopShortcutKey, DesktopShortcutValue, Value) and (Value = 1)" in recorded


def test_only_a_shortcut_to_an_existing_install_is_ours(code: str) -> None:
    ours = _body(code, "function DesktopShortcutIsOurs(const Link: String): Boolean;")
    assert "Result := False;" in ours
    # An unreadable shortcut leaves the function before Result can turn True.
    reader = ours.split("  try\n", 1)[1].split("  end;\n", 1)[0]
    assert "CreateOleObject('WScript.Shell')" in reader
    assert "Shell.CreateShortcut(Link)" in reader and "Shortcut.TargetPath" in reader
    assert "except" in reader and reader.rstrip().endswith("exit;")
    assert "CompareText(ExtractFileName(Target), 'Waveguide Generator.exe') = 0" in ours
    assert "FileExists(Target)" in ours
    assert "FileExists(AddBackslash(ExtractFileDir(Target)) + 'app\\APP-MANIFEST.json')" in ours
    assert "DeleteFile" not in ours


def test_settling_removes_only_ours_and_never_a_recorded_choice_when_silent(code: str) -> None:
    settle = _body(code, "procedure SettleDesktopShortcut();")
    ticked = settle.split("if WizardIsTaskSelected('desktopicon') then", 1)[1].split("    exit;\n  end;", 1)[0]
    assert "RegWriteDWordValue(HKCU, DesktopShortcutKey, DesktopShortcutValue, 1)" in ticked
    assert "DeleteFile" not in ticked
    order = [
        settle.index("if WizardIsTaskSelected('desktopicon') then"),
        settle.index("if WizardSilent() and DesktopShortcutRecorded() then"),
        settle.index("if not DesktopShortcutIsOurs(Link) then"),
        settle.index("if DeleteFile(Link) then"),
    ]
    assert order == sorted(order)
    assert settle.count("DeleteFile(") == 1
    removed = settle.split("if DeleteFile(Link) then", 1)[1]
    assert "DesktopShortcutRemoved := True;" in removed
    assert "RegDeleteValue(HKCU, DesktopShortcutKey, DesktopShortcutValue);" in removed
    # The declaration, the wizard's offer and this one: nothing else on the
    # desktop is named, let alone deleted.
    assert code.count("DesktopShortcutPath()") == 3
    assert "{autodesktop}" not in settle


def test_settling_runs_after_the_commit_and_before_the_previous_copy_note(code: str) -> None:
    step = _body(code, "procedure CurStepChanged(CurStep: TSetupStep);")
    post = step.split("if CurStep = ssPostInstall then", 1)[1]
    assert post.index("CommitProtectedReplace();") < post.index("SettleDesktopShortcut();") < post.index("NotePreviousCopy();")
    assert code.count("SettleDesktopShortcut();") == 2  # declaration and the one call


def test_the_wizard_offers_a_recorded_shortcut_again_interactive_only(code: str) -> None:
    init = _body(code, "procedure InitializeWizard();")
    assert ("DesktopShortcutPreselectPending := not WizardSilent() and not TaskChoiceOnCommandLine() and\n"
            "    DesktopShortcutRecorded() and DesktopShortcutIsOurs(DesktopShortcutPath());") in init
    assert "WizardSelectTasks(" not in init
    page = _body(code, "procedure CurPageChanged(CurPageID: Integer);")
    guard = "if (CurPageID = wpSelectTasks) and DesktopShortcutPreselectPending and not WizardSilent() then"
    preselect = page.split(guard, 1)[1].split("\n  end;", 1)[0]
    assert preselect.index("DesktopShortcutPreselectPending := False;") < preselect.index("WizardSelectTasks('desktopicon');")


def test_the_desktop_task_stays_unticked_by_default(script: str) -> None:
    assert "\nUsePreviousTasks=no\n" in script
    assert "Flags: unchecked" in script.split('Name: "desktopicon"', 1)[1].splitlines()[0]


def test_uninstall_forgets_the_choice(code: str) -> None:
    uninstall = _body(code, "procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);")
    assert "RegDeleteValue(HKCU, DesktopShortcutKey, DesktopShortcutValue);" in uninstall
    assert "RegDeleteKeyIfEmpty(HKCU, DesktopShortcutKey);" in uninstall
