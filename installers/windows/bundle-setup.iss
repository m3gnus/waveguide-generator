; Waveguide Generator -- the Windows installer for the standalone bundle.
;
; NOT related to install-and-update.bat in this directory. That one installs
; from a Git checkout for people building from source; this one packages the
; self-contained bundle that scripts/build_bundle.py assembles, and is what a
; person downloads from a release. build_bundle.py invokes ISCC on this file
; and supplies every define below; there are no defaults, so a missing one is
; a compile error rather than a silently wrong installer.
;
; WHY AN INSTALLER AT ALL
;
; The .zip it replaces made users do two things by hand, and failing either
; produced a confusing error rather than a clear one:
;
;   1. Unblock the .zip before extracting. Explorer copies the download mark
;      onto every file it extracts, so the unsigned launcher then met
;      SmartScreen's "Windows protected your PC", whose only visible button is
;      "Don't run". An installer writes its payload itself, and files it writes
;      carry no Zone.Identifier -- measured 2026-08-27 with an installer that
;      still carried ZoneId=3 itself, whose payload came out clean. So the
;      installed app never meets SmartScreen. Setup.exe still does, once,
;      which is a single dialog at the moment the user chose to run something.
;
;   2. Extract to a short path. See the length check below.

#ifndef AppVersion
  #error AppVersion must be defined by the build
#endif
#ifndef PayloadDir
  #error PayloadDir must be defined by the build
#endif
#ifndef MaxPayloadDepth
  #error MaxPayloadDepth must be defined by the build
#endif

[Setup]
; Never change AppId. It is how Windows recognises an existing install as the
; same product, so a new value would leave the old one stranded in Apps &
; features with no way to remove it.
AppId={{D8F99D24-D991-4FB0-91FE-E86D79128D2B}
AppName=Waveguide Generator
AppVersion={#AppVersion}
AppVerName=Waveguide Generator {#AppVersion}
AppPublisher=Hornlab
VersionInfoVersion={#AppVersion}
VersionInfoProductName=Waveguide Generator
VersionInfoCompany=Hornlab

; PER-USER, AND NOT NEGOTIABLE.
;
; launchers/apply_update.py applies an update by renaming directories in place
; inside the install tree -- os.replace over `runtime` and `app`. It has no
; elevation path; the failure it surfaces is PermissionError / [WinError 5]
; Access is denied. So an install root the user cannot write is not a
; permissions inconvenience, it silently breaks in-app updates for every
; non-admin user, and the break appears later, at update time, far from here.
;
; PrivilegesRequiredOverridesAllowed is deliberately empty: without it, /ALLUSERS
; or an elevated launch would put the tree under Program Files and reintroduce
; exactly that. If you are tempted to "tidy up" this default to Program Files,
; that is the defect you would be shipping. It also means no UAC prompt, which
; matters while setup.exe is unsigned.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
DefaultDirName={localappdata}\Programs\Waveguide Generator
DefaultGroupName=Waveguide Generator
UsePreviousAppDir=yes

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
SetupIconFile={#PayloadDir}\WaveguideGenerator.ico
UninstallDisplayIcon={app}\Waveguide Generator.exe,0
UninstallDisplayName=Waveguide Generator

; The payload is ~200 MB across ~7700 mostly-small files, which is the case
; solid LZMA2 is for. Explorer's own extraction of the equivalent .zip took
; 252 seconds when measured; this is a large part of why.
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Every page here is a click between the user and a working app, so the ones
; that carry no decision are gone. What remains is the licence, the directory
; (which is the one choice that can go wrong, and is checked), and progress.
DisableWelcomePage=yes
DisableReadyPage=yes
LicenseFile={#PayloadDir}\app\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Waveguide Generator"; Filename: "{app}\Waveguide Generator.exe"
Name: "{group}\Uninstall Waveguide Generator"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Waveguide Generator"; Filename: "{app}\Waveguide Generator.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Waveguide Generator.exe"; Description: "Start Waveguide Generator"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Bytecode the installer never wrote, and so does not know to remove. The
; launcher redirects it to %LOCALAPPDATA%\WaveguideGenerator\cache, but only
; when LOCALAPPDATA is set; with it unset there is no prefix at all and Python
; writes __pycache__ beside every .py in the tree -- hundreds of directories,
; not one, which is why naming a single path here would not work.
;
; These two directories are the bundle's own layers and hold nothing a user
; put there, so removing them wholesale is safe. {app} itself is only removed
; if empty, which leaves anything the user added in the install root alone
; rather than trusting a wildcard with a path they were able to edit.
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\app"
Type: dirifempty; Name: "{app}"

[Code]
{ The bundle's own deepest relative path is measured at build time and passed
  in as MaxPayloadDepth, rather than written here as a number that would quietly
  rot the first time a dependency gains a deeper file. Windows resolves most
  path operations against MAX_PATH of 260 including the terminating null, so a
  usable path is 259 characters; the install root gets whatever is left after
  the payload's own depth and the separator joining them.

  Without this check the failure is Explorer's: it names one deep file, gives no
  hint that length is the cause, and leaves a half-written tree behind. }

function MaxRootLength(): Integer;
begin
  Result := 259 - 1 - {#MaxPayloadDepth};
end;

function TooLongMessage(Root: String): String;
begin
  Result :=
    'That folder is too long for Windows.' + #13#10#13#10 +
    'Waveguide Generator''s own files add up to {#MaxPayloadDepth} characters, and Windows' + #13#10 +
    'cannot open a path over 259. This folder is ' + IntToStr(Length(Root)) +
    ' characters, so it has to be' + #13#10 + 'at most ' + IntToStr(MaxRootLength()) + '.' + #13#10#13#10 +
    'A short path such as C:\wg always works. The app runs from anywhere;' + #13#10 +
    'only the length matters.';
end;

{ /DIR= skips the wizard's directory page, so a silent install would otherwise
  reach extraction with an unchecked root. This runs before the wizard exists
  and before any file is written.

  The WizardSilent split is not cosmetic. Returning a message from
  PrepareToInstall, or showing a MsgBox here, puts up a modal dialog that
  /SUPPRESSMSGBOXES does NOT cover -- measured 2026-08-27: a silent install with
  an over-long /DIR sat on a "Setup - Waveguide Generator" window indefinitely
  rather than failing. A silent run has nobody to answer a dialog, so it has to
  fail with an exit code instead. Setup that hangs is worse than setup that
  refuses: it takes a CI job's whole timeout with it and reports nothing. }
function InitializeSetup(): Boolean;
var
  Dir: String;
begin
  Result := True;
  Dir := ExpandConstant('{param:DIR|}');
  if (Dir <> '') and (Length(Dir) > MaxRootLength()) then
  begin
    if WizardSilent() then
      Log('Refusing /DIR: ' + IntToStr(Length(Dir)) +
          ' characters, over the limit of ' + IntToStr(MaxRootLength()))
    else
      MsgBox(TooLongMessage(Dir), mbError, MB_OK);
    Result := False;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then
    if Length(WizardDirValue) > MaxRootLength() then
    begin
      MsgBox(TooLongMessage(WizardDirValue), mbError, MB_OK);
      Result := False;
    end;
end;

{ Backstop for a root that reached this point without passing either check --
  an upgrade inheriting a previous directory, say. Guarded on WizardSilent for
  the reason above: InitializeSetup owns every silent path, and this one must
  never be the thing that blocks one. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if (not WizardSilent()) and (Length(WizardDirValue) > MaxRootLength()) then
    Result := TooLongMessage(WizardDirValue);
end;
