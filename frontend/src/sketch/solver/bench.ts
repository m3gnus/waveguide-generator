import { branchCases, contourChain, contourChainConstrained, diagnosticCases } from './corpus';
import { SweepTracker } from './common';
import { compareTrajectory, dragScenarioNames, measureDragScenario, residuals, maximum, type DragMeasurement } from './drag-scenarios';
import { createSolver, solverNames, type SolverName } from './types';
export interface Distribution { p50: number; p95: number; p99: number; max: number }
export function distribution(samples: number[]): Distribution | null {
  if (!samples.length) return null;
  const sorted = [...samples].sort((a, b) => a - b);
  const q = (p: number) => sorted[Math.max(0, Math.ceil(p * sorted.length) - 1)];
  return { p50: q(.5), p95: q(.95), p99: q(.99), max: sorted[sorted.length - 1] };
}
export type CorpusVariant = 'underconstrained' | 'constrained';
export interface FrameSample {
  frame: number; totalMs: number; updateMs: number; solveMs: number; readbackMs: number; cursorError: number; maxResidual: number; status: string; nativeStatus?: string | number; iterations?: number; valid: boolean;
}
export interface BenchRow {
  solver: SolverName; n: number; variant: CorpusVariant; setupMs?: number; frameSamples: FrameSample[]; status: 'running' | 'complete' | 'timeout' | 'error'; reason?: string;
  plannedFrames: number; distribution: 'partial' | 'complete'; dragScenarios: DragMeasurement[];
  cursorError: Distribution | null; updateMs: Distribution | null; solveMs: Distribution | null; readbackMs: Distribution | null;
  coldStartMs?: number; frames: number; totalMs: Distribution | null; failedFrames: number; branchFlips: number;
  diagnosisMs?: number; diagnosisSolveMs?: number; diagnosisStatus?: string; dof?: number;
  memory?: { source: 'performance.memory' | 'wasm-buffer' | 'unavailable'; before?: number; after?: number; cycles: number };
  branchCases: { name: string; failedFrames: number; flips: number }[];
}
export interface BenchState {
  done: boolean; results: BenchRow[]; startedAt: string; userAgent: string;
  notes: string[];
}
export const ROW_BUDGET_MS = 120000, RUN_BUDGET_MS = 900000;
export const plannedFrames = (n: number) => n <= 32 ? 300 : n === 60 ? 120 : 30;
export interface Progress {
  kind: 'progress'; frames: number; plannedFrames: number; coldStartMs?: number; setupMs?: number;
  failedFrames: number; branchFlips: number;
  /** Percentiles of a deterministic uniform reservoir of at most 256 frames. */
  totalMs: Distribution | null; updateMs: Distribution | null; solveMs: Distribution | null;
  readbackMs: Distribution | null; cursorError: Distribution | null;
}
export type BenchMessage = Progress | { kind: 'result'; row: BenchRow };
export class FrameReservoir {
  readonly samples: FrameSample[] = [];
  private count = 0;
  private seed = 1;
  add(sample: FrameSample) {
    this.count++;
    if (this.samples.length < 256) this.samples.push(sample);
    else {
      this.seed = (Math.imul(this.seed, 1664525) + 1013904223) >>> 0;
      const index = Math.floor(this.seed / 4294967296 * this.count);
      if (index < 256) this.samples[index] = sample;
    }
  }
}
function summaries(samples: FrameSample[]) {
  return { totalMs: distribution(samples.map(f => f.totalMs)), updateMs: distribution(samples.map(f => f.updateMs)),
    solveMs: distribution(samples.map(f => f.solveMs)), readbackMs: distribution(samples.map(f => f.readbackMs)),
    cursorError: distribution(samples.map(f => f.cursorError)) };
}
function emptyRow(solver: SolverName, n: number, variant: CorpusVariant): BenchRow {
  return { solver, n, variant, plannedFrames: plannedFrames(n), distribution: 'partial', dragScenarios: [],
    ...summaries([]), frameSamples: [], status: 'running', frames: 0, failedFrames: 0, branchFlips: 0, branchCases: [] };
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
export async function measureRow(solver: SolverName, n: number, variant: CorpusVariant, update: (message: BenchMessage) => void): Promise<BenchRow> {
  const row = emptyRow(solver, n, variant), reservoir = new FrameReservoir();
  const deadline = performance.now() + ROW_BUDGET_MS;
  const progress = () => update({ kind: 'progress', frames: row.frames, plannedFrames: row.plannedFrames,
    coldStartMs: row.coldStartMs, setupMs: row.setupMs, failedFrames: row.failedFrames,
    branchFlips: row.branchFlips, ...summaries(reservoir.samples) });
  const watch = observeWasmMemory();
  let s: Awaited<ReturnType<typeof createSolver>> | undefined;
  try {
    const start = performance.now(); s = await createSolver(solver); row.coldStartMs = performance.now() - start;
    const sample = (variant === 'constrained' ? contourChainConstrained : contourChain)(n), sketch = sample.sketch, rim = sketch.entities.find(e => e.id === 'rim');
    if (rim?.kind !== 'point') throw new Error('Missing rim point');
    s.load(sketch);
    const setup = performance.now(); s.beginDrag(['rim']); row.setupMs = performance.now() - setup;
    progress();
    const tracker = new SweepTracker(sketch);
    for (let frame = 0; frame < row.plannedFrames; frame++) {
      if (performance.now() >= deadline) { row.status = 'timeout'; row.reason = 'Row budget expired between frames'; break; }
      const theta = frame / (row.plannedFrames - 1) * 2 * Math.PI;
      const target = { pointId: 'rim', x: rim.x + 10 * Math.sin(theta), y: 0 };
      const r = s.dragTo([target]); row.frames++;
      const maxResidual = maximum(Object.values(residuals(sketch, r.points)));
      const valid = r.status === 'ok' && Number.isFinite(maxResidual) && maxResidual < 1e-4;
      const f: FrameSample = { frame, ...r.timings, status: r.status, nativeStatus: r.nativeStatus,
        iterations: r.iterations, valid, maxResidual, cursorError: Math.hypot(r.points.rim.x - target.x, r.points.rim.y - target.y) };
      row.frameSamples.push(f); reservoir.add(f);
      if (!valid) row.failedFrames++;
      row.branchFlips += tracker.update(r.points);
      progress();
    }
    row.distribution = row.frames === row.plannedFrames ? 'complete' : 'partial';
    Object.assign(row, summaries(row.frameSamples));
    if (row.status === 'timeout') return row;
    const diagnosis = s.endDrag();
    row.diagnosisMs = diagnosis.timings.totalMs; row.diagnosisSolveMs = diagnosis.timings.solveMs;
    row.diagnosisStatus = diagnosis.status; row.dof = diagnosis.dof; s.destroy(); s = undefined;
    for (const sample of branchCases) {
      s = await createSolver(solver); s.load(sample.sketch); s.beginDrag([...new Set(sample.targets.map(t => t.pointId))]);
      const branches = new SweepTracker(sample.sketch); const b = { name: sample.name, failedFrames: 0, flips: 0 };
      for (const target of sample.targets) {
        const r = s.dragTo([target]);
        if (r.status !== 'ok') b.failedFrames++;
        b.flips += branches.update(r.points);
      }
      row.branchCases.push(b); s.destroy(); s = undefined;
    }
    if (n === 8 || n === 24) {
      s = await createSolver(solver);
      for (const name of dragScenarioNames) {
        if (performance.now() >= deadline) throw new Error('Row budget expired before drag scenarios completed');
        row.dragScenarios.push(measureDragScenario(s, n, name));
      }
      s.destroy(); s = undefined;
    }
    // Small constrained arc per lifecycle cycle exercises allocation and native cleanup,
    // without repeating a 256-segment solve 1,000 times. No forced GC is requested.
    const before = memorySample(watch.bytes);
    row.memory = { source: before.source, before: before.value, cycles: 0 };
    for (let i = 0; i < 1000; i++) {
      s = await createSolver(solver); s.load(branchCases[0].sketch); s.solve({ mode: 'full' }); s.destroy(); s = undefined;
      row.memory.cycles++;
      if (performance.now() >= deadline) throw new Error('Row budget expired during memory cycles');
      if (i % 50 === 0) progress();
    }
    row.memory.after = memorySample(watch.bytes).value;
    row.status = 'complete';
  } catch (e) { row.status = performance.now() >= deadline ? 'timeout' : 'error'; row.reason = String(e); }
  finally { s?.destroy(); watch.restore();
    row.distribution = row.frames === row.plannedFrames ? 'complete' : 'partial';
    Object.assign(row, summaries(row.frameSamples));
    update({ kind: 'result', row });
  }
  return row;
}
/** One worker at a time; a parent wall-clock watchdog can terminate even a stuck synchronous solve. */
export async function runBench(update: (state: BenchState) => void, workerFactory: () => Worker, filters: { solver?: SolverName; n?: number; variant?: CorpusVariant } = {}): Promise<BenchState> {
  const state: BenchState = { done: false, results: [], startedAt: new Date().toISOString(), userAgent: navigator.userAgent,
    notes: ['Each row uses a fresh worker/module cache. Cold start includes adapter/package import and WASM initialization.',
      'Drag sessions build once; setupMs is separate. totalMs includes target update, native solve and point readback; no rendering cost.',
      'Native branch flips are observed, without continuation or branch correction.',
      'Memory cycles load and solve a small arc; buffers measure capacity, not live allocations; GC is uncontrolled.',
      'Row budget 120 s; total run budget 900 s. Complete drags use 300/120/30 frames at n<=32/60/256.',
      'Progress contains only counts and percentiles from a fixed 256-frame reservoir; final distributions use all samples.',
      'Hard worker termination retains counts and reservoir estimates; full samples are unavailable if the worker cannot return.',
      'Reachable, unreachable and 60-frame trajectory scenarios run at n=8 and 24; trajectory reference is planegcs for the same variant.'] };
  const deadline = performance.now() + RUN_BUDGET_MS;
  let lastRender = -Infinity, scheduled: ReturnType<typeof setTimeout> | undefined;
  const publish = () => {
    if (scheduled !== undefined) return;
    scheduled = setTimeout(() => { scheduled = undefined; lastRender = performance.now(); update(state); },
      Math.max(0, 250 - (performance.now() - lastRender)));
  };
  for (const solver of solverNames.filter(s => !filters.solver || s === filters.solver))
    for (const n of [8, 16, 24, 32, 60, 256].filter(n => !filters.n || n === filters.n))
      for (const variant of (['underconstrained', 'constrained'] as const).filter(v => !filters.variant || v === filters.variant)) {
    const remaining = deadline - performance.now();
    const row = emptyRow(solver, n, variant);
    const slot = state.results.length; state.results.push(row);
    if (remaining <= 0) { row.status = 'timeout'; row.reason = 'Run budget exhausted before this row started'; publish(); continue; }
    await new Promise<void>(resolve => {
      const worker = workerFactory();
      const finish = () => { clearTimeout(timer); worker.terminate(); resolve(); };
      const timer = setTimeout(() => {
        state.results[slot].status = 'timeout'; state.results[slot].reason = 'Worker terminated at wall-clock deadline';
        finish(); publish();
      }, Math.min(remaining, ROW_BUDGET_MS));
      worker.onmessage = (event: MessageEvent<BenchMessage>) => {
        if (event.data.kind === 'result') {
          const completed = event.data.row;
          state.results[slot] = completed;
          const trajectory = completed.dragScenarios.find(s => s.name === 'trajectory');
          const reference = state.results.find(r => r.solver === 'planegcs' && r.n === n && r.variant === variant)
            ?.dragScenarios.find(s => s.name === 'trajectory');
          if (trajectory && reference) compareTrajectory(trajectory, reference);
          finish();
        } else {
          const { kind: _kind, ...summary } = event.data;
          Object.assign(state.results[slot], summary);
          // Progress distributions remain explicitly partial until the final full-sample result.
        }
        publish();
      };
      worker.onerror = e => { state.results[slot].status = 'error'; state.results[slot].reason = e.message;
        finish(); publish(); };
      worker.postMessage({ solver, n, variant });
    });
  }
  state.done = true; publish();
  // Flush through the same rate limit, including the final render.
  await new Promise<void>(resolve => {
    const poll = () => scheduled === undefined ? resolve() : setTimeout(poll, 10);
    poll();
  });
  return state;
}
// Keep a tiny plain-line fixture exported for automation that needs a cheap load.
export const lifecycleSketch = diagnosticCases[0].sketch;
