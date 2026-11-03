import { httpErrorMessage } from './httpError';
import { deadlineFetch, deadlineSignal } from './deadlineFetch';
import { selectFolderRoot } from './folders';
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

type OutputMember = { filename: string; blob: Blob } | { filename: string; artifactId: string };

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
  members: readonly OutputMember[],
  fetcher: typeof fetch = fetch,
  existing: 'reject' | 'merge_identical' | 'overwrite' | 'confirm' = 'overwrite',
  destination?: string,
  confirmReplacements?: ConfirmReplacements,
  jobId?: string,
): Promise<OutputFolderWrite> {
  const post = async (policy: string): Promise<OutputFolderWrite> => {
    let body: FormData | string;
    let headers: Record<string, string> | undefined;
    // Retained geometry uses the same destination and collision owner. Only
    // the content source changes; no file payload crosses the browser.
    if (members.every((m): m is { filename: string; artifactId: string } => 'artifactId' in m)) {
      body = JSON.stringify({
        subdirectory: destination ? '' : subdirectory, existing: policy,
        ...(destination ? { destination } : {}), ...(jobId ? { job_id: jobId } : {}),
        members: members.map(m => ({ relative_path: m.filename, artifact_id: m.artifactId })),
      });
      headers = { 'Content-Type': 'application/json' };
    } else {
      const form = new FormData();
      form.append('subdirectory', destination ? '' : subdirectory);
      form.append('existing', policy);
      if (destination) form.append('destination', destination);
      if (jobId) form.append('job_id', jobId);
      members.forEach(member => {
        if (!('blob' in member)) throw new Error('WG could not combine these exports. Export the files separately.');
        form.append('relative_path', member.filename);
        form.append('file', member.blob, member.filename);
      });
      body = form;
    }
    const response = await fetcher('/api/workspace/write-export', { method: 'POST', body, ...(headers ? { headers } : {}) });
    if (!response.ok) {
      throw await collisionFrom(response) ?? new Error(await httpErrorMessage(response, 'WG could not read the Runs folder. Choose the WG folder in Settings.'));
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
  try {
    return await (existing === 'confirm'
      ? writeConfirmingReplacements(post, confirmReplacements)
      : post(existing));
  } catch (error) {
    // Successful writes release their bytes in the workspace writer. A
    // declined replacement or failed write has no further use for them.
    await Promise.allSettled(members.map(async member => {
      if ('artifactId' in member) {
        await fetcher(`/api/export/artifacts/${encodeURIComponent(member.artifactId)}`, {
          method: 'DELETE', signal: deadlineSignal(),
        });
      }
    }));
    throw error;
  }
}
