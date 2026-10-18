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

Version preparation and publication are deliberate phases, with a qualified
candidate build between them. **The tag is created last, by CI**:

```bash
../hornlab-policy/release.sh waveguide-generator patch
# After phase 1 has pushed the versioned commit to main:
gh workflow run rc-build.yml --ref main -f sha=<full-release-sha> -f sign_candidate=true
# Approve update-signing, wait for that exact run to succeed, then promote it:
../hornlab-policy/release.sh waveguide-generator publish --source-run-id <qualified-run-id>
```

Phase 1 prepares the version; nothing is tagged
and no version is spent. `rc-build.yml` is the only builder: it runs `ci.yml` on
the exact source commit, builds the SPA and platform bundles, checks app-layer
identity, and runs installed BEAT CPU and Quit gates on all three platforms
plus Windows setup gates. Its protected signing job runs only after all these
jobs succeed. Stable, alpha, beta and RC versions use this same route; build
stamps remain refused. **Every release, including a hotfix, must be built from
`main` through rc-build's signed route** (`sign_candidate=true`). An unsigned
branch rehearsal cannot be promoted; there is no branch-release override.

Phase 2 requires a separate approval of the existing protected `update-signing`
environment on the publish job. Signing approval for a platform rehearsal does
not authorize publication. `release.sh` dispatches `.github/workflows/release.yml`
with the full source SHA and that **explicit qualified run id**. It builds and signs nothing. It refuses a
version that does not move forward past published tags or a commit outside
`origin/main`, and requires the dispatch source to equal the release SHA on
`main`. It checks the source run through GitHub's API: the same repository,
`rc-build.yml`, manual dispatch on `main`, matching SHA, completed success,
and successful platform builds and protected signing job.

The publisher downloads the source run's complete signing inputs and signed
candidate, validates every checksum, version, source identity and runtime
identity, and verifies the unchanged Ed25519 signature against compiled keys.
It verifies every payload's GitHub attestation with repository, RC workflow,
source SHA and `main` constraints, and requires its verified invocation to
match the selected run **and attempt**. Missing/expired artifacts, missing
attestations and mixed attempts fail closed. If a candidate run is re-run, its
latest attempt must be a **full re-run** (GitHub's **Re-run all jobs**).
**Re-run failed jobs** leaves attestations from older attempts and is refused,
even if GitHub reports success. The run is checked again before staging, so a
concurrent re-run is refused. Promotion never rebuilds a missing artifact.
Promotions are serialized across source commits. A retry may reuse a published
companion only if its source tag, exact inventory, sizes and GitHub SHA-256
digests match the staged candidate. It is then left untouched; a differing
companion or draft inventory is refused before publication. An orphan companion
tag is also refused rather than reused or moved automatically.

The policy repository owns `release.sh`, the only supported publication entry
point. Land its companion promotion change (including `--source-run-id` and the
promotion capability check) before using this application's promotion workflow.
Do not bypass its guards with a raw workflow dispatch.

The five artifacts promotion consumes (`release-spa`, the three
`rc-<platform>-sign-inputs`, and `rc-signed-candidate`) explicitly retain for
**90 days**. This matches [GitHub's default retention](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/remove-workflow-artifacts)
and the public-repository maximum, allowing normal qualification, approval and
retry windows without reducing the previous default. Missing or expired inputs
still fail closed; retention does not make deleted artifacts recoverable.

The SPA job builds `update-spa-<version>.tar.gz`. Each platform builds its
full installer; Windows also builds the portable ZIP. The canonical app archive,
identity manifest and runtime archives remain internal attested artifacts.
All published payloads come from the qualified run without byte changes.

The publisher validates the exact inventory and every checksum, then splits it
across **two releases**. The user-facing `v<version>` carries the three full
installers plus `SHA256SUMS` and `SHA256SUMS.sig`. The companion
`v<version>-updates` holds only the SPA archive, its checksum sidecar, and the
portable Windows ZIP. It is always a pre-release and never Latest. The signed
manifest covers the three installers and the portable Windows ZIP.
Layer update archives are no longer published.

**0.3.3-rc and earlier: download the installer.** Installed 0.3.4 and 0.3.5
clients continue to discover and verify signed full-installer updates.

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
nothing before the tag push. Recovery depends on how far publication got:

- **Before the companion is public:** fix the candidate and use a new signed run
  or a full re-run, then publish through `release.sh` with the selected run id.
- **After `v<version>-updates` is public:** only the same source run id and its
  original artifact bytes can finish that version. Builds are not
  byte-reproducible; a fresh run or a full re-run can change the archives even
  for the same SHA, and the public companion must match exactly. Keep that
  source commit at main's tip while retrying. Retry `release.sh ... publish
  --source-run-id <original-run-id>` without re-running rc-build. If that run's
  artifacts are expired, lost or unusable, prepare a **new version bump** and a
  new signed run. Do not replace the public companion or move its tag.
- **After the annotated release tag was pushed, but `gh release edit
  --draft=false` failed:** both `release.sh` and the workflow refuse the existing
  tag, so an ordinary retry cannot finish. With publication authorization,
  manually verify the existing remote tag is annotated and peels to the original
  release SHA, the user-facing release is still a draft with the correct
  pre-release flag, and both releases' exact inventories, sizes and SHA-256
  digests match the original qualified run. Verify `SHA256SUMS`, its Ed25519
  signature and payload attestations as described in
  [BUILD-PROVENANCE.md](reference/BUILD-PROVENANCE.md). Then finish only the
  visibility change with `gh release edit v<version> --draft=false`, and verify
  the public release and tag again. If the release is already public, verify it
  instead of repeating publication. Do not delete, recreate or move the tag.

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
the same preparation and promotion phases apply each time. Between each pair,
dispatch the signed RC build above on the new full SHA and record its successful
run id:

```bash
../hornlab-policy/release.sh waveguide-generator rc       # 0.3.1      -> 0.3.2-rc.1
../hornlab-policy/release.sh waveguide-generator publish --source-run-id <qualified-run-id>
../hornlab-policy/release.sh waveguide-generator rc       # 0.3.2-rc.1 -> 0.3.2-rc.2
../hornlab-policy/release.sh waveguide-generator publish --source-run-id <qualified-run-id>
../hornlab-policy/release.sh waveguide-generator patch    # 0.3.2-rc.2 -> 0.3.2   (the release)
../hornlab-policy/release.sh waveguide-generator publish --source-run-id <qualified-run-id>
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
`server/updates/installer_checker.py` refuses any tag but a plain triple on the stable
channel (`eligible_release(..., channel="stable")`), so a
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


## Signed candidate artifacts without publication

After authorized version preparation, dispatch `rc-build.yml` on `main` with
`sha` set to the exact full 40-character tip and `sign_candidate=true`.
The normal candidate build and installed gates run first. Extra artifacts retain
all three installers, the Windows portable archive, canonical app archive and
identity manifest, three runtime archives, SPA, and checksum sidecars.

The optional signing job uses the existing protected `update-signing`
Environment and its reviewer gate. It validates the complete inventory, source
identity, runtime closure and checksums before signing four unchanged subjects:
the three installers and the Windows portable archive, as in the published release
contract. The SPA, app and runtime archives are checked but not signed. The signature is
verified against the active compiled key before `rc-signed-candidate` is uploaded. The compact `rc-signed-manifest`
artifact also provides the signature, manifest and exact installer sizes/digests,
so a platform rehearsal can download only its own installer.
This workflow creates no tag or GitHub release and has no publisher job.
Only a successful signed run is eligible for promotion by `release.yml`.
The signatures authenticate updates; they do not provide Apple Developer ID
notarization or a Windows code-signing certificate.

For a private 0.3.5-to-candidate rehearsal, start the installed client with
`WG2_UPDATES_API_BASE=http://127.0.0.1:<port>`. Serve GitHub-shaped release metadata
and exact artifact bytes at that literal loopback origin. The primary release
metadata must expose exactly three installers plus `SHA256SUMS` and
`SHA256SUMS.sig`; the SPA archive and portable ZIP belong in separate companion metadata.
Actions artifact URLs are not accepted installer URLs. Keep signature, version,
checksum and same-origin verification enabled. This exercises a private update,
not production channel discovery. Publication requires its own protected-environment
approval.

Promotion permissions: the release publish job retains `contents: write` and
adds only `actions: read` to read the selected run, jobs and artifacts. The
workflow-level `actions: read` already existed. The removed release build and
sign jobs no longer need their former permissions or signing secret; RC job
permissions, Environment and secrets are unchanged. The publish job also uses
`update-signing` for publication approval. Its checkout does not persist
credentials; `GH_TOKEN` is scoped to API/attestation checks, the tag push and
making the draft public. Attestations cover the portable ZIP and runtime archives as well as the existing RC subjects.
