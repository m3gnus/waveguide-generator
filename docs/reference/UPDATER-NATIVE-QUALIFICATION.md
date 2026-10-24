# Full-installer native qualification

Run this qualification on real installed release candidates on Windows x86-64,
macOS arm64, and the supported Linux x86-64 target. Record the exact OS, machine
architecture, running versions, app commits, artifact hashes, commands, helper logs,
and observed results. A unit fixture, source checkout, successful download, or a
file comparison alone does not qualify the native updater.

Qualify an actual supported installed release → candidate through the signed
full-installer updater, following [the current client contract](INSTALLER-CLIENT-CONTRACT.md)
and [handoff contract](INSTALLER-HANDOFF-CONTRACT.md). Also qualify a manual
full-installer upgrade for releases without a supported in-app update path;
0.3.3-rc and earlier require downloading the installer. The layer-update receiver
was removed in 0.3.6, so the old 0.3.2 → bridge B sequence is no longer a
qualification requirement. Keep a separate evidence directory and ledger for each
platform and transition. Obtain owner-approved signed candidates; this procedure
neither builds nor publishes them.

## Prepare the controls

Use a private qualification account or isolated test installation and private data
directories. Keep evidence and golden extraction outside every directory being
observed. Record installation location and kind before starting. Do not use a
production profile for destructive failure scenarios.

Populate data with representative projects, `cadlink.db`, and `simulations.db`.
Close WG and stop its server, jobs, CAD delivery, and any installer/helper writers
before each snapshot or verification. Record the server/helper PIDs you stopped.
The tool checks the data directory's `locks/server.pid` and every explicit
`--stopped-pid`; the `--stopped` flag is an owner attestation covering writers that
have no discoverable PID record. The tool never kills a process.

Name external controls explicitly, including the private Fusion AddIns directory,
a developer-owned WGLink source tree, and another installation's owned WGLink/data
directories. A developer symlink in the AddIns control is recorded as a literal
target and never followed; nominate the developer source directory separately to
prove its content was preserved too. Use disjoint ordinary directories as control
roots. Symlink or Windows junction roots are refused.

For removal qualification, nominate one `.py` module and one runtime package that
are present in the installed release and deliberately absent from the candidate.
Use an approved qualification candidate that actually contains this removal, and
record its exact commit. A made-up absent path cannot satisfy the proof. An unchanged production
package that has no removal fixture leaves this scenario owed.

## Snapshot before each transition

`scripts/qualify_installer_artifact.py` uses the standard library and the compiled
release-signing public key(s). Run it from the checkout being qualified, using an
independent Python interpreter; it does not start the packaged runtime. Save the
checkout commit and script hash in the owner ledger. All examples below use
placeholders for owner-selected paths and versions. Quote paths containing spaces.

```text
python scripts/qualify_installer_artifact.py snapshot --platform <platform> --data-dir <private-data> --installed-root <installed-bundle> --external fusion-addins=<private-addins-dir> --external developer-wglink=<developer-source-dir> --external other-install=<other-install-control-dir> --stopped-pid <stopped-server-pid> --stopped --out <evidence-dir>/before.json
```

Platform names are `macos-arm64`, `windows-x86_64`, and `linux-x86_64`.
`--installed-root` is the `.app` directory on macOS and the bundle directory
containing `app/` and `runtime/` on Windows/Linux. The snapshot hashes **all** data
entries except the top-level `update-install/` entry. It hashes named external
controls without exclusions, and the complete pre-upgrade installed app/runtime.
Empty directories, file sizes and SHA-256 hashes, and literal symlink targets are
recorded. Special files, FIFO/device inputs, junctions, escaping payload symlinks,
and changes observed during hashing fail the check. No SQLite checkpoint, journal
cleanup, or data migration is performed by the tool.

If a stopped app changes an ordinary lock or cache outside `update-install/`, the
data comparison reports it. Investigate and record the discrepancy; do not add a
new exclusion or edit the saved snapshot to make the qualification pass.

## Bind an independent golden payload

Retain the exact full installer, its `SHA256SUMS`, and raw 64-byte
`SHA256SUMS.sig` from the same release. The tool verifies the signature with compiled
trust, the exact `v<version>` line, the full-installer filename/platform, and the
installer's SHA-256. It provides no fetched-key or command-line key override.

Independently extract the signed candidate into a fresh golden directory using
the native owner's trusted extraction method. Never copy the upgraded installation
as the golden reference, reuse its files through hardlinks, modify the golden tree,
or let the app run from that directory. Record the extraction tool/version,
command, exact artifact hash, source/destination, and extraction time in the ledger.

- Linux: nominate the extracted `waveguide-generator/` directory. The tool also
  streams the tar archive, validates every member's path/type, and compares its
  bundle entries directly to the golden tree. It never calls `extractall` or writes
  tar members. Traversal, duplicate names, hardlinks/devices, escaping links, and
  children under non-directories are refused.
- macOS: nominate the independently copied `.app` payload from the exact DMG.
  Mounting, copying, unmounting, and quarantine inspection are separate owner
  actions. The stdlib tool cannot decode a DMG; its artifact-to-golden binding is
  explicitly **owner-attested**.
- Windows: nominate a trusted independent extraction of the exact Inno setup
  app/runtime payload. A portable ZIP
  from another asset is not a reference for the setup executable. The stdlib tool
  cannot decode an Inno executable; this binding is explicitly **owner-attested**.
  Generated native files such as the uninstaller are outside this tool's explicit
  app/runtime scope and must be checked in the native ledger.

The complete `app/` and `runtime/` directories are compared, including manifests;
there are no exclusions within those directories. Linux additionally binds the
entire golden bundle, including its launcher, to the signed tar payload. App manifests must carry the expected version,
full commit and tree digest, and match the runtime manifest's ID and platform.
The report preserves both manifests and includes its own complete path/hash/link
table. This independent table covers runtime files even when the runtime manifest
does not enumerate them. It does not recompute the app builder's Git-mode-based
`treeSha256`; signed artifact binding plus byte-for-byte manifest/tree equality is
the evidence supplied here. POSIX executable permissions and native ownership,
ACLs, signatures, quarantine, and registry changes require the native checks below.

## Execute and verify each actual update

For the in-app path, start the actual supported installation and use its real
update UI/API. Record the selected release, eligibility, download, approval,
helper start, shutdown, and restart sequence. For the manual path, download and
run the full installer at the existing installation destination and record its
native installation and restart sequence. After it finishes, inspect the running
version/commit and readiness on that host. Preserve the raw outcome journal and helper log before an
app startup consumes the journal. Then stop all writers again.

```text
python scripts/qualify_installer_artifact.py verify --snapshot <evidence-dir>/before.json --artifact <exact-full-installer> --manifest <release-dir>/SHA256SUMS --signature <release-dir>/SHA256SUMS.sig --tag <v-target-version> --from-version <actual-before-version> --platform <platform> --candidate-root <golden-bundle> --installed-root <same-installed-bundle> --extraction-method <recorded-native-extraction-method> --outcome <preserved-outcome.json> --stopped --out <evidence-dir>/after.json
```

For **removal qualification** on either path, add:

```text
--require-removals --removed-app <relative-module.py> --removed-runtime <relative-runtime-package-dir>
```

Paths are relative to `app/` and `runtime/`, respectively, or those directories
under `Contents/Resources` on macOS. They must have existed with the correct type
in the pre-upgrade snapshot and be absent from both installed and golden trees.
Use repeatable flags to nominate multiple removals.

The outcome reader preserves the producer's raw `ok`/`installed`/`failed`/
`rollback_incomplete` result spelling and journal hash; it requires the correct
from/to versions, timestamp and log fields. It never consumes the journal or
infers that a previous payload remains from failure alone. No journal is reported
as missing evidence, even if the files match. A failed or incomplete-rollback
journal cannot be used as a successful upgrade claim.

Evidence output must be a new file outside observed roots. Both snapshot and
verification require stopped-app attestation. The report always says
`classification: artifact-verification-only` and `nativeQualificationComplete:
false`. It records the signed artifact, prior/target manifests, commit, runtime ID,
tree table, removal proof, data/control hashes, binding limitation, and outcome.
Use the owner ledger to reconcile these with actual native observations.

## Native scenario ledger

For every row record `passed`, `failed`, or `owed`, the exact candidate/artifact,
host, procedure, observation, and log/evidence paths. A file-comparison report or
unit test cannot mark these rows passed.

| Scenario | Required native evidence |
| --- | --- |
| Signed full-installer update | Supported installed release → candidate through the real update UI/API; one helper, successful outcome/restart and exact candidate equality. |
| Manual full-installer upgrade | Release without a supported in-app path → candidate at the existing destination; native install/restart succeeds and installed files equal the candidate. |
| Removal proof | Pre-existing app module and runtime package absent after full-installer replacement; exact candidate equality. |
| Data and WGLink | Data hashes unchanged; no new WGLink without consent; developer and other-install controls untouched during silent update. |
| Kill during copy | Kill the owned installer/helper mid-copy; restart runs the previous version; journal says failed and recovery/backup state is observed. |
| Invalid download | Corrupt byte, truncation, wrong signature/version, and foreign origin are refused before restart approval latches. |
| Staging failures | Disk full, unwritable parent, translocated macOS app and portable Windows ZIP fail cleanly or remain notify-only; macOS has no fallback location. |
| macOS staged tamper | Tamper a staged file; `--update` rejects it without re-signing. |
| Native download trust | Quarantine/Mark-of-the-Web absent as intended; installed candidate starts without Gatekeeper/SmartScreen prompts; permissions/signatures/registry are correct. |
| Approval races | 1,000 concurrent job/approve attempts: jobs started before approval are interrupted or requeued; none starts after approval. |
| Work during restart latch | CAD ingest, preparation, live delivery return 409 `update_restart_pending`. |
| Duplicate launch/install | Double-click during helper execution respects native mutex/lock; two clicks/windows create one helper; launch works after install. |
| Spawn failure | Failed helper spawn releases the latch and work is admitted again. |
| Manual recovery | Reinstall previous stable over new version; it opens the same `cadlink.db` and `simulations.db`. |

Archive the owner ledger, untouched pre/post JSON, signed artifacts, golden
extraction attestation, raw journals, and logs. Report outstanding rows explicitly.
The release owner decides qualification from real platform evidence; this tool
does not certify, approve, install, or publish a release.
