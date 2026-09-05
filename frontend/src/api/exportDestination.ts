/**
 * Where one manual export goes, chosen for that export and no other.
 *
 * Separate from the workspace on purpose. The workspace is one persistent
 * setting -- automatic exports, run archives and CAD projects all live under it
 * -- so answering "put this STL on the Desktop" must not move it. See
 * `ExportDestinationStore` in `server/workspace/api.py`.
 *
 * The server hands back a handle rather than accepting a path from the browser
 * at write time: the only folders WG writes an export into are ones it resolved
 * itself, from the native picker or from a path it was asked to remember.
 */
export interface ExportDestination {
  /** The absolute folder, for the user to read. */
  path: string | null;
  /** The handle `write-export` accepts; null when no folder could be offered. */
  token: string | null;
  /** True when `path` is the folder the last export used, not the workspace. */
  remembered: boolean;
  /** True when the user just chose this folder, rather than being offered it. */
  selected: boolean;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
  } catch { /* the status is still worth reporting */ }
  return `Export destination request failed (${response.status})`;
}

async function read(response: Response): Promise<ExportDestination> {
  if (!response.ok) throw new Error(await errorMessage(response));
  const payload = await response.json() as Record<string, unknown>;
  return {
    path: typeof payload.path === 'string' && payload.path ? payload.path : null,
    token: typeof payload.token === 'string' && payload.token ? payload.token : null,
    remembered: payload.remembered === true,
    selected: payload.selected === true,
  };
}

/** The folder the next export would default to: the last one used, or the workspace. */
export async function getExportDestination(fetcher: typeof fetch = fetch): Promise<ExportDestination> {
  return read(await fetcher('/api/workspace/export-destination'));
}

/**
 * Choose the folder, or accept one that was typed.
 *
 * With no path the server opens its native picker, which runs on the machine
 * hosting the server; a browser on another machine types a path instead, as it
 * already does for the workspace. Cancelling answers `selected: false` and
 * leaves both the remembered folder and the filesystem untouched.
 */
export async function chooseExportDestination(
  path?: string,
  fetcher: typeof fetch = fetch,
): Promise<ExportDestination> {
  return read(await fetcher('/api/workspace/export-destination', path ? {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  } : { method: 'POST' }));
}

/** Files in the chosen folder that an export would replace with different bytes. */
export interface ExportCollision {
  directory: string;
  paths: string[];
}

/**
 * The server refused an `existing=confirm` write and named what it would
 * replace. Nothing was written; the same request repeated as `overwrite` is
 * what the user's "Replace" answers.
 */
export class ExportCollisionError extends Error {
  constructor(readonly collision: ExportCollision) {
    super(`${collision.paths.length} file(s) in ${collision.directory} would be replaced`);
    this.name = 'ExportCollisionError';
  }
}

/** The user answered a question with "no". Not a failure, and not an error state. */
export class ExportCancelledError extends Error {
  constructor(message = 'Export cancelled. No files were written.') {
    super(message);
    this.name = 'ExportCancelledError';
  }
}

/** Asks the user, once, about every file an export would replace. */
export type ConfirmReplacements = (collision: ExportCollision) => Promise<boolean>;

/**
 * Read a refused write as a collision, or `null` when it is something else.
 *
 * The response is cloned, so the caller's own error path can still read the
 * body it was going to report.
 */
export async function collisionFrom(response: Response): Promise<ExportCollisionError | null> {
  if (response.status !== 409) return null;
  try {
    const body = await response.clone().json() as Record<string, unknown>;
    if (body.code !== 'export_collision') return null;
    const paths = Array.isArray(body.paths)
      ? body.paths.filter((path): path is string => typeof path === 'string')
      : [];
    return new ExportCollisionError({
      directory: typeof body.directory === 'string' ? body.directory : '',
      paths,
    });
  } catch { return null; }
}

/**
 * Write, and if that would replace files the user did not put there, ask once.
 *
 * `confirm` is the whole reason the `confirm` policy exists: the server writes
 * nothing and names every differing file, the user answers one question about
 * the export as a whole, and the identical request is repeated as `overwrite`.
 * Without a `confirm` callback the refusal is raised, so no caller can silently
 * inherit an overwrite it never asked about.
 */
export async function writeConfirmingReplacements<T>(
  post: (existing: 'confirm' | 'overwrite') => Promise<T>,
  confirm?: ConfirmReplacements,
): Promise<T> {
  try {
    return await post('confirm');
  } catch (error) {
    if (!(error instanceof ExportCollisionError) || !confirm) throw error;
    if (!await confirm(error.collision)) throw new ExportCancelledError();
    return post('overwrite');
  }
}
