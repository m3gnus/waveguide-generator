import { describe, expect, it, vi } from 'vitest';
import { selectCadWorkspace } from './cadWorkspace';

describe('CAD workspace API', () => {
  it('sends a manually entered folder as JSON', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ selected: true, path: '/shared/wglink' })),
    );

    const result = await selectCadWorkspace('/shared/wglink', fetcher);

    expect(result).toEqual({ selected: true, path: '/shared/wglink' });
    expect(fetcher).toHaveBeenCalledWith('/api/cad-workspace/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: '/shared/wglink' }),
    });
  });

  it('uses the native picker when no path is supplied', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ selected: false, path: null })),
    );

    await selectCadWorkspace(undefined, fetcher);

    expect(fetcher).toHaveBeenCalledWith('/api/cad-workspace/select', { method: 'POST' });
  });
});
