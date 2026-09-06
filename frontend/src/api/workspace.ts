import {
  collisionFrom,
  writeConfirmingReplacements,
  type ConfirmReplacements,
} from './exportDestination';

/**
 * The one folder WG writes into: run exports and CAD project archives alike.
 *
 * A CAD project's archive is `<workspace>/<project>`, so "the projects folder"
 * and "the output folder" are the same setting seen from two panels. It is
 * exposed here once rather than fetched separately by each surface, so the two
 * can never show different paths.
 *
 * Selection happens on the server, in the native picker, exactly as v1 did: a
 * directory picker in the browser exists only in Chromium and would leave WG
 * unusable in every other browser and behind a remote host.
 */
export interface WorkspaceFolder {
  path: string | null;
  selected: boolean;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
  } catch { /* the status is still worth reporting */ }
  return `Workspace folder request failed (${response.status})`;
}

async function read(response: Response): Promise<WorkspaceFolder> {
  if (!response.ok) throw new Error(await errorMessage(response));
  const payload = await response.json() as { path?: unknown; selected?: unknown };
  return {
    path: typeof payload.path === 'string' && payload.path ? payload.path : null,
    selected: payload.selected === true,
  };
}

export async function getWorkspaceFolder(fetcher: typeof fetch = fetch): Promise<WorkspaceFolder> {
  return read(await fetcher('/api/workspace/path'));
}

export async function openWorkspaceFolder(fetcher: typeof fetch = fetch): Promise<WorkspaceFolder> {
  return read(await fetcher('/api/workspace/open', { method: 'POST' }));
}

/**
 * Choose the folder, or accept one that was typed.
 *
 * With no path the server opens its native picker; with one it takes the path
 * as given. The request carries no body in the first case so that a server
 * predating the typed form still answers it.
 */
export async function selectWorkspaceFolder(
  path?: string,
  fetcher: typeof fetch = fetch,
): Promise<WorkspaceFolder> {
  return read(await fetcher('/api/workspace/select', path ? {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  } : { method: 'POST' }));
}

export interface OutputFolderWrite {
  /** Absolute directory the files landed in, for reporting back to the user. */
  directory: string;
  files: string[];
  /** Files that already existed and were replaced, named rather than counted. */
  replaced: string[];
}

/**
 * Write files into the output folder through the backend.
 *
 * Design-level exports used to hand the browser an `<a download>` blob. That
 * works in a browser tab and does nothing useful in the desktop window, which
 * is a WebView2 host with no download UI and no download handler wired -- so
 * the export succeeded on the server and the file never reached the user.
 * Run-result exports already avoid this by writing server-side; this is the
 * same path for the File menu, and it also means every export can name the
 * folder it went to.
 */
export async function writeToOutputFolder(
  subdirectory: string,
  members: readonly { filename: string; blob: Blob }[],
  fetcher: typeof fetch = fetch,
  existing: 'reject' | 'merge_identical' | 'overwrite' | 'confirm' = 'overwrite',
  destination?: string,
  confirmReplacements?: ConfirmReplacements,
): Promise<OutputFolderWrite> {
  const post = async (policy: string): Promise<OutputFolderWrite> => {
    const body = new FormData();
    // A folder the user chose for this export receives the files themselves.
    // The subdirectory groups exports inside the shared output folder, and
    // grouping is exactly what someone answering "put it here" did not ask for.
    body.append('subdirectory', destination ? '' : subdirectory);
    body.append('existing', policy);
    if (destination) body.append('destination', destination);
    members.forEach(({ filename }) => body.append('relative_path', filename));
    members.forEach(({ filename, blob }) => body.append('file', blob, filename));
    const response = await fetcher('/api/workspace/write-export', { method: 'POST', body });
    if (!response.ok) {
      throw await collisionFrom(response) ?? new Error(await errorMessage(response));
    }
    const payload = await response.json() as {
      directory?: unknown; files?: unknown; replaced?: unknown;
    };
    return {
      directory: typeof payload.directory === 'string' ? payload.directory : '',
      files: Array.isArray(payload.files)
        ? payload.files.filter((name): name is string => typeof name === 'string')
        : [],
      replaced: Array.isArray(payload.replaced)
        ? payload.replaced.filter((name): name is string => typeof name === 'string')
        : [],
    };
  };
  // `confirm` is the only policy that can come back asking a question, so it is
  // the only one routed through the retry.
  return existing === 'confirm'
    ? writeConfirmingReplacements(post, confirmReplacements)
    : post(existing);
}
