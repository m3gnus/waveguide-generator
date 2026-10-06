# Releasing Waveguide Generator

How versions are numbered and how a release is built and published. This is for
maintainers; users only need the [README](../README.md).

Versions are `MAJOR.MINOR.PATCH`, optionally with a release pre-release label —
`0.3.2-rc.1`. The application is still being built,
so it stays **pre-1.0**: the line is `0.MINOR.PATCH`, a minor for features and a
patch for fixes, and 1.0.0 is reserved for the first release that is no longer a
beta. The original application is a separate, retired 1.x line, and nothing
resolves this project by version, so the two never collide.

The version lives in `shared/version.json` — `/health` and the FastAPI metadata
read it at runtime, and Vite injects it into the SPA as `__WG2_VERSION__` at
build time. npm keeps two further copies in `frontend/package.json` and
`frontend/package-lock.json`, and the macOS app has two bundle-version keys, so
move all of them with one command rather than by hand:

```bash
python scripts/bump_version.py patch
```

`major` and `minor` do the obvious thing, `rc`, `beta` and `alpha` produce a
release candidate (below), `--set X.Y.Z` sets an exact version, and `--check`
proves every copy agrees. CI's drift job runs `--check`, and so does
`server/tests/test_version_consistency.py`.

Releases are two deliberate commands, and **the tag is created last, by CI**:

```bash
../hornlab-policy/release.sh waveguide-generator patch   # bump, commit, push main, wait for CI
../hornlab-policy/release.sh waveguide-generator publish # build, validate, tag, publish
```

Phase 1 stops once CI is green on the exact release commit; nothing is tagged
and no version is spent. Phase 2 dispatches
`.github/workflows/release.yml` against that **commit**, which **refuses to run
when `shared/version.json` does not move forward past every published tag, the
commit is not reachable from `origin/main`, or `ci.yml`, which the workflow
runs on that exact commit before building anything, fails** — a build that
misreports itself is worse than a failed release.

Its SPA job attaches `update-spa-<version>.tar.gz`; the macOS job
builds the canonical platform-neutral app ZIP and manifest, the macOS runtime ZIP,
and `Waveguide.Generator-<version>-macos-arm64.dmg`; the Windows job checks its
independently built app ZIP against the canonical one by content digest and builds
the Windows runtime ZIP, `Waveguide.Generator-<version>-windows-x86_64-setup.exe`,
and the portable `Waveguide.Generator-<version>-windows-x86_64.zip`.

A final publisher job validates the exact inventory and every checksum, then
splits it across **two releases**. The user-facing `v<version>` carries exactly
two files, the macOS disk image and the Windows installer, so the page a person
lands on is one row per platform. Everything else — the update layers, the SPA
archive, and the portable Windows ZIP — goes to a companion release tagged
`v<version>-updates`, flagged as a pre-release so it is never "Latest" and the
stable update channel never sees it. It is a separate *release* rather than a
separate repository deliberately: the assets stay in this repository, so the
updater's `trusted_asset_url` keeps rejecting anything served from elsewhere.
The in-app updater reads the companion and falls back to the release's own
assets, so where a layer sits is not something an installed client depends on.

Checksums are computed and verified for every asset; only the SPA archive's
`.sha256` is published, because GitHub serves a per-asset digest the updater
reads and `scripts/fetch_spa.py` fetches by URL without touching the releases
API. The publisher uploads to a draft, **then** creates the annotated
tag and makes the release public.

That order is deliberate. The tag used to be pushed by hand and was what
triggered the build, so the version was committed to before anything was known
to build. When a cross-platform gate failed twice on 2026-08-26, `v0.2.5` and
`v0.2.6` became permanently dead tags with no assets and the work shipped as
`v0.2.7`. A draft release does not create a git ref, so a failure now costs
nothing and the same version is retried.

Installer filenames use dots because those are the names GitHub serves; the
installed application and extracted Windows folder retain spaces. The prebuilt
SPA means installing Waveguide Generator needs no Node runtime.

Build metadata (`+build`) is deliberately unsupported: the tag is built as `v` +
this string, and nothing in this project can compare it.

## Release candidates

A pre-release rehearses the release itself — packaging, the installers, and the
in-app update path — against a version number that has not been spent. Publish
one when the change touches packaging, the app layer, the updater, or anything
cross-platform; a pure solver or UI change does not need one
(the workspace `GIT-WORKFLOW.md` §4). `rc`, `beta` and `alpha` are levels like any other, so
the sequence is the same two commands each time:

```bash
../hornlab-policy/release.sh waveguide-generator rc       # 0.3.1      -> 0.3.2-rc.1
../hornlab-policy/release.sh waveguide-generator publish
../hornlab-policy/release.sh waveguide-generator rc       # 0.3.2-rc.1 -> 0.3.2-rc.2
../hornlab-policy/release.sh waveguide-generator publish
../hornlab-policy/release.sh waveguide-generator patch    # 0.3.2-rc.2 -> 0.3.2   (the release)
../hornlab-policy/release.sh waveguide-generator publish
```

**`patch` on a candidate removes the label and keeps the core numbers.** That is
the row to get right: `0.3.2-rc.2` finalises to `0.3.2`, the version its
candidates were candidates *for*. Incrementing to `0.3.3` would strand `0.3.2`
forever, because a published tag is immutable and `0.3.2` sorts above its own
RCs, so nothing could ever fill the hole. `minor` and `major` drop the label the
same way when the core already names the version being finalised —
`0.4.0-rc.1` + `minor` = `0.4.0` — and otherwise bump as usual, so
`0.3.2-rc.1` + `minor` = `0.4.0`.

These are [node-semver's `inc`
rules](https://github.com/npm/node-semver#functions), which npm's `version`
command uses. [`cargo release`](https://github.com/crate-ci/cargo-release)
spells the candidate levels identically (`1.0.0` → `1.0.1-rc.1`, `1.0.1-rc.1` →
`1.0.1-rc.2`) and calls the finalising step `release`; it differs on `minor` and
`major` from a candidate, where it would give `0.5.0` and strand `0.4.0`. We
follow node-semver, for the reason above.

`alpha` → `beta` → `rc` promotes within the same version (`0.3.2-beta.2` →
`0.3.2-rc.1`); going back down is refused, because
[SemVer](https://semver.org/spec/v2.0.0.html) rule 11 compares alphanumeric
identifiers in ASCII order and the result would not move forward. No ladder is
written down anywhere — `bump_version.py` compares the two versions with the
same comparator `release.yml` orders published tags with.

**A candidate never reaches the stable channel**, and two independent things
enforce that. `release.yml` derives GitHub's pre-release flag from the declared
version (`shared/release_assets.is_prerelease`) and sets it on the release, and
GitHub defines `/releases/latest` as the newest release *without* that flag —
which is the only endpoint the stable channel reads. Independently,
`server/updates/service.py` refuses any tag but a plain triple on the stable
channel (`_is_offerable_release_tag(..., allow_prerelease=False)`), so a
pre-release arriving there anyway is still not offered. See
[`docs/reference/UPDATE-CHANNELS.md`](reference/UPDATE-CHANNELS.md).

## A candidate is not a build stamp

SemVer gives both the same slot, and they are different things:

| | Example | What it is | Published by |
|---|---|---|---|
| Release pre-release | `0.3.2-rc.1` | A release. A candidate for `0.3.2`. | `release.yml`, flagged as a pre-release |
| Build stamp | `0.4.0-main.7` | A build of `main`, not a release at all | its own workflow — see UPDATE-CHANNELS.md |

**The identifier is what tells them apart**, and it is the only thing that can:
a label beginning `alpha`, `beta` or `rc` is a release pre-release, and every
other label is a build stamp. `shared/release_assets.release_prerelease` owns
that whitelist, and every place both can appear asks it:

- `scripts/bump_version.py --check` — which `ci.yml`'s drift job runs —
  accepts a stable version or a candidate, and refuses a build stamp. A
  release tree may not carry one. `--build-stamp` is the build's own route to
  the same verification.
- `scripts/bump_version.py <level>` refuses to compute anything from a build
  stamp: there is no next patch after `0.4.0-main.7`.
- `release.yml`'s guard refuses a build stamp outright. It publishes releases;
  a build of `main` is published by its own workflow.

A whitelist rather than a pattern, because that is the safe direction: a
build-stamp prefix nobody has thought of yet is refused by the release path the
day it is invented, where the reverse would publish it as a release.
