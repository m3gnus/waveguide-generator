# Windows installer gates

What to check before a release that ships `Waveguide.Generator-<version>-windows-x86_64-setup.exe`, and how to check it without believing anything untested.

`installers/windows/gates.ps1` runs everything here that a machine can decide for itself:

```powershell
installers\windows\gates.ps1 -Setup path\to\Waveguide.Generator-<version>-windows-x86_64-setup.exe
```

It installs, inspects, and uninstalls. Run it on a machine you are willing to have the app installed on for a minute.

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
| 3 | A silent run exits with a code rather than sitting on a modal box | `/SUPPRESSMSGBOXES` does not cover every dialog; `InitializeSetup` owns that path. A blocking `MessageBoxW` has stalled CI here before, invisibly and at 0%. |
| 4 | An over-long install root is refused, not attempted | The failure this prevents is a half-extracted tree and a "corrupt download" support thread. |
| 5 | The installed payload carries no mark of the web | This is the claim the installer exists to make. Test it properly: mark the setup executable `ZoneId=3` first, or the result is vacuous. |
| 6 | Shortcuts and the uninstall entry show the app icon | The launcher is a byte copy of `pythonw.exe` and nothing patches its resources, so any icon read from the `.exe` is Python's. |
| 7 | SmartScreen and the real first-run experience | **Cannot be decided on a box with UAC disabled.** See below. |
| 8 | The update path can rename `app` and `runtime` in place, unelevated | Directly exercises what gate 2 protects. |
| 9 | Uninstall clears the tree, including bytecode the installer never wrote | `[UninstallDelete]` removes `runtime` and `app` wholesale and `{app}` only if empty, so a planted `__pycache__` is the case worth testing. |

## Gate 7 needs a different machine, and a human

Where `EnableLUA=0`, every process runs at High integrity and **any** SmartScreen or mark-of-the-web result from that box is untrustworthy — including a negative one. A false "SmartScreen is fine" is exactly the finding that ships a bad installer, so the script skips this gate and says so rather than producing a green line. It needs a machine with UAC enabled, and it is the one gate no CI can answer either: what a first-time user actually sees.

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
