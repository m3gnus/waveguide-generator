# Update signing runbook

Every release carries `SHA256SUMS` and `SHA256SUMS.sig` beside its installers.
`SHA256SUMS` is `sha256sum` output for the three installers with one leading comment
line, `# version <tag>`. `SHA256SUMS.sig` is the raw 64-byte Ed25519 signature over
the manifest bytes. The application verifies the signature with a public key compiled
in at `server/updates/manifest.py` (`UPDATE_SIGNING_PUBLIC_KEYS_HEX`), then that the
manifest version equals the release tag, then the installer's SHA-256. The key is never
fetched.

What this protects: a swapped or corrupted release asset or redirect, and an asset
uploaded by someone without the signing secret. What it does not protect: a compromised
workflow, repository write access that can edit `release.yml`, or theft of the secret.
The Environment below narrows that: a workflow change alone cannot sign, because the
signing job waits for a reviewer.

Step 3 is done: the real public key is embedded (2026-10-01). With the all-zero
placeholder key, verification refuses everything and the signing job refuses to sign.

## One-time setup

1. Generate the keypair on your own machine, not on a CI runner:

   ```
   openssl genpkey -algorithm ed25519 -out update-signing.pem
   openssl pkey -in update-signing.pem -pubout -outform DER | tail -c 32 | xxd -p -c 64
   ```

   The second command prints the 64-hex-character public key. (Python alternative:
   `Ed25519PrivateKey.generate()` from `cryptography`, `private_bytes(PEM, PKCS8,
   NoEncryption())`, and `public_key().public_bytes(Raw, Raw).hex()`.) macOS ships
   LibreSSL, whose `openssl` may lack Ed25519: use Homebrew `openssl@3` or the Python route.
2. Store the private key.
   - Repository Settings, Environments, New environment, named exactly `update-signing`.
   - Add yourself under Required reviewers. Leave branch restrictions to your taste.
   - Add an Environment secret named `UPDATE_SIGNING_KEY` whose value is the whole PEM
     file, including the `-----BEGIN/END PRIVATE KEY-----` lines.
   - Keep an offline backup of `update-signing.pem` (password manager or an encrypted
     drive), then delete the working copy. GitHub will never show the secret again, and
     losing the key means a rotation with no old-key signature (see below).
3. Paste the public key into `UPDATE_SIGNING_PUBLIC_KEY_HEX` in
   `server/updates/manifest.py` and land it. The signing job compares the secret's
   public key with this active constant, requires it in `UPDATE_SIGNING_PUBLIC_KEYS_HEX`,
   and fails on a mismatch, so a wrong paste or a wrong secret stops the release
   instead of shipping a manifest no client can verify.

## Each release

The workflow's `sign` job runs after all three builds and waits for your approval in the
Actions run. It fails immediately, with a message naming this file, if `UPDATE_SIGNING_KEY`
is absent from the Environment. It verifies its own output with the same code the
application uses before uploading. `publish` then re-verifies against the compiled
accepted keys and the staged installers, and attaches both files to the release.

Check a download by hand: `sha256sum -c --ignore-missing SHA256SUMS` (the version
comment is ignored).

## Rotation

Rotation means shipping a new key in a release signed by the old one.

1. Generate a new keypair (step 1). Do not change the Environment secret yet.
2. Release N carries both old and new keys in `UPDATE_SIGNING_PUBLIC_KEYS_HEX`.
   Keep `UPDATE_SIGNING_PUBLIC_KEY_HEX` and the secret on the old key so installed
   clients can verify N and acquire the new compiled key. Both workflow verification
   steps use the accepted tuple; every new signature still requires the active key.
3. In a later release, switch `UPDATE_SIGNING_PUBLIC_KEY_HEX` and the
   `UPDATE_SIGNING_KEY` secret together to the new key, keeping both accepted keys.
   Keep the new offline backup and retire the old private key. A subsequent release
   can remove the old public key from the accepted tuple.

Stable clients with bridge discovery first check `/releases/latest`. If it is
ineligible, they search at most five pages of 100 releases for the highest verified
stable version at or above the installed version. Keep an old-signed bridge reachable
within that window for clients that miss N. This finite search cannot guarantee
recovery for indefinitely offline clients, and clients predating bridge discovery
can still require a manual installation if latest is signed by an unknown key.

If the key is lost or stolen, there is no old-key signature to chain from. Generate a
new key, ship it in a release, and tell users to install that release by hand once.
