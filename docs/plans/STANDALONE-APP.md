# Standalone application

Status: in progress (branch `feature/standalone-app`). This plan turns the
checkout-bound `.app` into a self-contained, self-updating desktop application
without Electron, Tauri, or a frozen interpreter. It is written as the contract
for the implementation batches; each numbered step is independently shippable
and is reviewed before the next starts.

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
  WebSockets. It is a native window around the same loopback server; nothing
  in the frontend changes for it except the two JavaScript `window.open` calls
  (job log, radiation-impedance download), which WKWebView's new-window hook
  ignores.

Freezing tools (PyInstaller, py2app, Nuitka) are rejected: numba/llvmlite,
gmsh's `find_library` lookup, bempp's hooks and WG's `sys.executable` worker
subprocesses all fight them. The relocatable interpreter keeps every module as
plain Python.

## Bundle layout

```
Waveguide Generator.app/Contents/
  Info.plist                      CFBundleExecutable = "Waveguide Generator"
  MacOS/Waveguide Generator       bash stub: Rosetta guard, then exec
                                  ../Resources/python/bin/python3.13 -m launchers.desktop
  Resources/WaveguideGenerator.icns
  Resources/runtime/              relocatable CPython + site-packages (the "runtime layer")
    bin/python3.13
    lib/python3.13/site-packages/...
    RUNTIME-MANIFEST.json         {"schemaVersion":1,"python":"3.13.12","platform":"macos-arm64",
                                   "requirementsSha256":"...", "pinsSha256":"..."}
  Resources/app/                  the "app layer": server/ launch/ launchers/ shared/
                                  scripts/ frontend/dist/ integrations/wglink/ docs/ LICENSE
    APP-MANIFEST.json             {"schemaVersion":1,"version":"0.2.5","commit":"<sha>",
                                   "runtimeRequirementsSha256":"..."}
```

The two layers are independent release assets:

| Asset | Contents | Changes when |
|---|---|---|
| `waveguide-generator-app-<version>.zip` | `Resources/app` | every release (a few MB) |
| `waveguide-generator-runtime-macos-arm64-<runtimeId>.zip` | `Resources/runtime` | `requirements-*.txt` or `pins.json` change |
| `Waveguide Generator-<version>-macos-arm64.dmg` | complete bundle | every release (first install) |

`runtimeId` is the first 12 hex digits of SHA-256 over the concatenation of
`server/requirements-runtime.txt`, `server/requirements-pins.txt`, and the
python-build-standalone version string. The app layer's `APP-MANIFEST.json`
names the runtime id it was built against, so the updater knows whether the
runtime must be replaced too.

Windows mirrors this with `Waveguide Generator/` as a folder (no bundle
concept): `Waveguide Generator.exe` is a copy of `pythonw.exe` beside
`runtime/` and `app/`; the zip is the distributable until an installer exists.

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
  `<a download>` exports land in the user's Downloads folder;
  `OPEN_EXTERNAL_LINKS_IN_BROWSER` stays True (documentation links).
  Must run on the main thread (Cocoa); the status window and the desktop
  window are therefore mutually exclusive front ends of the same controller.
- Startup failures before the window exists reuse
  `launchers/statusapp/__main__._report_startup_failure`, which already
  writes `statusapp.log` and shows a native dialog.
- `launchers/statusapp/__main__.py` gains `--window` and `--browser`; the
  status app remains the default for the checkout in this step (the bundle
  flips the default in step 2).
- A JS bridge object is exposed (`webview.create_window(..., js_api=api)`)
  with one method, `open_window(url)`, which opens a second pywebview window
  for the given same-origin URL. The frontend calls it only when
  `window.pywebview` exists.
- Frontend: replace the three `window.open(...)` sites in
  `frontend/src/shell/JobsPanel.tsx`:
  - Job log → an in-app `LogDialog` (same `role="dialog"` pattern and focus
    trap as `SettingsDialog.tsx`) that fetches `/api/jobs/{id}/log`, shows it
    monospaced with a Copy button and a Refresh button. Works identically in
    browser mode.
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
   -r server/requirements-pins.txt`. Remove `lib/tcl*`, `lib/tk*`, `lib/itcl*`,
   `lib/python3.13/idlelib`, `tkinter`, `turtledemo`, `ensurepip`, `pip`,
   `test`/`tests` directories under site-packages, and `__pycache__`. The
   Metal helper must exist at
   `site-packages/hornlab_metal_bem/metal/native_helper/.build/release/HornlabMetalBemNative`
   after the install; the build fails loudly if it does not (it means Swift
   was missing on the build machine — a source-only package would fall back
   to interpreting the helper at runtime and time out, as the 2026-06
   HornLab Studio attempt found).
2. **App layer.** Copy the tracked files of `server/ launch/ launchers/ shared/
   scripts/ integrations/wglink/ docs/ LICENSE README.md` (use `git ls-files`
   so ignored and untracked files never ship) plus `frontend/dist` (built
   fresh by the same job; refuse an unstamped or missing dist). Write
   `APP-MANIFEST.json`.
3. **Bundle.** Assemble the `.app`, write `Info.plist` from the existing one
   (`CFBundleShortVersionString` from `shared/version.json`), copy the `.icns`,
   then `codesign --force --deep --sign - "Waveguide Generator.app"` (ad-hoc;
   `/usr/bin/codesign` ships with macOS) and `hdiutil create -volname
   "Waveguide Generator" -srcfolder ... -format UDZO`. Every asset gets a
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
fetches `/` and `/api/health`.

`.github/workflows/release.yml` gains a `macos-bundle` job on
`macos-latest` (arm64; Xcode present for Swift). It runs after the SPA job,
downloads the SPA artifact instead of rebuilding it, runs
`scripts/build_bundle.py --spa <tarball>`, and attaches the DMG, the app
layer zip, the runtime zip (only when the runtime id is not already attached
to an earlier release — checked with `gh release view`), and their `.sha256`
files. The release body lists the assets.

## Step 3 — in-app updater

The update check in `server/updates/service.py` stays. What changes is what
happens when the running instance is a bundle (`WG2_BUNDLE=1`):

- `checkout_status` returns `kind: "bundle"`, `updateSupported: true`, with
  the installed `APP-MANIFEST.json` version and runtime id.
- `_parse_release` also recognises the bundle assets and records the runtime
  id the release's app layer requires (read from the release's
  `APP-MANIFEST.json`, which the build job uploads as a separate small asset
  `waveguide-generator-app-<version>.manifest.json`).
- `update_action` for a bundle is `{"kind": "bundle_download", "assets": [...],
  "downloadBytes": N}`; the UI shows size and a single **Install update**
  button, no command fallback.
- `POST /api/updates/install` in bundle mode downloads the app layer (and the
  runtime layer when the id differs) into `<data>/updates/<version>/`,
  verifies each against its `.sha256`, extracts with the same path-safety
  checks as `scripts/fetch_spa.py`, and writes `update-request.json` for the
  desktop launcher. Progress is exposed through `/api/updates/status`
  (`installState: downloading | verifying | ready | failed`, with bytes).
- `launchers/desktop.py` observes the request file exactly as the status
  window does today, stops the server, and runs `launchers/apply_update.py`
  **from the staged new app layer** (so a bug in the old updater cannot block
  an update forever): it swaps `Resources/app` (and `Resources/runtime`) with
  the staged directories using rename-into-place with a `.previous`
  fallback, re-signs the bundle ad-hoc, and relaunches through
  `open -n <bundle>` (macOS) or the exe path (Windows). The previous layers
  are kept until the new version has started once and reported healthy, then
  deleted.
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
  located through a sibling `Waveguide Generator.pth`-style bootstrap) and a
  zip. pywebview uses WebView2 through pythonnet; the Evergreen WebView2
  runtime ships with Windows 11 and recent Windows 10, and the launcher
  reports a precise repair hint when it is absent.
- numba/llvmlite need the MSVC runtime; the build job copies
  `vcruntime140.dll`, `vcruntime140_1.dll`, `msvcp140.dll` from the runner's
  redistributable into `runtime/` so a clean machine does not need the
  installer. `scripts/check_backends.py` is the gate, as on macOS.
- SmartScreen shows "unknown publisher" on first run; documented the same
  way as Gatekeeper.
- The updater is shared code; only the relaunch command differs.

Runs on `windows-latest` in `release.yml`. The fresh-machine gate for Windows
(`TODO.md`) stays open until a real machine has run the zip.

## Out of scope

Developer ID signing and notarization (a separate purchase decision that
plugs into `build_bundle.py` later), Linux bundles, and any change to the
checkout-based development workflow, which remains the way the application
is developed and the way CI tests it.
