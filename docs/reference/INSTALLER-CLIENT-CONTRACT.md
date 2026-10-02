# Signed full-installer client

`server.updates.installer_client.InstallerClient` checks releases and downloads a
platform installer. Restart approval, helper launch, installation and exit belong to
the handoff service. The legacy receiving files remain compatible with older clients.

## Status and actions

`get_status(force=False)` returns immediately. Startup, scheduled six-hour checks and
explicit refreshes run in background workers. A pending check sets `checking: true`;
network failures become `lastError`, leaving the server responsive. Stable requests
`releases/latest`. Beta reads `per_page=100`, follows only the repository's trusted
`Link` next page, stops on the first page with an eligible release, and selects its
highest SemVer. Five pages is the limit. HTTP ETags and bounded disk caches retain
API observations; signatures are reverified when loading cached eligibility.

Status schema version 2 includes `runningVersion`, `channel`, `availability`
(`unknown`, `incomplete`, `available`, `current`, `ahead`), `freshness`, `cached`,
`checking`, `checkedAt`, `nextCheckAt`, `lastError`, `release`, `checkout`, `action`,
`canInstall`, `lastOutcome`, and download progress. Download progress is
`installState` (`idle`, `downloading`, `verifying`, `ready`, `failed`),
`downloadedBytes`, `totalBytes`, `activeVersion`, `error`.

Release fields are `version`, `tag`, `url`, `publishedAt`, `notes`, `assetsReady`,
and `installer: {name, url, size, sha256}`. API asset `size` is the download size.
Action is `{kind: "full_installer", tag, version, name, size}` or null. Installation
fields are `{kind, installRoot, updateSupported, reason}`. `installRoot` is for the
server's exact-destination handoff, and does not need to appear in the dialog.

All three installer assets, their checksums, `SHA256SUMS` and its raw 64-byte
Ed25519 signature must be present. The signed manifest tag must equal the release
tag. Only canonical project SemVer tags are accepted; companion tags, build stamps,
non-canonical numeric identifiers and build-metadata tags are refused. Stable refuses
prereleases. Trust keys are compiled in, never read from releases or caches.

## Download and handoff seam

`request_download(on_verified=None)` starts one download only on explicit request.
The callback receives a frozen `VerifiedInstaller(tag, version, path, sha256, size,
install_root, platform)` exactly once after verification. It must call
`verified_installer()` immediately before publishing the helper request. That method
rechecks the signature, version, file size, checksum and current installation
destination, and refuses cancellation or shutdown. Callback errors become failed
download status; the callback must release any restart approval it acquired.

The downloader uses the repository-bound release URL and the existing GitHub CDN
redirect checks, checks free space with a 64 MiB reserve, enforces both API size and
Content-Length when present, verifies the checksum, and atomically publishes the
finished download. Downloaded installers stay in `update-install/<version>/`.

`cancel_download(reason)` invalidates the candidate and suppresses handoff. Download
streaming stops at the next bounded network read. An ongoing hash may finish; the final
candidate/cancellation check refuses publication. `reset_download()` clears finished progress to idle,
and refuses while a worker remains active. Channel changes during downloads are
refused. Generations prevent an older channel's checker from publishing over a newer
selection. `close()` cancels future work without waiting indefinitely for network I/O.

The constructor accepts injectable `fetcher(url, etag)`, trusted streaming `opener`,
clock, installation probe and disk-space reader for unit tests. Production uses the
existing redirect-checked transport. `ReleaseResponse` has `payload`, `etag`, `link`,
`not_modified`. `platform_name` is a release asset platform identifier
(`macos-arm64`, `windows-x86_64`, `linux-x86_64`); omission detects the running host.

## Destinations and outcomes

Windows requires its exact per-user registered installer destination; portable and
unregistered copies notify only. macOS requires the WG bundle identity, a writable
parent, and a path outside App Translocation. Linux requires the supported architecture,
valid matching app/runtime manifests and the exact `waveguide-generator` destination
basename. Source and unsupported installations notify only.

The next client instance consumes a bounded `update-install/outcome.json` once and
retains it in status. Fields are `{from, to, result, when, log, backupPath?, reason?,
previousKept?}`. Result is `installed`, `failed`, or `rollback_incomplete`.
`previousKept` is only true for an explicit `failed` record that reports it; rollback
incomplete always preserves the recovery location and never claims the previous
installation survived. Malformed records remain on disk for diagnosis.

## Compiled key rotation

`UPDATE_SIGNING_PUBLIC_KEYS_HEX` lists the compiled accepted keys.
`UPDATE_SIGNING_PUBLIC_KEY_HEX` remains the active signing key constant for existing
release tooling. A transition release carries both old and new public keys and is
signed with the old secret, allowing already installed clients to accept it. A later
release switches signing to the new secret; a subsequent release can remove the old
public key. The signing workflow's active-key equality guard needs an explicit
transition amendment before an old-signed rotation release is published. Supporting
multiple verifier keys alone does not authorize or configure that workflow change.
