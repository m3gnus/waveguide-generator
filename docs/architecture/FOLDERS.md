# User-visible folders

Settings and Results & export preferences offer one **WG root folder**. The
normal default is `Documents/Waveguide Generator`; an explicit `--data-dir` or
`WG2_DATA_DIR` override defaults to `<data>/workspace-root`.

| Folder | Default contents |
| --- | --- |
| WGLink folder: `<root>/cadlink` | `wglink/` outgoing bundles and `wgreturn/` returned geometry |
| Runs folder: `<root>/runs` | All run archives, automatic exports and captured CAD documents, grouped by project and run |

Changing the root moves no files. New work uses its `cadlink/` and `runs/`
children unless an earlier folder choice is kept as an override. Overrides are
shown read-only with “kept from an earlier setup”. Diverged folders are never
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

If neither folder was chosen, the default root is used. If the two effective
choices are `X/runs` and `X/cadlink`, `X` becomes the root without overrides.
Otherwise the default root is used, and differing effective choices are kept as
overrides. Missing selected folders remain recorded, with the existing
unavailable-folder refusal; their contents are never replaced with a different
folder silently.

Both compatibility mirrors are written atomically before `folders.json` is
published atomically, last:

- `workspace_settings.json` / `workspacePath`: effective Runs folder.
- `cadlink_settings.json` / `cadLinkPath`: effective WGLink folder, with
  `captureDocument` and `captureMode` preserved during migration and root changes.

No-op starts do not rewrite these files. An interrupted migration is rerun from
the durable mirrors. An interrupted root change is recovered conservatively:
any changed mirror is kept as an override, so neither folder loses its contents.

## Rollback and recovery

An older WG ignores `folders.json` and continues using the mirrors. If it changes
a choice, the next new WG adopts that mirror as an override and logs the change
once. A startup notice is also available in the root-folder controls; subsequent
starts see the adopted configuration and do not repeat the drift notice.

Broken schema-1 configuration is reconstructed from mirrors. An unsupported
schema is never overwritten: this version works from mirrors and disables root
editing until WG is updated. The mirror key meanings stay compatible with the
WGLink add-in's direct settings-file reader.

## Windows

Documents is resolved with `SHGetKnownFolderPath(FOLDERID_Documents)`, including
OneDrive redirection; failure falls back to `%USERPROFILE%/Documents`. Comparisons
use case-insensitive normalized spelling and existing filesystem identity.
Mirrors retain the chosen spelling, including drive mappings and symlink aliases,
rather than converting choices to resolved UNC paths.

Root selection reserves 180 UTF-16 characters below `runs/` for project/run names,
export filenames and staging components within MAX_PATH. Longer roots are
refused with a shorter-path message. This is headroom, not a bound on arbitrary
user-supplied names. Export/archive publication also checks the concrete member
and staging paths before writing, refusing long Windows paths without truncation.
