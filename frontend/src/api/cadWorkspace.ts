import { httpErrorMessage } from './httpError';
import { deadlineFetch } from './deadlineFetch';
import { selectFolderRoot } from './folders';

/** Keep each Fusion version once in the model's cad/ folder, or capture nothing.
 * `run` is the compatibility spelling of Keep for older WG.
 */
export type CadCaptureMode = 'off' | 'run';

export interface CadWorkspacePath {
  selected: boolean;
  path: string | null;
  missingPath?: string | null;
  /** Whether returns carry a copy of the CAD document they were taken from. */
  captureDocument?: boolean;
  captureMode?: CadCaptureMode | 'project';
}

export async function getCadWorkspace(
  fetcher: typeof fetch = fetch,
): Promise<CadWorkspacePath> {
  const response = await deadlineFetch(fetcher, '/api/cad-workspace/path');
  if (!response.ok) throw new Error(await httpErrorMessage(response, 'WGLink folder request failed'));
  return response.json() as Promise<CadWorkspacePath>;
}

export async function selectCadWorkspace(
  path?: string,
  fetcher: typeof fetch = fetch,
): Promise<CadWorkspacePath> {
  const selection = await selectFolderRoot(path, fetcher);
  const available = selection.cadlinkExists !== false;
  return {
    selected: selection.selected && available,
    path: available ? selection.cadlink ?? null : null,
    missingPath: available ? null : selection.cadlink,
  };
}

export async function openCadWorkspace(
  fetcher: typeof fetch = fetch,
): Promise<CadWorkspacePath> {
  const response = await fetcher('/api/cad-workspace/open', { method: 'POST' });
  if (!response.ok) throw new Error(await httpErrorMessage(response, 'WGLink folder request failed'));
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
  if (!response.ok) throw new Error(await httpErrorMessage(response, 'WGLink folder request failed'));
  const saved = ((await response.json()) as { captureMode: CadCaptureMode | 'project' }).captureMode;
  return saved === 'off' ? 'off' : 'run';
}
