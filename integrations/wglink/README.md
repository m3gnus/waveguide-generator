# WGLink packaging contract

Waveguide Generator's macOS and Windows platform installers install WGLink for
Fusion 360. The user does not clone `hornlab-fusion-addin` or create its
virtual environment: the installer fetches the exact reviewed Git commit in
[`source.json`](source.json) into a disposable directory, verifies that Git
resolved that commit, builds a deterministic source package, and keeps only
the verified package and extracted runtime payload. WGLink Update uses WG's
existing pinned Python, NumPy, SciPy, and hornlab-waveguide-mesher environment.

The package contains the complete `fusion-addins/WGLink` source tree, its
resampler, the upstream AGPL-3.0-or-later `LICENSE`, and `provenance.json` with
the upstream repository, full commit, WGLink version, WG version, and SHA-256
of every member. `scripts/install_wglink.py` rechecks that inventory before any
Fusion registration is changed. A non-WG-managed copy or symlink is preserved
unless the installer is explicitly run with `--replace-wglink`.

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
