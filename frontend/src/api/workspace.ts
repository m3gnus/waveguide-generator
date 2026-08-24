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
