# CAD Link folders: replacement PLAN item

The policy PLAN lives outside this checkout. Apply this replacement to its
“CAD Link folders” item when landing the slice:

- **CAD Link folders:** one WG root folder contains the WGLink folder (`cadlink/`)
  and Runs folder (`runs/`). `folders.json` schema 1 records the root and any
  earlier choices retained as overrides. Migration runs under backend ownership
  before consumers mount, moves/deletes nothing, and atomically writes the
  legacy settings mirrors before publishing `folders.json` last. Rollback reads
  the same mirrors; changes made by an older WG are adopted as overrides once.
  Broken configuration recovers from mirrors and newer schemas are preserved.
  Settings and Preferences share one chooser; diverged overrides remain
  read-only. Pickers start at the current choice. Windows Documents follows
  known-folder redirection, comparison ignores case, chosen path spelling is
  retained, and root selection checks MAX_PATH headroom. One-off exports and
  internal application data remain independent. See the
  [folder model and rollback](../architecture/FOLDERS.md).
