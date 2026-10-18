# WGLink packaging contract

Waveguide Generator's Windows setup offers WGLink as an explicit Fusion 360
integration task. The release build fetches the exact reviewed Git commit in
[`source.json`](source.json), verifies that Git resolved that commit, and puts
a deterministic source package inside the verified application layer. Setup
installs from that verified package without Git or network access and uses WG's
existing pinned Python, NumPy, SciPy, and hornlab-waveguide-mesher environment.
An interactive install preselects the task only when Fusion's AddIns directory
already exists; a silent install must opt in with `/TASKS="wglink"`.

The package contains the complete `fusion-addins/WGLink` source tree, its
resampler, the upstream AGPL-3.0-or-later `LICENSE`, and `provenance.json` with
the upstream repository, full commit, WGLink version, WG version, and SHA-256
of every member. `scripts/install_wglink.py` rechecks that inventory before any
Fusion registration is changed. Setup preserves an unmanaged copy, a
developer-marked copy or a symlink. The source-checkout installer can explicitly
replace an external copy with `--replace-wglink`.

Packaged WG's healthy-startup check is a separate path:
[`server/cadlink/fusion/addin_update.py`](../../server/cadlink/fusion/addin_update.py) installs
an absent copy where Fusion is installed, updates the copy owned by that WG
installation, and replaces an unmanaged hand-copied add-in. It uses the exact
revision in the verified bundled package, offline. It preserves developer-marked
copies, symlinks and copies owned by another WG installation. Activation waits
until Fusion is closed; WG reports the pending change and can finish it when
Fusion closes while WG is running, or at WG's next start. A displaced managed
copy is retained in WG's data folder for rollback; an unmanaged copy is not.
WG reads Fusion's registration list to report duplicate registrations, another
registered location or a missing Run on Startup setting, but does not edit it.
Windows Setup reports its install outcome and writes it to the setup log.
See the [CAD Link guide](../../docs/CAD-LINK-GUIDE.md) for the user workflow.

WGLink can cold-start a never-opened WG install by falling back from a missing or
invalid capabilities launcher to the managed add-in's `wglink_install.json`
`waveguideGeneratorRoot`. Moved or uninstalled bundles and checkout/developer
installs require starting WG manually when no valid launcher is available. Fusion
must inherit any non-default `WG2_DATA_DIR`; a fresh overridden data folder also
needs a root selected in WG before Send or Solve can use CAD exchange. See
[installed launcher details](../../docs/INSTALL.md) for recovery and platform behavior.

To review and advance the pin:

1. Land and test the compatible change in [`wglink`](https://github.com/m3gnus/wglink).
2. Update the full commit and manifest `addinVersion` in `source.json`. Update
   `server/cadlink/wglink_protocol.source.json` to the same repository and commit,
   and verify its SHA-256 against the upstream protocol file. If its bytes changed,
   review compatibility and copy it byte-for-byte to `server/cadlink/wglink_protocol.py`.
   Update the endpoint oracle's `PROVENANCE.json` `addin_commit` to the package pin.
   Keep `wg_commit` at the WG baseline that generated those fixtures; regenerate
   with `server/tests/tools/generate_cadlink_endpoint_oracle.py --regenerate` only
   when the endpoint contract changes, updating the packaged-pair baseline assertion
   too. Verify shared fixtures against upstream and re-copy changed files with an
   updated `MANIFEST.sha256`.
3. Build from a checkout at that exact commit:

   ```sh
   python3 scripts/build_wglink_package.py \
     --source-root ../wglink \
     --output /tmp/wglink.zip
   ```

4. Run `server/tests/test_wglink_package.py`, `test_wglink_packaged_pair.py`,
   `test_wglink_shared_protocol.py` and the upstream WGLink tests. The WG pair tests
   accept `WGLINK_SOURCE_GIT="$(pwd)/../wglink"` for an offline Git source and fetch the pinned
   commit into a temporary checkout.
5. Rehearse the platform path with `--wglink-archive /tmp/wglink.zip`.

For WGLink development, keep using the upstream symlink installer. The WG
platform installer detects that registration and leaves it alone; alternatively
pass `--skip-wglink`.

The pinned source includes Fusion event receipt acknowledgement for builds that
return false from `fireCustomEvent` after delivery. Undelivered startup work is
cancelled within the existing startup envelope; the shared protocol is unchanged.

The pinned source follows immutable WG export bundles: Fusion updates resolve the
latest export through its locator and check the handoff's export id. Add-ins without
the `immutableBundles` capability keep automatic Send; WG asks them to update for a
manual Update.
