import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SolverFrameAxisOption, SolverFramePreview, SolverFrameState } from '../api/solverFrame';
import type { SolverFrameAxis } from '../viewport/solverFrame';
import {
  confirmDisplayedFrame,
  resetCadSolverFrameStore,
  useCadSolverFrameStore,
} from './cadSolverFrame';

const IDENTITY = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
const ALL: SolverFrameAxis[] = ['+z', '-z', '+x', '-x', '+y', '-y'];

function frame(
  preselected: SolverFrameAxis | null,
  confirmed: SolverFrameAxis | null = null,
): SolverFrameState {
  const axes: SolverFrameAxisOption[] = ALL.map((axis) => ({
    axis, allowed: true, reason: null, solverFromAssembly: IDENTITY, previewFromRecord: IDENTITY,
  }));
  return {
    linked: false,
    ingestId: 'wgi_1',
    contract: 'solver-frame-v2',
    requirement: null,
    recordAxis: '+z',
    recordStatesFrame: true,
    confirmed: confirmed ? { axis: confirmed, confirmedAt: '2026-09-29T10:00:00Z' } : null,
    axes,
    suggestion: null,
    preselected: preselected ? { axis: preselected, source: confirmed ? 'confirmed' : 'suggested' } : null,
    differs: null,
  };
}

function answering(confirmedAxis: SolverFrameAxis) {
  const calls: Array<{ url: string; method: string | undefined; body: unknown }> = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), method: init?.method, body: init?.body ? JSON.parse(String(init.body)) : null });
    return new Response(JSON.stringify(frame(confirmedAxis, confirmedAxis)), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  }) as unknown as typeof fetch;
  return { calls, fetcher };
}

function show(preview: SolverFramePreview): void {
  useCadSolverFrameStore.getState().apply('wgi_1', preview);
}

describe('confirmDisplayedFrame', () => {
  beforeEach(() => resetCadSolverFrameStore());

  it('confirms the axis the card shows, and takes the server answer as the new frame', async () => {
    show(frame('+y'));
    const { calls, fetcher } = answering('+y');

    await expect(confirmDisplayedFrame('wgi_1', fetcher)).resolves.toBe('+y');

    expect(calls).toEqual([{
      url: '/api/cadlink/solver-frame', method: 'PUT', body: { ingestId: 'wgi_1', axis: '+y' },
    }]);
    expect(useCadSolverFrameStore.getState().frames.wgi_1.frame?.confirmed?.axis).toBe('+y');
  });

  it('confirms the user\'s pick rather than the preselection', async () => {
    show(frame('+y'));
    useCadSolverFrameStore.getState().pick('wgi_1', '-x');
    const { calls, fetcher } = answering('-x');

    await expect(confirmDisplayedFrame('wgi_1', fetcher)).resolves.toBe('-x');

    expect(calls.map(({ body }) => body)).toEqual([{ ingestId: 'wgi_1', axis: '-x' }]);
  });

  it('does not send an axis the project already confirmed', async () => {
    show(frame('+y', '+y'));
    const { calls, fetcher } = answering('+y');

    await expect(confirmDisplayedFrame('wgi_1', fetcher)).resolves.toBe('+y');

    expect(calls).toEqual([]);
  });

  it('sends nothing when the card never showed a frame, is loading, failed, or shows a linked model', async () => {
    const { calls, fetcher } = answering('+z');

    await expect(confirmDisplayedFrame('wgi_1', fetcher)).resolves.toBeNull();

    show({ linked: true });
    await expect(confirmDisplayedFrame('wgi_1', fetcher)).resolves.toBeNull();

    useCadSolverFrameStore.setState({ frames: { wgi_1: {
      ingestId: 'wgi_1', status: 'error', frame: frame('+z'), linked: false, axis: '+z', picked: false,
      changedFrom: null, error: 'unreachable',
    } } });
    await expect(confirmDisplayedFrame('wgi_1', fetcher)).resolves.toBeNull();

    expect(calls).toEqual([]);
  });

  it('refuses, and sends nothing, while the user still has to choose an axis', async () => {
    show(frame(null));
    const { calls, fetcher } = answering('+z');

    await expect(confirmDisplayedFrame('wgi_1', fetcher)).rejects.toThrow(/Choose which way/);

    expect(calls).toEqual([]);
  });
});
