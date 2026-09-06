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

**Where each of those three stands, re-checked against the code 2026-09-05.** They
were written as one sentence and are three different pieces of work. None of them
is closed here: what follows records what the code does today and what each still
needs, so the release owner can decide. **Whether these three gates hold 0.3.2 or
move to 0.3.3 is an open question with the release owner, not settled below.**

- **Publisher authentication — open.** `server/updates/bundle.py` verifies
  integrity only: GitHub's per-asset `digest` over TLS, with the `.sha256` sidecar
  as fallback. That establishes the bytes match what the API described; it
  establishes nothing about who produced them. The bundle is ad-hoc signed —
  `codesign --force --deep --sign -`, in `scripts/build_bundle.py` and again in the
  post-swap reseal in `launchers/apply_update.py` — which lets the app run but
  carries no publisher identity.

  Closing this is a trust anchor, a signing step and key management, plus the
  release decision about which of those to adopt. Two independent routes exist and
  should not be conflated: **platform signing** (a Developer ID / Authenticode
  identity, which also addresses Gatekeeper and SmartScreen) and **an updater
  signature of WG's own** — signing the release manifest with a key whose public
  half ships inside the installed updater, verified before a layer is swapped in.
  The second does not depend on the first and is ordinary code plus a key to
  protect. Which to take, and where the private key lives, is the decision.

  **Re-checked 2026-09-06; it stays open. What was checked, so the next reader need
  not repeat it:**

  - The integrity side has no hole left to close as a consolation prize.
    `_valid_proof` already refuses an asset carrying neither a GitHub `digest` nor a
    checksum URL bound to that exact release, layer and filename — and refuses it
    during validation, before the download rather than after. A digest mismatch
    refuses before extraction. There is no bounded "verify harder" change available
    that does not need a key.
  - What *was* implementable with no secret and no trust decision was the wording,
    and it was wrong in one place: the README described the download as
    "checksum-verified" without saying what that does not establish. It now says
    integrity, not authenticity, in the paragraph a user reads before clicking
    **Install update**.

  **The gate, stated as the decision it is.** One of these is required from the
  release owner. No agent can supply either, and neither is a code question:

  1. **A signing identity** — an Apple Developer ID (with notarization) and/or a
     Windows Authenticode certificate. A purchase and a key-custody decision. It
     also closes Gatekeeper and SmartScreen, which nothing else here does.
  2. **A key for WG's own updater signature** — a private key that must live
     somewhere protected, plus the decision about where. The verification half is
     ordinary bounded code; it is worthless until something signs.

  A third route, **Sigstore keyless signing** from the release workflow, needs no
  long-lived private key — but it needs `id-token: write` on the workflow, which is
  `GIT-WORKFLOW.md` §1.2.5 and Magnus's call, every time. Named so the option is not
  lost, not proposed as taken.

  Until one of those exists, **0.3.2 ships with integrity verification and says so**,
  in the README and in the release notes. Nothing in the product may describe the
  GitHub digest or TLS as a publisher signature: they authenticate the repository
  and the transport, never the person who produced the bytes.

- **Recovery across the layer swap — a durable transaction journal now decides it;
  the platform acceptance is still owed.** *(Implemented 2026-09-06.)*

  `launchers/apply_update.py` records what it is about to rename before it renames
  any of it, in `<data>/update-transaction.json`, outside the bundle so the record
  survives the very directories the transaction moves and does not have to join the
  macOS reseal dance that `.previous` does. The record names the two layers, the
  staged directory each is coming from, and the `runtimeId` each layer carried
  before the swap and will carry after it — the one question the manifests cannot
  answer afterwards, because a layer says what it *is*, never which of two renames
  produced it.

  Reconciliation reads that record and the live directories, and nothing else. The
  staged directory is the marker that does the work: it exists until the moment it
  *becomes* the live layer, so its presence says "this layer was not installed"
  whatever order the renames reached the disk in. That is why no decision here
  depends on rename ordering.

  What the journal did not do is remove the file-system limits, so they are stated
  rather than glossed:

  - **Publication is durable only where a directory can be flushed.** The record is
    written to a temporary name, flushed, and renamed into place; the rename is
    durable once the directory is flushed, which POSIX can do and Windows cannot
    (`os.open` offers no `FILE_FLAG_BACKUP_SEMANTICS`, and `FlushFileBuffers` is not
    documented to do anything useful for a directory handle). Three states follow
    from that, and each is answered: the new record; the previous record with the
    temporary file beside it, which `read_journal` reports as unresolved whatever is
    at the published name; or no record at all, which leaves the manifest and
    directory checks that predate the journal in charge. `write_journal` reports
    which guarantee it obtained and logs when it got the weaker one.

    **This is not a claim that every state a power cut can leave is covered.** It is
    the narrower one the tests support: these three *journal* states are handled
    conservatively, and the journal adds information without subtracting the safety
    that existed before it. Torn writes inside a layer directory, a file system that
    reorders more than renames, and hardware that acknowledges a flush it has not
    performed are all outside what any of this establishes.
  - **macOS `fsync` is not a media flush.** `F_FULLFSYNC` is used where the file
    system implements it. Where it does not — reported by `ENOTSUP`, `EINVAL` and
    friends — the code falls back to `fsync` and logs that the guarantee is weaker.
    Any other error propagates, so a failed write is never quietly downgraded into
    a quieter flush that then reports success.
  - **A restore is as interruptible as the swap.** A rollback killed after its first
    layer leaves exactly the shape a finished swap leaves. Both are covered: every
    path that restores writes a `rolling-back` state before it starts, and for every
    bundle this project builds the two manifests carry `runtimeId`s that disagree in
    that state anyway.

    **That marker is the one journal write that is not advisory, and it is
    required rather than best-effort.** The others are: reconciliation decides
    from the live directories, so losing "swapped" changes nothing, and a
    terminal state is written only after the work it describes has been done and
    observed, so a start that misses it reaches the same conclusion again. The
    `rolling-back` marker is different because a branch depends on it — and the
    manifests can only stand in for it when the two layers carry *different*
    `runtimeId`s, which an app-only update and any same-runtime update do not. So
    nothing is renamed until it is recorded, exactly as no swap begins until its
    own intent is; a restore that cannot record itself refuses and leaves the
    installation as it found it, which the next start still reconciles from the
    record the swap already wrote. "Deliberately not written" — an untrusted
    record, left alone on purpose — is distinguished from "the write failed",
    because an untrusted record already sends reconciliation down the restoring
    path and needs no marker to steer it.

  Recovery now runs for **every start mode**. It used to live inside
  `DesktopWindow._wait_for_frontend`, so `--browser` and `--no-gui` skipped it
  entirely — the modes a user reaches for when the window will not open. It is
  called from `launchers/statusapp/__main__.py:main` before the branch that chooses
  a mode, which is also the point at which the least of the application has been
  imported.

  Three answers, because two of them were once collapsed into one. A recovery that
  *decides* the installation is broken refuses. A recovery that **raised** refuses
  too: it had already begun, and it renames directories, so the exception may have
  arrived between two of them. A recovery module that could not be **imported**
  refuses for an installed bundle and starts for a source checkout — a checkout has
  no swappable layers, which is evidence in itself, while "nothing ran" describes
  only the invocation and says nothing about an installation that may already be
  part-way through a change.

  A cheaper check was tried there and removed. It asked whether both layers existed
  and `layers_disagree` was false, and that helper answers false when either
  manifest is missing or unreadable — deliberately, since it is the compatibility
  path for bundles predating the field. Two empty directories passed it as
  "verified": a check that says yes to the state it exists to catch. Matching ids
  would not have sufficed either, because a restore whose renames finished and whose
  seal did not looks exactly like a matched pair. Real evidence means reading the
  scoped journal and re-checking the seal, which is reconciliation; a second, weaker
  copy of it written to keep a broken installation starting is the wrong trade.

  The mixed-generation path re-seals the macOS bundle, which it did not before
  (`_roll_back_mixed_generation` returned without calling `repair_bundle`, unlike
  the missing-layer path beside it).

  An external entry point exists for the case where a layer is the thing that is
  missing: `apply_update.py --recover --bundle … --data-dir …`. It could not
  previously run in that case at all — the module imported `shared.safe_names` from
  inside the `app` layer at import time. That import is still eager, because nothing
  may import lazily once renaming has begun, but its failure is now recorded rather
  than fatal, and installing launcher files, the one thing that needs it, refuses
  without it. A copy is now staged at `<data>/rollback/apply_update.py` when the
  transaction opens, not only when a handled failure hands off, so the copy exists
  for the crash that never reaches a handoff.

  **The swap's last window, and what now closes it.** *(Implemented 2026-09-06.)*
  Automatic recovery ran inside the application, and every platform launcher
  reached the application through the `app` layer: Linux refused outright
  (`exit 71`), macOS `chdir`-ed into `app` before `exec` and failed there, and the
  Windows bootstrap it needed (`sitecustomize` → `wg_desktop_bootstrap`) lived in
  the app layer. So a swap killed between `app` → `app.previous` and
  `staged` → `app` was recoverable only by hand.

  The route that closes it is staged **beside** the two layers rather than inside
  one. `scripts/build_bundle.write_recovery_layer` writes `<resources>/recovery`
  into every bundle: a byte copy of `launchers/apply_update.py`, the entry
  `launchers/bundle_recovery.py` that runs it, and `RECOVERY-MANIFEST.json`
  recording their digests. It is inside the bundle — the same download, the same
  macOS seal, the same reinstall — and outside `app` and `runtime`, which are the
  only directories the transaction renames.

  - Linux — the generated launcher runs the entry with whichever of
    `runtime/bin/python3.13` and `runtime.previous/bin/python3.13` survived, then
    re-checks; the `exit 71` refusal remains for what recovery cannot fix.
  - macOS — `launchers/macos/launcher.c` checks for the app layer before it
    `chdir`s, runs the same entry as a child, waits for it, and continues into the
    application if the layer came back.
  - Windows — `Waveguide Generator._pth` lists `recovery` ahead of `app`, so the
    `import site` hook that starts everything is found in a directory an update
    never renames. `recovery/sitecustomize.py` hands straight back to the app
    layer's own `wg_desktop_bootstrap` whenever that layer is present, so the half
    that changes with the application still ships and updates with it and only the
    shim is frozen at install time.

  **What it will and will not run.** The helper is named by this project, not by
  the journal, and the interpreter is the bundle's own, by absolute path. Nothing
  comes from `PATH`, from the data directory, or from the record of the interrupted
  transaction — the journal decides *whether* there is something to recover, inside
  the helper, and never *what to run*. The staged `<data>/rollback/apply_update.py`
  copy remains the manual route and is deliberately **not** what the automatic one
  executes: it is writable by anything that can write the data directory.

  The digest beside the helper is an **integrity** check, not publisher
  authentication. It catches a truncated, partially written or accidentally
  replaced copy — the states an interrupted install actually produces — and it does
  **not** catch someone who can write the installation directory, because they
  rewrite the manifest too. The macOS seal cannot stand in for it here either: the
  seal is invalid during an interrupted rename, which is exactly why recovery has to
  re-seal. Authenticating the helper needs a publisher key this project does not
  have, and no claim above or below assumes one.

  **The live-updater interlock.** An update in progress is indistinguishable from an
  interrupted one from outside, because a layer really is absent between two
  renames. Waiting is not an interlock — an updater slower than any wait is still
  mid-swap — so every path that decides a transaction takes the same exclusive
  claim, `launchers/update_lock.py`: the updater CLI for an apply, a rollback **and**
  a `--recover`; the in-application startup recovery in
  `launchers/statusapp/updater.py`; and the detached rollback helper, which is that
  same CLI running from a copy. Each fails closed, leaving an installation somebody
  else owns exactly as they left it.

  It is an OS lock on a descriptor rather than a pid file on purpose — the kernel
  drops it when the holder dies, so a killed updater releases it by dying, which is
  precisely the case this whole route exists for.

  **Nothing blocks, so the relaunch protocol is safe.** Acquisition is non-blocking
  everywhere. The updater installs while holding the claim and then starts the
  application; the application's startup recovery finds the claim held, concludes it
  has nothing to decide, and starts. Waiting there would deadlock the updater against
  the child it had just launched. For the same reason the bootstrap recovery does
  *not* hold the claim across the helper it runs: the helper takes it, and the answer
  comes back as the shared `EXIT_UPDATE_IN_PROGRESS` exit code. A dwell is kept in
  front of all of it as a courtesy, because most updates finish in well under a
  second and should cost nobody a refusal.

  **Scope.** The claim is keyed on the resolved installation path and lives in the
  per-user cache root the launchers already redirect caches into — `Library/Caches`,
  `%LOCALAPPDATA%`, `$XDG_CACHE_HOME` — not under the data directory and not inside
  the bundle. `--data-dir` is the caller's choice, so a claim rooted there is one a
  second process steps around by naming a different directory, while both rename the
  same installation's layers; documenting that as an invariant would not have made it
  one. Inside the bundle is worse: on macOS it is sealed, and writing there would
  break the signature recovery exists to restore. So two data directories pointing at
  one installation now share one claim, and two installations sharing one data
  directory keep two. The limit that remains, stated rather than papered over: the
  root is per user, so two *different* users updating one shared installation do not
  exclude each other. Closing that needs a writable system-wide location this
  application does not claim.

  **A busy claim is not permission to run.** The startup recovery takes the claim like
  every other path that decides a transaction, and when it cannot get it the answer
  depends on the *installed generation*, not on the claim — because the claim looks
  identical in the two cases that matter. A consistent installation (both layers
  present, manifests agreeing) is the updater's legitimate post-seal relaunch, and it
  starts. Anything else — a layer absent, or an app and a runtime from two
  generations — is a live half-swap that nothing has reconciled, and it refuses with
  a message naming what it found. A claim that could not be attempted at all (an
  unreadable or uncreatable lock) refuses too: ownership unknown over an installation
  that may be mid-change is the definition of the fail-closed case. An earlier
  revision answered both with "nothing to recover" and started anyway;
  `failclosed-before-after.log` records the same two controls failing against that
  behaviour and passing here.

  **No silent bypass.** `apply_update.py` imports the claim the two ways it is ever
  run — from the app layer as a package, and from a staged copy beside its own
  dependency — and does not fall back to a no-op. An earlier revision aliased
  `contextlib.nullcontext` when the import failed, which turned "this copy was staged
  wrong" into "this copy silently has no exclusion", on the detached rollback, which
  renames layers. Both staging paths (`stage_recovery_helper` and
  `launch_rollback_handoff`) now copy `update_lock.py` beside the helper, and a copy
  missing it refuses to start rather than running unguarded.

  **What counts as recovered.** The helper's exit code is the verdict, and a
  restored directory is not one. `recover_transaction` reports failure when it could
  not re-seal the bundle and leaves the transaction open on purpose so the next
  start tries again; reading the file system instead would launch a bundle whose
  signature still describes the generation that was replaced. The bootstrap requires
  both a zero exit **and** an installation whose two layers are present with their
  manifests, and it waits on that whole condition rather than on `app` alone —
  `swap_staged_layers` takes one layer at a time, so a kill inside the runtime's turn
  leaves `app` already replaced and no interpreter to run it with.

  **Which installations get this.** It arrives with the installer and only with the
  installer:

  | Upgrade path | Recovery capability |
  |---|---|
  | Fresh install from a new DMG, Windows setup or Linux tarball | **yes** — `assemble_*_bundle` stages `recovery/`, and the launchers are the new ones |
  | An installation made before this, receiving new app/runtime layers in-app | **no** — the layer archives are built from the layer trees and carry no `recovery/`; `refresh_launcher_files` transports only what the runtime manifest's `launcherFiles` names (the renamed pythonw and its DLLs); the `._pth` and the native launchers are not refreshed at all. It keeps recovering exactly as it did before, which is: whenever the app layer survived |
  | An installation that has `recovery/`, receiving later app layers in-app | keeps the `recovery/` it was installed with. It does **not** become newer, and nothing here claims it does |

  The seam that had to be preserved, and is: `write_windows_bootstrap` still writes
  `app/sitecustomize.py`. An older installation's `._pth` lists only `app`, so that
  copy is its only site hook, and dropping it would have left a double-click doing
  nothing on every old Windows install that took this app layer. On a bundle that
  has `recovery`, that directory precedes `app` on the import path, so the shim is
  what `site` finds and the app copy is never imported. Both orders are asserted.

  `test_the_native_launchers_reach_recovery_without_an_app_layer` asserts the three
  wirings so the statement cannot rot silently, and
  `server/tests/test_bundle_recovery.py` drives the first two end to end against
  installations interrupted after a real `begin_update_transaction`: the real
  compiled macOS binary with no app layer, the same binary with no *runtime* layer
  and an emptied `PATH`, and the real generated Linux script. On one fixture the
  shipped launcher exits 71 having recovered nothing; the changed one restores the
  layers and continues into the application, with user data intact. The negative
  control runs the real helper against a bundle that cannot be re-sealed: the layers
  come back, the helper fails, and nothing is started.

  **The Windows limit, stated plainly.** Its arm is exercised as the Python it is —
  the shim's decision, the `._pth` ordering, the staged files — and not as a
  double-click on a renamed `pythonw.exe`. That needs a Windows machine, which this
  work has never had, and it is the one part of this section that is not measured.

  **Evidence, and its limits.** `server/tests/test_update_transaction.py` kills a
  real updater subprocess with `SIGKILL` at each of the four renames and at three
  points inside a restore, then recovers in a process that starts afterwards and
  shares nothing with the one that died. That is a real proof of fresh-process
  recovery; it is **not** a proof of power-loss durability, because the file system
  is never actually interrupted, so every write those processes issued did land.
  The states only a lost write can produce are constructed explicitly instead.

  **Still owed, and not claimable from this machine:** a real interrupted upgrade on
  an installed macOS bundle and an installed Windows bundle — app and runtime
  changing together, the machine cut off rather than the process killed, user data
  checked afterwards. The Windows half in particular has never run: all of the above
  was developed and measured on macOS.

- **Release-action and tool provenance — closed for the release-building
  workflows.** *(Implemented 2026-09-06.)* `release.yml` and `rc-build.yml` pin
  every action to an immutable commit with the release it belonged to in a trailing
  comment, and Node to the exact patch `20.20.2`. The SHAs were resolved with
  `gh api repos/<action>/commits/<tag>` on 2026-09-05 and are asserted against a
  reviewed table in `scripts/tests/test_release_workflow.py`, which also fails if
  the two workflows ever disagree — an RC hand-tested by different actions than the
  release does not test the release.

  Tool downloads, checked rather than assumed:

  - **uv** — `astral-sh/setup-uv` validates the download against a `KNOWN_CHECKSUMS`
    table compiled into the action. Verified at the pinned commit that the table
    contains entries for uv `0.11.2` on all three build platforms, so the check
    cannot silently fall through (it returns without validating for a version it
    does not know).
  - **CPython** — pinned twice, `PYTHON_VERSION = "3.13.12"` and
    `PYTHON_BUILD = "20260325"`, and `require_python_build` fails the build when
    uv's catalog resolves a different python-build-standalone release.
  - **Node** — `actions/setup-node` performs **no** digest check; verified by
    reading its distribution sources at the pinned commit, which contain no
    checksum code. Nothing in this repository can add one, so the workflows assert
    the exact version after installation instead, which is what a substituted
    download would change loudly.
  - **Inno Setup** — the chocolatey package embeds its installer rather than
    fetching one, so there is no separate artifact to checksum. The workflows
    assert the compiler's own version banner instead.

  **What none of this establishes.** The version checks above are *drift detection*
  — a substituted binary can print whatever version it likes, so a version match is
  not authentication of bytes. Authenticating the Node download is possible and is
  **not implemented**: it would mean fetching the archive in the workflow and
  checking it against a digest pinned in this repository, which is its own change
  with its own review. The four pinned tools are uv, the CPython standalone build,
  Node and Inno Setup — that is the list, not a summary of everything the build
  touches. The shell, tar, git, `hdiutil`, `codesign`, the system Python and the
  platform toolchains all arrive with the hosted runner image (`ubuntu-latest`,
  `macos-latest`, `windows-latest`, `ubuntu-24.04`) at whatever version it carries
  that week, and those images are rebuilt weekly with no identity a workflow can
  name. So this is meaningful drift reduction, and it is neither a reproducible nor
  an authenticated toolchain; neither should be claimed from it. The scoping is
  written into both workflow files, not only here.

  `ci.yml` was deliberately left alone. It gates the release commit rather than
  building the release artifacts, and it is outside this change's scope; pinning it
  is a recommendation for the owner, not a claim made here.

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

  **Superseded 2026-09-05.** Linux bundles shipped, which crossed this
  document's own scope boundary while the deferral stayed put — and a Fedora
  user reported the result: pywebview present, no backend, a status window
  and a browser where the plan's own goal is one native window. The window is
  now offered on Linux too, on `PySide6` + `QtPy` pinned beside pywebview
  (the dependencies of its `[pyside6]` extra, written out so their
  platform markers and exact versions remain explicit in the lock). GTK was rejected for
  needing WebKitGTK *from the distribution*, which the bundle's "nothing is
  installed system-wide" promise does not allow. What remains out of scope
  here is the packaging half: see `Linux bundles` below.
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

**Linux bundles are no longer out of scope** — one ships, and a tarball plus
`install.sh` is what it ships as. What is still out of scope, and what the
2026-09-05 Fedora report asks for, is a *graphical* install route: every
other platform has one (a `.command` to double-click, an `.exe`), Linux has a
terminal. An AppImage is the closest equivalent and the bundle is most of the
way to being one, but it is a single immutable file and `launchers/apply_update.py`
replaces `app` and `runtime` in place — so it needs a deliberate decision
about the in-app updater (keep the tarball as the self-updating route and
offer the AppImage as an on-ramp, or move AppImage users to zsync) before it
is worth building.
