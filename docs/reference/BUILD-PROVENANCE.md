# Build provenance

Waveguide Generator's free provenance option is a GitHub Actions artifact
attestation. A public repository can create these attestations on GitHub's
current plans. GitHub's `actions/attest` action obtains a short-lived Sigstore
certificate through the Actions OIDC token; it does not require a purchased
certificate or a repository secret.

The RC build, the release workflow and the manual Beta proposal (when
activated) attest their own files immediately after the build and before
uploading them. One attestation may contain several subjects, so an installer
is bound to the update archive and manifest produced by the same job. A later
job only downloads those already-attested files; it never creates a new build
claim for an old download. The release's publish job has no attestation
permission and never attests: it publishes files the build jobs already
attested. An attestation describes origin and build provenance; it is not a
CPU qualification result, and the RC's CPU qualification steps stay after the
upload as before.

| Workflow | Job | Subjects |
|---|---|---|
| `rc-build.yml` | `spa` | SPA archive |
| | `macos-bundle` | DMG, update-app ZIP, update-app manifest |
| | `windows-bundle` | setup EXE |
| | `linux-bundle` | tarball |
| `release.yml` | `spa` | SPA archive |
| | `macos-bundle` | DMG, update-app ZIP, update-app manifest, macOS runtime ZIP |
| | `windows-bundle` | setup EXE, portable ZIP, Windows runtime ZIP |
| | `linux-bundle` | tarball, Linux runtime ZIP |

Payloads are subjects; `.sha256` sidecars are not, so verify the file itself.
The Windows jobs attest after the setup-driven installer gates, so a setup that
failed them never carries a build claim.

The attestation action is pinned to the immutable `actions/attest` v4.2.1
commit `508db95dd578ae2727ebd6217d5ba78e4fbda05d`. The action's binary
attestation needs these job permissions, and only the four build jobs of each
workflow receive them:

```yaml
permissions:
  contents: read
  id-token: write
  attestations: write
```

A job-level block replaces the workflow-level one rather than adding to it, so
`release.yml`'s build jobs also restate its workflow-level `actions: read`,
which the SPA job's CI check needs. The publish job keeps `contents: write`
alone. `artifact-metadata: write` is for linked registry storage records and is
not needed for these file subjects.

Releases and RCs built before this workflow change carry no attestation, and
verifying one fails.

## The source guard, and what it means for dispatching

The attestation records `github.sha` — the tip of the ref a run was dispatched
on — as its source digest, not the `sha` input the jobs check out. So before
building, every build job verifies that its checked-out `HEAD` equals
`github.sha`, and a run whose input resolves to a different commit fails before
any artifact is built or attested. The inactive Beta proposal applies the same
check in its identity job.

- **RC of a fix branch:** dispatch on that branch, with its tip as the input:
  `gh workflow run rc-build.yml --ref BRANCH -f sha=BRANCH_TIP`. The earlier
  habit of `--ref main -f sha=BRANCH_TIP` now fails at the guard. The defaults
  (`main` and `main`) still build main's tip.
- **Release:** phase 2 of `release.sh` dispatches `release.yml` on `main` with
  the release commit, which passes while that commit is still main's tip. If
  `main` has moved on in between, the run fails before building or tagging
  anything, so no version is spent. To build it anyway, dispatch from a branch
  whose tip is the release commit; the release guard still requires the commit
  to be on `main`.

## Verify an RC installer

Run the command from the directory containing the downloaded installer. Set
`SOURCE` to the exact candidate commit supplied to the RC dispatch. The
workflow has already failed closed if the checked-out candidate and dispatch
source differ, so keep the `--source-digest` constraint: current GitHub CLI
versions then make the command fail when the attestation names another source
revision.

```bash
SOURCE=COMMIT_SHA
ASSET=Waveguide.Generator-VERSION-macos-arm64.dmg

gh attestation verify "$ASSET" \
  --repo m3gnus/waveguide-generator \
  --signer-workflow m3gnus/waveguide-generator/.github/workflows/rc-build.yml \
  --source-digest "$SOURCE"
```

Use the matching asset and the same command for the Windows setup executable
or Linux tarball. The SPA archive is attested by the SPA job and can be checked
with the same repository and signer workflow constraints. To inspect the
subjects and the signed provenance record, add `--format json`; the GitHub CLI
manual documents the result under
`.verificationResult.statement`.

The build manifest and canonical app archive are subjects of the macOS job's
attestation. Verify both subjects with the same repository, signer workflow and
source constraint before inspecting their fields:

```bash
set -euo pipefail
for ASSET in update-app-VERSION.zip update-app-VERSION.manifest.json; do
  gh attestation verify "$ASSET" \
    --repo m3gnus/waveguide-generator \
    --signer-workflow m3gnus/waveguide-generator/.github/workflows/rc-build.yml \
    --source-digest "$SOURCE" \
    --format json
done
```

For a manual inspection of the verified subjects' source and content identity:

```bash
jq '{commit, sourceCommit, treeSha256}' update-app-VERSION.manifest.json
unzip -p update-app-VERSION.zip APP-MANIFEST.json \
  | jq '{commit, sourceCommit, treeSha256}'
```

An RC manifest's `commit` is the resolved checkout of the dispatch input. A
manual Beta manifest also has `sourceCommit` for the candidate input, `commit`
for the fixed build-stamp commit, and `treeSha256` for the stamped app tree.
Compare those fields with the candidate and with the exact archive under
review. The attestation signature covers the manifest subject.

## Verify a published release

The user-facing release carries the three installers. The update-app ZIP and
manifest, the three runtime ZIPs, the SPA archive and the portable Windows ZIP
are on its `-updates` companion release. Every one of them is verified the same
way, with `release.yml` as the signer workflow and the release commit — the
commit the annotated tag points at — as the source digest:

```bash
TAG=vVERSION
SOURCE="$(git rev-parse "$TAG^{commit}")"   # in a clone with tags fetched
ASSET=Waveguide.Generator-VERSION-windows-x86_64-setup.exe

gh attestation verify "$ASSET" \
  --repo m3gnus/waveguide-generator \
  --signer-workflow m3gnus/waveguide-generator/.github/workflows/release.yml \
  --source-digest "$SOURCE"
```

## Verify a manual Beta after activation

The Beta proposal remains inert under `docs/reference/` until the release owner
authorizes moving it into `.github/workflows/`. If it is activated under the
name below, use that path as the signer workflow:

```bash
SOURCE=COMMIT_SHA
ASSET=Waveguide.Generator-VERSION-windows-x86_64-setup.exe

gh attestation verify "$ASSET" \
  --repo m3gnus/waveguide-generator \
  --signer-workflow m3gnus/waveguide-generator/.github/workflows/main-build.yml \
  --source-digest "$SOURCE"
```

The identity job has the same dispatch-source check before it resolves the
candidate. Verify the Beta app archive and manifest with the same command and
constraints, substituting `main-build.yml` for `rc-build.yml` in the signer
workflow path. The stamped Beta app manifest must report that candidate in
`sourceCommit`, a distinct local `commit` created by the build stamp, and a
64-character lowercase `treeSha256`. `--source-digest` checks the workflow's
dispatch source; the manifest fields independently bind the candidate and its
stamped tree. Those values are evidence about the stamped tree; they do not
make the updater enforce signatures. The updater continues to use its existing
checksum and trusted-release checks. macOS Gatekeeper and Windows SmartScreen
behavior remains unchanged; paid platform signing is a separate future choice,
and SmartScreen reputation is separately accumulated.
