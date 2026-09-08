# Update channels

What each channel reads, where the choice lives, and what a "newest build of
`main`" channel would additionally require. Written because the second question
has a short answer that is easy to get wrong: the client is already able to
offer such a build, and nothing publishes one.

## The choice

Stable or Beta is chosen in the **update dialog** — click the version beside the
Waveguide Generator name in the top bar. It moved there from Settings so it sits
beside the verdict it decides; Settings keeps a pointer, because a setting that
vanishes reads as one that was removed.

The value is stored server-side, in the `updates` namespace of the settings
store (`UPDATE_SETTINGS_NAMESPACE`, `server/updates/service.py`). A
browser-scoped copy would be back on stable the first time the update it asked
for actually landed, which is the one moment the preference exists to survive.
Switching discards the cached verdict, because it answered the other channel's
question: `set_channel` clears the cached release and sets `nextCheckEpoch` to
0, and `get_status` independently forces a re-check when the cached channel does
not match the stored one.

## What each channel offers

| | Reads | Offers |
|---|---|---|
| `stable` | `GET /releases/latest` | The newest non-pre-release. GitHub's own definition, so a pre-release cannot arrive by accident. |
| `beta` | `GET /releases?per_page=20` | The highest version among recent releases, pre-releases included, by SemVer precedence (`_beta_release_payload`). |

Both refuse `-updates` companions: a companion is a GitHub pre-release too, and
it carries update layers with no installer, so offering one as a release would
hand a beta install a bag of files it cannot run.

**Beta is per release candidate, not per commit.** A beta exists when one is
published — `v0.4.0-beta.1` — and nothing publishes one per push to `main`.

## "Beta should be the newest build of `main`"

That is a different promise. The client half of it is implemented; what is
missing is publication, and that needs an authorization no code can grant.

### The client half

Already true of a pre-release tag before any of this work, and tested:

- **The tag shape.** `TAG_RE` (in `shared/release_assets.py`) admits any SemVer
  pre-release label, so `v0.4.0-main.7` parses exactly like `v0.4.0-beta.1`.
- **The install path.** `_is_installable_tag` accepts it, `update_action` builds
  the installer command with `--tag v0.4.0-main.7`, and `request_install`
  accepts it.
- **The transport.** `trusted_asset_url` accepts
  `https://github.com/<repo>/releases/download/<tag>/<asset>` for any release
  tag of this repository — and refuses an Actions-artifact URL.

Added for this requirement, because a channel that publishes often breaks
assumptions a channel that publishes rarely never tested:

- **Selection reads more than one page**, so a page holding no offerable release
  is not the end of the search. `_beta_release_payload` takes the highest
  version among the pages it read.
- **The update companion is resolved by its exact tag**, not by scanning a list
  it can fall out of, with the scan kept as a fallback.
- **The runtime-carrier search pages** for the same reason.
- **A build-only version stamp exists** (`bump_version.py --build-stamp`) and the
  app manifest can name the commit a build came from
  (`build_bundle.py --source-commit`).

That a hypothetical tag shape is accepted is not by itself the feature. It is
one precondition of it, and it is listed here as one.

### The release-list window, precisely

Three lookups read the recent-releases list (`releases?per_page=20`), and only
one of them is bounded by it. Getting this right matters, because the wrong
reading turns a compatibility question into an invented impossibility.

| Lookup | Reads | What it does now |
|---|---|---|
| `_beta_release_payload` | release list pages, newest publication first | Takes the **highest version among the pages it read**, reading another page only while no page has yielded a candidate, up to `MAX_RELEASE_PAGES`. Not a global maximum: a release older than the pages read is not offered. |
| `_updates_release(version)` | `GET /releases/tags/v<version>-updates` | **One request, by exact tag** — no list, so no window. The list scan remains the fallback for an origin that cannot answer it. |
| `_earlier_runtime_asset(id)` | release list pages | Pages until it finds a carrier holding that runtime layer, up to the same bound. It is content-addressed, so it can only be found by looking, and it sits further back the more has been published since. |

`_availability` then compares `_version(release.tag)` with the running version,
and `_parse_release` is what calls the last two — only for a bundle install, and
only after a release has been chosen.

**Why "the newest release is on the first page" is not the answer.** It proves
only that the newest *publication* is there. Companions are refused outright, so
a history that publishes one beside every release fills half of every page with
entries that can never be selected — and a busier schedule can fill a whole one,
at which point the first page answers nothing at all. That is why the beta scan
reads on rather than giving up, and why the policy above is stated as "the
highest version among the pages read" rather than as a guarantee about all
releases. `server/tests/test_update_channel.py` holds each half of it.

**Tag mutation is not required for a rolling channel, and nothing here asks for
it.** Immutable per-commit pre-releases are the design below.

### What is missing, and why it is not a client change

1. **Nothing publishes a main build.** `release.yml` runs on
   `workflow_dispatch` only, and `rc-build.yml` — which does build every
   installer from a commit on `main` — publishes them as **Actions artifacts**.
   An artifact is not reachable by the updater: it is served from a different
   origin and needs an authenticated API request, and `trusted_asset_url`
   accepts only this repository's release downloads. Widening that check is not
   the fix; it is what the check exists to prevent, and no part of this design
   asks for a private key or a new trusted origin.

2. **A main build must advertise the *next* version — supported now.** SemVer
   rule 11: a pre-release sorts below the release sharing its core numbers. A
   build named from `shared/version.json`, which still names the last release,
   is *older* than the copy already installed, and the honest verdict is "ahead"
   rather than an update. So the build carries `v<next>-main.<n>`, and
   `scripts/bump_version.py --build-stamp --set <version>` now writes that one
   string to every declared copy: `shared/version.json`, `frontend/package.json`,
   the npm lockfile and the macOS `Info.plist`. Without `--build-stamp` the
   script refuses a **build stamp**, so a release commit cannot acquire one by
   accident, and `release.yml` independently refuses one — a main build can
   never go out through it.

   Both refusals used to be a side effect of a narrower parser: neither
   understood a hyphen at all. That had to change, because a *release*
   pre-release — `0.3.2-rc.1` — is a release and both of them refused it too,
   so no release candidate could be published. The two are now told apart by
   the label's identifier (`shared/release_assets.release_prerelease`: `alpha`,
   `beta`, `rc` are releases, everything else is a build), and each refusal is
   asserted by its own test rather than inherited. `main.<n>` is a build stamp
   under that rule, which is what this design has always relied on. See
   README.md's *Releasing* section.

3. **Old clients must keep working while it runs.** Every *published* client
   resolves the companion inside the 20-entry window. The by-tag lookup below
   fixes that for clients carrying it and cannot fix the ones already
   installed, so publishing cadence remains a decision. See *Old-client
   compatibility* below.

4. **Authorization.** Publishing on push needs a workflow with `contents: write`
   on `push: [main]` (§1.2.5, repository settings and workflow permissions) and
   creates releases (§1.2.3). Both are per-operation authorizations from the
   release owner. Nothing in this branch enables either: the proposal below is a
   template outside `.github/workflows/`, so GitHub never registers it.

Until those are settled, the honest thing for the product to say is what the
dialog now says: Beta offers the newest release published on GitHub,
pre-releases included, and is per release candidate rather than per commit.

## The proposal: immutable per-commit main builds

[`main-build.workflow-proposal.yml`](main-build.workflow-proposal.yml), beside
this file, is the concrete workflow — deliberately **not** under
`.github/workflows/`, so it has no triggers and no permissions until someone
moves it there. It builds the same installers `rc-build.yml` already builds and
publishes them as an immutable pre-release; it introduces no signing identity,
asks for no private key, and does not widen `trusted_asset_url`.

### Version identity

- **Tag and version:** `v<next-patch>-main.<run_number>`, where `<next-patch>` is
  `shared/version.json` with its patch incremented. Monotonic by construction:
  `run_number` never repeats or goes backwards, so every build outranks the last
  and all of them outrank the release they are built past.
- **No tag ever moves or is deleted.** Each build is its own immutable
  pre-release, exactly like a beta.
- **The build stamps that version everywhere, and commits it inside the build.**
  `scripts/build_bundle.py` materializes the app layer from **Git blobs** at
  HEAD and refuses a dirty worktree, so a stamp left in the checkout fails the
  build — and if it did not, it would package the previous version under the new
  name. `scripts/bump_version.py --build-stamp --set <version>` writes the one
  string to every declared copy, and the stamp commit is what carries it into
  the packaged backend, the SPA it embeds, the manifest and the installer
  filenames together. That commit is never pushed and never tagged; it exists
  inside the runner. `scripts/tests/test_build_bundle.py` pins the
  materializer's half of this, and `server/tests/test_version_consistency.py`
  the stamp's.
- **The app manifest records the exact source commit.** `build_bundle.py
  --source-commit <sha>` writes `sourceCommit` beside the `commit` field the
  manifest already carries, which after the stamp names the build commit.
  Omitted entirely when not passed, so a release manifest is byte for byte what
  it always was.
- **When the next stable release lands**, `v<next-patch>` outranks every
  `v<next-patch>-main.<n>` (SemVer rule 11 again), so a main-build install moves
  onto it without any special case.

### What has and has not been verified

Stated plainly, because "it parses" is not "it builds":

| Checked | How |
|---|---|
| The workflow is a complete graph | `scripts/tests/test_main_build_proposal.py`: every `needs` names a job that exists and has steps, artifacts match by name across jobs, and every asset `shared/release_assets.py` produces is matched by an upload path. |
| One commit is built | The identity job resolves `inputs.sha` once; every other job checks out that SHA, and the manifest records it. |
| The flags it calls exist | The test runs `--help` on both scripts. |
| **The stamp runs on a runner with nothing installed** | The test executes the template's own stamp commands as argv — no shell, so it means the same thing on every platform the suite runs on — in a fresh checkout with site-packages disabled, and proves that disabling them is what stops a server import. `bump_version.py` reaches every copy of the version — including the OpenAPI snapshot's `info.version` — using only the standard library, so no dependency set has to exist before the version is decided. |
| The four platform stamp commits are the same commit | The same test stamps two independent checkouts and compares the resulting SHA. |
| Actions are pinned to digests, Node to a patch | Asserted, against the same action names `rc-build.yml` uses. A fixed ref detects **tool drift**; it is not publisher authentication of anything the workflow produces. |
| A stamp reaches the app layer only once committed | `scripts/tests/test_build_bundle.py`, against the real materializer. |
| The native version fields take the value passed to them | `server/tests/test_version_consistency.py` for the plist, `scripts/tests/test_build_bundle.py` for the Inno defines. |
| An installed copy accepts a pre-release update request | `server/tests/test_update_handoff.py`, both the bundle and the release-tag paths. |
| **Not checked: that Inno Setup compiles the script** | ISCC is Windows-only and was not run. What is checked is that it is handed four numbers rather than a SemVer string, which is the input that made it refuse. |
| **Not checked: the PowerShell host itself** | The Windows stamp block is held to resolving to the same commands as the others; a run on real Windows CI is owed. |
| **Not checked: that macOS packages, or that any installer runs** | Those need the runners, and a published build needs the authorizations below. |
| **Not checked: that WG's own artifacts are signed** | They are not, here or for a release. Whether that is acceptable for a channel that installs more often is an open owner decision, and no pin in this workflow bears on it. |

### Old-client compatibility

Stated as behaviour, per audience:

- **Stable installs — the selection is unaffected; the layer lookup was not.**
  Two separate things, and only the first was ever safe by construction.
  *Selection*: `releases/latest` never returns a pre-release, so no main build
  can be offered on the stable channel. This is the property to re-check first
  if the design changes. *Resolving what to install*: a stable install still has
  to find `v<version>-updates` to get its update layers, and on a client that
  scans a twenty-entry list that companion can be pushed out by anything
  published since — main builds included. Such an install is not offered a beta;
  it is offered its own stable update and then cannot resolve the layers, which
  surfaces as "update preparing" and stays there. That is the failure the by-tag
  lookup removes, and it is why publishing cadence is a real decision rather
  than a formality.
- **Beta installs, any version.** Offered the newest main build. That is the
  point, and it needs no client change.
- **Every install's companion lookup — the one real hazard.** A published client
  resolves `v<version>-updates` by scanning 20 recent releases. **The client fix
  is implemented**: `_updates_release` asks for the companion by its exact tag,
  with the list scan kept as the fallback, and `_earlier_runtime_asset` and the
  beta scan page. What that fixes is every install **that has it**.
  - **It does not reach installations already out there.** A copy running 0.3.1
    resolves its companion by scanning twenty entries and will keep doing so
    until it updates — and if its companion has already fallen out of that
    window, it cannot update in-app at all, which is precisely the state this
    must not create. Shipping the fix once is not the same as fixing old
    clients.
  - So the cadence decision stands and belongs to the release owner: until the
    versions in the field carry the by-tag lookup, keep the live pre-release
    count inside the window — dispatch-only publishing, which is what the
    proposal does, so the companion of the current release stays on the first
    page. A rate limit on the channel, not on the tags: nothing is deleted or
    moved.

The 20-entry window is a real bound with a real fix; it is not a reason to
mutate a published tag, and no disposition here does.
