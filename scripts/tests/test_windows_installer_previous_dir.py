"""Static checks for how the Windows installer treats a previous install folder.

``UsePreviousAppDir=yes`` with Inno's default ``DisableDirPage=auto`` made every
upgrade reuse the registered folder without showing the directory page, so a
folder chosen once (``C:\\wg``) was reused forever. The interactive wizard now
offers the standard folder when the registered one is not standard; unattended
runs, the in-app updater above all, must never move an install.

Inno Setup cannot run where this suite runs, so these tests read
``bundle-setup.iss`` as text, like the other installer tests. They pin the
properties a careless edit would lose. The behavioural proof is a Windows run
of the wizard; see the docs/validation/windows-installer-gates.md section on
the install folder.
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


def _setup_section(script: str) -> str:
    return script.split("\n[Setup]\n", 1)[1].split("\n[Languages]", 1)[0]


def test_the_directory_page_decision_moves_into_code(script: str, code: str) -> None:
    setup = _setup_section(script)
    assert "\nUsePreviousAppDir=yes\n" in setup
    # auto would hide the page before ShouldSkipPage is ever asked.
    assert "\nDisableDirPage=no\n" in setup
    skip = _body(code, "function ShouldSkipPage(PageID: Integer): Boolean;")
    # Inno's auto rule (skip when a previous folder is registered), except for
    # the one case ChooseInstallDir marks. Nothing else may be skipped here.
    assert re.search(
        r"begin\s+Result := \(PageID = wpSelectDir\) and \(WizardForm\.PrevAppDir <> ''\) and\s+"
        r"not OfferStandardDir;\s+end;",
        skip,
    )


def test_the_offered_folder_is_the_default_folder(script: str, code: str) -> None:
    default = re.search(r"^DefaultDirName=(.+)$", _setup_section(script), re.MULTILINE)
    assert default and default.group(1) == r"{localappdata}\Programs\Waveguide Generator"
    standard = _body(code, "function StandardInstallDir(): String;")
    assert f"Result := ExpandConstant('{default.group(1)}');" in standard


def test_only_an_interactive_run_without_dir_is_offered_the_standard_folder(code: str) -> None:
    choose = _body(code, "procedure ChooseInstallDir();")
    assert "PreviousInstallDir := WizardForm.PrevAppDir;" in choose
    order = [
        "OfferStandardDir := False;",
        "if PreviousInstallDir = '' then\n    exit;",
        "if IsStandardInstallDir(PreviousInstallDir) then",
        "if WizardSilent() or (ExpandConstant('{param:DIR|}') <> '') then",
        "OfferStandardDir := True;",
        "WizardForm.DirEdit.Text := StandardInstallDir();",
    ]
    positions = [choose.index(item) for item in order]
    assert positions == sorted(positions)
    # Each guard leaves before the offer is made.
    for guard in order[2:4]:
        branch = choose.split(guard, 1)[1].split("end;", 1)[0]
        assert "exit;" in branch, guard
    # The only places the chosen folder or the flag are ever set.
    assert code.count("WizardForm.DirEdit.Text :=") == 1
    assert code.count("OfferStandardDir := True;") == 1
    initialize = _body(code, "procedure InitializeWizard();")
    assert initialize.split("begin", 1)[1].lstrip().startswith("ChooseInstallDir();")


def test_the_standard_rule_is_strict_descent_from_four_named_roots(code: str) -> None:
    standard = _body(code, "function IsStandardInstallDir(const Dir: String): Boolean;")
    roots = re.findall(r"DirIsInside\(Dir, ExpandConstant\('([^']+)'\)\)", standard)
    assert roots == [r"{localappdata}\Programs", "{userpf}", "{commonpf64}", "{commonpf32}"]
    inside = _body(code, "function DirIsInside(const Dir, Root: String): Boolean;")
    # The separator goes on before the prefix test, so "Program Files (x86)"
    # is not inside "Program Files", and the root itself is not inside it.
    assert "Prefix := AddBackslash(Prefix);" in inside
    assert "(Length(Candidate) > Length(Prefix))" in inside
    assert "(Copy(Candidate, 1, Length(Prefix)) = Prefix)" in inside
    comparable = _body(code, "function ComparableDir(const Dir: String): String;")
    # . and .. folded, and compared without case, as Windows does.
    assert "AnsiLowerCase(RemoveBackslashUnlessRoot(ExpandFileName(Trim(Dir))))" in comparable
    assert "if Trim(Dir) = '' then\n    exit;" in comparable


def test_nothing_in_the_previous_folder_is_deleted_or_run(code: str) -> None:
    names = ("ChooseInstallDir", "NotePreviousCopy", "HoldsWaveguideGenerator", "ShouldSkipPage")
    for name in names:
        routine = _body(code, re.search(rf"(?:procedure|function) {name}\b[^\n]*", code).group(0))
        for forbidden in ("DelTree", "DeleteFile", "RemoveDir", "RenameFile", "Exec(", "ShellExec", "RunNative"):
            assert forbidden not in routine, (name, forbidden)
    for line in code.splitlines():
        if "PreviousInstallDir" in line:
            assert not re.search(r"\b(?:DelTree|DeleteFile|RemoveDir|RenameFile|Exec|ShellExec)\b", line), line


def test_a_copy_left_behind_is_reported_after_commit(code: str) -> None:
    step = _body(code, "procedure CurStepChanged(CurStep: TSetupStep);")
    post = step.split("if CurStep = ssPostInstall then", 1)[1]
    assert post.index("CommitProtectedReplace();") < post.index("NotePreviousCopy();")
    assert post.index("NotePreviousCopy();") < post.index("if WizardIsTaskSelected(WgLinkTaskName) then")
    note = _body(code, "procedure NotePreviousCopy();")
    assert "SameDir(PreviousInstallDir, ExpandConstant('{app}'))" in note
    assert "if not HoldsWaveguideGenerator(PreviousInstallDir) then" in note
    # The advice the user needs, including why the old uninstaller is unsafe
    # and the WGLink add-in that may still run from the old copy.
    assert "Do not run the uninstaller inside it" in note
    assert "Installed apps" in note
    assert "WGLink" in note
    holds = _body(code, "function HoldsWaveguideGenerator(const Dir: String): Boolean;")
    assert "'Waveguide Generator.exe'" in holds and "'app\\APP-MANIFEST.json'" in holds
    finish = _body(code, "procedure CurPageChanged(CurPageID: Integer);")
    assert "if PreviousCopyNotice <> '' then" in finish


def test_the_directory_page_notice_exists_only_for_the_offer(code: str) -> None:
    choose = _body(code, "procedure ChooseInstallDir();")
    offer = choose.split("OfferStandardDir := True;", 1)[1]
    assert "PreviousDirNotice := TNewStaticText.Create(WizardForm);" in offer
    assert "PreviousDirNotice.Parent := WizardForm.SelectDirPage;" in offer
    # A path cannot wrap, so it is shortened to the width it has.
    assert "MinimizePathName(PreviousInstallDir, PreviousDirNotice.Font, PreviousDirNotice.Width)" in offer
    assert code.count("TNewStaticText.Create(") == 1


def test_the_in_app_updater_names_the_folder_and_runs_silently() -> None:
    """The guards above rely on this: an update is silent and carries /DIR."""

    source = (ROOT / "launchers/full_installer.py").read_text(encoding="utf-8")
    command = source.split("command = [str(request.installer),", 1)[1].split("]", 1)[0]
    assert '"/VERYSILENT"' in command
    assert 'f"/DIR={install_root}"' in command
