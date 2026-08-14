export interface CadWorkspacePath {
  selected: boolean;
  path: string | null;
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
  return response.json() as Promise<CadWorkspacePath>;
}

export async function openCadWorkspace(
  fetcher: typeof fetch = fetch,
): Promise<CadWorkspacePath> {
  const response = await fetcher('/api/cad-workspace/open', { method: 'POST' });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<CadWorkspacePath>;
}
