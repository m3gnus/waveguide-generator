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
; The version a person reads and the version Windows stores are not the same
; string. VersionInfoVersion is written into the binary VERSIONINFO resource --
; up to four dot-separated NUMBERS, four 16-bit words -- so a build of `main`
; named 0.4.0-main.7 is not a value it accepts, and ISCC refuses the compile.
; The build supplies both: AppVersion keeps the SemVer identity everywhere a
; person sees it, VersionInfoVersion carries its numeric form.
; https://jrsoftware.org/ishelp/topic_setup_versioninfoversion.htm
#ifndef VersionInfoVersion
  #error VersionInfoVersion must be defined by the build
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
VersionInfoVersion={#VersionInfoVersion}
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
; A prior interactive selection must never become consent for an unattended
; upgrade. With Inno's default UsePreviousTasks=yes, /VERYSILENT could restore
; the old wglink task even when this invocation names no /TASKS option. The
; interactive wizard still makes a fresh Fusion-aware recommendation below;
; silent deployment must opt in on every invocation.
UsePreviousTasks=no

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
SetupIconFile={#PayloadDir}\WaveguideGenerator.ico
; The launcher is a renamed pythonw.exe and nothing patches its resources,
; so its embedded icon is Python's. Point every icon Windows shows at the
; .ico the build stages beside it instead.
UninstallDisplayIcon={app}\WaveguideGenerator.ico
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
; Keep the outcome of the optional WGLink action available after setup exits.
; This is particularly important for a silent deployment, where the final page
; is absent and the setup log is the only actionable record.
SetupLogging=yes
UninstallLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
; WGLink changes Fusion's per-user AddIns directory, so it is a separate,
; visible choice rather than a side effect of starting Waveguide Generator.
; It is preselected only in the interactive wizard and only when an existing
; Fusion AddIns directory was found. Silent installs must name /TASKS="wglink"
; explicitly; otherwise they preserve the user's non-consent.
Name: "wglink"; Description: "Install the &WGLink add-in for Autodesk Fusion"; GroupDescription: "Fusion integration:"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Waveguide Generator"; Filename: "{app}\Waveguide Generator.exe"; IconFilename: "{app}\WaveguideGenerator.ico"
Name: "{group}\Uninstall Waveguide Generator"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Waveguide Generator"; Filename: "{app}\Waveguide Generator.exe"; IconFilename: "{app}\WaveguideGenerator.ico"; Tasks: desktopicon

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
const
  WgLinkTaskName = 'wglink';
  WgLinkMarkerName = 'wglink_install.json';
  WgLinkDeveloperMarkerName = 'wglink_dev.json';
  WgLinkTransactionJournalName = '.WGLink-install-transaction.json';

var
  WgLinkStatus: String;

function SetEnvironmentVariable(Name, Value: String): Boolean;
  { No setuponly/uninstallonly qualifier: this process-local Windows API is
    required by both setup and CurUninstallStepChanged. Inno imports an
    unqualified external into both the setup and uninstaller executables. }
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

procedure WgLinkOutput(const S: String; const Error, FirstLine: Boolean);
begin
  if Error then
    Log('WGLink stderr: ' + S)
  else
    Log('WGLink stdout: ' + S);
end;

function WgLinkAddInsDirectory(): String;
var
  OverrideDir, Legacy, Current: String;
begin
  { WGLINKADDINSDIR is deliberately an undocumented gate hook. It permits the
    release gate to exercise the real setup executable against a disposable
    directory without pretending Fusion is installed. The directory must
    already exist: ordinary setup never creates a Fusion-looking tree merely
    because a command-line value was misspelled. }
  OverrideDir := ExpandConstant('{param:WGLINKADDINSDIR|}');
  if (OverrideDir <> '') and DirExists(OverrideDir) then
  begin
    Result := OverrideDir;
    exit;
  end;

  Legacy := ExpandConstant('{userappdata}\Autodesk\Autodesk Fusion 360\API\AddIns');
  Current := ExpandConstant('{userappdata}\Autodesk\Autodesk Fusion\API\AddIns');
  if DirExists(Legacy) then
    Result := Legacy
  else if DirExists(Current) then
    Result := Current
  else
    Result := '';
end;

function WgLinkTarget(AddInsDirectory: String): String;
begin
  Result := AddBackslash(AddInsDirectory) + 'WGLink';
end;

function WgLinkManagedByThisInstall(Target: String): Boolean;
var
  AddInsDirectory, Parameters: String;
  ExitCode: Integer;
  PreviousBundleFlag, PreviousAppRoot: String;
begin
  Result := False;
  if not FileExists(ExpandConstant('{app}\runtime\python.exe')) then
    exit;
  { Let install_wglink.py decide ownership. Its structured marker validator is
    the authority for schema, types, pin and developer-marker precedence; a
    substring check here could disagree with it and report an update that the
    Python installer correctly preserved. }
  AddInsDirectory := ExtractFileDir(Target);
  PreviousBundleFlag := GetEnv('WG2_BUNDLE');
  PreviousAppRoot := GetEnv('WG2_APP_ROOT');
  SetEnvironmentVariable('WG2_BUNDLE', '1');
  SetEnvironmentVariable('WG2_APP_ROOT', ExpandConstant('{app}\app'));
  try
    Parameters :=
      AddQuotes(ExpandConstant('{app}\app\scripts\install_wglink.py')) +
      ' --print-managed-target --root ' + AddQuotes(ExpandConstant('{app}\app')) +
      ' --platform windows --addins-dir ' + AddQuotes(AddInsDirectory);
    if ExecAndLogOutput(
      ExpandConstant('{app}\runtime\python.exe'), Parameters, ExpandConstant('{app}'),
      SW_HIDE, ewWaitUntilTerminated, ExitCode, @WgLinkOutput
    ) then
      Result := ExitCode = 0
    else
      Log('WGLink ownership query could not start for ' + Target + '.');
  finally
    SetEnvironmentVariable('WG2_BUNDLE', PreviousBundleFlag);
    SetEnvironmentVariable('WG2_APP_ROOT', PreviousAppRoot);
  end;
end;

function WgLinkHasMarker(Target: String): Boolean;
begin
  Result := FileExists(AddBackslash(Target) + WgLinkMarkerName);
end;

function WgLinkHasDeveloperMarker(Target: String): Boolean;
begin
  Result := FileExists(AddBackslash(Target) + WgLinkDeveloperMarkerName);
end;

function FusionDetected(): Boolean;
begin
  Result := WgLinkAddInsDirectory() <> '';
end;

procedure InstallWGLink();
var
  AddInsDirectory, Target, Parameters: String;
  ExitCode: Integer;
  PreviousBundleFlag, PreviousAppRoot: String;
  WasManaged: Boolean;
begin
  AddInsDirectory := WgLinkAddInsDirectory();
  if AddInsDirectory = '' then
  begin
    WgLinkStatus :=
      'WGLink was not installed because Autodesk Fusion was not detected.' + #13#10 +
      'Install Fusion first, then run this installer again and select WGLink.';
    Log('WGLink: skipped; no existing Fusion AddIns directory was found.');
    exit;
  end;

  Target := WgLinkTarget(AddInsDirectory);
  WasManaged := False;
  if not WgLinkHasDeveloperMarker(Target) then
    WasManaged := WgLinkManagedByThisInstall(Target);
  if not FileExists(ExpandConstant('{app}\runtime\python.exe')) then
  begin
    WgLinkStatus :=
      'WGLink could not be installed because the bundled Python runtime is missing.' + #13#10 +
      'Repair Waveguide Generator, then run the installer again.';
    Log('WGLink: failed; bundled runtime python.exe is missing.');
    exit;
  end;

  PreviousBundleFlag := GetEnv('WG2_BUNDLE');
  PreviousAppRoot := GetEnv('WG2_APP_ROOT');
  SetEnvironmentVariable('WG2_BUNDLE', '1');
  SetEnvironmentVariable('WG2_APP_ROOT', ExpandConstant('{app}\app'));
  try
    Parameters :=
      AddQuotes(ExpandConstant('{app}\app\scripts\install_wglink.py')) +
      ' --root ' + AddQuotes(ExpandConstant('{app}\app')) +
      ' --platform windows --offline-only --addins-dir ' + AddQuotes(AddInsDirectory);
    Log('WGLink: running the packaged installer for ' + Target);
    if not ExecAndLogOutput(
      ExpandConstant('{app}\runtime\python.exe'), Parameters, ExpandConstant('{app}'),
      SW_HIDE, ewWaitUntilTerminated, ExitCode, @WgLinkOutput
    ) then
    begin
      WgLinkStatus :=
        'WGLink could not be started. See the setup log for details, then run the installer again.';
      Log('WGLink: Exec failed to start the packaged installer.');
      exit;
    end;
  finally
    SetEnvironmentVariable('WG2_BUNDLE', PreviousBundleFlag);
    SetEnvironmentVariable('WG2_APP_ROOT', PreviousAppRoot);
  end;

  if ExitCode <> 0 then
  begin
    WgLinkStatus :=
      'WGLink could not be installed (exit code ' + IntToStr(ExitCode) + ').' + #13#10 +
      'See the setup log for details, then run the installer again.';
    Log('WGLink: packaged installer failed with exit code ' + IntToStr(ExitCode) + '.');
  end
  { The developer marker always wins, including if a stale or copied WG
    ownership marker happens to be beside it. install_wglink.py observes the
    same rule, so setup must not turn its preserved result into "updated". }
  else if WgLinkHasDeveloperMarker(Target) then
  begin
    WgLinkStatus :=
      'WGLink was not changed because its developer marker was preserved.' + #13#10 +
      'Remove that developer-managed copy yourself if you want this installer to manage WGLink.';
    Log('WGLink: preserved developer marker at ' + Target + '.');
  end
  else if WgLinkManagedByThisInstall(Target) then
  begin
    if WasManaged then
      WgLinkStatus := 'WGLink was updated. Restart Fusion to load the update.'
    else
      WgLinkStatus :=
        'WGLink was installed. Restart Fusion, then enable Run on Startup in Scripts and Add-Ins.';
    Log('WGLink: installed or updated managed copy at ' + Target + '.');
  end
  else if WgLinkHasMarker(Target) then
  begin
    WgLinkStatus :=
      'WGLink was not changed because an existing installation marker was preserved.' + #13#10 +
      'Remove that existing copy yourself if you want this installer to manage WGLink.';
    Log('WGLink: preserved non-owned target with an installation marker at ' + Target + '.');
  end
  else
  begin
    WgLinkStatus :=
      'WGLink was not changed because an existing non-Waveguide Generator copy was preserved.' + #13#10 +
      'Remove that copy yourself if you want this installer to manage WGLink.';
    Log('WGLink: preserved an existing non-managed copy at ' + Target + '.');
  end;
end;

procedure UninstallWGLink();
var
  AddInsDirectory, Target, Parameters, Journal: String;
  ExitCode: Integer;
  PreviousBundleFlag, PreviousAppRoot: String;
  HasTransaction: Boolean;
begin
  AddInsDirectory := WgLinkAddInsDirectory();
  if AddInsDirectory = '' then
  begin
    Log('WGLink uninstall: no Fusion AddIns directory was found; nothing to remove.');
    exit;
  end;

  Target := WgLinkTarget(AddInsDirectory);
  Journal := AddBackslash(AddInsDirectory) + WgLinkTransactionJournalName;
  HasTransaction := FileExists(Journal);
  { Do not invoke the Python cleanup for a developer copy or one owned by a
    different WG root. The script has the same guard, but this early branch
    means setup never even opens an external add-in while uninstalling. An
    interrupted replacement is the exception: install_wglink.py owns its
    durable recovery protocol, so it must see the journal before we decide
    whether the current target is ours. }
  if WgLinkHasDeveloperMarker(Target) and not HasTransaction then
  begin
    Log('WGLink uninstall: preserved developer-managed target at ' + Target + '.');
    exit;
  end;
  if (not WgLinkManagedByThisInstall(Target)) and not HasTransaction then
  begin
    Log('WGLink uninstall: preserved non-owned target at ' + Target + '.');
    exit;
  end;
  if not FileExists(ExpandConstant('{app}\runtime\python.exe')) then
  begin
    Log('WGLink uninstall: managed target preserved because bundled python.exe is missing.');
    exit;
  end;

  PreviousBundleFlag := GetEnv('WG2_BUNDLE');
  PreviousAppRoot := GetEnv('WG2_APP_ROOT');
  SetEnvironmentVariable('WG2_BUNDLE', '1');
  SetEnvironmentVariable('WG2_APP_ROOT', ExpandConstant('{app}\app'));
  try
    Parameters :=
      AddQuotes(ExpandConstant('{app}\app\scripts\install_wglink.py')) +
      ' --uninstall --yes --root ' + AddQuotes(ExpandConstant('{app}\app')) +
      ' --platform windows --addins-dir ' + AddQuotes(AddInsDirectory);
    if HasTransaction then
      Log('WGLink uninstall: recovering an interrupted replacement before managed cleanup.')
    else
      Log('WGLink uninstall: removing the managed target before bundle layers.');
    if not ExecAndLogOutput(
      ExpandConstant('{app}\runtime\python.exe'), Parameters, ExpandConstant('{app}'),
      SW_HIDE, ewWaitUntilTerminated, ExitCode, @WgLinkOutput
    ) then
      Log('WGLink uninstall: could not start the managed cleanup command.')
    else if ExitCode <> 0 then
      Log('WGLink uninstall: managed cleanup failed with exit code ' + IntToStr(ExitCode) + '.')
    else if DirExists(Target) and HasTransaction then
      Log('WGLink uninstall: recovery settled; the resulting non-owned target was preserved at ' + Target + '.')
    else if DirExists(Target) then
      Log('WGLink uninstall: managed cleanup returned success but left ' + Target + '.')
    else
      Log('WGLink uninstall: removed managed target ' + Target + '.');
  finally
    SetEnvironmentVariable('WG2_BUNDLE', PreviousBundleFlag);
    SetEnvironmentVariable('WG2_APP_ROOT', PreviousAppRoot);
  end;
end;

procedure InitializeWizard();
begin
  { A normal interactive setup may make the Fusion-aware recommendation. A
    silent invocation has no user to make that choice, so it must opt in with
    /TASKS="wglink" instead. }
  if FusionDetected() then
  begin
    Log('WGLink: Fusion AddIns directory detected at ' + WgLinkAddInsDirectory() + '.');
    if not WizardSilent() then
      WizardSelectTasks(WgLinkTaskName);
  end
  else
    Log('WGLink: no Fusion AddIns directory detected; task remains unchecked.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected(WgLinkTaskName) then
      InstallWGLink()
    else
    begin
      WgLinkStatus := 'WGLink was not installed because it was not selected.';
      Log('WGLink: not selected.');
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { usUninstall runs before [UninstallDelete], while both the app script and
    bundled runtime still exist. Keep the managed Fusion cleanup ahead of the
    app/runtime deletion below; external targets are preserved above. }
  if CurUninstallStep = usUninstall then
    UninstallWGLink();
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and (WgLinkStatus <> '') then
    WizardForm.FinishedLabel.Caption :=
      'Waveguide Generator was installed.' + #13#10#13#10 + WgLinkStatus;
end;

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
