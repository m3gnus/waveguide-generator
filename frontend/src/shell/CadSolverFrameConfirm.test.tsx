import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fixture from '../viewport/solverFrame.fixture.json';
import { CadSolverFrameConfirm } from './CadSolverFrameConfirm';

const AXES = ['+z', '-z', '+x', '-x', '+y', '-y'] as const;
const MSH = [
  '$MeshFormat', '2.2 0 8', '$EndMeshFormat',
  '$Nodes', '3', '1 0 0 0', '2 1 0 0', '3 0 1 0', '$EndNodes',
  '$Elements', '1', '1 2 2 1 1 1 2 3', '$EndElements',
].join('\n');

const preview = (confirmed: string | null = null, allowed: readonly string[] = AXES) => ({
  linked: false,
  ingestId: 'wgi_1',
  contract: 'cad-solver-frame-v1',
  requirement: { contract: 'cad-solver-frame-v1', export_frame: 'root-component' },
  recordAxis: '+z',
  recordStatesFrame: true,
  confirmed: confirmed ? { axis: confirmed, confirmedAt: 'now' } : null,
  axes: AXES.map((axis) => ({
    axis,
    allowed: allowed.includes(axis),
    reason: allowed.includes(axis) ? null : 'a half or quarter model is solved only as modelled (+z)',
    solverFromAssembly: fixture.axes[axis],
    previewFromRecord: fixture.axes[axis],
  })),
});

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});

describe('confirming an unlinked model solver frame', () => {
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
    vi.restoreAllMocks();
  });

  it('previews the chosen axis and confirms exactly it before solving', async () => {
    const puts: unknown[] = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/cadlink/solver-frame?operationId=op-1' && !init?.method) return json(preview());
      if (url === '/api/cadlink/ingest/wgi_1/viewport-mesh') return new Response(MSH, { status: 200 });
      if (url === '/api/cadlink/solver-frame' && init?.method === 'PUT') {
        puts.push(JSON.parse(String(init.body)));
        return json(preview('+y'));
      }
      throw new Error(`unexpected ${url}`);
    });
    const onConfirmed = vi.fn();
    await act(async () => root.render(<CadSolverFrameConfirm
      operationId="op-1" label="Authored horn" fetcher={fetcher} onConfirmed={onConfirmed}
    />));
    await vi.waitFor(() => expect(host.querySelector('[data-frame-preview="ready"]')).not.toBeNull());
    // Nothing is preselected as confirmed: the modelled frame is only the starting choice.
    const confirm = host.querySelector<HTMLButtonElement>('button[data-action="confirm-frame"]')!;
    expect(confirm.textContent).toContain('+z');
    const plusY = host.querySelector<HTMLInputElement>('input[value="+y"]')!;
    await act(async () => { plusY.click(); });
    expect(host.textContent).toContain('model +y → solver +Z');
    expect(host.querySelector('[data-frame-preview-axis]')?.getAttribute('data-frame-preview-axis')).toBe('+y');
    await act(async () => { host.querySelector<HTMLButtonElement>('button[data-action="confirm-frame"]')!.click(); });
    await vi.waitFor(() => expect(onConfirmed).toHaveBeenCalledWith('+y'));
    expect(puts).toEqual([{ operationId: 'op-1', axis: '+y' }]);
  });

  it('offers only the modelled frame for a half model, and falls back to the solve mesh', async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/cadlink/solver-frame?')) return json(preview(null, ['+z']));
      if (url.endsWith('/viewport-mesh')) return new Response('', { status: 404 });
      if (url === '/api/cadlink/ingest/wgi_1/mesh') return new Response(MSH, { status: 200 });
      throw new Error(`unexpected ${url}`);
    });
    await act(async () => root.render(<CadSolverFrameConfirm
      operationId="op-1" label="Half" fetcher={fetcher} onConfirmed={() => undefined}
    />));
    await vi.waitFor(() => expect(host.querySelector('[data-frame-preview="ready"]')).not.toBeNull());
    const disabled = [...host.querySelectorAll<HTMLInputElement>('input[type="radio"]')]
      .filter((input) => input.disabled).map((input) => input.value);
    expect(disabled).toEqual(['-z', '+x', '-x', '+y', '-y']);
    expect(host.textContent).toContain('solved only as modelled');
  });

  it('says why it cannot preview and confirms nothing', async () => {
    const fetcher = vi.fn(async () => json({ detail: 'This CAD operation has no preparation yet' }, 409));
    const onConfirmed = vi.fn();
    await act(async () => root.render(<CadSolverFrameConfirm
      operationId="op-1" label="x" fetcher={fetcher} onConfirmed={onConfirmed}
    />));
    await vi.waitFor(() => expect(host.textContent).toContain('no preparation yet'));
    expect(host.querySelector('button[data-action="confirm-frame"]')).toBeNull();
    expect(onConfirmed).not.toHaveBeenCalled();
  });
});
