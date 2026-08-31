import { cadWorkspaceSelection } from '../stores/cadWorkspaceSelection';

/**
 * Where a captured CAD document is filed in the run archive.
 *
 * `run` puts the model beside the run it produced, which is where people look
 * for it and is never pruned; `project` keeps only the newest model state for
 * the whole project -- archiving a later state deletes the last, which costs
 * less when one geometry is swept many times; `off` asks the add-in not to
 * carry the document at all.
 */
export type CadCaptureMode = 'off' | 'project' | 'run';

export interface CadWorkspacePath {
  selected: boolean;
  path: string | null;
  /** Whether returns carry a copy of the CAD document they were taken from. */
  captureDocument?: boolean;
  captureMode?: CadCaptureMode;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
  } catch { /* HTTP status remains useful. */ }
  return `WGLink folder request failed (${response.status})`;
}

export async function getCadWorkspace(
  fetcher: typeof fetch = fetch,
): Promise<CadWorkspacePath> {
  const response = await fetcher('/api/cad-workspace/path');
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<CadWorkspacePath>;
}

export async function selectCadWorkspace(
  path?: string,
  fetcher: typeof fetch = fetch,
): Promise<CadWorkspacePath> {
  const response = await fetcher('/api/cad-workspace/select', {
    method: 'POST',
    ...(path ? {
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    } : {}),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  const selection = await response.json() as CadWorkspacePath;
  // Both routes to a folder land here -- the native picker when `path` is
  // absent, the manual field when it is not -- so this is the only place that
  // sees every one of them. The CAD Link coordinator is asleep until it hears
  // this; see `stores/cadWorkspaceSelection`.
  cadWorkspaceSelection.noteSelection(selection.selected);
  return selection;
}

export async function openCadWorkspace(
  fetcher: typeof fetch = fetch,
): Promise<CadWorkspacePath> {
  const response = await fetcher('/api/cad-workspace/open', { method: 'POST' });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<CadWorkspacePath>;
}

/**
 * Choose whether a return carries the CAD document, and where WG files it.
 *
 * The setting is written where the Fusion add-in already reads WG's CAD-link
 * configuration, so there is one switch rather than the same choice offered in
 * both applications. The add-in reads only whether to capture; the mode is
 * WG's own filing decision.
 */
export async function setCaptureMode(
  mode: CadCaptureMode,
  fetcher: typeof fetch = fetch,
): Promise<CadCaptureMode> {
  const response = await fetcher('/api/cad-workspace/capture-document', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return ((await response.json()) as { captureMode: CadCaptureMode }).captureMode;
}
