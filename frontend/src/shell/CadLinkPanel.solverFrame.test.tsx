import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnIngestRecord } from '../api/cadlink';
import fixture from '../viewport/solverFrame.fixture.json';
import { useCadOperationsStore } from '../stores/cadOperations';
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

const MANIFEST = `sha256:${'a'.repeat(64)}`;
const record = (verdict: 'unlinked' | 'per-instance') => ({
  ingest_id: 'wgi_1',
  manifest_sha256: MANIFEST,
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
    useCadOperationsStore.setState({ operations: {} });
  });

  const unconfirmedFetcher = () => vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith('/api/cadlink/solver-frame?')) return json(preview(null));
    if (url.endsWith('/viewport-mesh')) return new Response(MSH, { status: 200 });
    throw new Error(`unexpected ${url}`);
  });

  it('asks nothing while the waiting solve on screen already asks for the frame', async () => {
    useCadOperationsStore.setState({ operations: { 'manual-solve:op': {
      operationId: 'manual-solve:op', kind: 'prepare_and_solve', state: 'needs_user_input', stage: 'ready',
      reason: 'frame_confirmation_required', message: null, jobId: null, attemptGeneration: 1,
      setupRevisionId: 'wgs_1', preparationId: 'wgi_1',
      snapshot: { manifestSha256: MANIFEST, documentName: 'Authored horn', projectLineageId: 'wgl_1' },
      legacy: false, createdAt: 'now', updatedAt: 'now',
    } } });
    const fetcher = unconfirmedFetcher();
    await act(async () => root.render(<SolverFrameSection record={record('unlinked')} fetcher={fetcher}/>));
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalled());
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(host.textContent).toBe('');
  });

  it('offers a first choice, never change-mode copy, for a frame that was never set', async () => {
    const fetcher = unconfirmedFetcher();
    await act(async () => root.render(<SolverFrameSection record={record('unlinked')} fetcher={fetcher}/>));
    await vi.waitFor(() => expect(host.querySelector('.cad-solver-frame-line')?.textContent)
      .toBe('Solver frame not chosen yet · WG asks before the first solve · Choose now'));
    expect(host.querySelector('.cad-state-chip.warn')).toBeNull();
    await act(async () => { host.querySelector<HTMLButtonElement>('button[data-action="change-solver-frame"]')!.click(); });
    await vi.waitFor(() => expect(host.querySelector('[data-frame-preview="ready"]')).not.toBeNull());
    expect(host.textContent).not.toContain('Confirmed for this project');
    expect(host.textContent).not.toContain('nothing yet');
    expect(host.textContent).toContain('Choose the model axis that points out of the mouth');
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
    // One quiet line, not a drawer with a warning chip.
    await vi.waitFor(() => expect(host.querySelector('.cad-solver-frame-line')?.textContent).toBe('Radiates along +y · Change'));
    expect(host.querySelector('.cad-drawer')).toBeNull();
    expect(host.textContent).toContain('This preparation was meshed along +z: prepare it again to solve along +y.');
    await act(async () => { host.querySelector<HTMLButtonElement>('button[data-action="change-solver-frame"]')!.click(); });
    await vi.waitFor(() => expect(host.querySelector('[data-frame-preview="ready"]')).not.toBeNull());
    expect(host.textContent).toContain('runs already solved keep the frame they were solved in');
    await act(async () => { host.querySelector<HTMLInputElement>('input[value="+x"]')!.click(); });
    await act(async () => { host.querySelector<HTMLButtonElement>('button[data-action="confirm-frame"]')!.click(); });
    await vi.waitFor(() => expect(host.querySelector('.cad-solver-frame-line')?.textContent).toBe('Radiates along +x · Change'));
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
