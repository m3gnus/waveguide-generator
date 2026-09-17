import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnIngestRecord } from '../api/cadlink';
import fixture from '../viewport/solverFrame.fixture.json';
import { SolverFrameSection } from './CadLinkPanel';

const AXES = ['+z', '-z', '+x', '-x', '+y', '-y'] as const;
const MSH = [
  '$MeshFormat', '2.2 0 8', '$EndMeshFormat',
  '$Nodes', '3', '1 0 0 0', '2 1 0 0', '3 0 1 0', '$EndNodes',
  '$Elements', '1', '1 2 2 1 1 1 2 3', '$EndElements',
].join('\n');

const preview = (confirmed: string | null, recordAxis = '+z') => ({
  linked: false, ingestId: 'wgi_1', contract: 'cad-solver-frame-v1',
  requirement: { contract: 'cad-solver-frame-v1', export_frame: 'root-component' },
  recordAxis, recordStatesFrame: true,
  confirmed: confirmed ? { axis: confirmed, confirmedAt: 'now' } : null,
  axes: AXES.map((axis) => ({
    axis, allowed: true, reason: null,
    solverFromAssembly: fixture.axes[axis], previewFromRecord: fixture.axes[axis],
  })),
});

const record = (verdict: 'unlinked' | 'per-instance') => ({
  ingest_id: 'wgi_1',
  freshness: { verdict, instances: [] },
  project: { lineage_id: 'wgl_1', design_id: null, document_native_id: 'urn:x', document_name: 'Authored horn', archive_stem: null },
}) as unknown as CadReturnIngestRecord;

const json = (body: unknown) => new Response(JSON.stringify(body), {
  status: 200, headers: { 'Content-Type': 'application/json' },
});

describe('changing a project solver frame', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
  });

  it('shows the confirmed axis and changes it for later preparations only', async () => {
    let confirmed = '+y';
    const puts: unknown[] = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/cadlink/solver-frame?ingestId=wgi_1' && !init?.method) return json(preview(confirmed));
      if (url.endsWith('/viewport-mesh')) return new Response(MSH, { status: 200 });
      if (url === '/api/cadlink/solver-frame' && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { axis: string };
        puts.push(body);
        confirmed = body.axis;
        return json(preview(confirmed));
      }
      throw new Error(`unexpected ${url}`);
    });
    await act(async () => root.render(<SolverFrameSection record={record('unlinked')} fetcher={fetcher}/>));
    await vi.waitFor(() => expect(host.textContent).toContain('along +y'));
    await act(async () => { host.querySelector<HTMLButtonElement>('.section-head')!.click(); });
    expect(host.textContent).toContain('This preparation was meshed along +z: prepare it again to solve along +y.');
    await act(async () => { host.querySelector<HTMLButtonElement>('button[data-action="change-solver-frame"]')!.click(); });
    await vi.waitFor(() => expect(host.querySelector('[data-frame-preview="ready"]')).not.toBeNull());
    expect(host.textContent).toContain('runs already solved keep the frame they were solved in');
    await act(async () => { host.querySelector<HTMLInputElement>('input[value="+x"]')!.click(); });
    await act(async () => { host.querySelector<HTMLButtonElement>('button[data-action="confirm-frame"]')!.click(); });
    await vi.waitFor(() => expect(host.textContent).toContain('along +x'));
    expect(puts).toEqual([{ ingestId: 'wgi_1', axis: '+x' }]);
    expect(host.querySelector('[data-frame-preview]')).toBeNull();
  });

  it('is not shown, and asks nothing, for a model linked to a WG design', async () => {
    const fetcher = vi.fn(async () => { throw new Error('no request expected'); });
    await act(async () => root.render(<SolverFrameSection record={record('per-instance')} fetcher={fetcher}/>));
    expect(host.textContent).toBe('');
    expect(fetcher).not.toHaveBeenCalled();
  });
});
