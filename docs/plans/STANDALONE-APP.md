# Standalone application

Status: all four steps implemented on `feature/standalone-app`, macOS verified
end to end, four-lens review remediation applied, and Windows still unverified
(see "Verification status"). This plan turns the
checkout-bound `.app` into a self-contained, self-updating desktop application
without Electron, Tauri, or a frozen interpreter. It is written as the contract
for the implementation batches; each numbered step is independently shippable
and is reviewed before the next starts.

## Verification status

Verified on Apple Silicon, by building and running the real artifacts:

- The DMG mounts, and the copied `.app` runs from anywhere: its own interpreter,
  Metal ready, `/` and `/health` answering, the SPA rendered in the native
  window, no reference to the build checkout, and the ad-hoc signature still
  valid after a run.
- A built bundle updated itself from a locally served release: app layer only,
  the already-installed runtime reused, layers swapped, re-signed, relaunched
  with its original arguments, and the rollback layers and downloads removed
  after the healthy start.
- A deliberately broken release rolled back to the previous version and said so
  in a dialog.

The branch was then reviewed through four independent lenses: packaging/release,
launcher/update lifecycle, update security, and frontend/documentation/test quality.
The review found and this branch fixed the following actionable defect classes:

- Packaging now materializes the app layer from committed blobs at the packaged
  commit with canonical text/modes, a stored cross-platform ZIP, a verified SPA tree,
  and no tracked test trees. Runtime construction uses the constraint lock and records
  its pinned Python distribution build and recipe. Installer assets and checksum
  sidecars use the dotted filenames GitHub actually serves. A final publisher validates
  all seven asset pairs and keeps the release draft-only until every platform succeeds.
- Windows direct launch recognizes CPython's no-script argv, while update relaunches
  explicitly use `"<exe>" -m launchers.desktop <args...>`. Launcher-file and archive
  names are validated with Windows-strict rules on every host.
- Update download origins and every redirect hop are constrained to the repository's
  release origin (or one literal loopback rehearsal origin). Active installers are
  bound to one version/asset set, embedded and detached runtime identities are checked,
  extraction has measured per-layer/member/ratio/disk limits, and staging is moved to
  the destination volume before the application closes.
- Lifecycle recovery uses a non-destructive Windows process probe, a stable updater
  working directory, no-throw recovery logging, transactional launcher rollback,
  required macOS sign/verify steps, and browser-fallback handoff polling. Rollback
  files remain until the native application has initialized, and preflight/handoff or
  relaunch failures reopen the known-good version or present native recovery guidance.
- The interface now keeps polling through unchanged progress and transient read
  failures, rejects malformed nested update payloads, explains that installation closes
  and restarts the app, and gives keyboard users a focusable log region. Log preview,
  rendering, and copying are capped at 1.0 MB; the complete file uses a download action.
  TypeScript tests exercise the real bundle state lifecycle, repeated progress samples,
  recovery after a polling error, decoder rejection, and large/empty log behavior.

The review also left release-design work that these remediations do not claim to close:
a publisher signature rooted in the installed updater (release SHA sidecars alone do
not authenticate the publisher), a durable journal for power loss between multi-layer
renames, and fully pinned release-action and tool provenance. These are release gates,
not properties implied by the macOS happy-path evidence above.

Not verifiable here, and therefore open:

- Everything Windows: nothing for this branch has executed on a Windows host yet. The
  first `windows-bundle` CI run must show the uv Windows
  layout, the launcher loading its adjacent DLLs through the isolated `._pth`,
  a no-argument executable starting the desktop path, `-c` remaining usable by worker
  subprocesses, bempp/numba ready, the server answering, and the app-layer ZIP matching the
  macOS one byte for byte. A real Windows machine must then show an
  Explorer double-click starting without a console, the SmartScreen prompt, a
  clean-machine numba load from the bundled MSVC DLLs, the WebView2 window and
  its browser fallback, and one in-app update including launcher refresh and the
  `-m launchers.desktop` argument-preserving relaunch.
- Gatekeeper on a genuinely downloaded DMG (quarantined by the browser), which
  needs the release assets to exist.
- The Windows executable keeps the generic Python icon; embedding the ICO as a
  PE resource is deferred.

## Problem

The current `launchers/macos/Waveguide Generator.app` is a shell stub that must
live inside a Git checkout and needs a system CPython 3.13, the installer-built
`.venv`, Git (pinned modules), and the Xcode command-line tools (the Metal
helper is compiled with `swift build` during `pip install`). Users also get a
browser tab rather than a window.

## Verified approach (2026-08-21 spike)

- **Runtime.** `uv python install 3.13` plus `uv pip install` of the locked
  runtime set (`server/requirements-runtime.txt` + `requirements-pins.txt`)
  into one directory produced a 531 MB tree in 81 s. Copied to a different
  path, `scripts/check_backends.py` reported Metal, bempp and the axisymmetric
  engine ready and `launch/serve.py` served the interface. python-build-standalone
  links `libpython` through `@rpath`, so the tree is relocatable without
  `install_name_tool` post-processing.
- **Window.** pywebview 6.x on CPython 3.13 (pure pyobjc on macOS, WebView2
  through pythonnet on Windows) renders the live interface with WebGL and
  WebSockets. It is a native window around the same loopback server. The
  frontend uses an in-app dialog for job logs and a same-origin download anchor
  for radiation impedance, so it does not depend on a new-window JavaScript
  bridge and behaves the same in pywebview and a normal browser.

Freezing tools (PyInstaller, py2app, Nuitka) are rejected: numba/llvmlite,
gmsh's `find_library` lookup, bempp's hooks and WG's `sys.executable` worker
subprocesses all fight them. The relocatable interpreter keeps every module as
plain Python.

## Bundle layout

```
Waveguide Generator.app/Contents/
  Info.plist                      CFBundleExecutable = "Waveguide Generator"
  MacOS/Waveguide Generator       bash stub: Rosetta guard, then exec
                                  ../Resources/runtime/bin/python3.13 -m launchers.desktop
  Resources/WaveguideGenerator.icns
  Resources/runtime/              relocatable CPython + site-packages (the "runtime layer")
    bin/python3.13
    lib/python3.13/site-packages/...
    RUNTIME-MANIFEST.json         {"schemaVersion":1,"python":"3.13.12","pythonBuild":"...",
                                   "platform":"macos-arm64","requirementsSha256":"...",
                                   "pinsSha256":"...","lockSha256":"...","runtimeRecipe":"..."}
  Resources/app/                  the "app layer": server/ launch/ launchers/ shared/
                                  scripts/ frontend/dist/ integrations/wglink/ docs/ LICENSE
    APP-MANIFEST.json             {"schemaVersion":1,"version":"0.2.5","commit":"<sha>",
                                   "runtimeId":"..."}
```

The two layers are independent release assets:

| Asset | Contents | Changes when |
|---|---|---|
| `update-app-<version>.zip` | `Resources/app` | every release (a few MB) |
| `update-runtime-macos-arm64-<runtimeId>.zip` | `Resources/runtime` | requirements, lock, Python distribution build, or runtime recipe changes |
| `Waveguide.Generator-<version>-macos-arm64.dmg` | complete bundle | every release (first install); this is the filename GitHub serves |

`runtimeId` is the first 12 hex digits of a length-delimited SHA-256 identity over
`server/requirements-runtime.txt`, `server/requirements-pins.txt`,
`server/requirements-lock.txt`, the exact Python patch and python-build-standalone
build, and the versioned runtime recipe. The app layer's `APP-MANIFEST.json`
names the runtime id it was built against, so the updater knows whether the
runtime must be replaced too.

Windows mirrors this with `Waveguide Generator/` as a folder (no bundle
concept): `Waveguide Generator.exe` is a copy of `pythonw.exe` beside
`runtime/` and `app/`; `Waveguide.Generator-<version>-windows-x86_64.zip` is
the distributable until an installer exists.

## Step 1 — desktop window from the checkout

Goal: `launch-wg.command --window` (macOS), `launch-wg.bat --window`
(Windows) and the status app's default open the interface in a native
pywebview window instead of a browser tab. Nothing else changes for the
checkout workflow; the status window, `--no-gui`, and browser mode keep
working.

- Add `pywebview==6.2.1` to `server/requirements-runtime.txt`
  (`scripts/bootstrap.py` consumes it; the lock files are regenerated the
  usual way). On Linux it needs GTK or Qt; the window is **not** offered on
  Linux in this step — `--window` there reports why and falls back to the
  browser.
- New module `launchers/desktop.py` with a `DesktopWindow` that owns a
  `StatusController` (it already starts/stops the server process, knows the
  port, and polls health). Flow: start controller → wait for the frontend lamp
  → `webview.create_window(title, url, width=1440, height=900,
  min_size=(1100, 700))` → `webview.start(func=poll_loop)` → on window close,
  `controller.close()`. `webview.settings['ALLOW_DOWNLOADS'] = True` so
  `<a download>` exports land in the user's Downloads folder. External
  documentation links currently use pywebview's browser-opening default; the
  launcher does not set `OPEN_EXTERNAL_LINKS_IN_BROWSER` as an application contract.
  Must run on the main thread (Cocoa); the status window and the desktop
  window are therefore mutually exclusive front ends of the same controller.
- Startup failures before the window exists reuse
  `launchers/statusapp/__main__._report_startup_failure`, which already
  writes `statusapp.log` and shows a native dialog.
- `launchers/statusapp/__main__.py` gains `--window` and `--browser`; the
  status app remains the default for the checkout in this step (the bundle
  flips the default in step 2).
- Frontend: replace the three `window.open(...)` sites in
  `frontend/src/shell/JobsPanel.tsx`:
  - Job log → an in-app `LogDialog` (same `role="dialog"` pattern and focus
    trap as `SettingsDialog.tsx`) that streams at most a 1.0 MB preview from
    `/api/jobs/{id}/log`, shows it monospaced with Copy-preview, full-download,
    and Refresh actions, and makes the scrollable output keyboard-focusable.
    A 50 MB log therefore mounts and copies only its first 1.0 MB. Works
    identically in browser mode.
  - Radiation impedance → a same-tab `<a download>` so the file goes through
    the download path in both modes.
- Tests: Python unit tests for the window module with pywebview stubbed
  (controller lifecycle, ALLOW_DOWNLOADS set, close stops the server, a
  missing pywebview reports the repair hint); Vitest for `LogDialog` and the
  JobsPanel buttons.

## Step 2 — bundle build and release assets (macOS)

`scripts/build_bundle.py` (stdlib + `uv` on PATH) builds the layout above into
`build/bundle/` and produces the three assets:

1. **Runtime layer.** `uv python install --install-dir <tmp> 3.13` (pin the
   exact python-build-standalone version in the script), then
   `uv pip install --python <that> --no-cache -r server/requirements-runtime.txt
   -r server/requirements-pins.txt -c server/requirements-lock.txt`. Remove
   `lib/tcl*`, `lib/tk*`, `lib/itcl*`,
   `lib/python3.13/idlelib`, `tkinter`, `turtledemo`, `ensurepip`, `pip`,
   `test`/`tests` directories under site-packages, and `__pycache__`. The
   Metal helper must exist at
   `site-packages/hornlab_metal_bem/metal/native_helper/.build/release/HornlabMetalBemNative`
   after the install; the build fails loudly if it does not (it means Swift
   was missing on the build machine — a source-only package would fall back
   to interpreting the helper at runtime and time out, as the 2026-06
   HornLab Studio attempt found).
2. **App layer.** Resolve the packaged commit and materialize its committed blobs
   for `server/ launch/ launchers/ shared/ scripts/ integrations/wglink/ docs/
   LICENSE README.md`, excluding test trees and unsafe/case-colliding paths.
   Worktree edits, ignored files, untracked files, symlinks, and checkout filters
   cannot change the layer. Add the verified `frontend/dist` from the release SPA
   (or require and recheck a canonical tree digest for an existing dist), then write
   `APP-MANIFEST.json` with LF newlines.
3. **Bundle.** Assemble the `.app`, write `Info.plist` from the existing one
   (`CFBundleShortVersionString` from `shared/version.json`), copy the `.icns`,
   then `codesign --force --deep --sign - "Waveguide Generator.app"` (ad-hoc;
   `/usr/bin/codesign` ships with macOS) and `hdiutil create -volname
   "Waveguide Generator" -srcfolder ... -format UDZO`, writing the public asset as
   `Waveguide.Generator-<version>-macos-arm64.dmg`. Every asset gets a
   `.sha256` sidecar in `sha256sum` format, the same as the SPA tarball.

The `MacOS/Waveguide Generator` stub keeps the `sysctl.proc_translated`
Rosetta guard and sets `WG2_BUNDLE=1`. `launch/serve.py` and
`server/platform/paths.py` learn to resolve `REPO_ROOT` from an explicit
`WG2_APP_ROOT` environment variable when set (the app layer), falling back to
the checkout-relative computation.

Verification is part of the script: after building, it launches
`Resources/runtime/bin/python3.13 scripts/check_backends.py` from a *copy*
of the bundle in a temporary directory and requires Metal ready (on Apple
Silicon), then starts the server with `--no-browser` on a free port and
fetches `/` and `/health`, then re-verifies the ad-hoc signature (the stub
redirects `__pycache__` and the numba cache outside the bundle so a run
never breaks the seal).

`.github/workflows/release.yml` gains a `macos-bundle` job on
`macos-latest` (arm64; Xcode present for Swift). It runs after the SPA job,
downloads the SPA artifact instead of rebuilding it, runs
`scripts/build_bundle.py --spa <tarball>`, and attaches the DMG, the app
layer ZIP/manifest, runtime ZIP, and their `.sha256` files as workflow artifacts.
The Windows job contributes its complete installer and runtime pairs. A final
publisher validates exactly those seven pairs and their sidecars, uploads them to a
draft, and publishes only after every build job succeeds.

## Step 3 — in-app updater

The update check in `server/updates/service.py` stays. What changes is what
happens when the running instance is a bundle (`WG2_BUNDLE=1`):

- `checkout_status` returns `kind: "bundle"`, `updateSupported: true`, with
  the installed `APP-MANIFEST.json` version and runtime id.
- `_parse_release` also recognises the bundle assets and records the runtime
  id the release's app layer requires (read from the release's
  `APP-MANIFEST.json`, which the build job uploads as a separate small asset
  `update-app-<version>.manifest.json`).
- `update_action` for a bundle is `{"kind": "bundle_download", "assets": [...],
  "downloadBytes": N}`; the UI shows size and a single **Install update**
  button, no command fallback.
- `POST /api/updates/install` in bundle mode downloads the app layer (and the
  runtime layer when the id differs) into `<data>/updates/<version>/`,
  verifies each against its `.sha256`, extracts with the same path-safety
  checks as `scripts/fetch_spa.py`, and writes `update.json` for the
  desktop launcher. Progress is exposed through `/api/updates/status`
  (`installState: downloading | verifying | ready | failed`, with active version
  and bytes). The interface keeps polling through unchanged byte counts,
  verification pauses, and transient status-read failures until a terminal state.
- `launchers/desktop.py` observes the request file exactly as the status
  window does today, stops the server, and runs `launchers/apply_update.py`
  **from the staged new app layer** (so a bug in the old updater cannot block
  an update forever): it swaps `Resources/app` (and `Resources/runtime`) with
  the staged directories using rename-into-place with a `.previous`
  fallback, re-signs the bundle ad-hoc, and relaunches through
  `open -n <bundle>` (macOS) or `"<exe>" -m launchers.desktop <args...>`
  (Windows). The previous layers are kept until the new native application has
  initialized successfully; macOS cleanup is signed and verified before rollback
  material is deleted.
- Files the app downloads carry no quarantine attribute, so the relaunch does
  not trigger Gatekeeper. The first launch of a freshly downloaded DMG still
  needs **Open Anyway** once; the README documents this already.

Tests: service-level tests with a fake fetcher for bundle classification and
asset selection; an apply-update test that builds two fake layouts in a temp
directory and checks swap, fallback on a failed swap, and the `.previous`
cleanup.

## Step 4 — Windows

- `scripts/build_bundle.py --platform windows` produces
  `Waveguide Generator/` with `runtime/` (uv Windows x86-64 build), `app/`,
  `Waveguide Generator.exe` (copied `pythonw.exe`, `launchers/desktop.py`
  located through a sibling `Waveguide Generator._pth`/`sitecustomize`
  bootstrap) and a `Waveguide.Generator-<version>-windows-x86_64.zip`. A
  no-argument launch enters the desktop bootstrap; interpreter `-c`, `-m`, and
  script commands stay available to workers. pywebview uses WebView2 through
  pythonnet; the Evergreen WebView2
  runtime ships with Windows 11 and recent Windows 10, and the launcher
  reports a precise repair hint when it is absent.
- numba/llvmlite need the MSVC runtime; the build job copies
  `vcruntime140.dll`, `vcruntime140_1.dll`, `msvcp140.dll` from the runner's
  redistributable into `runtime/` so a clean machine does not need the
  installer. `scripts/check_backends.py` is the gate, as on macOS.
- SmartScreen shows "unknown publisher" on first run; documented the same
  way as Gatekeeper.
- The updater is shared code; its Windows relaunch command is explicitly
  `"<exe>" -m launchers.desktop <args...>` so application arguments are not
  consumed as CPython options.

Runs on `windows-latest` in `release.yml`. The Windows CI and real-machine gates
remain recorded in this plan's **Verification status** section until that CI job
and a fresh Windows machine have actually run the ZIP.

## Out of scope

Developer ID signing and notarization (a separate purchase decision that
plugs into `build_bundle.py` later), Linux bundles, and any change to the
checkout-based development workflow, which remains the way the application
is developed and the way CI tests it.
