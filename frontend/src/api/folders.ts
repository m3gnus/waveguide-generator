import { deadlineFetch } from './deadlineFetch';
export interface FolderRoot {
  path: string | null;
  selected: boolean;
  runs: string;
  cadlink: string;
  overrides: { runs?: string; cadlink?: string };
  notice?: string | null;
  readOnly?: boolean;
}

async function request(action: 'path' | 'open' | 'select', path?: string): Promise<FolderRoot> {
  const response = await deadlineFetch(fetch, `/api/folders/${action}`, action === 'path' ? undefined : {
    method: 'POST',
    ...(path ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }) } : {}),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Folder request failed');
  return body as FolderRoot;
}

export const getFolderRoot = () => request('path');
export const openFolderRoot = () => request('open');
export const selectFolderRoot = (path?: string) => request('select', path);
