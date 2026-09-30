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
[`server/cadlink/addin_update.py`](../../server/cadlink/addin_update.py) installs
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

To review and advance the pin:

1. Land and test the compatible change in `hornlab-fusion-addin`.
2. Update the full commit and `addinVersion` in `source.json`.
3. Build from a checkout at that exact commit:

   ```sh
   python3 scripts/build_wglink_package.py \
     --source-root ../hornlab-fusion-addin \
     --output /tmp/wglink.zip
   ```

4. Run `server/tests/test_wglink_package.py` and the upstream WGLink tests.
5. Rehearse the platform path with `--wglink-archive /tmp/wglink.zip`.

For WGLink development, keep using the upstream symlink installer. The WG
platform installer detects that registration and leaves it alone; alternatively
pass `--skip-wglink`.
