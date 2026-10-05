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
; The native launcher uses the staged product icon for installed shortcuts.
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
; Updater /WGLOG uses bounded diagnostics. Automatic vendor logging would create
; an unbounded second stream. Explicit /LOG remains available for manual debug.
SetupLogging=no
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
; The root boot files are staged, not overwritten while the old layers are
; needed for rollback. Native commit records their exact identities then moves
; them into place. The public native entry is published before displacement.
Source: "{#PayloadDir}\Waveguide Generator.exe"; DestName: "wg-installer-helper.exe"; Flags: dontcopy
Source: "{#PayloadDir}\app\*"; DestDir: "{app}\app"; Flags: recursesubdirs createallsubdirs ignoreversion; BeforeInstall: RecordCopyStart; AfterInstall: RecordCopyDone
Source: "{#PayloadDir}\runtime\*"; DestDir: "{app}\runtime"; Flags: recursesubdirs createallsubdirs ignoreversion; BeforeInstall: RecordCopyStart; AfterInstall: RecordCopyDone
Source: "{#PayloadDir}\recovery\*"; DestDir: "{app}\recovery"; Flags: recursesubdirs createallsubdirs ignoreversion; BeforeInstall: RecordCopyStart; AfterInstall: RecordCopyDone
Source: "{#PayloadDir}\*"; DestDir: "{app}\.wg-install-new"; Excludes: "Waveguide Generator.exe"; Flags: ignoreversion; BeforeInstall: RecordCopyStart; AfterInstall: RecordCopyDone

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
Type: filesandordirs; Name: "{app}\.wg-install-old"
Type: filesandordirs; Name: "{app}\.wg-install-new"
; Root files are moved from staging by the native helper, so Inno's file log
; records staging names. Name the owned final boot files explicitly.
Type: files; Name: "{app}\Waveguide Generator.exe"
Type: files; Name: "{app}\wg-python.exe"
Type: files; Name: "{app}\wg-python._pth"
Type: files; Name: "{app}\Waveguide Generator._pth"
Type: files; Name: "{app}\pyvenv.cfg"
Type: files; Name: "{app}\python313.dll"
Type: files; Name: "{app}\python3.dll"
Type: files; Name: "{app}\vcruntime140.dll"
Type: files; Name: "{app}\vcruntime140_1.dll"
Type: files; Name: "{app}\msvcp140.dll"
Type: files; Name: "{app}\WaveguideGenerator.ico"
Type: files; Name: "{app}\READ ME FIRST.txt"
Type: files; Name: "{app}\.upgrade-in-progress"
Type: files; Name: "{app}\.wg-install-lock"
Type: filesandordirs; Name: "{app}\.native-start"
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
  { The /WAITPID waits run on the wizard's UI thread. One blocking wait of up
    to 120 s left the window unpainted and marked "Not Responding"; waiting in
    slices this long and pumping messages between them keeps it live. }
  WaitSliceMs = 100;
  PM_REMOVE = 1;
  WM_QUIT = $0012;

type
  { Opaque buffer for a Windows MSG. Only Message and WParam are read, which
    matches the 32-bit layout Setup runs with; the spare fields keep the
    buffer larger than MSG on any layout. }
  TWinMsg = record
    Wnd, Message, WParam, LParam, Time, X, Y: Integer;
    Spare1, Spare2, Spare3, Spare4, Spare5, Spare6, Spare7, Spare8, Spare9: Integer;
  end;

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
  RollbackIncomplete: Boolean;
  OutcomeWritten: Boolean;
  NativeHelperPath: String;
  WgLogPath: String;
  WgLogReady: Boolean;
  LastCopiedFile: String;
  LastCopyCompleted: Boolean;
  LastLogPercent: Integer;

function DiagnosticArgument(const S: String): String;
var
  I, Slashes, Copies, K: Integer;
begin
  Result := '"';
  I := 1;
  while I <= Length(S) do
  begin
    Slashes := 0;
    while I <= Length(S) do
    begin
      if S[I] <> '\' then
        break;
      Slashes := Slashes + 1;
      I := I + 1;
    end;
    Copies := Slashes;
    if I > Length(S) then
      Copies := Slashes * 2
    else if S[I] = '"' then
      Copies := Slashes * 2;
    for K := 1 to Copies do
      Result := Result + '\';
    if I <= Length(S) then
    begin
      if S[I] = '"' then
        Result := Result + '\';
      Result := Result + S[I];
      I := I + 1;
    end;
  end;
  Result := Result + '"';
end;

function BoundedDiagnostic(const S: String): String;
begin
  Result := S;
  if Length(Result) > 4096 then
  begin
    Result := Copy(Result, 1, 4060);
    if (Ord(Result[Length(Result)]) >= $D800) and
       (Ord(Result[Length(Result)]) <= $DBFF) then
      Delete(Result, Length(Result), 1);
    Result := Result + ' [message truncated]';
  end;
end;

function EnsureNativeHelper(): Boolean;
begin
  Result := NativeHelperPath <> '';
  if Result then
    exit;
  try
    ExtractTemporaryFile('wg-installer-helper.exe');
    NativeHelperPath := ExpandConstant('{tmp}\wg-installer-helper.exe');
    Result := True;
  except
    NativeHelperPath := '';
    Result := False;
  end;
end;

function OutcomeLogPath(): String;
begin
  if WgLogPath <> '' then
    Result := WgLogPath
  else
    Result := ExpandConstant('{param:LOG|}');
end;

function InitializeWgLog(): Boolean;
var
  I, Code: Integer;
  Params: String;
begin
  WgLogPath := ExpandConstant('{param:WGLOG|}');
  WgLogReady := False;
  LastLogPercent := -1;
  Result := WgLogPath = '';
  if Result then
    exit;
  for I := 1 to ParamCount do
    if (CompareText(ParamStr(I), '/LOG') = 0) or
       (CompareText(Copy(ParamStr(I), 1, 5), '/LOG=') = 0) then
      exit;
  if not EnsureNativeHelper() then
    exit;
  Params := '--update-log-init ' + DiagnosticArgument(WgLogPath) + ' ' +
    DiagnosticArgument('Setup start: target version {#AppVersion}.');
  Result := Exec(NativeHelperPath, Params, ExpandConstant('{tmp}'), SW_HIDE,
    ewWaitUntilTerminated, Code);
  if Result then
    Result := Code = 0;
  WgLogReady := Result;
end;

procedure WgLog(const S: String);
var
  Code: Integer;
  Params: String;
begin
  if WgLogPath = '' then
  begin
    Log(S);
    exit;
  end;
  if not WgLogReady then
    exit;
  Params := '--update-log-append ' + DiagnosticArgument(WgLogPath) + ' ' +
    DiagnosticArgument(BoundedDiagnostic(S));
  { The extracted native supervisor owns its exact worker handle and enforces
    a five-second deadline. A failed late write disables all further attempts. }
  if not Exec(NativeHelperPath, Params, ExpandConstant('{tmp}'), SW_HIDE,
    ewWaitUntilTerminated, Code) then
    WgLogReady := False
  else if Code <> 0 then
    WgLogReady := False;
end;

procedure RecordCopyStart();
begin
  LastCopiedFile := CurrentFilename();
  LastCopyCompleted := False;
end;

procedure RecordCopyDone();
begin
  LastCopiedFile := CurrentFilename();
  LastCopyCompleted := True;
end;

function InstallPercent(CurProgress, MaxProgress: Integer): Integer;
var
  Low, High, Middle, Threshold: Integer;
begin
  Result := 0;
  if (CurProgress <= 0) or (MaxProgress <= 0) then
    exit;
  if CurProgress >= MaxProgress then
  begin
    Result := 100;
    exit;
  end;
  Low := 0;
  High := 100;
  while Low < High do
  begin
    Middle := (Low + High + 1) div 2;
    { ceil(Middle * MaxProgress / 100), with every intermediate <= MaxInt. }
    Threshold := (MaxProgress div 100) * Middle +
      ((MaxProgress mod 100) * Middle + 99) div 100;
    if CurProgress >= Threshold then
      Low := Middle
    else
      High := Middle - 1;
  end;
  Result := Low;
end;

procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
var
  Percent: Integer;
begin
  if (WgLogPath = '') or not WgLogReady or (MaxProgress <= 0) then
    exit;
  Percent := InstallPercent(CurProgress, MaxProgress);
  if Percent <> LastLogPercent then
  begin
    LastLogPercent := Percent;
    WgLog('Install progress: ' + IntToStr(Percent) + '%; file: ' + LastCopiedFile);
  end;
end;

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

{ Pascal Script exposes no Application.ProcessMessages, so the pump is the
  three user32 calls VCL's own loop makes. }
function PeekMessageW(var Msg: TWinMsg; Wnd, FilterMin, FilterMax, Remove: Integer): Integer;
  external 'PeekMessageW@user32.dll stdcall setuponly';
function TranslateMessage(var Msg: TWinMsg): Integer;
  external 'TranslateMessage@user32.dll stdcall setuponly';
function DispatchMessageW(var Msg: TWinMsg): Integer;
  external 'DispatchMessageW@user32.dll stdcall setuponly';
procedure PostQuitMessage(ExitCode: Integer);
  external 'PostQuitMessage@user32.dll stdcall setuponly';
{ Milliseconds since boot as a DWORD. Pascal Script Integer subtraction wraps
  without an overflow check (measured with Inno Setup 6.7), so Now - Start is
  the true elapsed time across the 49.7-day rollover. }
function GetTickCount(): Integer;
  external 'GetTickCount@kernel32.dll stdcall setuponly';

{ Dispatches every message queued for this thread's windows, so the wizard
  repaints and answers Windows while a /WAITPID wait is in progress. A
  WM_QUIT is put back for VCL's own loop rather than swallowed here. }
procedure PumpMessages();
var
  Msg: TWinMsg;
begin
  while PeekMessageW(Msg, 0, 0, 0, PM_REMOVE) <> 0 do
  begin
    if Msg.Message = WM_QUIT then
    begin
      PostQuitMessage(Msg.WParam);
      exit;
    end;
    TranslateMessage(Msg);
    DispatchMessageW(Msg);
  end;
end;

function OpenMutexW(DesiredAccess, InheritHandle: Integer; Name: String): Integer;
  external 'OpenMutexW@kernel32.dll stdcall setuponly';

function CreateFileW(Name: String; Access, Share: Integer; Security: Integer;
  Creation, Flags: Integer; Template: Integer): Integer;
  external 'CreateFileW@kernel32.dll stdcall setuponly';
function FlushFileBuffers(Handle: Integer): Boolean;
  external 'FlushFileBuffers@kernel32.dll stdcall setuponly';

function PersistRecoveryRecord(const Path: String): Boolean;
var
  Handle: Integer;
begin
  Result := False;
  Handle := CreateFileW(Path, $40000000, 3, 0, 3, $80, 0);
  if Handle = -1 then
    exit;
  try
    Result := FlushFileBuffers(Handle);
  finally
    CloseHandle(Handle);
  end;
end;

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
    WgLog('WGLink stderr: ' + S)
  else
    WgLog('WGLink stdout: ' + S);
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
  WgLog(Reason);
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
      WgLog('WGLink: invalid /WGLINKADDINSDIR; skipping install or uninstall without Fusion fallback: ' + OverrideDir);
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
      WgLog('WGLink ownership query could not start for ' + Target + '.');
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
    WgLog('WGLink setup choice: bundled runtime python.exe is missing; continuing setup.');
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
    WgLog('WGLink setup choice: recording the selected task.');
    if not ExecAndLogOutput(
      ExpandConstant('{app}\runtime\python.exe'), Parameters, ExpandConstant('{app}'),
      SW_HIDE, ewWaitUntilTerminated, ExitCode, @WgLinkOutput
    ) then
      WgLog('WGLink setup choice: could not start the recording command; continuing setup.')
    else if ExitCode <> 0 then
      WgLog('WGLink setup choice: recording failed with exit code ' + IntToStr(ExitCode) + '; continuing setup.');
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
      WgLog('WGLink: failed; invalid AddIns override.');
      exit;
    end;
    WgLinkStatus :=
      'WGLink was not installed because Autodesk Fusion was not detected.' + #13#10 +
      'Install Fusion first, then run this installer again and select WGLink.';
    WgLog('WGLink: skipped; no existing Fusion AddIns directory was found.');
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
    WgLog('WGLink: failed; bundled runtime python.exe is missing.');
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
    WgLog('WGLink: running the packaged installer for ' + Target);
    if not ExecAndLogOutput(
      ExpandConstant('{app}\runtime\python.exe'), Parameters, ExpandConstant('{app}'),
      SW_HIDE, ewWaitUntilTerminated, ExitCode, @WgLinkOutput
    ) then
    begin
      WgLinkStatus :=
        'WGLink could not be started. See the setup log for details, then run the installer again.';
      WgLog('WGLink: Exec failed to start the packaged installer.');
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
    WgLog('WGLink: packaged installer failed with exit code ' + IntToStr(ExitCode) + '.');
  end
  { The developer marker always wins, including if a stale or copied WG
    ownership marker happens to be beside it. install_wglink.py observes the
    same rule, so setup must not turn its preserved result into "updated". }
  else if WgLinkHasDeveloperMarker(Target) then
  begin
    WgLinkStatus :=
      'WGLink was not changed because its developer marker was preserved.' + #13#10 +
      'Remove that developer-managed copy yourself if you want this installer to manage WGLink.';
    WgLog('WGLink: preserved developer marker at ' + Target + '.');
  end
  else if WgLinkManagedByThisInstall(Target) then
  begin
    if WasManaged then
      WgLinkStatus := 'WGLink was updated. Restart Fusion to load the update.'
    else
      WgLinkStatus :=
        'WGLink was installed. Restart Fusion, then enable Run on Startup in Scripts and Add-Ins.';
    WgLog('WGLink: installed or updated managed copy at ' + Target + '.');
  end
  else if WgLinkHasMarker(Target) then
  begin
    WgLinkStatus :=
      'WGLink was not changed because an existing installation marker was preserved.' + #13#10 +
      'Remove that existing copy yourself if you want this installer to manage WGLink.';
    WgLog('WGLink: preserved non-owned target with an installation marker at ' + Target + '.');
  end
  else
  begin
    WgLinkStatus :=
      'WGLink was not changed because an existing non-Waveguide Generator copy was preserved.' + #13#10 +
      'Remove that copy yourself if you want this installer to manage WGLink.';
    WgLog('WGLink: preserved an existing non-managed copy at ' + Target + '.');
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
    WgLog('WGLink uninstall: no Fusion AddIns directory was found; nothing to remove.');
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
    WgLog('WGLink uninstall: preserved developer-managed target at ' + Target + '.');
    exit;
  end;
  if (not WgLinkManagedByThisInstall(Target)) and not HasTransaction then
  begin
    WgLog('WGLink uninstall: preserved non-owned target at ' + Target + '.');
    exit;
  end;
  if not FileExists(ExpandConstant('{app}\runtime\python.exe')) then
  begin
    WgLog('WGLink uninstall: managed target preserved because bundled python.exe is missing.');
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
      WgLog('WGLink uninstall: recovering an interrupted replacement before managed cleanup.')
    else
      WgLog('WGLink uninstall: removing the managed target before bundle layers.');
    if not ExecAndLogOutput(
      ExpandConstant('{app}\runtime\python.exe'), Parameters, ExpandConstant('{app}'),
      SW_HIDE, ewWaitUntilTerminated, ExitCode, @WgLinkOutput
    ) then
      WgLog('WGLink uninstall: could not start the managed cleanup command.')
    else if ExitCode <> 0 then
      WgLog('WGLink uninstall: managed cleanup failed with exit code ' + IntToStr(ExitCode) + '.')
    else if DirExists(Target) and HasTransaction then
      WgLog('WGLink uninstall: recovery settled; the resulting non-owned target was preserved at ' + Target + '.')
    else if DirExists(Target) then
      WgLog('WGLink uninstall: managed cleanup returned success but left ' + Target + '.')
    else
      WgLog('WGLink uninstall: removed managed target ' + Target + '.');
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
    WgLog('OpenCL: could not open the help page; error ' + IntToStr(ErrorCode) + '.');
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
    WgLog('WGLink: Fusion AddIns directory detected at ' + WgLinkAddInsDirectory() + '.');
    if not WizardSilent() then
      WizardSelectTasks(WgLinkTaskName);
  end
  else
    WgLog('WGLink: no Fusion AddIns directory detected; task remains unchecked.');
end;

{ ---- Outcome record, /WAITPID and /RELAUNCH for the in-app updater ----------

  The updater starts this setup silently and detached. Nothing of WG's own
  Python is involved, so setup is also the helper: /WAITPID=<pid> waits for
  the application to exit, /OUTCOME=<file> reports what happened for the next
  start to show, /WGLOG=<install.log> is the capped updater diagnostic stream,
  and /RELAUNCH starts the application again afterwards. Explicit manual /LOG
  remains Inno's debug switch; it cannot be combined with /WGLOG. All are
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
  Path, Tmp, ResultName, RecoveryField, Kept: String;
  Lines: TArrayOfString;
begin
  if OutcomeWritten then
    exit;
  Path := ExpandConstant('{param:OUTCOME|}');
  if Path = '' then
    exit;
  OutcomeWritten := True;
  ResultName := Verdict;
  RecoveryField := '';
  Kept := 'false';
  { Only native identity-verified recovery may claim a previous version kept.
    This fallback has no such evidence, including on a failed fresh install. }
  if Verdict = 'ok' then
    ResultName := 'installed';
  if RollbackIncomplete then
  begin
    ResultName := 'rollback_incomplete';
    RecoveryField := ', "backupPath": null, "journalPath": ' + JsonStringOrNull(ExpandConstant('{app}') + '\' + ProtectionMarkerName);
  end;
  SetArrayLength(Lines, 1);
  Lines[0] :=
    '{"from": ' + JsonStringOrNull(PreviousVersion) +
    ', "to": ' + JsonStringOrNull('{#AppVersion}') +
    ', "result": ' + JsonStringOrNull(ResultName) +
    ', "previousKept": ' + Kept +
    ', "when": ' + JsonStringOrNull(GetDateTimeString('yyyy-mm-dd"T"hh:nn:ss', '-', ':')) +
    ', "log": ' + JsonStringOrNull(OutcomeLogPath()) + RecoveryField + '}';
  ForceDirectories(ExtractFileDir(Path));
  Tmp := Path + '.tmp';
  if not SaveStringsToUTF8FileWithoutBOM(Tmp, Lines, False) then
  begin
    WgLog('Outcome: could not write ' + Tmp + '.');
    exit;
  end;
  DeleteFile(Path);
  if RenameFile(Tmp, Path) then
    WgLog('Outcome: wrote ' + Path + ' (' + Verdict + ').')
  else
    WgLog('Outcome: could not move ' + Tmp + ' to ' + Path + '.');
end;

{ False when the process named by /WAITPID is still running after the cap. A
  process that cannot be opened is treated as gone: it already exited, or it
  never existed. The cap is measured on the tick clock, not by counting
  slices: a 100 ms kernel wait lasts about 109 ms at the default timer
  resolution, so a slice count stretched the 120 s cap to about 131 s. }
function WaitForApplicationExit(): Boolean;
var
  Pid, Handle, Slice, Start, Elapsed: Integer;
  Exited: Boolean;
begin
  Result := True;
  Pid := StrToIntDef(ExpandConstant('{param:WAITPID|0}'), 0);
  if Pid <= 0 then
    exit;
  Handle := OpenProcess(SYNCHRONIZE, 0, Pid);
  if Handle = 0 then
  begin
    WgLog('/WAITPID: process ' + IntToStr(Pid) + ' is not running.');
    exit;
  end;
  try
    WgLog('/WAITPID: waiting up to ' + IntToStr(WaitForProcessLimitMs div 1000) +
        ' s for process ' + IntToStr(Pid) + '.');
    Exited := False;
    Start := GetTickCount();
    Elapsed := 0;
    while not Exited and (Elapsed < WaitForProcessLimitMs) do
    begin
      Slice := WaitForProcessLimitMs - Elapsed;
      if Slice > WaitSliceMs then
        Slice := WaitSliceMs;
      if WaitForSingleObject(Handle, Slice) = WAIT_OBJECT_0 then
        Exited := True
      else
      begin
        PumpMessages();
        Elapsed := GetTickCount() - Start;
      end;
    end;
    { A modal dialog opened inside the pump (Cancel's confirmation, say) can
      hold it past the deadline after the process has exited. Look once more
      before calling it a timeout, as the single blocking wait would have. }
    if not Exited then
      Exited := WaitForSingleObject(Handle, 0) = WAIT_OBJECT_0;
    if Exited then
      WgLog('/WAITPID: process ' + IntToStr(Pid) + ' exited.')
    else
    begin
      WgLog('/WAITPID: process ' + IntToStr(Pid) + ' was still running after ' +
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

{ Native recovery runs before any Python initialization, including when a
  killed setup has displaced the interpreter itself. Its flushed journal
  records the exact old/new object identities before a layer is renamed. }

function RunNative(const Mode: String): Integer;
var
  Params: String;
  Code: Integer;
begin
  Result := 3;
  Params := Mode + ' "' + ExpandConstant('{app}') + '"';
  if Mode = '--installer-prepare' then
    Params := Params + ' "' + PreviousVersion + '" "{#AppVersion}" "' +
      ExpandConstant('{param:OUTCOME|}') + '" "' + OutcomeLogPath() + '"';
  WgLog('Protection: starting ' + Mode + '.');
  if Exec(NativeHelperPath, Params, ExpandConstant('{app}'), SW_HIDE,
    ewWaitUntilTerminated, Code) then
    Result := Code;
  WgLog('Protection: ' + Mode + ' returned ' + IntToStr(Result) + '.');
end;

procedure RollBackProtectedReplace();
var
  Code: Integer;
begin
  Code := RunNative('--installer-rollback');
  RollbackIncomplete := Code = 3;
  if FileExists(ExpandConstant('{param:OUTCOME|}')) then
    OutcomeWritten := True;
  if RollbackIncomplete then
    WgLog('Protection: native recovery refused an incomplete or foreign object; its journal and verified backups were retained.');
end;

procedure BeginProtectedReplace();
var
  RecordPath: String;
begin
  WgLog('Install phase: native exclusion confirmed; preparing replacement.');
  ForceDirectories(ExpandConstant('{app}'));
  if not EnsureNativeHelper() then
    RaiseException('Waveguide Generator could not extract its native recovery helper. Nothing was renamed.');
  RecordPath := ExpandConstant('{param:OUTCOME|}');
  if RecordPath <> '' then
  begin
    ForceDirectories(ExtractFileDir(RecordPath));
    if FileExists(RecordPath) and not DeleteFile(RecordPath) then
      RaiseException('Waveguide Generator could not replace its previous install outcome. Nothing was renamed.');
  end;
  { Installing the native entry is completed before the journal or any layer
    displacement. An older bridge can therefore still recover a killed first
    full upgrade without importing Python from the displaced runtime. }
  if RunNative('--installer-entry') <> 0 then
    RaiseException('Waveguide Generator could not publish its native recovery entry. Nothing was renamed.');
  ProtectionStarted := True;
  if RunNative('--installer-prepare') <> 0 then
  begin
    RollbackIncomplete := FileExists(ExpandConstant('{app}\.upgrade-in-progress'));
    RaiseException('Waveguide Generator could not prepare the upgrade safely. See the install journal and log.');
  end;
end;

procedure CommitProtectedReplace();
begin
  if not ProtectionStarted then
    exit;
  if RunNative('--installer-commit') <> 0 then
    RaiseException('Waveguide Generator could not commit the complete installation. Native recovery will restore the previous version.');
  ProtectionCommitted := True;
  if FileExists(ExpandConstant('{param:OUTCOME|}')) then
    OutcomeWritten := True;
end;

{ SetupMutex is already held at ssInstall. No new native entry can be
  admitted while this check runs. Running is retained by both parent and child
  until the app/worker exits, including a killed native parent. }
function WaitForRunningApplicationExit(): Boolean;
var
  Handle, Error, Start, Elapsed, Slice: Integer;
  WaitRequested: Boolean;
begin
  Result := False;
  WaitRequested := ExpandConstant('{param:WAITPID|0}') <> '0';
  { The 120 s cap is measured on the tick clock, as in WaitForApplicationExit:
    counting 1201 sleeps of 100 ms stretched it to about 131 s. }
  Start := GetTickCount();
  while True do
  begin
    Handle := OpenMutexW(SYNCHRONIZE, 0, 'WaveguideGeneratorRunning');
    if Handle = 0 then
    begin
      Error := DLLGetLastError();
      Result := Error = 2;
      exit;
    end;
    CloseHandle(Handle);
    if not WaitRequested then
    begin
      WgLog('Install refused: native application or worker is still running.');
      exit;
    end;
    { The deadline is tested only here, right after a fresh check of the
      mutex, so a refusal never follows a sleep or a pump -- a modal dialog
      held open in the pump is always followed by one more look. }
    Elapsed := GetTickCount() - Start;
    if Elapsed >= WaitForProcessLimitMs then
      break;
    Slice := WaitForProcessLimitMs - Elapsed;
    if Slice > WaitSliceMs then
      Slice := WaitSliceMs;
    Sleep(Slice);
    PumpMessages();
  end;
  WgLog('Install refused: native Running handles remained after 120 seconds.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    if not WaitForApplicationExit() or not WaitForRunningApplicationExit() then
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
    WgLog('Install phase: payload copied; committing verified complete installation.');
    CommitProtectedReplace();
    if WizardIsTaskSelected(WgLinkTaskName) then
    begin
      RecordWGLinkSetupChoice();
      InstallWGLink();
    end
    else
    begin
      WgLinkStatus := 'WGLink was not installed because it was not selected.';
      WgLog('WGLink: not selected.');
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
    WgLog('Update staging: nothing to remove at ' + Target + '.');
    exit;
  end;
  { A staging folder is where a downloaded update lands; a reparse point left
    there could point anywhere the uninstalling account can reach, so it is
    left alone rather than followed and deleted -- the same refusal
    server/updates/bundle.py applies with _is_link_or_junction(). }
  if IsReparsePoint(Target) then
  begin
    WgLog('Update staging: left ' + Target + ' alone because it is a reparse point.');
    exit;
  end;
  if DelTree(Target, True, True, True) then
    WgLog('Update staging: removed ' + Target + '.')
  else
    WgLog('Update staging: could not remove ' + Target + '.');
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
  Result := InitializeWgLog();
  if not Result then
  begin
    WriteOutcome('failed');
    exit;
  end;
  Result := ValidateWgLinkAddInsOverride(WizardSilent());
  if not Result then
    exit;
  if not RegQueryStringValue(HKCU, UninstallRegistryKey, 'DisplayVersion', PreviousVersion) then
    PreviousVersion := '';
  Dir := ExpandConstant('{param:DIR|}');
  if (Dir <> '') and (Length(Dir) > MaxRootLength()) then
  begin
    if WizardSilent() then
      WgLog('Refusing /DIR: ' + IntToStr(Length(Dir)) +
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
  if LastCopiedFile <> '' then
  begin
    if LastCopyCompleted then
      WgLog('Last file completed: ' + LastCopiedFile)
    else
      WgLog('Last file not confirmed complete: ' + LastCopiedFile);
  end;
  if ProtectionStarted and not ProtectionCommitted then
    RollBackProtectedReplace();
  if ProtectionCommitted then
    WriteOutcome('ok')
  else
    WriteOutcome('failed');
  if ProtectionCommitted then
    WgLog('Setup exit: installation committed.')
  else
    WgLog('Setup exit: installation did not commit; native recovery/outcome is authoritative.');
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
