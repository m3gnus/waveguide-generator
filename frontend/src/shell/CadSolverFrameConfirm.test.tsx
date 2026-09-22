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

const framePreview = (confirmed: string | null = null, allowed: readonly string[] = AXES) => ({
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

const button = (host: HTMLElement) => host.querySelector<HTMLButtonElement>('button[data-action="confirm-frame"]');

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

  it('chooses nothing for the user, then previews and confirms exactly the chosen axis', async () => {
    const puts: unknown[] = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/cadlink/solver-frame?operationId=op-1' && !init?.method) return json(framePreview());
      if (url === '/api/cadlink/ingest/wgi_1/mesh') return new Response(MSH, { status: 200 });
      if (url === '/api/cadlink/solver-frame' && init?.method === 'PUT') {
        puts.push(JSON.parse(String(init.body)));
        return json(framePreview('+y'));
      }
      throw new Error(`unexpected ${url}`);
    });
    const onConfirmed = vi.fn();
    await act(async () => root.render(<CadSolverFrameConfirm
      snapshot={{ operationId: 'op-1' }} label="Authored horn" fetcher={fetcher} onConfirmed={onConfirmed}
    />));
    await vi.waitFor(() => expect(host.querySelector('[data-frame-preview="ready"]')).not.toBeNull());
    expect(fetcher).not.toHaveBeenCalledWith('/api/cadlink/ingest/wgi_1/viewport-mesh');
    // Nothing preselected, not even the modelled +z, and nothing can be confirmed yet.
    expect([...host.querySelectorAll<HTMLInputElement>('input[type="radio"]')].some((input) => input.checked)).toBe(false);
    expect(button(host)!.disabled).toBe(true);
    // Unchosen, it reads as the instruction it is, not as a dead button.
    expect(button(host)!.textContent).toBe('Pick an axis above');
    expect(host.querySelector('[data-frame-preview-axis]')).toBeNull();
    await act(async () => { host.querySelector<HTMLInputElement>('input[value="+y"]')!.click(); });
    expect(button(host)!.disabled).toBe(false);
    expect(button(host)!.textContent).toBe('Confirm +y and solve');
    expect(host.textContent).toContain('model +y → solver +Z');
    expect(host.querySelector('[data-frame-preview-axis]')?.getAttribute('data-frame-preview-axis')).toBe('+y');
    await act(async () => { button(host)!.click(); });
    await vi.waitFor(() => expect(onConfirmed).toHaveBeenCalledWith('+y'));
    expect(puts).toEqual([{ operationId: 'op-1', axis: '+y' }]);
  });

  it('keeps the form when confirming fails, says why, and lets the user try again', async () => {
    let attempts = 0;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith('/api/cadlink/solver-frame?')) return json(framePreview());
      if (url.endsWith('/mesh')) return new Response(MSH, { status: 200 });
      if (init?.method === 'PUT') {
        attempts += 1;
        return attempts === 1 ? json({ detail: 'the store is busy' }, 503) : json(framePreview('-x'));
      }
      throw new Error(`unexpected ${url}`);
    });
    const onConfirmed = vi.fn();
    await act(async () => root.render(<CadSolverFrameConfirm
      snapshot={{ operationId: 'op-1' }} label="Authored horn" fetcher={fetcher} onConfirmed={onConfirmed}
    />));
    await vi.waitFor(() => expect(host.querySelector('[data-frame-preview="ready"]')).not.toBeNull());
    await act(async () => { host.querySelector<HTMLInputElement>('input[value="-x"]')!.click(); });
    await act(async () => { button(host)!.click(); });
    await vi.waitFor(() => expect(host.querySelector('[role="alert"]')?.textContent).toContain('the store is busy'));
    expect(host.querySelector('[data-frame-preview="ready"]')).not.toBeNull();
    expect(host.querySelector<HTMLInputElement>('input[value="-x"]')!.checked).toBe(true);
    expect(button(host)!.disabled).toBe(false);
    expect(onConfirmed).not.toHaveBeenCalled();
    await act(async () => { button(host)!.click(); });
    await vi.waitFor(() => expect(onConfirmed).toHaveBeenCalledWith('-x'));
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });

  it('changes a confirmed frame by ingestion, starting from the confirmed axis', async () => {
    const puts: unknown[] = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/cadlink/solver-frame?ingestId=wgi_1' && !init?.method) return json(framePreview('+y'));
      if (url.endsWith('/mesh')) return new Response(MSH, { status: 200 });
      if (init?.method === 'PUT') {
        puts.push(JSON.parse(String(init.body)));
        return json(framePreview('-z'));
      }
      throw new Error(`unexpected ${url}`);
    });
    const onConfirmed = vi.fn();
    await act(async () => root.render(<CadSolverFrameConfirm
      snapshot={{ ingestId: 'wgi_1' }} mode="change" label="Authored horn" fetcher={fetcher} onConfirmed={onConfirmed}
    />));
    await vi.waitFor(() => expect(host.querySelector('[data-frame-preview="ready"]')).not.toBeNull());
    expect(host.querySelector<HTMLInputElement>('input[value="+y"]')!.checked).toBe(true);
    expect(host.textContent).toContain('runs already solved keep the frame they were solved in');
    // Re-confirming the same axis changes nothing.
    expect(button(host)!.disabled).toBe(true);
    await act(async () => { host.querySelector<HTMLInputElement>('input[value="-z"]')!.click(); });
    expect(button(host)!.textContent).toBe('Use -z for this project');
    await act(async () => { button(host)!.click(); });
    await vi.waitFor(() => expect(onConfirmed).toHaveBeenCalledWith('-z'));
    expect(puts).toEqual([{ ingestId: 'wgi_1', axis: '-z' }]);
  });

  it('offers only the modelled frame for a half model and uses the solve mesh', async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/cadlink/solver-frame?')) return json(framePreview(null, ['+z']));
      if (url === '/api/cadlink/ingest/wgi_1/mesh') return new Response(MSH, { status: 200 });
      throw new Error(`unexpected ${url}`);
    });
    await act(async () => root.render(<CadSolverFrameConfirm
      snapshot={{ operationId: 'op-1' }} label="Half" fetcher={fetcher} onConfirmed={() => undefined}
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
      snapshot={{ operationId: 'op-1' }} label="x" fetcher={fetcher} onConfirmed={onConfirmed}
    />));
    await vi.waitFor(() => expect(host.textContent).toContain('no preparation yet'));
    expect(button(host)).toBeNull();
    expect(onConfirmed).not.toHaveBeenCalled();
  });
});
