import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { useSolveOptionsStore } from '../stores/solveOptions';
import { ParamPanel } from './ParamPanel';

const RESOLUTION = {
  quadrants: 14, xz: false, yz: true,
  reasons: { xz: ['the profile is not mirrored about XZ'], yz: [] },
  tolerance_mm: 1e-7, relative_tolerance: 2e-4,
};

/**
 * `AutoSymmetryReadout` used to fire a raw debounced fetch on every design
 * revision, with no dedupe, no cache and no abort. Surface sampling costs
 * 57-150 ms a call, so an editing session paid it over and over for shapes it
 * had already resolved.
 */
describe('auto symmetry resolution', () => {
  let host: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let symmetryCalls: string[];

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.clear();
    resetDesignStore();
    useSolveOptionsStore.getState().setSymmetry('auto');
    symmetryCalls = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/api/design/symmetry')) {
        symmetryCalls.push(String(init?.body));
        return new Response(JSON.stringify(RESOLUTION), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify({ engines: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    client.clear();
    vi.unstubAllGlobals();
  });

  const tick = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });

  /**
   * Pump past the 400 ms debounce. `until` lets a case that expects a request
   * stop as soon as it lands; a case asserting that nothing fires has to spend
   * the whole budget, or it would pass simply by not having waited.
   */
  const settle = async (until?: () => boolean, budget = 250) => {
    for (let attempt = 0; attempt < budget; attempt += 1) {
      if (until?.()) return;
      await tick();
    }
  };

  /** The resolved domain name, or 'resolving…' / '—' while it is not in yet. */
  const readout = () => host.querySelector('.resolved-mode b')?.textContent ?? '';

  const mount = async () => {
    await act(async () => {
      root.render(<QueryClientProvider client={client}><ParamPanel tab="simulation" /></QueryClientProvider>);
    });
    await settle(() => readout() === 'Half domain (YZ)');
  };

  const edit = async (value: number, until?: () => boolean) => {
    act(() => useDesignStore.getState().updateValue('mesh.wall_thickness', value));
    await settle(until);
  };

  it('resolves once per settled shape and reads the answer back from cache', async () => {
    await mount();
    expect(symmetryCalls).toHaveLength(1);
    expect(readout()).toBe('Half domain (YZ)');

    await edit(9, () => symmetryCalls.length >= 2);
    expect(symmetryCalls).toHaveLength(2);

    // Back to a shape already resolved: served from cache, no new request.
    // No early exit here -- the point is that the full budget elapses quietly.
    await edit(5);
    expect(symmetryCalls).toHaveLength(2);
    expect(readout()).toBe('Half domain (YZ)');
  });

  it('does not resolve for a revision that produces identical wire bytes', async () => {
    await mount();
    expect(symmetryCalls).toHaveLength(1);
    const before = symmetryCalls[0];

    // Re-applying the value the design already holds bumps designRevision --
    // which is what the old effect keyed on, and what used to make it refetch.
    const revisionBefore = useDesignStore.getState().designRevision;
    act(() => useDesignStore.getState().updateValue('mesh.wall_thickness', 5));
    expect(useDesignStore.getState().designRevision).toBeGreaterThan(revisionBefore);
    await settle();
    expect(symmetryCalls).toHaveLength(1);
    expect(symmetryCalls[0]).toBe(before);
  });

  it('sends the shape it resolved, in order, on each distinct payload', async () => {
    await mount();
    await edit(9, () => symmetryCalls.length >= 2);
    expect(symmetryCalls).toHaveLength(2);
    const [first, second] = symmetryCalls.map((body) => JSON.parse(body) as { mesh: { wall_thickness: number } });
    expect(first.mesh.wall_thickness).toBe(5);
    expect(second.mesh.wall_thickness).toBe(9);
  });

  it('reports the resolver reasons rather than a bare dash', async () => {
    await mount();
    await settle(() => host.textContent?.includes('mirrored about XZ') ?? false);
    expect(host.textContent).toContain('the profile is not mirrored about XZ');
  });
});
