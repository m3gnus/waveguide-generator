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
;
; /WAITPID times out before rename-aside at CurStepChanged(ssInstall). It
; writes a failed outcome, shows a MsgBox only interactively, then calls Abort.
; Abort at ssInstall exits with code 3: measured on Windows 11 with Inno Setup 6.7.3, 2026-09-30 (gate 16).
; Abort raises a silent exception; no timeout dialog or /RELAUNCH in silent mode.

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
; One setup per user at a time, and the name the launcher looks for: the
; per-user launcher refuses to start while this mutex exists (OpenMutexW), so
; an application cannot be started on a tree that setup is replacing. Inno
; creates it after InitializeSetup returns and holds it until setup exits.
; /WAITPID therefore runs at the start of ssInstall, with the mutex held.
SetupMutex=WaveguideGeneratorSetup

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
; The in-app updater runs this setup silently and asks for /RELAUNCH so the
; application comes back. Setup still holds SetupMutex while [Run] executes, and
; the launcher refuses to start under that mutex, so the start goes through a
; short delay that lets setup exit first.
Filename: "{cmd}"; Parameters: "/C ping -n 4 127.0.0.1 >nul & start """" ""{app}\Waveguide Generator.exe"""; Flags: nowait runhidden; Check: RelaunchRequested

[UninstallDelete]
; Bytecode the installer never wrote, and so does not know to remove. The
; launcher redirects it to %LOCALAPPDATA%\WaveguideGenerator\cache, but only
; when LOCALAPPDATA is set; with it unset there is no prefix at all and Python
; writes __pycache__ beside every .py in the tree -- hundreds of directories,
; not one, which is why naming a single path here would not work.
;
; These three directories are the bundle's own and hold nothing a user put
; there, so removing them wholesale is safe. {app} itself is only removed if
; empty, which leaves anything the user added in the install root alone rather
; than trusting a wildcard with a path they were able to edit.
;
; recovery is listed for the same reason as the two layers. Its
; sitecustomize.py is imported by every start of the bundled interpreter,
; before the launcher has redirected the bytecode cache, so an ordinary launch
; writes recovery\__pycache__. Without this entry an uninstall after any use
; left that folder behind, and {app} with it.
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\app"
; Left by an upgrade that was killed mid-copy; see BeginProtectedReplace.
Type: filesandordirs; Name: "{app}\.app.old"
Type: filesandordirs; Name: "{app}\.runtime.old"
Type: files; Name: "{app}\.upgrade-in-progress"
Type: filesandordirs; Name: "{app}\recovery"
Type: dirifempty; Name: "{app}"

[Code]
const
#include "opencl-guidance.iss"
  WgLinkTaskName = 'wglink';
  WgLinkMarkerName = 'wglink_install.json';
  WgLinkDeveloperMarkerName = 'wglink_dev.json';
  WgLinkTransactionJournalName = '.WGLink-install-transaction.json';
  { launchers/apply_update.py STAGING_ROOT_SUFFIX: kept identical so this
    installer and the server name the same folder. }
  UpdateStagingRootSuffix = '.update-staging';
  { Prefixed: Inno Setup 6.7.3 predefines FILE_ATTRIBUTE_REPARSE_POINT, and a
    second declaration is a "Duplicate identifier" compile error there. }
  WG_FILE_ATTRIBUTE_REPARSE_POINT = $400;
  INVALID_FILE_ATTRIBUTES = -1;
  { The Inno uninstall key of this product (AppId above, plus "_is1"). Setup
    is per-user, so it lives in HKCU. Read only to report the version being
    replaced in the outcome record. }
  UninstallRegistryKey =
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{D8F99D24-D991-4FB0-91FE-E86D79128D2B}_is1';
  { Rename-aside names for install-time protection (BeginProtectedReplace). }
  AppLayerName = 'app';
  RuntimeLayerName = 'runtime';
  AppAsideName = '.app.old';
  RuntimeAsideName = '.runtime.old';
  ProtectionMarkerName = '.upgrade-in-progress';
  SYNCHRONIZE = $00100000;
  WAIT_OBJECT_0 = 0;
  WaitForProcessLimitMs = 120000;

var
  OpenClHelpButton: TNewButton;
  OpenClNotice: TNewMemo;
  WgLinkStatus: String;
  PreviousVersion: String;
  { True once replacement began at ssInstall: this run owns the layer folders. }
  ProtectionStarted: Boolean;
  { True once ssPostInstall was reached: the new tree is complete and the old
    one may be deleted. Nothing may be restored after this. }
  ProtectionCommitted: Boolean;
  OutcomeWritten: Boolean;

function SetEnvironmentVariable(Name, Value: String): Boolean;
  { No setuponly/uninstallonly qualifier: this process-local Windows API is
    required by both setup and CurUninstallStepChanged. Inno imports an
    unqualified external into both the setup and uninstaller executables. }
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

function GetFileAttributesW(lpFileName: String): Integer;
  { Pascal Script has no built-in reparse-point check. A junction or symlink
    left at the staging path -- deliberately or by another program -- must
    never be followed and deleted; this is how RemoveUpdateStagingRoot below
    tells one apart from an ordinary directory before touching it. }
  external 'GetFileAttributesW@kernel32.dll stdcall';

function OpenProcess(DesiredAccess: Integer; InheritHandle: Integer; ProcessId: Integer): Integer;
  external 'OpenProcess@kernel32.dll stdcall setuponly';

function WaitForSingleObject(Handle: Integer; Milliseconds: Integer): Integer;
  external 'WaitForSingleObject@kernel32.dll stdcall setuponly';

function CloseHandle(Handle: Integer): Integer;
  external 'CloseHandle@kernel32.dll stdcall setuponly';

function IsReparsePoint(const Path: String): Boolean;
var
  Attributes: Integer;
begin
  Attributes := GetFileAttributesW(Path);
  Result := (Attributes <> INVALID_FILE_ATTRIBUTES) and
    ((Attributes and WG_FILE_ATTRIBUTE_REPARSE_POINT) <> 0);
end;

procedure WgLinkOutput(const S: String; const Error, FirstLine: Boolean);
begin
  if Error then
    Log('WGLink stderr: ' + S)
  else
    Log('WGLink stdout: ' + S);
end;

function WgLinkAddInsOverrideSpecified(): Boolean;
var
  Index: Integer;
  Argument: String;
begin
  Result := False;
  for Index := 1 to ParamCount do
  begin
    Argument := Uppercase(ParamStr(Index));
    if (Argument = '/WGLINKADDINSDIR') or
       (Pos('/WGLINKADDINSDIR=', Argument) = 1) then
    begin
      Result := True;
      exit;
    end;
  end;
end;

function ValidateWgLinkAddInsOverride(Silent: Boolean): Boolean;
var
  OverrideDir, Reason: String;
begin
  Result := True;
  if not WgLinkAddInsOverrideSpecified() then
    exit;
  OverrideDir := ExpandConstant('{param:WGLINKADDINSDIR|}');
  if (OverrideDir <> '') and DirExists(OverrideDir) then
    exit;
  Reason := 'Refusing /WGLINKADDINSDIR: supply an existing directory. No files have been changed. Value: ' + OverrideDir;
  Log(Reason);
  if not Silent then
    MsgBox(Reason, mbError, MB_OK);
  Result := False;
end;

function InitializeUninstall(): Boolean;
begin
  { Initialization refusal returns a nonzero exit before any uninstall deletion. }
  Result := ValidateWgLinkAddInsOverride(UninstallSilent());
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
  if WgLinkAddInsOverrideSpecified() then
  begin
    Result := '';
    if (OverrideDir <> '') and DirExists(OverrideDir) then
      Result := OverrideDir
    else
      Log('WGLink: invalid /WGLINKADDINSDIR; skipping install or uninstall without Fusion fallback: ' + OverrideDir);
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

procedure RecordWGLinkSetupChoice();
var
  Parameters: String;
  ExitCode: Integer;
  PreviousBundleFlag, PreviousAppRoot: String;
begin
  if not FileExists(ExpandConstant('{app}\runtime\python.exe')) then
  begin
    Log('WGLink setup choice: bundled runtime python.exe is missing; continuing setup.');
    exit;
  end;
  PreviousBundleFlag := GetEnv('WG2_BUNDLE');
  PreviousAppRoot := GetEnv('WG2_APP_ROOT');
  SetEnvironmentVariable('WG2_BUNDLE', '1');
  SetEnvironmentVariable('WG2_APP_ROOT', ExpandConstant('{app}\app'));
  try
    Parameters :=
      AddQuotes(ExpandConstant('{app}\app\scripts\install_wglink.py')) +
      ' --record-setup-choice';
    Log('WGLink setup choice: recording the selected task.');
    if not ExecAndLogOutput(
      ExpandConstant('{app}\runtime\python.exe'), Parameters, ExpandConstant('{app}'),
      SW_HIDE, ewWaitUntilTerminated, ExitCode, @WgLinkOutput
    ) then
      Log('WGLink setup choice: could not start the recording command; continuing setup.')
    else if ExitCode <> 0 then
      Log('WGLink setup choice: recording failed with exit code ' + IntToStr(ExitCode) + '; continuing setup.');
  finally
    SetEnvironmentVariable('WG2_BUNDLE', PreviousBundleFlag);
    SetEnvironmentVariable('WG2_APP_ROOT', PreviousAppRoot);
  end;
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
    if WgLinkAddInsOverrideSpecified() then
    begin
      WgLinkStatus :=
        'WGLink could not be installed because /WGLINKADDINSDIR is not an existing directory.' + #13#10 +
        'Create a usable directory, then run the installer again and select WGLink.';
      Log('WGLink: failed; invalid AddIns override.');
      exit;
    end;
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

procedure OpenClHelpClick(Sender: TObject);
var
  ErrorCode: Integer;
begin
  if WizardSilent() then
    exit;
  { Opening help is optional and never a condition of finishing setup. }
  if not ShellExec('open', ExpandConstant('{app}\app\shared\opencl-guidance.html'),
    '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode) then
    Log('OpenCL: could not open the help page; error ' + IntToStr(ErrorCode) + '.');
end;

procedure InitializeWizard();
begin
  if not WizardSilent() then
  begin
    OpenClNotice := TNewMemo.Create(WizardForm);
    OpenClNotice.Parent := WizardForm.FinishedPage;
    OpenClNotice.ReadOnly := True;
    OpenClNotice.WordWrap := True;
    OpenClNotice.ScrollBars := ssVertical;
    OpenClNotice.Anchors := [akLeft, akTop, akRight, akBottom];
    OpenClHelpButton := TNewButton.Create(WizardForm);
    OpenClHelpButton.Parent := WizardForm.FinishedPage;
    OpenClHelpButton.Caption := OpenClGuidanceTitle;
    OpenClHelpButton.SetBounds(WizardForm.FinishedLabel.Left,
      WizardForm.FinishedPage.ClientHeight - WizardForm.NextButton.Height - ScaleY(8),
      ScaleX(180), WizardForm.NextButton.Height);
    OpenClHelpButton.Anchors := [akLeft, akBottom];
    OpenClHelpButton.OnClick := @OpenClHelpClick;
  end;
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

{ ---- Outcome record, /WAITPID and /RELAUNCH for the in-app updater ----------

  The updater starts this setup silently and detached. Nothing of WG's own
  Python is involved, so setup is also the helper: /WAITPID=<pid> waits for
  the application to exit, /OUTCOME=<file> reports what happened for the next
  start to show, /LOG=<file> is Inno's own switch and its path is echoed in the
  record, and /RELAUNCH starts the application again afterwards. All are
  optional; an ordinary interactive install ignores every one of them. }

function JsonEscape(const S: String): String;
begin
  Result := S;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
end;

function JsonStringOrNull(const S: String): String;
begin
  if S = '' then
    Result := 'null'
  else
    Result := '"' + JsonEscape(S) + '"';
end;

{ The record holds from, to, result, when and log; "when" is local wall-clock
  time, ISO 8601 without an offset. Written once, to a temporary name and then
  renamed, so a reader never sees half a file. No braces in this comment: a
  Pascal comment ends at the first closing brace. }
procedure WriteOutcome(const Verdict: String);
var
  Path, Tmp: String;
  Lines: TArrayOfString;
begin
  if OutcomeWritten then
    exit;
  Path := ExpandConstant('{param:OUTCOME|}');
  if Path = '' then
    exit;
  OutcomeWritten := True;
  SetArrayLength(Lines, 1);
  Lines[0] :=
    '{"from": ' + JsonStringOrNull(PreviousVersion) +
    ', "to": ' + JsonStringOrNull('{#AppVersion}') +
    ', "result": ' + JsonStringOrNull(Verdict) +
    ', "when": ' + JsonStringOrNull(GetDateTimeString('yyyy-mm-dd"T"hh:nn:ss', '-', ':')) +
    ', "log": ' + JsonStringOrNull(ExpandConstant('{param:LOG|}')) + '}';
  ForceDirectories(ExtractFileDir(Path));
  Tmp := Path + '.tmp';
  if not SaveStringsToUTF8FileWithoutBOM(Tmp, Lines, False) then
  begin
    Log('Outcome: could not write ' + Tmp + '.');
    exit;
  end;
  DeleteFile(Path);
  if RenameFile(Tmp, Path) then
    Log('Outcome: wrote ' + Path + ' (' + Verdict + ').')
  else
    Log('Outcome: could not move ' + Tmp + ' to ' + Path + '.');
end;

{ False when the process named by /WAITPID is still running after the cap. A
  process that cannot be opened is treated as gone: it already exited, or it
  never existed. }
function WaitForApplicationExit(): Boolean;
var
  Pid, Handle: Integer;
begin
  Result := True;
  Pid := StrToIntDef(ExpandConstant('{param:WAITPID|0}'), 0);
  if Pid <= 0 then
    exit;
  Handle := OpenProcess(SYNCHRONIZE, 0, Pid);
  if Handle = 0 then
  begin
    Log('/WAITPID: process ' + IntToStr(Pid) + ' is not running.');
    exit;
  end;
  try
    Log('/WAITPID: waiting up to ' + IntToStr(WaitForProcessLimitMs div 1000) +
        ' s for process ' + IntToStr(Pid) + '.');
    if WaitForSingleObject(Handle, WaitForProcessLimitMs) = WAIT_OBJECT_0 then
      Log('/WAITPID: process ' + IntToStr(Pid) + ' exited.')
    else
    begin
      Log('/WAITPID: process ' + IntToStr(Pid) + ' was still running after ' +
          IntToStr(WaitForProcessLimitMs div 1000) + ' s.');
      Result := False;
    end;
  finally
    CloseHandle(Handle);
  end;
end;

function RelaunchRequested(): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if (CompareText(ParamStr(I), '/RELAUNCH') = 0) or
       (CompareText(ParamStr(I), '/RELAUNCH=1') = 0) then
      Result := True;
end;

{ ---- Install-time protection of the two bundle layers ------------------------

  Inno's [Files] section overlays: it writes the new files over the old tree,
  never removes a file the new package dropped, and does not restore a file it
  already overwrote if setup is stopped half way. So an upgrade could leave a
  tree that is neither version, and a module deleted from the package survived
  every upgrade.

  Therefore, at the start of ssInstall <app>\app and <app>\runtime are renamed
  aside (a rename inside one directory, so it is atomic and fails cleanly when
  something still holds a file open -- a running application, say). Setup then
  writes complete new layers into empty folders. Only at ssPostInstall, when
  every file is in place, are the renamed-aside folders deleted. If setup ends
  before that (a failure, a cancel, an error), DeinitializeSetup deletes the
  partial new layers and renames the old ones back.

  A killed setup runs no DeinitializeSetup at all. So the run also leaves
  ProtectionMarkerName in <app>: present means "an earlier run died before it
  committed", and the next setup restores from the renamed-aside folders before
  doing anything else. The marker is deleted first when committing, so a run
  killed while deleting the old folders never mistakes complete new layers for
  partial ones.

  Deliberately NOT an [InstallDelete] section: Inno processes [InstallDelete]
  after CurStepChanged(ssInstall), so an entry naming the renamed-aside folders
  would delete the very copies that this run has just made to restore from. }

procedure DeleteTreeSafely(const Path: String);
begin
  if not DirExists(Path) then
    exit;
  { Never follow a junction or symlink out of the install root. }
  if IsReparsePoint(Path) then
  begin
    Log('Left alone because it is a reparse point: ' + Path);
    exit;
  end;
  if DelTree(Path, True, True, True) then
    Log('Removed ' + Path)
  else
    Log('Could not remove ' + Path);
end;

function AppSubPath(const Name: String): String;
begin
  Result := AddBackslash(ExpandConstant('{app}')) + Name;
end;

{ Returns False only when the layer exists and could not be renamed aside.
  Moved reports whether a rename happened. }
function MoveLayerAside(const Layer, Aside: String; var Moved: Boolean): Boolean;
var
  Source, Target: String;
begin
  Moved := False;
  Result := True;
  Source := AppSubPath(Layer);
  Target := AppSubPath(Aside);
  if not DirExists(Source) then
    exit;
  if IsReparsePoint(Source) then
  begin
    Log('Protection: refusing to rename a reparse point at ' + Source + '.');
    Result := False;
    exit;
  end;
  if not RenameFile(Source, Target) then
  begin
    Log('Protection: could not rename ' + Source + ' to ' + Target + '.');
    Result := False;
    exit;
  end;
  Moved := True;
  Log('Protection: renamed ' + Source + ' to ' + Target + '.');
end;

{ Puts a renamed-aside layer back, removing whatever partial copy is in its
  place. Nothing to do (and True) when there is no renamed-aside folder. }
function RestoreLayer(const Layer, Aside: String): Boolean;
var
  Source, Target: String;
begin
  Result := True;
  Source := AppSubPath(Aside);
  Target := AppSubPath(Layer);
  if not DirExists(Source) then
    exit;
  if DirExists(Target) then
  begin
    if IsReparsePoint(Target) then
    begin
      Log('Protection: cannot restore over a reparse point at ' + Target + '.');
      Result := False;
      exit;
    end;
    DeleteTreeSafely(Target);
    if DirExists(Target) then
    begin
      Result := False;
      exit;
    end;
  end;
  if RenameFile(Source, Target) then
    Log('Protection: restored ' + Target + '.')
  else
  begin
    Log('Protection: could not restore ' + Target + ' from ' + Source + '.');
    Result := False;
  end;
end;

procedure RollBackProtectedReplace();
var
  AppOk, RuntimeOk: Boolean;
begin
  AppOk := RestoreLayer(AppLayerName, AppAsideName);
  RuntimeOk := RestoreLayer(RuntimeLayerName, RuntimeAsideName);
  { Only forget the marker once both layers are back. If one could not be
    restored, the marker stays so that the next setup tries again. }
  if AppOk and RuntimeOk then
    DeleteFile(AppSubPath(ProtectionMarkerName))
  else
    Log('Protection: the previous version could not be fully restored; the marker is kept for the next setup.');
end;

procedure BeginProtectedReplace();
var
  AppMoved, RuntimeMoved, Moved: Boolean;
begin
  ProtectionStarted := True;
  ForceDirectories(ExpandConstant('{app}'));

  { An earlier setup that was killed before it committed left the marker and
    the renamed-aside folders: put those back first, so that this run
    protects a whole tree and not a half-written one. }
  if FileExists(AppSubPath(ProtectionMarkerName)) then
  begin
    Log('Protection: an earlier upgrade did not finish; restoring the previous version first.');
    RollBackProtectedReplace();
    if FileExists(AppSubPath(ProtectionMarkerName)) then
    begin
      WriteOutcome('failed');
      RaiseException('Waveguide Generator could not restore the version left by an interrupted upgrade. Nothing was changed; see the setup log.');
    end;
  end
  else
  begin
    { No marker: these are leftovers of an upgrade that did finish. }
    DeleteTreeSafely(AppSubPath(AppAsideName));
    DeleteTreeSafely(AppSubPath(RuntimeAsideName));
  end;

  { A first install has nothing to protect. }
  if not (DirExists(AppSubPath(AppLayerName)) or DirExists(AppSubPath(RuntimeLayerName))) then
    exit;

  if not SaveStringToFile(AppSubPath(ProtectionMarkerName), 'upgrade in progress', False) then
  begin
    Log('Protection: could not write the marker; not touching the installed version.');
    WriteOutcome('failed');
    RaiseException('Waveguide Generator could not prepare the folder for the upgrade. The installed version was not changed.');
  end;

  Moved := MoveLayerAside(AppLayerName, AppAsideName, AppMoved);
  if Moved then
    Moved := MoveLayerAside(RuntimeLayerName, RuntimeAsideName, RuntimeMoved);
  if not Moved then
  begin
    { Most often the running application still holds a file. Put back
      whatever was moved and stop before anything is written. }
    RollBackProtectedReplace();
    WriteOutcome('failed');
    RaiseException('Waveguide Generator could not replace its files because they are in use. Close Waveguide Generator and run setup again. The installed version was not changed.');
  end;
end;

procedure CommitProtectedReplace();
begin
  if not ProtectionStarted then
    exit;
  ProtectionCommitted := True;
  { The marker goes first: a run killed while deleting the old folders must
    not later be read as "the new layers are partial". }
  DeleteFile(AppSubPath(ProtectionMarkerName));
  DeleteTreeSafely(AppSubPath(AppAsideName));
  DeleteTreeSafely(AppSubPath(RuntimeAsideName));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    if not WaitForApplicationExit() then
    begin
      WriteOutcome('failed');
      if not WizardSilent() then
        MsgBox('Waveguide Generator is still running. Close it and run setup again.', mbError, MB_OK);
      Abort;
    end;
    BeginProtectedReplace();
  end;
  if CurStep = ssPostInstall then
  begin
    CommitProtectedReplace();
    if WizardIsTaskSelected(WgLinkTaskName) then
    begin
      RecordWGLinkSetupChoice();
      InstallWGLink();
    end
    else
    begin
      WgLinkStatus := 'WGLink was not installed because it was not selected.';
      Log('WGLink: not selected.');
    end;
  end;
end;

{ launchers/apply_update.py destination_staging_root(): "<the bundle's own
  parent directory>\.<the bundle's own directory name>.update-staging" --
  beside <app>, never inside it. <app> is the installed bundle
  (launchers/apply_update.py bundle_from_app_layer treats <app>\app's own
  parent as the bundle on Windows), and its directory name is whatever the
  user chose on the wizard's directory page, so the path is computed here
  from <app> itself rather than assumed to be the default. Never a wildcard:
  only this one exact path is ever named. }
function UpdateStagingRoot(): String;
var
  AppDir: String;
begin
  AppDir := RemoveBackslashUnlessRoot(ExpandConstant('{app}'));
  Result := AddBackslash(ExtractFileDir(AppDir)) + '.' + ExtractFileName(AppDir) +
    UpdateStagingRootSuffix;
end;

procedure RemoveUpdateStagingRoot();
var
  Target: String;
begin
  Target := UpdateStagingRoot();
  if not DirExists(Target) then
  begin
    Log('Update staging: nothing to remove at ' + Target + '.');
    exit;
  end;
  { A staging folder is where a downloaded update lands; a reparse point left
    there could point anywhere the uninstalling account can reach, so it is
    left alone rather than followed and deleted -- the same refusal
    server/updates/bundle.py applies with _is_link_or_junction(). }
  if IsReparsePoint(Target) then
  begin
    Log('Update staging: left ' + Target + ' alone because it is a reparse point.');
    exit;
  end;
  if DelTree(Target, True, True, True) then
    Log('Update staging: removed ' + Target + '.')
  else
    Log('Update staging: could not remove ' + Target + '.');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { usUninstall runs before [UninstallDelete], while both the app script and
    bundled runtime still exist. Keep the managed Fusion cleanup ahead of the
    app/runtime deletion below; external targets are preserved above. The
    staging folder lives outside <app> and does not depend on either, so its
    order relative to them does not matter. }
  if CurUninstallStep = usUninstall then
  begin
    UninstallWGLink();
    RemoveUpdateStagingRoot();
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID <> wpFinished) or WizardSilent() then
    exit;
  if WgLinkStatus <> '' then
    WizardForm.FinishedLabel.Caption :=
      'Waveguide Generator was installed.' + #13#10#13#10 + WgLinkStatus;
  OpenClNotice.Text := WizardForm.FinishedLabel.Caption + #13#10#13#10 +
    OpenClGuidanceTitle + #13#10 + OpenClGuidanceText;
  { The complete runtime step and warning can exceed the finish page height.
    Keep them scrollable, with room below for the single launch checkbox and
    help button. All coordinates use the wizard's DPI scale. }
  WizardForm.FinishedLabel.Visible := False;
  WizardForm.RunList.Height := ScaleY(28);
  WizardForm.RunList.Top := OpenClHelpButton.Top - WizardForm.RunList.Height - ScaleY(8);
  OpenClNotice.SetBounds(WizardForm.FinishedLabel.Left,
    WizardForm.FinishedLabel.Top, WizardForm.FinishedLabel.Width,
    WizardForm.RunList.Top - WizardForm.FinishedLabel.Top - ScaleY(8));
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
  Result := ValidateWgLinkAddInsOverride(WizardSilent());
  if not Result then
    exit;
  if not RegQueryStringValue(HKCU, UninstallRegistryKey, 'DisplayVersion', PreviousVersion) then
    PreviousVersion := '';
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
  if not Result then
    WriteOutcome('failed');
end;

{ Runs at the very end of every setup that reached it, whatever the outcome.
  Roll back first: the record must describe the state the disk is left in. }
procedure DeinitializeSetup();
begin
  if ProtectionStarted and not ProtectionCommitted then
    RollBackProtectedReplace();
  if ProtectionCommitted then
    WriteOutcome('ok')
  else
    WriteOutcome('failed');
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
