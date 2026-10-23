import { describe, expect, it, vi } from 'vitest';
import { useWorkspaceFolderStore } from '../stores/workspaceFolder';
import { selectCadWorkspace } from './cadWorkspace';

describe('CAD workspace API', () => {
  it('sends a manually entered folder as JSON', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ selected: true, path: '/shared', cadlink: '/shared/cadlink', cadlinkExists: true })),
    );

    const result = await selectCadWorkspace('/shared', fetcher);

    expect(result).toEqual({ selected: true, path: '/shared/cadlink', missingPath: null });
    expect(fetcher).toHaveBeenCalledWith('/api/folders/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: '/shared' }),
    });
  });

  it('uses the native picker when no path is supplied', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ selected: false, path: null })),
    );

    await selectCadWorkspace(undefined, fetcher);

    expect(fetcher).toHaveBeenCalledWith('/api/folders/select', { method: 'POST' });
  });

  it('updates Runs after a Send picker and rejects an older path read', async () => {
    useWorkspaceFolderStore.getState().resetForTests();
    let finish!: (response: Response) => void;
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { finish = resolve; })));
    try {
      const pending = useWorkspaceFolderStore.getState().load();
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({
        selected: true, path: '/chosen', runs: '/chosen/runs', runsSelected: true,
        cadlink: '/chosen/cadlink', cadlinkExists: true,
      })));
      await selectCadWorkspace(undefined, fetcher);
      expect(useWorkspaceFolderStore.getState().path).toBe('/chosen/runs');
      finish(new Response(JSON.stringify({ path: '/old/runs', selected: true })));
      await pending;
      expect(useWorkspaceFolderStore.getState().path).toBe('/chosen/runs');
    } finally { vi.unstubAllGlobals(); useWorkspaceFolderStore.getState().resetForTests(); }
  });

  it('does not report CAD as selected when an earlier override is missing', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({
      selected: true, path: '/chosen', cadlink: '/old/exchange', cadlinkExists: false,
    })));
    expect(await selectCadWorkspace(undefined, fetcher)).toEqual({
      selected: false, path: null, missingPath: '/old/exchange',
    });
  });

});
