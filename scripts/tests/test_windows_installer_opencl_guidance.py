"""Finish-page structure checks; compile, layout and browser opening need Windows."""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from scripts import build_bundle

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def script() -> str:
    return (ROOT / "installers/windows/bundle-setup.iss").read_text(encoding="utf-8")


def body(script: str, header: str) -> str:
    start = script.index(header)
    following = re.search(r"\n(?:procedure|function) ", script[start + len(header):])
    end = start + len(header) + following.start() if following else len(script)
    return script[start:end]


def test_installer_uses_generated_constants_and_ships_the_help_page(script: str) -> None:
    assert '[Code]\nconst\n#include "opencl-guidance.iss"' in script
    files = script.split("\n[Files]\n", 1)[1].split("\n[Icons]", 1)[0]
    for layer in ("app", "runtime", "recovery"):
        assert (f'Source: "{{#PayloadDir}}\\{layer}\\*"; DestDir: "{{app}}\\{layer}"; '
                'Flags: recursesubdirs createallsubdirs ignoreversion') in files
    assert ('Source: "{#PayloadDir}\\*"; DestDir: "{app}\\.wg-install-new"; '
            'Excludes: "Waveguide Generator.exe"; Flags: ignoreversion') in files
    assert ('Source: "{#PayloadDir}\\Waveguide Generator.exe"; '
            'DestName: "wg-installer-helper.exe"; Flags: dontcopy') in files
    # Layers retain recursive packaged coverage; boot files stage for native
    # commit, and the one excluded public entry is published by that helper.
    assert "RunNative('--installer-entry')" in script
    assert "RunNative('--installer-commit')" in script
    # The generated page is part of the measured, manifested payload, not an
    # extra file injected by setup after the bundle was built.
    assert "shared" in build_bundle.APP_SOURCE_DIRECTORIES
    assert (ROOT / "shared/opencl-guidance.html").is_file()
    assert "opencl-guidance.html" not in files
    guidance = json.loads((ROOT / "shared/opencl-driver-guidance.v1.json").read_text(encoding="utf-8"))
    platform = guidance["platforms"]["windows"]
    for text in [platform["summary"], *[step["url"] for step in platform["steps"]],
                 *[warning["text"] for warning in guidance["warnings"]]]:
        assert text not in script, "The installer must not hand-copy the shared wording or URLs"


def test_help_button_is_created_only_for_the_interactive_finish_page(script: str) -> None:
    initialize = body(script, "procedure InitializeWizard();")
    interactive = initialize.split("if not WizardSilent() then\n  begin", 1)[1].split("\n  end;", 1)[0]
    assert "OpenClNotice := TNewMemo.Create(WizardForm);" in interactive
    assert "OpenClNotice.Parent := WizardForm.FinishedPage;" in interactive
    assert "OpenClNotice.ReadOnly := True;" in interactive
    assert "OpenClNotice.WordWrap := True;" in interactive
    assert "OpenClNotice.ScrollBars := ssVertical;" in interactive
    assert "OpenClHelpButton := TNewButton.Create(WizardForm);" in interactive
    assert "OpenClHelpButton.Parent := WizardForm.FinishedPage;" in interactive
    assert "OpenClHelpButton.Caption := OpenClGuidanceTitle;" in interactive
    assert "OpenClHelpButton.OnClick := @OpenClHelpClick;" in interactive
    assert script.index("procedure OpenClHelpClick(") < script.index("procedure InitializeWizard();")


def test_finish_notice_is_silent_safe_and_preserves_wglink_outcome(script: str) -> None:
    finish = body(script, "procedure CurPageChanged(CurPageID: Integer);")
    assert "if (CurPageID <> wpFinished) or WizardSilent() then\n    exit;" in finish
    assert "if WgLinkStatus <> '' then" in finish
    assert "'Waveguide Generator was installed.' + #13#10#13#10 + WgLinkStatus;" in finish
    # Guidance is outside the WGLink condition: even no outcome must get it.
    # Only a positive detection replaces the install advice; the default
    # branch is the full generated guidance. The note about a copy left at a
    # previous install folder sits between them, outside both conditions.
    assert "WgLinkStatus;\n  if PreviousCopyNotice <> '' then" in finish
    # A removed desktop shortcut is mentioned on its own only when no
    # previous-copy note (which already covers it) is shown.
    assert "PreviousCopyNotice\n  else if DesktopShortcutRemoved then" in finish
    assert "tick \"Create a desktop shortcut\".';\n  { Hide the install advice" in finish
    assert "if CpuOpenClRuntimeRegistered() then" in finish
    assert "OpenClGuidanceTitle + #13#10 + OpenClGuidanceText;" in finish
    assert finish.index("  else\n    OpenClNotice.Text") > finish.index("CpuOpenClRuntimeRegistered()")
    assert "WizardForm.FinishedLabel.Visible := False;" in finish
    assert "WizardForm.RunList.Height := ScaleY(28);" in finish
    assert "WizardForm.RunList.Top := OpenClHelpButton.Top - WizardForm.RunList.Height - ScaleY(8);" in finish
    assert "OpenClNotice.SetBounds(WizardForm.FinishedLabel.Left," in finish
    assert "WizardForm.RunList.Top - WizardForm.FinishedLabel.Top - ScaleY(8));" in finish


def test_help_opens_only_on_click_and_never_blocks_installation(script: str) -> None:
    click = body(script, "procedure OpenClHelpClick(Sender: TObject);")
    assert "if WizardSilent() then\n    exit;" in click
    assert "ShellExec('open', ExpandConstant('{app}\\app\\shared\\opencl-guidance.html')" in click
    assert "SW_SHOWNORMAL, ewNoWait, ErrorCode)" in click
    assert "Log(" in click
    assert "RaiseException" not in click and "MsgBox" not in click
    assert script.count("ShellExec('open',") == 1
    assert script.count("@OpenClHelpClick") == 1
    assert script.count("OpenClHelpClick(") == 1
    # No extra [Run] action: silent installs and Finish never launch help.
    run = script.split("\n[Run]\n", 1)[1].split("\n[UninstallDelete]", 1)[0]
    assert "opencl" not in run.lower()


def test_runtime_detection_is_conservative(script: str) -> None:
    detect = body(script, "function CpuOpenClRuntimeRegistered(): Boolean;")
    # Only an enabled, existing Intel CPU runtime ICD hides the advice.
    assert "Khronos" in detect and "OpenCL" in detect and "Vendors" in detect
    assert "'intelocl64.dll'" in detect and "FileExists(Dll)" in detect
    assert "(Disabled <> 0)" in detect
    # The Khronos ICD loader reads the vendors key under HKLM only.
    assert "HKEY_LOCAL_MACHINE" in detect and "HKEY_CURRENT_USER" not in detect
    # The default is "not found", so any doubt shows the full guidance.
    assert detect.split("begin\n", 1)[1].startswith("  Result := False;")
