import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadOperationSummary } from '../api/cadOperations';
import { useCadOperationsStore } from '../stores/cadOperations';
import fixture from '../viewport/solverFrame.fixture.json';

const solveOperation = vi.fn(async () => undefined);
vi.mock('./CadLinkCoordinator', () => {
  const snapshot = {
    solveOperation, dismissOperation: vi.fn(), approveOperation: vi.fn(),
    solveOperationWithSettings: vi.fn(), reconcileOperation: vi.fn(), reportError: vi.fn(),
    fusionStatus: null,
  };
  return { cadLinkCoordinatorBridge: { getSnapshot: () => snapshot, subscribe: () => () => undefined } };
});
vi.mock('./CadSolveInputs', () => ({ CadSolveInputs: () => null }));
vi.mock('./CadProjectPanel', () => ({ openCadProject: vi.fn() }));

const { CadOperationsSection } = await import('./CadOperationsSection');

const AXES = ['+z', '-z', '+x', '-x', '+y', '-y'] as const;
const MSH = [
  '$MeshFormat', '2.2 0 8', '$EndMeshFormat',
  '$Nodes', '3', '1 0 0 0', '2 1 0 0', '3 0 1 0', '$EndNodes',
  '$Elements', '1', '1 2 2 1 1 1 2 3', '$EndElements',
].join('\n');

const operation = (reason: string): CadOperationSummary => ({
  operationId: 'op-1', kind: 'prepare_and_solve', state: 'needs_user_input', stage: 'ready',
  reason, message: 'Confirm this model’s solver frame in WG first.', jobId: null, attemptGeneration: 2,
  setupRevisionId: 'wgs_1', preparationId: 'wgi_1',
  snapshot: { manifestSha256: `sha256:${'a'.repeat(64)}`, documentName: 'Authored horn', projectLineageId: 'wgl_1' },
  legacy: false, createdAt: 'now', updatedAt: 'now',
});

const json = (body: unknown) => new Response(JSON.stringify(body), {
  status: 200, headers: { 'Content-Type': 'application/json' },
});

describe('an operation waiting for its solver frame', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    solveOperation.mockClear();
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    useCadOperationsStore.setState({ operations: {} });
    vi.unstubAllGlobals();
  });

  it('shows the preview, confirms the frame, then solves through the backend operation', async () => {
    const puts: unknown[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith('/api/cadlink/solver-frame') && init?.method === 'PUT') {
        puts.push(JSON.parse(String(init.body)));
        return json({ linked: true });
      }
      if (url.startsWith('/api/cadlink/solver-frame')) {
        return json({
          linked: false, ingestId: 'wgi_1', contract: 'cad-solver-frame-v1',
          requirement: { contract: 'cad-solver-frame-v1', export_frame: 'root-component' },
          recordAxis: '+z', recordStatesFrame: true, confirmed: null,
          axes: AXES.map((axis) => ({
            axis, allowed: true, reason: null,
            solverFromAssembly: fixture.axes[axis], previewFromRecord: fixture.axes[axis],
          })),
        });
      }
      if (url.endsWith('/viewport-mesh')) return new Response(MSH, { status: 200 });
      throw new Error(`unexpected ${url}`);
    }));
    useCadOperationsStore.setState({ operations: { 'op-1': operation('frame_confirmation_required') } });
    await act(async () => root.render(<CadOperationsSection record={null}/>));
    expect(host.textContent).toContain('needs its solver frame confirmed');
    await vi.waitFor(() => expect(host.querySelector('[data-frame-preview="ready"]')).not.toBeNull());
    // No plain Solve now: solving without confirming would only wait again.
    expect(host.querySelector('button[aria-label="Solve now: Authored horn"]')).toBeNull();
    expect(host.querySelector<HTMLButtonElement>('button[data-action="confirm-frame"]')!.disabled).toBe(true);
    await act(async () => { host.querySelector<HTMLInputElement>('input[value="-x"]')!.click(); });
    await act(async () => { host.querySelector<HTMLButtonElement>('button[data-action="confirm-frame"]')!.click(); });
    await vi.waitFor(() => expect(solveOperation).toHaveBeenCalledWith('op-1'));
    expect(puts).toEqual([{ operationId: 'op-1', axis: '-x' }]);
  });

  it('offers no frame confirmation for any other reason', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('no request expected'); }));
    useCadOperationsStore.setState({ operations: { 'op-1': operation('engine_unavailable') } });
    await act(async () => root.render(<CadOperationsSection record={null}/>));
    expect(host.querySelector('.cad-solver-frame')).toBeNull();
    expect(host.querySelector('button[aria-label="Solve now: Authored horn"]')).not.toBeNull();
  });
});
