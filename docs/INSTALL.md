# Installing and running Waveguide Generator

The [README](../README.md) has the short version. This page has every detail: each
installer, first-launch warnings, launcher options, updates, output folders,
and running from source.

## Install

Clone the repository — do not download a ZIP, because the installer updates
itself with Git and the pinned HornLab modules are installed from Git too.
Then run the installer for your platform:

| | |
|---|---|
| macOS | double-click `installers/macos/install-wg.command` |
| Windows | double-click `installers\windows\install-and-update.bat` |
| Linux | `bash installers/linux/install.sh` |

For a self-contained macOS install, download the release's
**Waveguide.Generator-&lt;version&gt;-macos-arm64.dmg** and open it.

**macOS refuses to open the app on first launch, and the dialog offers no way
forward.** It reports *"Apple could not verify 'Waveguide Generator' is free of
malware that may harm your Mac or compromise your privacy"*, offering only
**Done** and **Move to Bin**. That is a statement about a missing Apple
signature rather than a finding about the app, and the dialog is not where you
approve it: the exception is granted in **System Settings → Privacy & Security**.

Drag the app to Applications first — an item still on the mounted disk image is
on read-only storage — then open it, click **Done**, and go straight to
**System Settings → Privacy & Security → Security**, where it is listed as
blocked. Click **Open Anyway**. Do it promptly: the entry describes the most
recent block, so opening something else first can replace it.

The disk image also carries **`Install Waveguide Generator.command`** beside the
app. It is an equivalent starting point rather than a fallback: it is refused
with the same wording and approved the same way, and it then copies the app to
Applications, clears the download flag and starts it, so you drag nothing. Both
routes were confirmed working on macOS 26.5.2.

If Privacy & Security lists neither item, drag the app to Applications and run
this once in Terminal instead:

```bash
xattr -dr com.apple.quarantine "/Applications/Waveguide Generator.app"
```

All three routes are spelled out inside the disk image in `READ ME FIRST.txt`,
and any of them is needed once, not on every launch.

**Why the warning appears at all.** Apps distributed outside the App Store need
a paid Apple Developer ID to be notarized. This build is signed *ad-hoc*
instead, which lets it execute but gives Gatekeeper no developer identity of
ours to show you, so the first-launch dialog is a dead end by design and
Privacy & Security is where the override lives.

Which items macOS offers an override for is not something this project can
promise. Measured 2026-09-02, the ad-hoc bundle assesses as `rejected` with no
`source` line while an unsigned script reports `source=no usable signature`,
and that difference was once read as meaning the app would never be listed and
the script always would. Two real installs on 2026-09-06 refuted it in both
directions: the app was listed and opened, and so was the script. The `source`
line is therefore not the predictor it was taken for, which is why all three
routes are documented and none is described as impossible.

Shipping the app unsigned is not an escape either: an unsigned arm64 executable
is killed by the kernel on Apple silicon whatever its quarantine state, which is
measured too. Control-clicking and choosing Open does not help; Apple removed
that bypass in macOS Sequoia. The transcripts are in
[docs/validation/2026-09/MACOS-GATEKEEPER.md](validation/2026-09/MACOS-GATEKEEPER.md).

For a self-contained Windows install, download
**Waveguide.Generator-&lt;version&gt;-windows-x86_64-setup.exe** from the release page
and run it. It is unsigned, so Microsoft Defender SmartScreen asks once — **More
info → Run anyway** — and then never again, because the installer writes its
payload itself and nothing it writes carries the download mark. It installs to
`%LOCALAPPDATA%\Programs` without elevation, which is what lets the in-app
updater replace files in place later, and it refuses an over-long install folder
up front instead of failing partway through. An upgrade stays in the folder you
used before when that folder is under `%LOCALAPPDATA%\Programs` or Program Files;
for any other folder, such as `C:\wg`, setup suggests the standard one instead, and
says on its last page how to remove the old copy, which it never deletes itself.
Setup also offers an explicit
**Install WGLink for Autodesk Fusion** task. It is preselected only when Fusion's
AddIns folder already exists, installs entirely from the verified bundle without
network access, and never replaces a developer or externally managed WGLink.
Silent installs opt in with `/TASKS="wglink"`.

For a self-contained Linux install, download
**Waveguide.Generator-&lt;version&gt;-linux-x86_64.tar.gz**, extract it, and run
`./install.sh` inside the extracted folder. It needs no root and no package
manager: it copies the application to `~/.local/share/waveguide-generator`, adds
a menu entry and icon, and puts `waveguide-generator` on your `PATH` at
`~/.local/bin`. Run it again to upgrade in place. `uninstall.sh` is kept next to
the installed application, so removing it does not need the download; add
`--data` to remove designs and job history too.

To check a Linux download, run `sha256sum "<downloaded-file>.tar.gz"` and
compare the result with the matching release asset's `digest` (without the
`sha256:` prefix) in GitHub's [release asset metadata](https://docs.github.com/en/rest/releases/assets#get-a-release-asset).
If sharing a build outside GitHub, include its filename, build commit and
SHA-256 from the original build with the link. A matching checksum confirms
that the copy matches that build; it is not a publisher signature. A missing
digest is not verification.

Per-user is deliberate, and it is the same reason the Windows installer avoids
Program Files: the in-app updater replaces files inside the installation and
cannot elevate, so a root-owned copy under `/opt` would install once and then
refuse every update it was offered.

The bundle brings its own Python, its own Tcl/Tk and every Python package. What
it does not carry is the system OpenGL and X11 libraries the mesher loads at
start — ordinary desktop libraries that a desktop system already has and a
server install may not. `install.sh` checks by importing gmsh with the bundled
interpreter and stops before copying anything, printing the exact command; on
Ubuntu 24.04 that is `sudo apt install libglu1-mesa libgl1 libgomp1
libfontconfig1 libxrender1 libxcursor1 libxft2 libxinerama1 libxi6 libxext6`.
The Linux bundle opens the interface in its own native window using the
bundled Qt/PySide6 backend. Qt also needs desktop libraries; if its startup
check fails, the launcher explains the missing dependency and offers the
existing status-window/browser recovery path. `--browser` requests that mode
explicitly. The application does not install system packages or ask for root.

**This build is Ubuntu 24.04 LTS on x86-64**, and distributions close enough to
it — not "Linux". There is no arm64 build and no musl build. Other
distributions may well work; they are not what it was built and verified
against.

A portable copy is still published, as
**Waveguide.Generator-&lt;version&gt;-windows-x86_64.zip** on the
`v<version>-updates` companion release rather than on the release page, so
there is one file per platform to choose from there. It needs both of the steps
the installer does for you: right-click the ZIP → Properties → tick **Unblock**
*before* extracting, or Explorer copies the download mark onto every extracted
file and the unsigned launcher meets a SmartScreen dialog whose only visible
button is "Don't run"; and **extract it to a short path such as `C:\wg`.** The
bundle's deepest internal path is 133
characters, so an install root longer than roughly 127 characters exceeds
Windows' 260-character limit and extraction fails with a flood of "cannot find
path" errors rather than one clear message. `C:\Program Files\...` is fine; a
OneDrive-redirected Documents folder with a long company name may not be. Then
double-click **Waveguide Generator.exe** in the extracted folder. The native
window requires the **Microsoft Edge
WebView2 Evergreen Runtime (x64)**, which is normally present on current Windows
10 and Windows 11 systems; when it is missing or its pythonnet bridge cannot load,
WG shows repair instructions containing the WebView2 download URL and opens the
interface in the default browser. The folder
also includes `WaveguideGenerator.ico` for a shortcut. The executable itself keeps
the generic Python icon until a future signed build adds the icon as a Windows
executable resource.

It fast-forwards the checkout, downloads that version's prebuilt interface from
the GitHub release and **refuses to extract it unless it matches the published
SHA-256**, creates `.venv` with CPython 3.13 and the locked dependency set,
checks that a solve can actually run, and starts the app. On macOS and Windows
it also installs the exact compatible WGLink source into Fusion 360 and reuses
WG's environment for spline resampling; users need no add-in checkout or second
virtual environment. Running the installer again updates WG's managed copy but
preserves a developer-managed WGLink registration. The exact source, integrity,
and takeover rules are in the [WGLink packaging contract](../integrations/wglink/README.md).

Prerequisites, all reported with the command that installs them: CPython 3.13
(exactly — the dependency set is locked against one series), Git 2.20+, the
Microsoft Visual C++ Redistributable on Windows, and the Xcode Command Line
Tools on Apple Silicon for the Metal solver.

Useful flags: `--tag vX.Y.Z` installs a specific release, `--skip-spa` leaves
`frontend/dist` alone while you are working on the interface, `--no-launch`
stops before starting the app, and `--force` fully repairs the environment by
reinstalling declared distributions and removing undeclared ones.
`--skip-wglink` leaves Fusion untouched; `--replace-wglink` deliberately
replaces a developer-managed copy; and `--wglink-archive PATH` rehearses an
already-built, provenance-checked package without fetching its source.

To check the solve backends at any time without a full install:

```
.venv/bin/python scripts/check_backends.py
```

### Uninstall

```
bash installers/macos/uninstall.sh         # macOS: also its managed WGLink copy
bash installers/linux/uninstall.sh         # Linux: same options
installers\windows\uninstall.bat            # Windows: also its managed WGLink copy
# Add --data to also remove designs, job history, meshes, and logs.
```

Neither touches the checkout itself — delete the folder yourself when you are
done with it.

## Launch

The launchers open a compact status window with separate backend and frontend
lamps, the local URL, an **Open in browser** button, and a **Quit** button. Quit
or close the window to stop the complete server process tree.

| | |
|---|---|
| macOS | open `launchers/macos/Waveguide Generator.app` |
| Windows | double-click `launchers\windows\launch-wg.bat` |
| Linux | `./launchers/linux/launch-wg.sh` |

The macOS launcher app is deliberately unsigned, and from a Git checkout that
costs you nothing: Gatekeeper only assesses files that carry the "downloaded
from the internet" flag, and `git clone` does not set it. Measured 2026-09-02 on
macOS 26.5.2 — it opens on the first double-click, with no dialog. That is not
true of the `.dmg` above, which *is* downloaded; see the disk-image instructions
in [Install](#install) above.

The repository root intentionally has no duplicate install or launch scripts;
use the platform folders above. On first launch the entry creates `.venv` with
CPython 3.13 and installs the locked dependencies.

On macOS, Windows and Linux, append `--window` to the command launcher to open the
interface in one native desktop window instead of the tkinter status window.
Closing that window stops the owned server. `--browser` explicitly keeps the
normal status-window/browser workflow. Linux uses Qt/PySide6 and checks that
its platform libraries can load before starting the native window. The source
environment installs this Linux backend through the runtime requirements.

`--help` prints usage and exits without starting a server or window. Unknown
options are rejected before startup.

For the original plain-terminal behavior, append `--no-gui`:

```
./launchers/macos/launch-wg.command --no-gui
./launchers/linux/launch-wg.sh --no-gui
launchers\windows\launch-wg.bat --no-gui
```

The launcher uses the first available port from 3100 through 3109. Advanced
server flags such as `--port`, `--no-browser`, and `--data-dir` can be appended.
The committed app icon is reproducible with
`python launchers/macos/generate_icon.py` on macOS; the generator uses only the
standard library and validates the resulting ICNS container with `iconutil`.

### When the status window does not open

The application and the status window fail independently. The window is drawn
with tkinter, which belongs to the Python installation rather than to Waveguide
Generator, so a Python without a working Tk gives an application that runs
perfectly under `--no-gui` and a window that never appears. Reinstalling
Waveguide Generator cannot change that, in either direction.

When the window cannot open, WG writes a full diagnosis to `statusapp.log` in
the application log directory and, on Windows, shows the cause and the remedy in
a dialog. The diagnosis names the interpreter it actually used, lists the Tk
files it looked for, and distinguishes the three causes, which have three
different fixes:

| What the report says | What it means |
|---|---|
| does not include tkinter | that Python was installed without Tk, or the launcher is using a different Python from the one you added Tk to |
| Tcl/Tk libraries could not be loaded | Tk is installed; something is stopping it loading. Re-ticking the installer option changes nothing |
| Tk loaded but failed to create a window | usually `TCL_LIBRARY` or `TK_LIBRARY` set by other software, or no interactive desktop session |

The same report can be produced on demand, which is the quickest thing to ask
for from a machine you cannot reach:

On Windows:

```
.venv\Scripts\python.exe launchers\statusapp\diagnostics.py
```

On macOS and Linux:

```
./.venv/bin/python launchers/statusapp/diagnostics.py
```

It exits 0 when the window can open. A machine with no graphical session at all
is reported as such and is not treated as a fault.

### Application updates

The version in the top-left corner checks GitHub's latest published full
release after the interface opens. When a newer, complete release is available
it turns amber and says **update available**. In the standalone application,
click **Install update** to download the checksum-verified app layer and, when
its content id changed, the matching runtime layer. WG stages them in its data
directory, closes only after verification succeeds, swaps the complete layers,
restores the ad-hoc bundle signature, and restarts. An asset that carries no
published digest is refused before it is downloaded, and one whose bytes do not
match it is refused before anything is extracted. That check establishes
**integrity, not authenticity**: it proves the copy matches the release GitHub
describes, over TLS to `api.github.com`. It is not a publisher signature — the
application is ad-hoc signed and carries no signing identity. The previous layers
remain
available for automatic rollback until the updated native application starts successfully.
An update interrupted part-way through is decided on the next start from a
transaction journal in the data directory: it is finished or rolled back before the
server starts, in whichever mode the application is opened. The one window that is
not automatic is an interruption while the application layer itself is being
replaced — the launcher needs that layer to run any of this — and the repair command
for it is written to the update log.
The same action is available from the command palette as **Application update**.

The job-log dialog reads and renders at most the first 1.0 MB. For example, opening
a 50 MB log keeps a 1.0 MB preview in the interface; **Download complete log** uses
the browser's download path for the full file, and **Copy preview** copies only the
bounded text shown in the dialog.

WG caches successful checks, retries incomplete releases quickly, and keeps the
last known result when the network is unavailable. It also inspects the local
checkout without changing it: modified, development, detached, and non-Git
installs are explained instead of being handed an action that would silently do
the wrong thing. Checkout-based installs keep their existing platform-installer
handoff and exact command fallback; automatic installation there is available
when WG was opened through its status window. For a copied checkout command,
close Waveguide Generator first so the installer can acquire the application
data lock.

### Output workspace

Manual and automatic run exports default to `Documents/Waveguide Generator/runs`.
This folder is user-visible and does not require browser download permission or
approval for a protected operating-system data directory. A different output
folder can be selected once in **Settings → Workspace**; the path displayed
there is authoritative. Internal databases, logs, and process locks remain
under the platform application-data directory; result exports do not.

With `--data-dir` or a non-empty `WG2_DATA_DIR`, the implicit output folder is
`<data dir>/workspace` instead. A workspace already saved in that data directory
still takes priority, including one outside it. Existing Documents runs are not
moved; select their folder in **Settings → Workspace** to see them, or remove the
override to use the normal default and checkout-output adoption.

The Fusion WGLink exchange folder is configured separately under **Settings →
CAD Link**. Changing the output folder never moves or disconnects Fusion's
`.wglink` and `.wgreturn` exchange.

## Run the server directly (dev)

```
python3.13 scripts/bootstrap.py
.venv/bin/python launch/serve.py --port 3100
```

The bootstrap is idempotent: unchanged, valid environments do not contact the
package index. Run `.venv/bin/python scripts/bootstrap.py --check` to validate
without installing. `--force` force-reinstalls every declared distribution and
removes distributions that are not declared by the dependency manifests.

## Headless evaluation

The installer also provisions a repository-aware `wg` command in `.venv/bin` on
macOS/Linux and `.venv\Scripts` on Windows. It validates or solves `.mwg`/`.cfg`
designs and accepts the same strict JSON `SolveRequest` as the HTTP API:

```text
.venv/bin/wg validate design.mwg --json
.venv/bin/wg solve --request request.json --events ndjson --output run-001
```

See the canonical [CLI contract](reference/CLI.md) and
[external evaluation API](reference/EXTERNAL-EVALUATION.md). A standard-library
[reference client](../examples/external_evaluator.py) demonstrates persistent HTTP use.

Flags: `--no-browser`, `--data-dir` (or `WG2_DATA_DIR`); `WG2_ENABLE_DRYRUN=1` exposes the dry-run engine (dev/test only).
