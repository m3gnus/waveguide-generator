import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { designForFamily } from '../stores/design';
import { MeshTriangleEstimate } from './MeshTriangleEstimate';

it('shows the build estimate and waits for the latest accuracy edit', async () => {
  const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ triangle_count: 4321 }), { headers: { 'Content-Type': 'application/json' } }));
  vi.stubGlobal('fetch', fetcher);
  const host = document.createElement('div');
  const root = createRoot(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const design = designForFamily('OSSE');
  const render = () => act(() => root.render(<QueryClientProvider client={client}><MeshTriangleEstimate design={{ ...design, mesh: { ...design.mesh } }} /></QueryClientProvider>));
  try {
    render();
    await vi.waitFor(() => expect(host.textContent).toContain('4,321'));
    design.mesh.surface_deviation_mm = 0.15;
    render();
    expect(host.textContent).toContain('calculating');
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2), { timeout: 2000 });
    const request = JSON.parse(String(fetcher.mock.calls[1][1]?.body));
    expect(request.design.mesh.surface_deviation_mm).toBe(0.15);
  } finally {
    act(() => root.unmount()); client.clear(); vi.unstubAllGlobals();
  }
});
