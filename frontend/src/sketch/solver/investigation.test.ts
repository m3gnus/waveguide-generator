// @vitest-environment node
/** Opt-in, bounded evidence run: SKETCH_INVESTIGATE=timing npx --no-install vitest run src/sketch/solver/investigation.test.ts */
import { expect, it, vi } from 'vitest';
import { GcsWrapper, init_planegcs_module, type SketchPrimitive } from '@salusoft89/planegcs';
import * as acs from 'acs-solver';
import wasmUrl from '@salusoft89/planegcs/dist/planegcs_dist/planegcs.wasm?url';
import { wasmInput } from './wasm';
import { contourChain, contourChainConstrained } from './corpus';
import { createSolver, solverNames } from './types';
import { distribution } from './bench';

const probeProcess = (globalThis as { process?: { env?: Record<string, string | undefined>; stdout?: { write: (s: string) => void } } }).process;
const mode = probeProcess?.env?.SKETCH_INVESTIGATE;
const log = (label: string, value: unknown) => probeProcess?.stdout?.write(`${label} ${JSON.stringify(value)}\n`);
it.runIf(['timing', 'acs', 'gcs'].includes(mode ?? ''))('bounded drag, ACS options and planegcs allocation probes', async () => {
  const start = performance.now(), deadline = start + 105000;
  // Three completed frames per row, including the exactly satisfied first frame.
  if (mode === 'timing') for (const name of solverNames) for (const generate of [contourChain, contourChainConstrained]) {
    const s = await createSolver(name), sample = generate(60);
    const rim = sample.sketch.entities.find(p => p.id === 'rim'); if (rim?.kind !== 'point') throw new Error('rim');
    try {
      log('DRAG_START', { solver: name, corpus: generate.name });
      s.load(sample.sketch); const setup = performance.now(); s.beginDrag(['rim']); const setupMs = performance.now() - setup;
      const frames = [];
      for (let frame = 0; frame < 3 && performance.now() < deadline; frame++) {
        const r = s.dragTo([{ pointId: 'rim', x: rim.x + 10 * Math.sin(frame / 299 * 2 * Math.PI), y: 0 }]);
        frames.push({ frame, totalMs: r.timings.totalMs, solveMs: r.timings.solveMs, status: r.status, nativeStatus: r.nativeStatus, iterations: r.iterations });
        log('DRAG_FRAME', { solver: name, corpus: generate.name, ...frames.at(-1) });
      }
      log('DRAG_EVIDENCE', ({ solver: name, corpus: generate.name, n: 60, setupMs,
        p50: distribution(frames.map(f => f.totalMs))?.p50, frames }));
      expect(frames).toHaveLength(3); expect(frames.every(f => f.status === 'ok')).toBe(true);
    } finally { s.destroy(); }
  }
  if (mode === 'acs') {
    const initializedAcs = await createSolver('acs'); initializedAcs.destroy();
    const primitives = [
      { type: 'point', id: 'a', x: 0, y: 0, fixed: true }, { type: 'point', id: 'b', x: 10, y: 2 },
      { type: 'distance', id: 'd', a: 'a', b: 'b', value: 7 },
    ];
    for (const options of [{ analyze: false }, { analyze: true }, { analyze: false, maxIterations: 1 },
      { options: { analyze: false, maxIterations: 1 } }]) {
      const r = JSON.parse(acs.acsSolveSketch(JSON.stringify({ version: 1, primitives, ...options })));
      log('ACS_OPTIONS', ({ options, status: r.status, dof: r.dof, conflicting: r.conflicting, redundant: r.redundant, stats: r.stats }));
    }
    // Reproduce the old ACS n=256 sample: frame zero is a zero-motion solve.
    const s = await createSolver('acs'), sample = contourChain(256);
    const rim = sample.sketch.entities.find(p => p.id === 'rim'); if (rim?.kind !== 'point') throw new Error('rim');
    try {
      s.load(sample.sketch); s.beginDrag(['rim']);
      for (let frame = 0; frame < 2 && performance.now() < deadline; frame++) {
        log('ACS_256_START', { frame });
        const r = s.dragTo([{ pointId: 'rim', x: rim.x + 10 * Math.sin(frame / 299 * 2 * Math.PI), y: 0 }]);
        log('ACS_256', ({ frame, totalMs: r.timings.totalMs, status: r.status, nativeStatus: r.nativeStatus, iterations: r.iterations }));
      }
    } finally { s.destroy(); }
  }
  if (mode === 'gcs') {
    // Capture exactly the adapter's build without a solve, then use independent modules.
    // A native abort never poisons the cached module used by conformance tests.
    let built: SketchPrimitive[] = [];
    const push = vi.spyOn(GcsWrapper.prototype, 'push_primitives_and_params');
    const capture = await createSolver('planegcs');
    try { capture.load(contourChain(256).sketch); capture.beginDrag(['rim']); built = structuredClone(push.mock.calls.at(-1)![0]) as SketchPrimitive[]; }
    finally { capture.destroy(); push.mockRestore(); }
    const bytes = await wasmInput(wasmUrl); if (typeof bytes === 'string') throw new Error('Node bytes required');
    // The shipped module declares memory min=max=337 pages. This diagnostic-only
    // byte copy raises both to 2048 pages; installed files and production loading are untouched.
    const larger = new Uint8Array(bytes);
    let cursor = 8, found = false;
    const leb = () => { let v = 0, shift = 0, b; do { b = larger[cursor++]; v |= (b & 127) << shift; shift += 7; } while (b & 128); return v; };
    while (cursor < larger.length) {
      const section = larger[cursor++], size = leb(), end = cursor + size;
      if (section === 5) {
        expect(leb()).toBe(1); expect(leb()).toBe(1);
        const min = cursor; expect(leb()).toBe(337); const max = cursor; expect(leb()).toBe(337);
        larger.set([0x80, 0x10], min); larger.set([0x80, 0x10], max); found = true; break;
      }
      cursor = end;
    }
    expect(found).toBe(true);
    for (const [label, binary] of [['shipped', bytes], ['128MiB-diagnostic-copy', larger]] as const) {
      if (performance.now() > deadline) throw new Error('Probe budget exhausted');
      let memory: WebAssembly.Memory | undefined;
      const mod = await init_planegcs_module({ instantiateWasm: (imports: WebAssembly.Imports, done: (instance: WebAssembly.Instance) => void) => {
        const instance = new WebAssembly.Instance(new WebAssembly.Module(new Uint8Array(binary)), imports);
        memory = Object.values(instance.exports).find(e => e instanceof WebAssembly.Memory) as WebAssembly.Memory;
        done(instance); return instance.exports;
      } });
      const w = new GcsWrapper(new mod.GcsSystem(), mod);
      let phase = 'push'; const t = performance.now();
      try {
        const rim = contourChain(256).sketch.entities.find(p => p.id === 'rim'); if (rim?.kind !== 'point') throw new Error('rim');
        w.push_sketch_param('goal0x', rim.x); w.push_sketch_param('goal0y', rim.y);
        w.push_primitives_and_params(structuredClone(built));
        log('GCS_MEMORY', ({ label, phase, bytes: memory?.buffer.byteLength, params: w.gcs.params_size() }));
        phase = 'first solve'; const status = w.solve(); w.apply_solution();
        log('GCS_MEMORY', ({ label, phase, status, totalMs: performance.now() - t, bytes: memory?.buffer.byteLength }));
        phase = 'second solve'; w.set_sketch_param('goal0x', rim.x + .21);
        const second = w.solve(); w.apply_solution();
        log('GCS_MEMORY', ({ label, phase, status: second, totalMs: performance.now() - t, bytes: memory?.buffer.byteLength }));
      } catch (e) { log('GCS_MEMORY', ({ label, phase, error: String(e), bytes: memory?.buffer.byteLength })); }
      finally { try { w.destroy_gcs_module(); } catch { /* An aborted experimental module is discarded. */ } }
    }
  }
  log('PROBE_TOTAL_MS', performance.now() - start);
}, 110000);
