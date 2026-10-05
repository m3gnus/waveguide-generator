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
    roots = re.findall(r"InsideStandardRoot\(Dir, ExpandConstant\('([^']+)'\)\)", standard)
    assert roots == [r"{localappdata}\Programs", "{userpf}", "{commonpf64}", "{commonpf32}"]
    assert "DirIsInside(" not in standard
    # A root constant that expanded to a bare drive must not make the whole
    # drive standard.
    guard = _body(code, "function InsideStandardRoot(const Dir, Root: String): Boolean;")
    assert "Result := (not IsDriveRoot(Root)) and DirIsInside(Dir, Root);" in guard
    assert code.index("function IsDriveRoot(") < code.index("function InsideStandardRoot(")
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
    names = (
        "ChooseInstallDir", "NotePreviousCopy", "HoldsWaveguideGenerator", "ShouldSkipPage",
        "ListPreviousCopy", "IsDriveRoot", "IsWaveguideGeneratorEntry", "InsideStandardRoot",
        "CanonicalDir", "DirsOverlap",
    )
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
    assert "if not HoldsWaveguideGenerator(PreviousInstallDir) then" in note
    # The advice the user needs, including why the old uninstaller is unsafe
    # and the WGLink add-in that may still run from the old copy.
    assert "Do not run the uninstaller there" in note
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
    # True even when the registered folder no longer exists.
    assert "'The previous installation is registered in:'" in offer
    assert "currently installed" not in offer
    assert code.count("TNewStaticText.Create(") == 1


def test_the_in_app_updater_names_the_folder_and_runs_silently() -> None:
    """The guards above rely on this: an update is silent and carries /DIR."""

    source = (ROOT / "launchers/full_installer.py").read_text(encoding="utf-8")
    command = source.split("command = [str(request.installer),", 1)[1].split("]", 1)[0]
    assert '"/VERYSILENT"' in command
    assert 'f"/DIR={install_root}"' in command


def _without_brace_comments(text: str) -> str:
    return re.sub(r"\{[^}]*\}", "", text)


def test_the_note_never_says_to_delete_the_folder_itself(code: str) -> None:
    """The previous folder can be a drive root or shared with other things;
    finding our exe or manifest there says nothing about the rest of it."""

    note = _body(code, "procedure NotePreviousCopy();")
    plain = _without_brace_comments(note).lower()
    for phrase in ("delete that folder", "delete the old folder", "delete the folder"):
        assert phrase not in plain, phrase
    assert "ListPreviousCopy(PreviousInstallDir, Owned, Foreign);" in note
    branch = note.split("if IsDriveRoot(PreviousInstallDir) or Foreign then", 1)[1]
    listed, otherwise = branch.split("\n  else\n", 1)
    # A drive root, or a folder with anything else in it, gets the exact list.
    assert "delete only these Waveguide Generator items in" in listed
    assert "nothing else there: ' + Owned" in listed
    # A folder holding only our entries gets the general wording.
    assert "delete the Waveguide Generator files in" in otherwise.split(";", 1)[0]

    root = _body(code, "function IsDriveRoot(const Dir: String): Boolean;")
    assert "ExtractFileDrive(ExpandFileName(Trim(Dir)))" in root
    listing = _body(code, "procedure ListPreviousCopy(")
    assert "FindFirst(AddBackslash(Dir) + '*', FindRec)" in listing
    assert "FindClose(FindRec);" in listing.split("finally", 1)[1]
    assert "Foreign := True;" in listing


def test_the_owned_names_match_what_uninstall_removes(script: str, code: str) -> None:
    """The note lists exactly the root entries [UninstallDelete] names, plus
    Inno's own unins###.exe and .dat."""

    section = script.split("\n[UninstallDelete]\n", 1)[1].split("\n[Code]\n", 1)[0]
    uninstall = {
        name.lower()
        for name in re.findall(r'^Type: \w+; Name: "\{app\}\\([^"\\]+)"', section, re.MULTILINE)
    }
    entry = _body(code, "function IsWaveguideGeneratorEntry(const Name: String): Boolean;")
    names = entry.split("Result := Pos('|' + Lower + '|',", 1)[1].split(") > 0;", 1)[0]
    owned = {name for name in "".join(re.findall(r"'([^']*)'", names)).split("|") if name}
    assert len(uninstall) > 15
    assert owned == uninstall
    assert "(Copy(Lower, 1, 5) = 'unins')" in entry
    assert "StrToIntDef(Copy(Lower, 6, 3), -1) >= 0" in entry


def test_the_note_covers_shortcuts_and_closing_the_app(code: str) -> None:
    note = _body(code, "procedure NotePreviousCopy();")
    # desktopicon is never remembered (UsePreviousTasks=no), so only this
    # run's choice can have replaced the old desktop shortcut.
    assert "if WizardIsTaskSelected('desktopicon') then" in note
    assert note.count("pin Waveguide Generator again from the Start menu") == 2
    assert "A desktop shortcut, or a taskbar or Start pin, made for the old copy still opens the old one" in note
    # Files of a running copy cannot be deleted; both wordings say so.
    assert note.count("closed Waveguide Generator") == 2


def test_the_note_never_points_at_the_install_just_made(code: str) -> None:
    """Equal, nested either way, or the same folder spelled differently (8.3
    name, junction, SUBST drive, mapped drive versus UNC): no note at all."""

    note = _body(code, "procedure NotePreviousCopy();")
    overlap_check = "if DirsOverlap(PreviousInstallDir, ExpandConstant('{app}')) then"
    assert overlap_check in note
    after = note.split(overlap_check, 1)[1]
    assert after.split("end;", 1)[0].rstrip().endswith("exit;")
    # Checked before anything is listed or any note is written.
    assert note.index(overlap_check) < note.index("HoldsWaveguideGenerator(")
    assert note.index(overlap_check) < note.index("ListPreviousCopy(")
    assert note.index(overlap_check) < note.index("PreviousCopyNotice :=\n")

    overlap = _body(code, "function DirsOverlap(const A, B: String): Boolean;")
    assert "Result := SameDir(A, B) or DirIsInside(A, B) or DirIsInside(B, A);" in overlap
    assert ("SameDir(CanonicalA, CanonicalB) or\n"
            "      DirIsInside(CanonicalA, CanonicalB) or DirIsInside(CanonicalB, CanonicalA);") in overlap
    assert "CanonicalA := CanonicalDir(A);" in overlap and "CanonicalB := CanonicalDir(B);" in overlap

    canonical = _body(code, "function CanonicalDir(const Dir: String): String;")
    # No access rights, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS: it can
    # neither create nor change anything.
    assert "CreateFileW(RemoveBackslashUnlessRoot(ExpandFileName(Trim(Dir))), 0, 7, 0, 3, $02000000, 0);" in canonical
    assert "GetFinalPathNameByHandleW(Handle, Result, 1024, 0)" in canonical
    assert "CloseHandle(Handle);" in canonical.split("finally", 1)[1]
    # Both long-path prefixes are stripped, UNC first, so the result compares
    # with an ordinary spelling.
    assert r"if Copy(Result, 1, 8) = '\\?\UNC\' then" in canonical
    assert r"Result := '\\' + Copy(Result, 9, Length(Result))" in canonical
    assert r"else if Copy(Result, 1, 4) = '\\?\' then" in canonical
    assert "GetFinalPathNameByHandleW@kernel32.dll stdcall setuponly" in code
