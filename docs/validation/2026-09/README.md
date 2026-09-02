# September 2026 validation evidence

Status: dated measurements. These describe the machines and commits stated inside
them; they are not a current release status dashboard.

- [macOS Gatekeeper](MACOS-GATEKEEPER.md) — what Gatekeeper actually does to an
  ad-hoc signed bundle, an unsigned script and an unsigned binary, all measured
  against genuinely quarantined artifacts; and what an Apple Developer ID would
  cost.

## Open: the one-pass manual test for the disk-image installer

Everything about the installer script is measured except the part no
command-line tool can observe — whether **System Settings → Privacy & Security**
offers "Open Anyway" for it. `spctl` says the script has the `source` line that
the exception attaches to and the app does not, which is the documented
precondition; it is not proof, and an inference from a signature difference was
already wrong once here. So this is a click test, and it is one pass.

Run it on a Mac with Apple silicon, signed in as an admin user. Total time is a
few minutes, most of it the copy.

**Before you start**

1. Quit Waveguide Generator if it is running.
2. In Finder, delete `/Applications/Waveguide Generator.app` if one is there, so
   what you see afterwards cannot be a leftover.
3. In Terminal, one line, to make sure the disk image really carries the download
   flag — an un-quarantined image proves nothing, and that is the trap this whole
   item keeps falling into:

   ```bash
   xattr -w com.apple.quarantine "0081;$(printf '%x' $(date +%s));Safari;$(uuidgen)" \
     ~/Downloads/Waveguide.Generator-0.3.0-macos-arm64.dmg
   xattr -p com.apple.quarantine ~/Downloads/Waveguide.Generator-0.3.0-macos-arm64.dmg
   ```

   The second line must print something like
   `0081;6a988261;Safari;3E700882-…`. If it prints nothing, stop: the rest of the
   test is meaningless.

   Downloading the `.dmg` in Safari or Chrome instead is equally good and needs no
   Terminal; this line exists so the test does not depend on a browser download.

**The test**

4. Double-click the `.dmg`. A Finder window opens showing four items:
   **Waveguide Generator**, **Applications**, **Install Waveguide
   Generator.command**, **READ ME FIRST.txt**.
5. Double-click **Install Waveguide Generator.command**.
   - *Expected:* a dialog titled **"Install Waveguide Generator.command" Not
     Opened**, saying Apple could not verify it is free of malware, with **Done**
     and **Move to Bin**.
   - *If instead* it opens a Terminal window and starts installing, that is a
     better outcome than expected — record it and skip to step 9.
6. Click **Done**. Do not click Move to Bin.
7. Open **System Settings → Privacy & Security** and scroll to the **Security**
   section at the bottom. Do this within a few minutes of step 5; the entry is
   about the last blocked item and does not persist indefinitely.
   - **This is the whole test.** Expected: a line reading
     `"Install Waveguide Generator.command" was blocked to protect your Mac.`
     with an **Open Anyway** button beside it.
   - **Failure looks like:** the Security section shows only the "Allow
     applications from" setting and no blocked-item line. That means the script
     gets no override either, the hypothesis is wrong, and the installer must be
     removed from the disk image and the `xattr` instruction restored as the
     primary route. Record it and stop.
8. Click **Open Anyway**, authenticate with Touch ID or your password, and click
   **Open** in the confirmation dialog.
9. *Expected:* a Terminal window opens and prints, in order:

   ```
   Installing Waveguide Generator
   ==============================

   Copying to /Applications ...
   Clearing the download quarantine flag ...
   Checking the app signature ...

   Installed: /Applications/Waveguide Generator.app

   You can eject the Waveguide Generator disk image now.
   Starting Waveguide Generator ...
   ```

   and Waveguide Generator opens its window. **At no point did you type a
   command.**
10. Confirm the app is genuinely unquarantined rather than merely running from an
    approval, so that later launches are clean too:

    ```bash
    xattr -p com.apple.quarantine "/Applications/Waveguide Generator.app"
    ```

    Expected: `No such xattr: com.apple.quarantine`.
11. Quit the app and open it again from Applications. Expected: it starts with no
    dialog at all.

**What each outcome means**

| Result | Meaning |
|---|---|
| Steps 7–11 as described | The installer replaces the Terminal command for every macOS user. Demote the `xattr` line in the README, the release notes and `READ ME FIRST.txt` to a fallback, or drop it. |
| Step 7 shows no blocked-item line | The hypothesis is wrong. Remove the installer from the disk image; it is a dead end presented as a solution, which is worse than the honest Terminal line. |
| Step 5 runs the script directly | Better than expected — the script is not Gatekeeper-blocked at all. Simplify `READ ME FIRST.txt` accordingly. |
| Step 9 fails partway | A bug in `installers/macos/dmg-install.command`, not in the Gatekeeper reasoning. The window stays open and prints what failed. |
