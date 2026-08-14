# CAD connection setup contract

Status: implemented 2026-08-14.

## Decision

Run exports and the Fusion exchange are separate resources. The output workspace is
configured under **Settings → Workspace** and remains usable with its default. Fusion
uses a required, user-selected WGLink root configured under **Settings → CAD Link**.
WG owns that setting; the Fusion add-in reads it and never keeps a second path.

The selected root is stored as `cadLinkPath` in the versioned
`cadlink_settings.json` file under WG's platform data directory. Existing installs
without that file adopt `workspacePath` once when its folder contains an existing
`wglink/` or `wgreturn/` exchange. The migration writes the dedicated setting
atomically; later output-folder changes cannot move CAD. An output-only folder has no
exchange evidence and therefore receives the guided setup.

The optional manual-path form posts to `/api/cad-workspace/select`. Like the native
picker routes, this endpoint is exposed only by WG's localhost-bound application
server; it must not be published as a general remote filesystem API.

WG writes outbound bundles beneath `<cadLinkPath>/wglink/`; Fusion writes returns
beneath `<cadLinkPath>/wgreturn/`. Machine-local status and command markers remain
under the application data directory and are not placed in a synced exchange folder.

Onshape does not use this folder. WG creates its transient bundle under application
data and uploads it directly over HTTPS, so asking Onshape users to choose a local
folder would add a setup step with no consumer.

## First-use UX

Settings presents a three-step ordered workflow for the selected CAD application.
Fusion covers add-in installation, WGLink-folder selection, and the first send.
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
