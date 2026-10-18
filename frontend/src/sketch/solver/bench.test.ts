// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import { FrameReservoir, plannedFrames, ROW_BUDGET_MS, RUN_BUDGET_MS, runBench, type BenchMessage, type BenchRow, type FrameSample } from './bench';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
function workerFactory(complete: boolean, progress = false) {
  const workers: { postMessage: ReturnType<typeof vi.fn>; terminate: ReturnType<typeof vi.fn> }[] = [];
  const factory = () => {
    const worker = { onmessage: undefined as ((event: MessageEvent<BenchMessage>) => void) | undefined,
      terminate: vi.fn(), postMessage: vi.fn((request: Pick<BenchRow, 'solver' | 'n' | 'variant'>) => {
        if (complete || progress) queueMicrotask(() => worker.onmessage?.({ data: complete
          ? { kind: 'result', row: { ...request, status: 'complete', frames: plannedFrames(request.n), plannedFrames: plannedFrames(request.n),
            distribution: 'complete', dragScenarios: [], frameSamples: [], totalMs: null, failedFrames: 0, branchFlips: 0, branchCases: [] } }
          : { kind: 'progress', frames: 1, plannedFrames: plannedFrames(request.n), totalMs: { p50: 32, p95: 32, p99: 32, max: 32 },
            failedFrames: 0, branchFlips: 0, updateMs: null, solveMs: null, readbackMs: null, cursorError: null },
        } as MessageEvent<BenchMessage>));
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
it('terminates each stuck worker at 120 seconds and starts no more after 900 seconds', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
  vi.stubGlobal('navigator', { userAgent: 'unit-test' });
  const { factory, workers } = workerFactory(false, true);
  const renders: number[] = [];
  const running = runBench(() => renders.push(performance.now()), factory);
  await vi.advanceTimersByTimeAsync(ROW_BUDGET_MS - 1); expect(workers[0].terminate).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1); expect(workers[0].terminate).toHaveBeenCalledOnce();
  await vi.advanceTimersByTimeAsync(RUN_BUDGET_MS - ROW_BUDGET_MS + 300);
  const state = await running;
  expect(workers).toHaveLength(8); expect(workers.every(w => w.terminate.mock.calls.length === 1)).toBe(true);
  expect(state.results).toHaveLength(36); expect(state.results.every(r => r.status === 'timeout')).toBe(true);
  expect(state.results[0].frames).toBe(1); expect(state.results[0].frameSamples).toHaveLength(0);
  expect(state.results[0].totalMs?.p50).toBe(32); expect(state.results.every(r => r.distribution === 'partial')).toBe(true);
  expect(state.results.filter(r => r.reason?.includes('before this row'))).toHaveLength(28); expect(state.done).toBe(true);
  expect(renders.every((t, i) => i === 0 || t - renders[i - 1] >= 250)).toBe(true);
});
it('keeps telemetry storage bounded and plans complete drags for each size', () => {
  expect([8, 16, 24, 32, 60, 256].map(plannedFrames)).toEqual([300, 300, 300, 300, 120, 30]);
  const reservoir = new FrameReservoir();
  for (let frame = 0; frame < 10000; frame++) reservoir.add({ frame, totalMs: frame } as FrameSample);
  expect(reservoir.samples).toHaveLength(256);
  expect(Math.max(...reservoir.samples.map(f => f.frame))).toBeGreaterThan(9000);
});

it('unwraps sweep through pi and detects a branch crossing sampled exactly at zero', async () => {
  const { SweepTracker } = await import('./common');
  const { arc, point } = await import('./corpus');
  const sketch = { entities: [point('c', 0, 0), point('s', 1, 0), point('e', 0, 1), arc('arc', 'c', 's', 'e')], constraints: [] };
  const positions = (angle: number) => ({ c: { x: 0, y: 0 }, s: { x: 1, y: 0 }, e: { x: Math.cos(angle), y: Math.sin(angle) } });
  const tracker = new SweepTracker(sketch, positions(3));
  expect(tracker.update(positions(Math.PI))).toBe(0); expect(tracker.update(positions(3.3))).toBe(0);
  expect(tracker.sweeps.arc).toBeCloseTo(3.3);
  const zero = new SweepTracker(sketch, positions(.1));
  expect(zero.update(positions(0))).toBe(1); expect(zero.update(positions(-.1))).toBe(0);
  expect(zero.sweeps.arc).toBeCloseTo(-.1);
});

it('publishes bounded progress and a single final full-sample row from a real drag', async () => {
  const { measureRow } = await import('./bench');
  const messages: BenchMessage[] = [];
  const row = await measureRow('slvs', 8, 'underconstrained', message => messages.push(message));
  expect(row.status).toBe('complete'); expect(row.distribution).toBe('complete');
  expect(row.frameSamples).toHaveLength(300); expect(row.dragScenarios).toHaveLength(3);
  expect(row.dragScenarios.every(s => s.frames.length === 60)).toBe(true);
  expect(messages.filter(m => m.kind === 'result')).toHaveLength(1);
  for (const message of messages) if (message.kind === 'progress') {
    expect(Object.values(message).some(Array.isArray)).toBe(false);
    expect(JSON.stringify(message).length).toBeLessThan(2000);
  }
  expect(row.frameSamples.every(f => Number.isFinite(f.cursorError) && Number.isFinite(f.maxResidual))).toBe(true);
}, 20000);
