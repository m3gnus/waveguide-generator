# macOS Gatekeeper, measured

Status: dated evidence. Measured 2026-09-02 on macOS 26.5.2 (build 25F84), Apple
silicon, against the published `Waveguide.Generator-0.3.0-macos-arm64.dmg` and
against synthetic bundles built to isolate one variable at a time.

Everything below was measured against artifacts that **actually carried
`com.apple.quarantine`**, verified with `xattr -p` before each assessment. An
un-quarantined artifact tells you nothing about this question, and assuming
otherwise is how the earlier wrong inference happened.

## The short version

| What | `spctl --assess --verbose=4 --type execute` | Runs? |
|---|---|---|
| Ad-hoc signed `.app` — what ships | `rejected` and **no `source` line at all** | yes, once quarantine is cleared |
| Unsigned `.command` script | `rejected`, `source=no usable signature` | yes, once approved |
| `.app` with the signature removed | `rejected`, `source=no usable signature` | **no** — SIGKILL, exit 137 |
| `.app` with only the linker's ad-hoc signature, bundle unsealed | assessment *errors*: `code has no resources but signature indicates they must be present` | yes |

The `source` line is what Gatekeeper attaches an "Open Anyway" exception to. The
ad-hoc bundle has none, so Privacy & Security lists nothing for it. **Ad-hoc
signing is worse than no signature for this path** — and no signature is not
available, because an unsigned arm64 executable does not run at all.

A script has no Mach-O to sign, so it is unsigned *and* runnable. That is the
whole basis for shipping `Install Waveguide Generator.command` inside the disk
image.

## Transcripts

### The shipped app, quarantined

Copied out of a freshly quarantined `Waveguide.Generator-0.3.0-macos-arm64.dmg`.
The mount propagates the flag: 8547 files inside the copy carried it.

```
$ xattr -l "Waveguide Generator.app"
com.apple.provenance:
com.apple.quarantine: 0281;00000000;;3E700882-A82D-4ECF-9C3B-DCD2756B49E4

$ spctl --assess --verbose=4 --type execute "Waveguide Generator.app"
Waveguide Generator.app: rejected
exit=3
```

There is no second line. Compare with the script below, which has one.

```
$ codesign -dvvv "Waveguide Generator.app"
Identifier=is.hornlab.waveguide-generator-v2
Format=app bundle with Mach-O thin (arm64)
CodeDirectory v=20400 size=314 flags=0x2(adhoc) hashes=3+3 location=embedded
Signature=adhoc
TeamIdentifier=not set
Sealed Resources version=2 rules=13 files=6863
```

`flags=0x2(adhoc)` with `TeamIdentifier=not set` is the state that leaves
Gatekeeper nothing to name in Privacy & Security.

### A quarantined script

```
$ xattr -p com.apple.quarantine "Install Waveguide Generator.command"
0081;6a988290;Safari;86260A6F-923D-468E-9962-E153A04E73DE

$ spctl --assess --verbose=4 --type execute "Install Waveguide Generator.command"
Install Waveguide Generator.command: rejected
source=no usable signature
exit=3
```

Assessed through the download context as well, for completeness:

```
$ spctl --assess --verbose=4 --type open --context kLSDownloadedFile "Install Waveguide Generator.command"
Install Waveguide Generator.command: rejected
source=Insufficient Context
exit=3
```

### Why "just ship it unsigned" is not available

Three synthetic bundles, identical but for their signature state, each
quarantined and then assessed:

```
########## Probe-adhoc.app          (codesign --force --deep --sign -)
Probe-adhoc.app: rejected
exit=3
Signature=adhoc
TeamIdentifier=not set

########## Probe-linkonly.app       (clang's linker signature, bundle not sealed)
Probe-linkonly.app: code has no resources but signature indicates they must be present
exit=1
CodeDirectory v=20400 size=382 flags=0x20002(adhoc,linker-signed)

########## Probe-stripped.app       (codesign --remove-signature)
Probe-stripped.app: rejected
source=no usable signature
exit=3
Probe-stripped.app: code object is not signed at all
```

Only the stripped one gets the `source` line, and only the stripped one cannot
run:

```
$ ./Probe-stripped.app/Contents/MacOS/Probe
exit=137          # SIGKILL from the kernel, on an unsigned arm64 binary

$ ./Probe-linkonly.app/Contents/MacOS/Probe
ran
exit=0
```

So the state Gatekeeper would offer an exception for is exactly the state Apple
silicon refuses to execute. Shipping unsigned is not a trade-off with a cost; it
is not an option. The middle case — keeping the linker's signature but not
sealing the bundle — makes the assessment *error out* rather than reject, which
is a less predictable state than the one we have, not a better one.

A shell script as the bundle's main executable was tried previously and is also
a dead end; see the docstring on `write_launcher_stub` in `scripts/build_bundle.py`.

### The block is real, not just an `spctl` opinion

Double-clicked equivalents, driven through LaunchServices with `open`:

```
$ open "Install Waveguide Generator.command"
_LSOpenURLsWithCompletionHandler() failed with error -128
exit=1
# the script did not run: its marker file was never written, and its
# com.apple.quarantine attribute was unchanged afterwards
```

`syspolicyd` logged the refusal for the script:

```
syspolicyd  [com.apple.syspolicy.exec] GK evaluateScanResult: 2, PST: (path: e92c141f6ee784cb),
            (team: (null)), (id: jl_AltRXm), (bundle_id: NOT_A_BUNDLE), 0, 0, 1, 0, 0, 0, 0
syspolicyd  [com.apple.syspolicy.exec] Fast Gatekeeper overrides are: inactive
syspolicyd  [com.apple.syspolicy.exec] Error Domain=GatekeeperPolicyScanError Code=-67018
            "Code did not match any currently allowed policy"
```

and for the app:

```
syspolicyd  [com.apple.syspolicy.exec] GK evaluateScanResult: 2, PST: (path: 10144c31b26fefbe),
            (team: (null)), (id: (null)), (bundle_id: (null)), 0, 0, 1, 0, 0, 0, 0
```

Note `(id: jl_AltRXm)` for the script against `(id: (null))` for the app: the
script is given a temporary signing identity — `securityd:gk temporarySigning
type=3` appears in the same log — and the app is not.

### What is NOT measured here

**Whether "Open Anyway" actually appears for the script, and whether clicking it
lets the script run.** That is a click in System Settings, and no command-line
tool observes it. The `source` line is the documented precondition, and it is
present for the script and absent for the app; that is a strong indication and
not a proof. `docs/validation/2026-09/README.md` carries the one-pass manual
test that settles it.

An earlier inference from a signature difference was already wrong once, so this
distinction is kept explicit rather than smoothed over.

### An `spctl` rejection is not a launch failure

Worth stating because it is easy to over-read the table above. After the
installer script clears the quarantine flag, the installed app still assesses as
`rejected` — Gatekeeper's *policy* opinion never changes — but it launches
normally, because Gatekeeper only enforces on quarantined items:

```
$ spctl --assess --verbose=4 --type execute ".../Waveguide Generator.app"
rejected
exit=3
$ ".../Waveguide Generator.app/Contents/MacOS/Waveguide Generator"
ran
exit=0
```

## What a Developer ID would cost

Declined on cost on 2026-08-27, restated here with numbers so the decision can be
revisited rather than re-argued.

- **US$99 per year**, Apple Developer Program, renewed annually. Requires a
  legal identity (individual or organization); an organization enrollment needs a
  D-U-N-S number.
- **Workflow changes**, all inside `.github/workflows/release.yml` and
  `scripts/build_bundle.py`:
  - Store the Developer ID Application certificate and its password as repository
    secrets, plus an App Store Connect API key (issuer id, key id, `.p8`) for
    `notarytool`. Five secrets, one of which expires and has to be rotated.
  - Import the certificate into a temporary keychain on the macOS runner.
  - Replace `codesign --force --deep --sign -` with a Developer ID signature,
    `--options runtime` (hardened runtime) and `--timestamp`. The hardened
    runtime is the part most likely to need work: the bundle ships a relocated
    CPython and loads native extensions, so it may need
    `com.apple.security.cs.disable-library-validation` and
    `com.apple.security.cs.allow-unsigned-executable-memory` entitlements. This
    is the only step with real technical risk; the rest is plumbing.
  - `xcrun notarytool submit --wait` on the `.dmg`, then `xcrun stapler staple`
    it. Adds a few minutes to the macOS release job and makes it depend on an
    Apple service being up.
- **What it retires:** the `xattr` instruction in the README, the release notes
  and `READ ME FIRST.txt`; the installer script and this whole line of
  investigation; the first-launch refusal itself. The user's install becomes drag
  to Applications, double-click, done. It does nothing for Windows/SmartScreen,
  which needs a separate paid Authenticode certificate.
