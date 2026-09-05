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

That is a different promise, and the client already keeps its half of it.

### What already works, with no client change

- **The tag shape.** `TAG_RE` (in `shared/release_assets.py`) admits any SemVer
  pre-release label, so `v0.4.0-main.7` parses exactly like `v0.4.0-beta.1`.
- **The ordering.** `_beta_release_payload` ranks it by release precedence and
  offers the highest.
- **The install path.** `_is_installable_tag` accepts it, `update_action` builds
  the installer command with `--tag v0.4.0-main.7`, and `request_install`
  accepts it.
- **The transport.** `trusted_asset_url` accepts
  `https://github.com/<repo>/releases/download/<tag>/<asset>` for any release
  tag of this repository.

`server/tests/test_update_channel.py` holds these as tests, so the claim is
checked rather than asserted.

### The release-list window, precisely

Three lookups read the recent-releases list (`releases?per_page=20`), and only
one of them is bounded by it. Getting this right matters, because the wrong
reading turns a compatibility question into an invented impossibility.

| Lookup | Reads | Bounded by the window? |
|---|---|---|
| `_beta_release_payload` | the list, taking the highest version | **No.** The newest release is on the first page by construction. |
| `_updates_release(version)` | the list, matching the exact tag `v<version>-updates` | **Yes.** It is an older, specific tag, and it falls off the end. |
| `_earlier_runtime_asset(id)` | the list, for a carrier holding that runtime layer | **Yes**, on the rare path where the runtime moved and the new release does not carry the layer. |

`_availability` then compares `_version(release.tag)` with the running version,
and `_parse_release` is what calls the two bounded lookups — only for a bundle
install, and only after a release has been chosen.

The bound is therefore not a reason to mutate anything. A companion is
addressable by its exact tag: `GET /repos/<repo>/releases/tags/<tag>` answers it
in one request without reading a list at all, and the runtime search can page.
**Tag mutation is not required for a rolling channel, and this document does not
ask for it.** Immutable per-commit pre-releases are the design below.

### What is missing, and why it is not a client change

1. **Nothing publishes a main build.** `release.yml` runs on
   `workflow_dispatch` only, and `rc-build.yml` — which does build every
   installer from a commit on `main` — publishes them as **Actions artifacts**.
   An artifact is not reachable by the updater: it is served from a different
   origin and needs an authenticated API request, and `trusted_asset_url`
   accepts only this repository's release downloads. Widening that check is not
   the fix; it is what the check exists to prevent, and no part of this design
   asks for a private key or a new trusted origin.

2. **A main build must advertise the *next* version.** SemVer rule 11: a
   pre-release sorts below the release sharing its core numbers. A build tagged
   from `shared/version.json`, which still names the last release, is *older*
   than the copy already installed, and the honest verdict is "ahead" rather
   than an update. So the build has to carry `v<next>-main.<n>` and stamp that
   same string into `shared/version.json` inside the build — otherwise the
   installed copy reports the stable version and is offered the same build
   forever. `release.yml`'s guard requires a plain `MAJOR.MINOR.PATCH`, so a
   main build cannot go through that workflow as it stands, and
   `scripts/bump_version.py --set` validates against
   `SEMVER = ^(\d+)\.(\d+)\.(\d+)$` and refuses the label.

3. **Old clients must keep working while it runs.** Every published client
   resolves the companion inside the 20-entry window, so publishing main builds
   faster than that window drains would break `v<version>-updates` resolution
   for **stable** installs that have not updated yet. This is a compatibility
   disposition to make, not an impossibility (§ above), and it has to be made
   explicitly. See *Old-client compatibility* below.

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
- **The build stamps that version into `shared/version.json`** before packaging,
  and commits nothing. The installed copy then reports the build it is, so the
  next check compares against the build rather than against the last release.
- **The app manifest records the exact source commit.** `sourceSha` alongside the
  version in `update-app-<version>.manifest.json`, so an installed main build is
  traceable to one commit rather than to a run number. This is an additive
  manifest field and a `scripts/build_bundle.py` change; it is **not** in this
  branch, because the manifest is a shipped artifact contract and 0.3.2 is in
  candidate qualification.
- **When the next stable release lands**, `v<next-patch>` outranks every
  `v<next-patch>-main.<n>` (SemVer rule 11 again), so a main-build install moves
  onto it without any special case.

### Old-client compatibility

Stated as behaviour, per audience:

- **Stable installs, any version.** Unaffected. `releases/latest` never returns a
  pre-release, so no main build is ever offered on the stable channel, and this
  is the property to re-check first if the design changes.
- **Beta installs, any version.** Offered the newest main build. That is the
  point, and it needs no client change.
- **Every install's companion lookup — the one real hazard.** A published client
  resolves `v<version>-updates` by scanning 20 recent releases. Two dispositions,
  and one has to be chosen before publishing starts:
  1. **Recommended.** Land the by-tag companion lookup —
     `GET /repos/<repo>/releases/tags/<tag>`, with the current list scan as the
     fallback — ship it in a release, and only then enable main-build publishing.
     It is a small change to `_updates_release` in `server/updates/service.py`
     and removes the bound for every client that has it. It is **not** in this
     branch: it changes the updater's request pattern, and the release owner owns
     that risk during a candidate.
  2. **Interim, if publishing must start sooner.** Keep the live pre-release
     count inside the window by publishing on demand
     (`workflow_dispatch`) rather than on every push, so the companion of the
     current release stays on the first page. This is a rate limit on the
     channel, not on the tags: nothing is deleted or moved.

The 20-entry window is a real bound with a real fix; it is not a reason to
mutate a published tag, and no disposition here does.
