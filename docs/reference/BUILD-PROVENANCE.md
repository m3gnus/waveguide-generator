# Build provenance

Waveguide Generator's free provenance option is a GitHub Actions artifact
attestation. A public repository can create these attestations on GitHub's
current plans. GitHub's `actions/attest` action obtains a short-lived Sigstore
certificate through the Actions OIDC token; it does not require a purchased
certificate or a repository secret.

The RC and manual Beta build jobs attest their own files immediately after the
build and before uploading them. One attestation may contain several subjects,
so the installer is bound to the update archive and its manifest produced by
the same job. A later job only downloads those already-attested files; it never
creates a new build claim for an old download. The CPU qualification steps stay
after the upload as they are today. An attestation describes origin and build
provenance; it is not a CPU qualification result.

The attestation action is pinned to the immutable `actions/attest` v4.2.1
commit `508db95dd578ae2727ebd6217d5ba78e4fbda05d`. The action's binary
attestation needs these job permissions:

```yaml
permissions:
  contents: read
  id-token: write
  attestations: write
```

`artifact-metadata: write` is for linked registry storage records and is not
needed for these file subjects. No workflow permission or repository setting is
activated by this reference document.

Before building, each RC job verifies that its checked-out `HEAD` equals
`github.sha`, the commit selected by the workflow dispatch ref. A candidate
input that resolves to a different commit fails before any artifact is built or
attested. The inactive Beta proposal applies the same check in its identity job.

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

The build manifest is a subject of the macOS job's attestation. For a manual
inspection of its source and content identity:

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
candidate. The stamped Beta app manifest must report that candidate in
`sourceCommit`, a distinct local `commit` created by the build stamp, and a
64-character lowercase `treeSha256`. `--source-digest` checks the workflow's
dispatch source; the manifest fields independently bind the candidate and its
stamped tree. Those values are evidence about the stamped tree; they do not
make the updater enforce signatures. The updater continues to use its existing
checksum and trusted-release checks, and macOS Gatekeeper and Windows
SmartScreen warnings remain until paid platform signing is separately chosen.
