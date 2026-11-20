import { httpErrorMessage } from './httpError';
import { deadlineFetch } from './deadlineFetch';
import { selectFolderRoot } from './folders';
import type { ServerMember } from './results';
import {
  collisionFrom,
  writeConfirmingReplacements,
  type ConfirmReplacements,
} from './exportDestination';

/** Effective Runs folder. Root changes are selected through /api/folders. */
export interface WorkspaceFolder {
  path: string | null;
  selected: boolean;
}

async function read(response: Response): Promise<WorkspaceFolder> {
  if (!response.ok) throw new Error(await httpErrorMessage(response, 'WG could not read the Runs folder. Choose the WG folder in Settings.'));
  const payload = await response.json() as { path?: unknown; selected?: unknown };
  return {
    path: typeof payload.path === 'string' && payload.path ? payload.path : null,
    selected: payload.selected === true,
  };
}

export async function getWorkspaceFolder(fetcher: typeof fetch = fetch): Promise<WorkspaceFolder> {
  return read(await deadlineFetch(fetcher, '/api/workspace/path'));
}

export async function openWorkspaceFolder(fetcher: typeof fetch = fetch): Promise<WorkspaceFolder> {
  return read(await fetcher('/api/workspace/open', { method: 'POST' }));
}

/**
 * Choose the WG folder, returning the effective Runs folder.
 *
 * With no path the server opens its native picker; with one it takes the path
 * as given. With no body the server starts the picker at the current root.
 */
export async function selectWorkspaceFolder(
  path?: string,
  fetcher: typeof fetch = fetch,
): Promise<WorkspaceFolder> {
  const selection = await selectFolderRoot(path, fetcher);
  const folder = await getWorkspaceFolder(fetcher);
  return { ...folder, selected: selection.selected };
}

export interface OutputFolderWrite {
  /** Absolute directory the files landed in, for reporting back to the user. */
  directory: string;
  files: string[];
  /** Files that already existed and were replaced, named rather than counted. */
  replaced: string[];
}

/**
 * What to do about files a previous export already wrote.
 *
 * Automatic post-run export is a background write nobody asked for twice, so
 * it merges and refuses to change anything: `merge_identical`. A user choosing
 * an export a second time is asking for the file again, so it replaces:
 * `overwrite`. Merging a repeat manual export cannot work -- the JSON and
 * summary builders stamp the current time into their output, and smoothing or
 * chart preferences change the bytes too, so every repeat differed and the
 * whole bundle was rejected with a 409.
 */
export type ExistingFilePolicy = 'reject' | 'merge_identical' | 'overwrite' | 'confirm';

/** One file of a workspace write: bytes from the browser, or a file the server holds. */
export type WorkspaceFile =
  | { filename: string; blob: Blob; server?: undefined }
  | { filename: string; server: ServerMember; blob?: undefined };

/**
 * The bytes one write-export request may carry, uploads and server members
 * together: `MAX_EXPORT_BYTES` in `server/workspace/api.py`, which a server
 * test checks this against.
 */
export const WRITE_EXPORT_MAX_BYTES = 256 * 1024 * 1024;

const OUTPUT_FOLDER_WRITE_FAILED = 'WG could not read the Runs folder. Choose the WG folder in Settings.';

async function serverMemberUnavailable(response: Response): Promise<boolean> {
  try {
    const body = await response.clone().json() as { code?: unknown };
    return body.code === 'server_member_unavailable';
  } catch {
    return false;
  }
}

function publicationUncertain(error: unknown): Error {
  return Object.assign(error instanceof Error ? error : new Error(String(error)), { publicationUncertain: true });
}

/**
 * Write files into the output folder through the backend: the one
 * `POST /api/workspace/write-export` client, for run exports, archives and
 * design-level exports alike.
 *
 * Design-level exports used to hand the browser an `<a download>` blob. That
 * works in a browser tab and does nothing useful in the desktop window, which
 * is a WebView2 host with no download UI and no download handler wired -- so
 * the export succeeded on the server and the file never reached the user.
 * Run-result exports already avoid this by writing server-side; this is the
 * same path for the File menu, and it also means every export can name the
 * folder it went to.
 *
 * A failed write carries what its caller needs to classify it:
 * `publicationUncertain` when the request or its answer was lost (files may
 * have been written), `destinationConflict` for a 409 about the folder, and
 * `serverMemberUnavailable` when a file the server was to copy is gone and
 * nothing was written. `failure` is the words for any other refusal.
 */
export async function writeToOutputFolder(
  subdirectory: string,
  members: readonly WorkspaceFile[],
  fetcher: typeof fetch = fetch,
  existing: ExistingFilePolicy = 'overwrite',
  destination?: string,
  confirmReplacements?: ConfirmReplacements,
  jobId?: string,
  failure: string = OUTPUT_FOLDER_WRITE_FAILED,
): Promise<OutputFolderWrite> {
  // A Blob from another realm (an iframe, a worker) is not `instanceof Blob`
  // here, and FormData would send its string form instead of its bytes.
  const compatibleBlobs = await Promise.all(members.map(async ({ blob }) => {
    if (blob === undefined) return undefined;
    const crossRealmBlob = blob as unknown as {
      arrayBuffer: () => Promise<ArrayBuffer>;
      type: string;
    };
    return blob instanceof Blob
      ? blob
      : new Blob([await crossRealmBlob.arrayBuffer()], { type: crossRealmBlob.type });
  }));
  const referencesServer = members.some(({ server }) => server !== undefined);
  const post = async (policy: string): Promise<OutputFolderWrite> => {
    const body = new FormData();
    // A folder the user chose for this export receives the files themselves.
    // The subdirectory groups exports inside the shared output folder, and
    // grouping is exactly what someone answering "put it here" did not ask for.
    body.append('subdirectory', destination ? '' : subdirectory);
    body.append('existing', policy);
    if (destination) body.append('destination', destination);
    if (jobId) body.append('job_id', jobId);
    members.forEach(({ filename }) => body.append('relative_path', filename));
    // A file the server holds is named, not sent: one source per member, in
    // order, and a file part for each upload.
    if (referencesServer) {
      members.forEach(({ server }) => body.append('member_source', server ? JSON.stringify(server) : 'upload'));
    }
    members.forEach(({ filename }, index) => {
      const blob = compatibleBlobs[index];
      if (blob) body.append('file', blob, filename);
    });
    let response: Response;
    try {
      response = await fetcher('/api/workspace/write-export', { method: 'POST', body });
    } catch (error) {
      throw publicationUncertain(error);
    }
    // The bytes are built once and posted again on confirmation: rebuilding the
    // bundle would re-render its charts and restamp its timestamps, so the
    // second request would not be the export the user was asked about.
    if (!response.ok) {
      const unavailable = response.status === 409 && await serverMemberUnavailable(response);
      const error = await collisionFrom(response) ?? new Error(await httpErrorMessage(response, failure));
      // A held file the server no longer has wrote nothing: not a conflict in
      // the user's folder, and the caller may take the bytes another way.
      if (unavailable) Object.assign(error, { serverMemberUnavailable: true });
      else if (response.status === 409) Object.assign(error, { destinationConflict: true });
      throw error;
    }
    let payload: { directory?: unknown; files?: unknown; replaced?: unknown };
    try {
      payload = await response.json() as typeof payload;
    } catch (error) {
      // Fetch resolves at response headers; losing the body can still hide a
      // completed publication. Never automatically regenerate that bundle.
      throw publicationUncertain(error);
    }
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
