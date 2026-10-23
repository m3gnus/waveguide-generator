# User-visible folders

Settings and Results & export preferences offer one **WG folder**. The
normal default is `Documents/Waveguide Generator`; an explicit `--data-dir` or
`WG2_DATA_DIR` override uses `<data>/workspace-root` as the WG folder. An existing
0.3.5 implicit Runs folder at `<data>/workspace` remains an override; a fresh
setup uses `<data>/workspace-root/runs` without an override. Explicit
`create_app(workspace_dir=…)` defaults are preserved too.

| Folder | Default contents |
| --- | --- |
| WGLink folder: `<root>/cadlink` | `wglink/` outgoing bundles and `wgreturn/` returned geometry |
| Runs folder: `<root>/runs` | All run archives, automatic exports and captured CAD documents, grouped by project and run |

Changing the WG folder moves no files. New work uses its `cadlink/` and `runs/`
children unless an earlier folder choice is kept as an override. Overrides appear as “Separate folder from an earlier setup: <path>”.
Only “Use the WG folder instead” clears one, after confirming that no files
move and naming the destination for new files. Choosing another WG folder
preserves all overrides, even when they already point at its children. Diverged folders are never
consolidated, moved or deleted by folder migration or root selection.

One-off exports remember `export_settings.json` / `lastExportPath` independently.
Application data still holds imports, databases, field traces, logs and
`ipc/wglink`. The add-in discovers IPC without a folder setting.

## Persistence and first start

`<data>/folders.json` contains `schemaVersion: 1`, `rootPath`, and optional
`overrides` with `runs` and/or `cadlink` paths. Effective paths are the overrides
where present, otherwise the root's respective children.

Migration runs during app assembly, before other routers and job runtime mount.
The production launcher acquires its InstanceLock before app assembly. Migration
first runs the existing legacy adoption checks: a populated old run-export
folder can be adopted, and a legacy shared folder with `wglink/` or `wgreturn/`
evidence can be adopted for CAD exchange.

If neither folder was chosen and no existing default holds runs, a fresh
ordinary install creates the default root and its children. A fresh isolated
data setup uses the same WG folder children without creating overrides, and
leaves CAD exchange unchosen until the WG folder is explicitly selected.
An existing isolated setup retains its earlier implicit Runs folder. Existing 0.3.5 defaults are retained
before considering the new default, including `%USERPROFILE%/Documents` on
Windows even when the known-folder API now returns redirected Documents.
An empty existing default retains its spelling too. If
the two effective
choices are `X/runs` and `X/cadlink`, `X` becomes the root without overrides.
Otherwise the default root is used, and differing effective choices are kept as
overrides. Missing selected folders remain recorded, with the existing
unavailable-folder refusal; their contents are never replaced with a different
folder silently. Startup and Open never create adopted roots, their children
or overrides. Only a fresh default or an explicit choice creates folders.

Both compatibility mirrors are written atomically before `folders.json` is
published atomically, last:

- `workspace_settings.json` / `workspacePath`: effective Runs folder.
- `cadlink_settings.json` / `cadLinkPath`: effective WGLink folder, with
  `captureDocument` and `captureMode` preserved during migration and root changes.

An implicit Runs default also records `workspacePathSelected: false` in its
mirror. Publishing a rollback path does not count as a user choice in
`/api/workspace/path`, including after a restart. A chosen root, chosen Runs
folder, adopted legacy workspace, or explicit `workspace_dir` counts as selected.
Their mirrors omit the marker, matching legacy selections. Older WG and WGLink
ignore the extra key and still read the same destination. An older WG's chooser
replaces the mirror without the marker, so even choosing the same path during a
rollback is recognized as an explicit choice on the next start.

No-op starts do not rewrite these files. An interrupted migration is rerun from
the durable mirrors. An interrupted root change is recovered conservatively:
any changed mirror is kept with its recorded spelling. If both mirrors already
name children of the same recorded parent, that parent becomes the root;
otherwise they remain overrides. Neither folder loses its contents.

## Rollback and recovery

An older WG ignores `folders.json` and continues using the mirrors. If it changes
a choice, the next new WG adopts that mirror as an override and logs the change
once. A startup notice is also available in the root-folder controls; subsequent
starts see the adopted configuration and do not repeat the drift notice.

Broken schema-1 configuration is reconstructed from mirrors. An unsupported
schema is never overwritten: this version works from mirrors and disables root
editing until WG is updated. Permission or I/O errors reading any of the three
settings files disable all folder and capture publication for that start,
with a logged reason; readable settings still supply effective paths. Unknown
schema-1 keys and override names are preserved and ignored. The mirror key
meanings stay compatible with the
WGLink add-in's direct settings-file reader.

## Windows

Documents is resolved with `SHGetKnownFolderPath(FOLDERID_Documents)`, including
OneDrive redirection; failure falls back to `%USERPROFILE%/Documents`. Comparisons
use case-insensitive normalized spelling and existing filesystem identity.
Mirrors retain the chosen spelling, including drive mappings and symlink aliases,
rather than converting choices to resolved UNC paths. A shared filesystem
identity is insufficient to infer a shared root: the two recorded parent
strings must match. Spelling-only mirror changes after rollback are adopted.
Choosing a WG folder preserves overrides and their alias spellings.
New choices that are WGLink folders (top-level `wglink/` or `wgreturn/`) or
Runs folders are refused. Existing WG folder choices like these remain effective
and Settings shows a note with the chooser.

Root selection reserves 180 UTF-16 characters below `runs/` for project/run names,
export filenames and staging components within MAX_PATH. Longer roots are
refused with a shorter-path message. This is headroom, not a bound on arbitrary
user-supplied names. One-off export destinations retain the 0.3.5 path
acceptance behavior and are
not subject to this root headroom check.

## Choosing and repairing folders

Every current UI chooser uses `/api/folders/select`. The compatibility
`/api/cad-workspace/select` endpoint also chooses the WG folder and returns the
effective WGLink folder; it never creates an override in an assembled app.
The wg CLI and WGLink do not call this endpoint. The installed qualification
script uses `/api/folders/select` directly.

An explicit `{ "useRoot": "cadlink" }` or `{ "useRoot": "runs" }` request to
`/api/folders/select` clears that override and creates the respective child.
The UI includes `expectedPath` with the confirmed child destination; a changed
WG folder refuses that action so it cannot send new files somewhere else.
The WGLink folder in Settings → CAD Link and the CAD Link panel report missing
paths as “WGLink folder not found: <path>”. “Create it” creates the chosen WG
folder's `cadlink/` child; a missing override instead offers “Use the WG folder
instead”. Startup and Open do not recreate missing saved folders. No files move.
`/api/cad-workspace/path` retains `path: null` for an unavailable folder and adds
`missingPath` with the stored effective path. Compatibility mirrors keep their
existing formats and meanings.
