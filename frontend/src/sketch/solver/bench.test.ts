// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import { runBench, type BenchRow } from './bench';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
function workerFactory(complete: boolean, progress = false) {
  const workers: { postMessage: ReturnType<typeof vi.fn>; terminate: ReturnType<typeof vi.fn> }[] = [];
  const factory = () => {
    const worker = { onmessage: undefined as ((event: MessageEvent<BenchRow>) => void) | undefined,
      terminate: vi.fn(), postMessage: vi.fn((request: Pick<BenchRow, 'solver' | 'n' | 'variant'>) => {
        if (complete || progress) queueMicrotask(() => worker.onmessage?.({ data: { ...request, status: complete ? 'complete' : 'running', frames: complete ? 300 : 1,
          frameSamples: complete ? [] : [{ frame: 0, totalMs: 32, status: 'ok', iterations: 0, valid: true }], totalMs: null, failedFrames: 0, branchFlips: 0, branchCases: [] } } as unknown as MessageEvent<BenchRow>));
      }) };
    workers.push(worker); return worker as unknown as Worker;
  };
  return { factory, workers };
}
it('filters solver and size, retaining both variants unless explicitly filtered', async () => {
  vi.stubGlobal('navigator', { userAgent: 'unit-test' });
  const { factory, workers } = workerFactory(true);
  const state = await runBench(() => {}, factory, { solver: 'acs', n: 60 });
  expect(state.done).toBe(true); expect(state.results.map(r => [r.solver, r.n, r.variant])).toEqual([
    ['acs', 60, 'underconstrained'], ['acs', 60, 'constrained'],
  ]);
  expect(workers.every(w => w.terminate.mock.calls.length === 1)).toBe(true);
  const single = await runBench(() => {}, factory, { solver: 'slvs', n: 8, variant: 'constrained' });
  expect(single.results).toHaveLength(1);
});
it('terminates each stuck worker at 30 seconds and starts no more after 300 seconds', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
  vi.stubGlobal('navigator', { userAgent: 'unit-test' });
  const { factory, workers } = workerFactory(false, true);
  const running = runBench(() => {}, factory);
  await vi.advanceTimersByTimeAsync(29999); expect(workers[0].terminate).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1); expect(workers[0].terminate).toHaveBeenCalledOnce();
  await vi.advanceTimersByTimeAsync(270000);
  const state = await running;
  expect(workers).toHaveLength(10); expect(workers.every(w => w.terminate.mock.calls.length === 1)).toBe(true);
  expect(state.results).toHaveLength(36); expect(state.results.every(r => r.status === 'timeout')).toBe(true);
  expect(state.results[0].frames).toBe(1); expect(state.results[0].frameSamples[0].iterations).toBe(0);
  expect(state.results.filter(r => r.reason?.includes('before this row'))).toHaveLength(26); expect(state.done).toBe(true);
});
