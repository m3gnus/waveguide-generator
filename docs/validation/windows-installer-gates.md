# Windows installer gates

What to check before a release that ships `Waveguide.Generator-<version>-windows-x86_64-setup.exe`, and how to check it without believing anything untested.

`installers/windows/gates.ps1` runs everything here that a machine can decide for itself:

```powershell
installers\windows\gates.ps1 -Setup path\to\Waveguide.Generator-<version>-windows-x86_64-setup.exe
```

It installs, inspects, and uninstalls. Every setup and uninstall uses private data
and pre-created Fusion AddIns directories, including the long-path rejection.
All setup and uninstaller launches go through `Start-SandboxedSetup`, which
checks those private folders and supplies the silent and AddIns arguments.
The gate still uses and deletes the default application root at
`%LOCALAPPDATA%\Programs\Waveguide Generator`, and setup writes per-user shortcuts
and uninstall registration. Use a disposable Windows machine; this is not a
complete machine sandbox. Before and after a gate run, verify that
`%APPDATA%\WaveguideGenerator` and the real Fusion AddIns trees are byte-identical.
Gate 16 is opt-in because it waits for the production 120 s timeout; use
`-RunWaitPidTimeout` for that manual validation run.

## Building the installer to test

```powershell
$env:WG2_ISCC = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
python scripts\build_bundle.py --platform windows --output build\bundle --spa <release-spa>.tar.gz
```

The build refuses, correctly, on four things worth knowing before you blame it: a missing or unstamped `frontend/dist` (pass `--spa`), a dirty Git worktree (do not download the SPA *into* the checkout), a non-empty output directory, and a SPA tarball with no `.sha256` beside it. Download the checksum the release publishes next to the archive; the builder does not extract unverified archives.

## The gates

| # | Gate | Why it is here |
|---|---|---|
| 1 | ISCC compiles `bundle-setup.iss` with `MaxPayloadDepth` supplied by the build | A number written into the `.iss` rots the first time a dependency gets deeper. Check the compiler line shows a measured `/DMaxPayloadDepth=`. |
| 2 | Install lands under `{localappdata}\Programs`, never Program Files | `launchers/apply_update.py` renames directories in place with no elevation path, so a Program Files install breaks in-app updates later, far from the installer. |
| 3 | A silent run exits with a code rather than sitting on a modal box | `/SUPPRESSMSGBOXES` does not cover script dialogs; `InitializeSetup` refuses an over-long `/DIR` without one. `/WAITPID` timeout writes a failed outcome and calls `Abort` at `ssInstall`; the interactive message is guarded by `not WizardSilent()`. A blocking `MessageBoxW` has stalled CI here before, invisibly and at 0%. |
| 4 | An over-long install root is refused, not attempted | The failure this prevents is a half-extracted tree and a "corrupt download" support thread. |
| 5 | The installed payload carries no mark of the web | This is the claim the installer exists to make. Test it properly: mark the setup executable `ZoneId=3` first, or the result is vacuous. |
| 6 | Shortcuts and the uninstall entry show the app icon | The launcher is a byte copy of `pythonw.exe` and nothing patches its resources, so any icon read from the `.exe` is Python's. |
| 7 | SmartScreen and the real first-run experience | **Never decided by the script.** It needs UAC on, an unelevated session and a person double-clicking the marked setup in Explorer (`gate7-prepare.ps1`). See below. |
| 8 | The update path can rename `app` and `runtime` in place, unelevated | Directly exercises what gate 2 protects. |
| 9 | Uninstall clears the tree, including bytecode the installer never wrote | `[UninstallDelete]` removes `runtime` and `app` wholesale and `{app}` only if empty, so a planted `__pycache__` is the case worth testing. It also verifies that the managed WGLink target and any replacement journal/workspace are gone before the disposable AddIns fixture is removed. |
| 10 | The real setup task installs WGLink with the packaged runtime | The gate creates a disposable Fusion AddIns directory, selects the explicit WGLink task, and verifies a zero setup exit, add-in source, ownership root, exact 40-character source pin, runtime pointer, and absence of a transaction journal/workspace. It never needs a real Fusion installation. |
| 11 | Setup preserves a developer-marked WGLink copy | The gate repeats setup against a separate disposable AddIns directory containing a developer marker and source file, then verifies both are byte-for-byte unchanged. |
| 12 | A silent upgrade needs a current WGLink opt-in | After an opted-in setup, the gate plants a unique sentinel in the managed add-in, reruns setup silently without `/TASKS="wglink"`, and verifies both the sentinel and entire tree are unchanged. This prevents Inno's remembered-task default from silently turning old consent into new consent even when the payload version is identical. |
| 13 | An upgrade leaves exactly the package, and reports it | Plants a module in `app\` and a package in `runtime\`, upgrades with the same setup, and requires both to be gone and `app\` + `runtime\` (ignoring bytecode) to equal the freshly installed package. Inno's `[Files]` only overlays, so without the rename-aside in `bundle-setup.iss` a file the package dropped survives every upgrade. Also requires no `.app.old`, `.runtime.old` or `.upgrade-in-progress` left behind, and an `/OUTCOME` record with `result: ok`. |
| 14 | `/WAITPID` holds setup before rename-aside until the named process exits | Starts a live stand-in and setup with `/WAITPID=<its pid>`. Waits at most 30 s for the log to confirm entry into the PID wait, then checks 8 s later for unchanged layers, a planted sentinel, no rename-aside/marker and no outcome. Ends the stand-in and requires exit 0, `result: ok`, sentinel removed and layers equal to the installed package. The 120 s timeout is opt-in gate 16 below. |
| 15 | SetupMutex excludes another setup during `/WAITPID` | Shares gate 14's live wait, probes `WaveguideGeneratorSetup` with `OpenExisting` (disposing the probe immediately), then starts a second silent setup at a disposable `/DIR`. Within 8 s it must either fail non-zero or still be waiting, with no destination tree and no successful outcome. The first must finish correctly after the stand-in exits, and the second must settle after mutex release. |
| 16 | Silent timeout exits by itself without a window | Opt-in `-RunWaitPidTimeout` keeps a stand-in alive through the real 120 s cap. Requires self-exit within 150 s of launch, a non-zero exit code, no visible window in the setup loader or its descendants, one failed outcome write, unchanged layers, no rename-aside/marker or restore, and no `[Run]` entry despite `/RELAUNCH`. Reports the actual exit code for Windows measurement. |

## Silent upgrade contract (used by the in-app updater)

The in-app updater starts `setup.exe` detached and silently, passing `/DIR=` (the exact
install location), and none of `/TASKS`, so WGLink is never installed or refreshed by an
update. Every switch below is optional and ignored by an interactive install.

| Switch | Effect |
|---|---|
| `/WAITPID=<pid>` | Waits first in `CurStepChanged(ssInstall)`, with `SetupMutex` held, before rename-aside or payload writes. After 120 s setup writes `result: failed` and calls `Abort`, without a script dialog on silent paths or `/RELAUNCH`. Exit code **3**, measured on Windows 11 with Inno Setup 6.7.3, 2026-09-30 (gate 16). |
| `/OUTCOME=<file>` | Writes `{"from", "to", "result", "when", "log"}` once at the end (`result` is `ok` or `failed`; `when` is local time without an offset; `log` echoes `/LOG=`, or `null`). |
| `/LOG=<file>` | Inno's own setup log. |
| `/RELAUNCH` | Starts the application again after a successful install (through a short delay, because setup still holds `SetupMutex=WaveguideGeneratorSetup`, and the launcher refuses to start under it). |

Upgrade protection: at the start of the install step `app\` and `runtime\` are renamed to
`.app.old` and `.runtime.old`; the new layers are written into empty folders; the old ones are
deleted only after every file is in place. If setup fails or is cancelled the partial new layers
are deleted and the old ones renamed back. A setup that is killed cannot run that rollback, so it
leaves `.upgrade-in-progress` in the install root, and the next setup restores the previous
version before doing anything else. If the rename cannot happen (typically the application still
has a file open) setup fails before writing anything and the installed version is unchanged.

### Why the wait belongs at the start of ssInstall

The measured 2026-08-27 comment above `InitializeSetup` records that returning a
`PrepareToInstall` message or showing a script `MsgBox` left a silent setup
sitting indefinitely on a "Setup - Waveguide Generator" window despite
`/SUPPRESSMSGBOXES`. That measurement governs this path. `PrepareToInstall`
retains its original directory backstop, guarded to interactive runs; it
returns an empty string on every silent path.

The [event reference](https://jrsoftware.org/ishelp/topic_scriptevents.htm)
places `CurStepChanged(ssInstall)` immediately before actual installation.
The [Abort reference](https://jrsoftware.org/ishelp/topic_isxfunc_abort.htm)
explicitly supports ending setup from this event and specifies a silent
exception that does not display an error message. The wait runs first in that
branch, before `BeginProtectedReplace` can set `ProtectionStarted` or rename
anything. Timeout writes `failed`, shows the existing `MsgBox` only
interactively, then calls `Abort`. Deinitialization cannot restore because
protection never started, and its outcome call cannot overwrite the write-once
record. Setup ends before `[Run]`, so `/RELAUNCH` is skipped.

The [SetupMutex reference](https://jrsoftware.org/ishelp/topic_setup_setupmutex.htm)
specifies exclusion at startup; the
[6.7.3 initialization source](https://github.com/jrsoftware/issrc/blob/is-6_7_3/Projects/Src/Setup.MainFunc.pas)
places mutex creation after `InitializeSetup`. The wait at `ssInstall` therefore
runs while setup holds the mutex. The
[6.7.3 install entry point](https://github.com/jrsoftware/issrc/blob/is-6_7_3/Projects/Src/Setup.MainForm.pas)
sets `ssInstall` before `PerformInstall`, and its exception handler selects
`ecNextStepError`. This suggests exit **3**, as described in the
[exit-code reference](https://jrsoftware.org/ishelp/topic_setupexitcodes.htm).
Exit **3** was then measured on Windows 11 with Inno Setup 6.7.3, 2026-09-30 (gate 16): setup ended by itself after 120.7 s with no window, one failed outcome and the layers untouched.

### Windows checks still required for these findings

1. **Measure the exit code of `Abort` at `ssInstall` first.** Expected **3**, to
   be measured on Windows; require non-zero regardless of the precise code.
   Rebuild with Inno Setup 6.7.3 and retain the compiler output.
2. Manually opt into the real 120 s timeout check (no short-cap hook exists):

   ```powershell
   installers\windows\gates.ps1 -Setup path\to\rebuilt-setup.exe -RunWaitPidTimeout
   ```

   Gate 16 launches `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /WAITPID=<pid>
   /OUTCOME=<file> /LOG=<file> /RELAUNCH` against the installed tree, with an
   explicit `/DIR`. Require self-exit within **150 s of launch** (120 s cap
   plus 30 s startup/termination margin), **no visible window**, a non-zero
   exit and `result: failed` written exactly once. Require unchanged layers,
   no rename-aside folders/marker or restore, and no application relaunch.
   The gate checks visible windows every polling iteration across the setup
   loader and descendants; observe the desktop too and retain console output.
   The gate retains the timeout log and outcome outside its disposable fixture
   and prints their evidence directory; keep those alongside console output.
3. Require all automated gates **1–6 and 8–16** to pass, especially updated 14
   and mutex gate 15. Gate 14 proves the first setup waits before
   renaming, then completes correctly after the stand-in exits; gate 15 proves
   a second silent setup cannot proceed during that wait. Static tests cannot
   qualify compilation, PowerShell execution or native event/mutex behaviour.
4. Repeat timeout without `/SUPPRESSMSGBOXES`: the silent `Abort` path must
   still exit without a dialog. In an interactive timeout, require the existing
   message, then termination after dismissal with unchanged layers.
5. Repeat with an already-exited PID: successful setup without the 120 s wait,
   `result: ok`. Verify successful `/RELAUNCH` still starts the application
   after setup exits, and stop only the application started by that check.

Gate 7 remains its separate manual release check on a UAC-enabled machine.

WGLink is an explicit Fusion-integration task. In the interactive wizard it is
preselected only when an existing Fusion AddIns directory is found; unchecking
it prevents installation. Silent deployment preserves that consent boundary:
name `/TASKS="wglink"` to opt in. If Fusion is absent, the task does not create
an AddIns directory and the final setup page says that WGLink was not installed.
An existing non-Waveguide Generator copy is preserved rather than overwritten.
Every silent upgrade needs that current `/TASKS="wglink"` opt-in; a prior setup
selection is deliberately not reused.
Selecting the task records CAD Link use in WG's data directory before attempting
the add-in install, even if Fusion is absent or installation fails; gates 10 and 12 check that record and its preservation by an unticked upgrade.

## The install folder on an upgrade

Setup reuses the folder the uninstall key names (`UsePreviousAppDir`). Inno's
default `DisableDirPage=auto` then hid the directory page, so a folder chosen once,
such as `C:\wg`, was reused by every later installer without being shown.
`DisableDirPage=no` and `ShouldSkipPage` now keep that rule except in one case: an
**interactive** run with no `/DIR` whose registered folder is **not standard** shows
the directory page with `%LOCALAPPDATA%\Programs\Waveguide Generator` pre-selected
and a note saying why. The user can still browse anywhere, including back.

- **Standard** means strictly inside `%LOCALAPPDATA%\Programs` (Inno's `{userpf}`)
  or a Program Files root (`{commonpf64}`, `{commonpf32}`), compared after
  `ExpandFileName` and without case. A drive root, a folder under the profile or
  Documents, and a network path are not standard. An upgrade from a standard folder
  looks exactly as before: the directory page stays hidden.
- **Unattended runs never move an install.** The in-app updater passes `/VERYSILENT`
  and `/DIR=<the running install>` (`launchers/full_installer.py`), and a silent run
  without `/DIR` keeps the registered folder.
- **Nothing is deleted.** After an install that landed somewhere else, the old tree
  stays where it was. The finish page names it and says to delete it by hand, not
  with its own uninstaller, which would also remove the new install's uninstall key
  and Start menu shortcut, since both installs share them. A WGLink add-in installed
  from the old copy keeps pointing there; `install_wglink.py` will not replace a copy
  another root manages, so the note says to delete that add-in folder too and select
  WGLink again.
- The over-long-root check applies to the offered folder like any other: the
  directory page runs it on **Next**, and `PrepareToInstall` remains the backstop.
- Data folders do not depend on the install folder: `%APPDATA%\WaveguideGenerator`,
  the `%LOCALAPPDATA%\WaveguideGenerator` cache, and Documents runs are unchanged.

Measured 2026-10-05 with Inno Setup 6.7.3 on a copy of `bundle-setup.iss` with a
throwaway AppId and private folders: a silent rerun without `/DIR` stayed in the
non-standard folder; the interactive wizard offered the standard folder; installing
elsewhere moved the uninstall key and shortcut and showed the note; and running the
old folder's `unins000.exe` afterwards removed the new install's uninstall key and
Start menu shortcut, which is why the note warns against it.

## Gate 7 needs a different machine, and a human

Where `EnableLUA=0`, every process runs at High integrity and **any** SmartScreen or mark-of-the-web result from that box is untrustworthy — including a negative one. A false "SmartScreen is fine" is exactly the finding that ships a bad installer, so the script never runs this gate and says so rather than producing a green line; its line reports whether UAC is on and whether the session is elevated. It needs UAC on and an unelevated session, and it is the one gate no CI can answer either: what a first-time user actually sees.

Three things have to hold at once, and each one silently voids the gate on its own:

- **UAC is on.** `gates.ps1` does not probe for this and cannot: gate 7's result is a hardcoded `$null`, so enabling UAC and re-running the script still prints `7 SKIP`. That is deliberate — there is nothing for a script to decide here — but it means gate 7 is never satisfied as a side effect of the other eight.
- **The setup executable carries `ZoneId=3`.** SmartScreen does not look at locally-authored files. Building the installer and running it from `build\` reproduces nothing a user will ever see. This is the same trap gate 5 already calls out, and it applies with equal force here.
- **It is launched by double-clicking it in Explorer.** See below. This is the one place where the launch rule the rest of this document gives is exactly wrong.

`installers/windows/gate7-prepare.ps1` asserts all three preconditions, stamps the mark of the web, and then stops:

```powershell
installers\windows\gate7-prepare.ps1 -Setup path\to\Waveguide.Generator-<version>-windows-x86_64-setup.exe
```

It deliberately does not launch anything. What it prints is the manual procedure, and the evidence for this gate is screenshots plus a click count.

## Result, 2026-09-03

First end-to-end run of the installer path. Worth stating plainly: **the Inno installer has never been through a release.** It does not exist at the `v0.3.0` tag, and that release's Windows asset is the `.zip`. `release.yml` builds and uploads `…-setup.exe` and the release-notes template points users at it, so the next release is the first one that will.

Built from `next` at `9a6fc0e8` with the icon fix applied, against the published `update-spa-0.3.0.tar.gz`. `/DMaxPayloadDepth=112`, installer 135.7 MiB.

| gate | result | evidence |
|---|---|---|
| 1 | PASS | `/DMaxPayloadDepth=112` on the ISCC line, measured from the payload |
| 2 | PASS | landed in `%LOCALAPPDATA%\Programs`, no Program Files copy, exit 0 |
| 3 | PASS | returned an exit code; no dialog, no stall |
| 4 | PASS | exit 1 for a 203-character root, and no tree created |
| 5 | PASS | 6373 files scanned, 0 marked, from a setup executable that was itself `ZoneId=3` |
| 6 | PASS | shortcut icon resolves to `…\WaveguideGenerator.ico,0` |
| 8 | PASS | `app` and `runtime` both renamed in place and restored |
| 9 | PASS | uninstaller exit 0, 0 files left, planted `__pycache__` gone; Start-menu folder, desktop shortcut and the Apps &amp; features entry all removed |
| 7 | SKIP | UAC disabled on the test box |

### One trap, paid for in wall-clock

Launch the installer with `Start-Process -NoNewWindow`, never `-WindowStyle`. `-WindowStyle` forces `UseShellExecute`, and ShellExecute on this installer hangs invisibly — the first gate run stalled for 600 s with no setup process to see and nothing in the log. `-NoNewWindow` goes through `CreateProcess` and returns.

**This rule is inverted for gate 7, and only for gate 7.** SmartScreen's block is a shell dialog: it exists on the ShellExecute path and nowhere else. `CreateProcess` does not consult it, so a gate 7 run driven the way gates 2–9 are driven cannot see the thing it is looking for and returns a pass that means nothing — the same vacuous green as running it with UAC off. Gate 7 has to go through the shell, which is to say: a person double-clicks it and looks.

That also makes the 600 s stall worth re-reading. An invisible block on the ShellExecute path is what SmartScreen *does*; a dialog rendered where the automation could not see it is a candidate explanation for that hang, not merely a scheduling accident. It is a further reason gate 7 is decided at the console rather than scripted.
