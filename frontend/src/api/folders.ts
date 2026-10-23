import { deadlineFetch } from './deadlineFetch';
import { applyWorkspaceFolderChoice } from '../stores/workspaceFolder';
import { cadWorkspaceSelection } from '../stores/cadWorkspaceSelection';
export interface FolderRoot {
  path: string | null;
  selected: boolean;
  runs: string;
  cadlink: string;
  cadlinkExists?: boolean;
  rootChildren?: { cadlink: string; runs: string };
  overrides: { runs?: string; cadlink?: string };
  notice?: string | null;
  rootNote?: string | null;
  readOnly?: boolean;
}

async function request(action: 'path' | 'open' | 'select', payload?: { path: string } | { useRoot: 'runs' | 'cadlink'; expectedPath?: string; expectedCurrentPath?: string }, fetcher: typeof fetch = fetch): Promise<FolderRoot> {
  const response = await deadlineFetch(fetcher, `/api/folders/${action}`, action === 'path' ? undefined : {
    method: 'POST',
    ...(payload ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) } : {}),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Folder request failed');
  if (action === 'select' && body.selected) {
    if (typeof body.runs === 'string') applyWorkspaceFolderChoice({ path: body.runs, selected: body.runsSelected !== false });
    cadWorkspaceSelection.noteSelection(body.cadlinkExists !== false);
  }
  return body as FolderRoot;
}

export const getFolderRoot = () => request('path');
export const openFolderRoot = () => request('open');
export const selectFolderRoot = (path?: string, fetcher?: typeof fetch) => request('select', path ? { path } : undefined, fetcher);
export const useRootFolder = (name: 'runs' | 'cadlink', expectedPath: string, expectedCurrentPath: string) => request('select', { useRoot: name, expectedPath, expectedCurrentPath });
