import { arcResidual, branchCases, contourChain, contourChainConstrained, diagnosticCases, residual } from './corpus';
import { orientation, pointsOf } from './common';
import { createSolver, solverNames, type SolverName, type XY } from './types';
export interface Distribution { p50: number; p95: number; p99: number; max: number }
export function distribution(samples: number[]): Distribution | null {
  if (!samples.length) return null;
  const sorted = [...samples].sort((a, b) => a - b);
  const q = (p: number) => sorted[Math.max(0, Math.ceil(p * sorted.length) - 1)];
  return { p50: q(.5), p95: q(.95), p99: q(.99), max: sorted[sorted.length - 1] };
}
export type CorpusVariant = 'underconstrained' | 'constrained';
export interface FrameSample {
  frame: number; totalMs: number; status: string; nativeStatus?: string | number; iterations?: number; valid: boolean;
}
export interface BenchRow {
  solver: SolverName; n: number; variant: CorpusVariant; setupMs?: number; frameSamples: FrameSample[]; status: 'running' | 'complete' | 'timeout' | 'error'; reason?: string;
  coldStartMs?: number; frames: number; totalMs: Distribution | null; failedFrames: number; branchFlips: number;
  diagnosisMs?: number; diagnosisSolveMs?: number; diagnosisStatus?: string; dof?: number;
  memory?: { source: 'performance.memory' | 'wasm-buffer' | 'unavailable'; before?: number; after?: number; cycles: number };
  branchCases: { name: string; failedFrames: number; flips: number }[];
}
export interface BenchState {
  done: boolean; results: BenchRow[]; startedAt: string; userAgent: string;
  notes: string[];
}
/** Capture memories only inside the dedicated benchmark worker. No app runtime is patched. */
function observeWasmMemory() {
  const memories = new Set<WebAssembly.Memory>(), original = WebAssembly.instantiate;
  const record = (instance: WebAssembly.Instance) => {
    for (const e of Object.values(instance.exports)) if (e instanceof WebAssembly.Memory) memories.add(e);
  };
  // Both overloads are preserved. Streaming instances are observed via the equivalent
  // ArrayBuffer route, which also lets cold-start measurements include actual compilation.
  WebAssembly.instantiate = (async (source: BufferSource | WebAssembly.Module, imports?: WebAssembly.Imports) => {
    if (source instanceof WebAssembly.Module) { const instance = await original(source, imports); record(instance); return instance; }
    const result = await original(source, imports); record(result.instance); return result;
  }) as typeof WebAssembly.instantiate;
  const streaming = WebAssembly.instantiateStreaming;
  WebAssembly.instantiateStreaming = async (response, imports) => {
    const r = await streaming(response, imports); record(r.instance); return r;
  };
  return { bytes: () => memories.size ? [...memories].reduce((n, m) => n + m.buffer.byteLength, 0) : undefined,
    restore: () => { WebAssembly.instantiate = original; WebAssembly.instantiateStreaming = streaming; } };
}
function memorySample(wasmBytes: () => number | undefined): { source: NonNullable<BenchRow['memory']>['source']; value?: number } {
  const memory = (performance as Performance & { memory?: { usedJSHeapSize: number } }).memory;
  if (memory) return { source: 'performance.memory', value: memory.usedJSHeapSize };
  const value = wasmBytes(); return { source: value === undefined ? 'unavailable' : 'wasm-buffer', value };
}
/** Run in a fresh worker for each row, giving each solver × size a genuine import/init cold start. */
export async function measureRow(solver: SolverName, n: number, variant: CorpusVariant, update: (row: BenchRow) => void): Promise<BenchRow> {
  const row: BenchRow = { solver, n, variant, frameSamples: [], status: 'running', frames: 0, totalMs: null, failedFrames: 0, branchFlips: 0, branchCases: [] };
  const watch = observeWasmMemory();
  let s: Awaited<ReturnType<typeof createSolver>> | undefined;
  try {
    const start = performance.now(); s = await createSolver(solver); row.coldStartMs = performance.now() - start;
    const sample = (variant === 'constrained' ? contourChainConstrained : contourChain)(n), sketch = sample.sketch, rim = sketch.entities.find(e => e.id === 'rim');
    if (rim?.kind !== 'point') throw new Error('Missing rim point');
    s.load(sketch);
    const setup = performance.now(); s.beginDrag(['rim']); row.setupMs = performance.now() - setup;
    update(structuredClone(row));
    let previous: Record<string, XY> = pointsOf(sketch);
    const times: number[] = [];
    const flips = (next: Record<string, XY>, prev: Record<string, XY>, model = sketch) => model.entities
      .filter(e => e.kind === 'arc').filter(a => orientation(a, next) * orientation(a, prev) < 0).length;
    for (let frame = 0; frame < 300; frame++) {
      const theta = frame / 299 * 2 * Math.PI;
      const r = s.dragTo([{ pointId: 'rim', x: rim.x + 10 * Math.sin(theta), y: 0 }]);
      times.push(r.timings.totalMs); row.frames++;
      const valid = r.status === 'ok' && Object.values(r.points).every(p => Number.isFinite(p.x) && Number.isFinite(p.y))
        && sketch.constraints.every(c => residual(sketch, c, r.points) < 1e-4)
        && sketch.entities.every(a => a.kind !== 'arc' || arcResidual(sketch, a, r.points) < 1e-4);
      row.frameSamples.push({ frame, totalMs: r.timings.totalMs, status: r.status, nativeStatus: r.nativeStatus, iterations: r.iterations, valid });
      if (!valid) row.failedFrames++;
      row.branchFlips += flips(r.points, previous); previous = r.points;
      // Publish every completed frame: a slow next call must not hide samples at timeout.
      row.totalMs = distribution(times); update(structuredClone(row));
    }
    row.totalMs = distribution(times);
    const diagnosis = s.endDrag();
    row.diagnosisMs = diagnosis.timings.totalMs; row.diagnosisSolveMs = diagnosis.timings.solveMs;
    row.diagnosisStatus = diagnosis.status; row.dof = diagnosis.dof; s.destroy(); s = undefined;
    for (const sample of branchCases) {
      s = await createSolver(solver); s.load(sample.sketch); s.beginDrag([...new Set(sample.targets.map(t => t.pointId))]);
      let prev = pointsOf(sample.sketch); const b = { name: sample.name, failedFrames: 0, flips: 0 };
      for (const target of sample.targets) {
        const r = s.dragTo([target]);
        if (r.status !== 'ok') b.failedFrames++;
        b.flips += flips(r.points, prev, sample.sketch); prev = r.points;
      }
      row.branchCases.push(b); s.destroy(); s = undefined;
    }
    // Small constrained arc per lifecycle cycle exercises allocation and native cleanup,
    // without repeating a 256-segment solve 1,000 times. No forced GC is requested.
    const before = memorySample(watch.bytes);
    row.memory = { source: before.source, before: before.value, cycles: 0 };
    for (let i = 0; i < 1000; i++) {
      s = await createSolver(solver); s.load(branchCases[0].sketch); s.solve({ mode: 'full' }); s.destroy(); s = undefined;
      row.memory.cycles++;
      if (i % 50 === 0) update(structuredClone(row));
    }
    row.memory.after = memorySample(watch.bytes).value;
    row.status = 'complete';
  } catch (e) { row.status = 'error'; row.reason = String(e); }
  finally { s?.destroy(); watch.restore(); }
  update(row); return row;
}
/** One worker at a time; a parent wall-clock watchdog can terminate even a stuck synchronous solve. */
export async function runBench(update: (state: BenchState) => void, workerFactory: () => Worker, filters: { solver?: SolverName; n?: number; variant?: CorpusVariant } = {}): Promise<BenchState> {
  const state: BenchState = { done: false, results: [], startedAt: new Date().toISOString(), userAgent: navigator.userAgent,
    notes: ['Each row uses a fresh worker/module cache. Cold start includes adapter/package import and WASM initialization.',
      'Drag sessions build once; setupMs is separate. totalMs includes target update, native solve and point readback; no rendering cost.',
      'Native branch flips are observed, without continuation or branch correction.',
      'Memory cycles load and solve a small arc; buffers measure capacity, not live allocations; GC is uncontrolled.',
      'The complete run has a 300 second wall-clock budget; unfinished rows are explicitly marked timeout.'] };
  const deadline = performance.now() + 300000;
  for (const solver of solverNames.filter(s => !filters.solver || s === filters.solver))
    for (const n of [8, 16, 24, 32, 60, 256].filter(n => !filters.n || n === filters.n))
      for (const variant of (['underconstrained', 'constrained'] as const).filter(v => !filters.variant || v === filters.variant)) {
    const remaining = deadline - performance.now();
    const row: BenchRow = { solver, n, variant, frameSamples: [], status: 'running', frames: 0, totalMs: null, failedFrames: 0, branchFlips: 0, branchCases: [] };
    const slot = state.results.length; state.results.push(row);
    if (remaining <= 0) { row.status = 'timeout'; row.reason = 'Run budget exhausted before this row started'; update(structuredClone(state)); continue; }
    await new Promise<void>(resolve => {
      const worker = workerFactory();
      const finish = () => { clearTimeout(timer); worker.terminate(); resolve(); };
      const timer = setTimeout(() => {
        state.results[slot].status = 'timeout'; state.results[slot].reason = 'Worker terminated at wall-clock deadline';
        finish(); update(structuredClone(state));
      }, Math.min(remaining, 30000));
      worker.onmessage = (event: MessageEvent<BenchRow>) => {
        state.results[slot] = event.data; update(structuredClone(state));
        if (event.data.status !== 'running') finish();
      };
      worker.onerror = e => { state.results[slot].status = 'error'; state.results[slot].reason = e.message;
        finish(); update(structuredClone(state)); };
      worker.postMessage({ solver, n, variant });
    });
  }
  state.done = true; update(state); return state;
}
// Keep a tiny plain-line fixture exported for automation that needs a cheap load.
export const lifecycleSketch = diagnosticCases[0].sketch;
