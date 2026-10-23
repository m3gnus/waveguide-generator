# CAD connection setup contract

Status: implemented 2026-08-14.

## Decision

Settings offers one **WG folder**. Its `cadlink/` child is the **WGLink folder**
and its `runs/` child is the **Runs folder**. Settings → CAD Link shows the
effective WGLink folder and the shared “Choose WG folder…” chooser.

`folders.json` owns the WG folder and any separate folders from earlier setups.
`cadLinkPath` in `cadlink_settings.json` mirrors the effective WGLink folder for
WGLink and older WG. Earlier choices remain effective until the user explicitly
chooses “Use the WG folder instead” and confirms that no files move. See
[folder persistence and recovery](../architecture/FOLDERS.md).

All current choosers, including the manual-path form and Send flow, post to
`/api/folders/select`. The compatibility `/api/cad-workspace/select` endpoint
also selects the WG folder; it no longer creates CAD overrides. These routes
are exposed only by WG's localhost-bound application server.

WG writes outbound bundles beneath `<cadLinkPath>/wglink/`; Fusion writes returns
beneath `<cadLinkPath>/wgreturn/`. Machine-local status and command markers remain
under the application data directory and are not placed in a synced exchange folder.

Onshape does not use this folder. WG creates its transient bundle under application
data and uploads it directly over HTTPS, so asking Onshape users to choose a local
folder would add a setup step with no consumer.

## First-use UX

Settings presents a three-step ordered workflow for the selected CAD application.
Fusion covers add-in installation, the WGLink folder and WG folder chooser, and the first send.
Onshape covers key creation, private local storage, and an account/plan verification.
The CAD Link panel reports a missing Fusion folder as a setup state with a direct
route back to settings; sending never opens an unexplained folder picker.

Secrets are never accepted in a browser form. The Onshape connection endpoint returns
only configuration state, credential-file location, authenticated account identity,
and plan information.

## External constraints checked

- Autodesk documents add-ins as folders Fusion discovers at startup or links through
  **Utilities → Scripts and Add-Ins**, with **Run on Startup** as the persistent
  activation mechanism.
- Onshape documents API keys under **My account → Developer**, notes that the secret is
  shown only once, and requires it to be protected like a password and kept out of
  source control.
