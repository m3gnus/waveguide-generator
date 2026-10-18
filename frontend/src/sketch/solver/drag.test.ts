// @vitest-environment node
import { describe, expect, it, vi } from 'vitest';
import type { SlvsModule } from 'slvs';
import { GcsWrapper } from '@salusoft89/planegcs';
import { createSolver, solverNames } from './types';
import { branchCases, constraintCase, contourChainConstrained, diagnosticCases, residual } from './corpus';

const capture = vi.hoisted(() => ({ slvs: undefined as SlvsModule | undefined }));
vi.mock('slvs', async importOriginal => {
  const actual = await importOriginal<typeof import('slvs')>();
  return { ...actual, default: async (...args: unknown[]) => { capture.slvs = await (actual.default as (...a: unknown[]) => ReturnType<typeof actual.default>)(...args); return capture.slvs; } };
});

for (const name of solverNames) describe(`${name} drag session`, () => {
  it('updates goals without rebuilding; release removes goals and diagnoses', async () => {
    const s = await createSolver(name);
    const push = vi.spyOn(GcsWrapper.prototype, 'push_primitives_and_params');
    if (name === 'slvs') expect(capture.slvs).toBeDefined();
    const clear = capture.slvs && vi.spyOn(capture.slvs, 'clearSketch');
    try {
      s.load(diagnosticCases[0].sketch); s.beginDrag(['a', 'b']);
      const pushCount = push.mock.calls.length, clearCount = clear?.mock.calls.length;
      for (const delta of [1, 5, -3]) {
        // Reverse input order to check stable named-goal/handle mapping.
        const r = s.dragTo([{ pointId: 'b', x: 10 + delta, y: 2 }, { pointId: 'a', x: delta, y: 1 }]);
        expect(r.status).toBe('ok'); expect(r.dof).toBeUndefined(); expect(r.conflicting).toBeUndefined();
        expect(r.points.a.x).toBeCloseTo(delta, 5); expect(r.points.b.x).toBeCloseTo(10 + delta, 5);
        expect(r.points.a.y).toBeCloseTo(1, 5); expect(r.points.b.y).toBeCloseTo(2, 5);
      }
      expect(push.mock.calls.length).toBe(pushCount); expect(clear?.mock.calls.length).toBe(clearCount);
      const final = s.endDrag(); expect(final.status).toBe('ok'); expect(final.dof).toBe(4);
      expect(final.conflicting).toEqual([]);
    } finally { s.destroy(); push.mockRestore(); clear?.mockRestore(); }
  });
  it('constrained arc drags preserve equations and release DOF', async () => {
    const s = await createSolver(name), sample = branchCases.at(-1)!;
    try {
      s.load(sample.sketch); s.beginDrag(['e']);
      for (const target of sample.targets) {
        const r = s.dragTo([target]); expect(r.status).toBe('ok');
        for (const c of sample.sketch.constraints) expect(residual(sample.sketch, c, r.points)).toBeLessThan(1e-5);
      }
      expect(s.endDrag().dof).toBe(1);
    } finally { s.destroy(); }
  });
  it('rejects invalid lifecycle/targets, and load and solve cancel sessions', async () => {
    const s = await createSolver(name);
    try {
      expect(() => s.beginDrag(['b'])).toThrow('load()'); s.load(diagnosticCases[0].sketch);
      expect(() => s.dragTo([])).toThrow('beginDrag'); expect(() => s.endDrag()).toThrow('No active');
      expect(() => s.beginDrag(['missing'])).toThrow('Expected point');
      expect(() => s.beginDrag(['b', 'b'])).toThrow('unique');
      s.beginDrag(['b']); expect(() => s.beginDrag(['b'])).toThrow('already active');
      expect(() => s.dragTo([])).toThrow('each session point');
      expect(() => s.dragTo([{ pointId: 'a', x: 0, y: 0 }])).toThrow('each session point');
      expect(() => s.dragTo([{ pointId: 'b', x: NaN, y: 0 }])).toThrow('finite');
      s.load(contourChainConstrained(8).sketch); expect(() => s.dragTo([])).toThrow('beginDrag');
      s.beginDrag(['rim']); expect(s.solve({ mode: 'full' }).dof).toBe(4);
      expect(() => s.endDrag()).toThrow('No active'); s.destroy(); expect(() => s.beginDrag(['rim'])).toThrow('destroyed');
    } finally { s.destroy(); }
  });
});
it('slvs restores sessions after another instance uses or destroys its global sketch', async () => {
  const a = await createSolver('slvs'), b = await createSolver('slvs');
  try {
    a.load(diagnosticCases[0].sketch); a.beginDrag(['b']);
    expect(a.dragTo([{ pointId: 'b', x: 20, y: 3 }]).points.b.x).toBeCloseTo(20);
    b.load(diagnosticCases[7].sketch); expect(b.solve({ mode: 'full' }).dof).toBe(0); b.destroy();
    expect(a.dragTo([{ pointId: 'b', x: 25, y: 4 }]).points.b.x).toBeCloseTo(25);
    // Destroying a nonowner must not clear the live owner's handles.
    const c = await createSolver('slvs'); c.destroy();
    expect(a.dragTo([{ pointId: 'b', x: 30, y: 5 }]).points.b.y).toBeCloseTo(5);
    expect(a.endDrag().dof).toBe(4);
  } finally { a.destroy(); b.destroy(); }
});

it('slvs preserves the loaded unsigned-distance side through cursor fallback and rebuilds', async () => {
  const s = await createSolver('slvs');
  try {
    const sketch = constraintCase('pointLineDistance', 'opposite');
    s.load(sketch); s.beginDrag(['p']);
    for (const x of [5, 6, 7]) {
      const r = s.dragTo([{ pointId: 'p', x, y: 4 }]);
      expect(r.status).toBe('ok'); expect(r.points.p.y).toBeCloseTo(-3, 6);
      expect(residual(sketch, sketch.constraints[0], r.points)).toBeLessThan(1e-6);
    }
    expect(s.endDrag().points.p.y).toBeCloseTo(-3, 6);
  } finally { s.destroy(); }
});

it('slvs reports its heap and does not grow it across drag frames or rebuilds', async () => {
  const s = await createSolver('slvs'), sample = contourChainConstrained(8);
  const rim = sample.sketch.entities.find(e => e.kind === 'point' && e.id === 'rim') as { x: number };
  try {
    s.load(sample.sketch); s.beginDrag(['rim']);
    for (let f = 0; f < 200; f++) s.dragTo([{ pointId: 'rim', x: rim.x + Math.sin(f / 20), y: 0 }]);
    const before = s.memoryBytes(); expect(before).toBeGreaterThan(0);
    // The unpatched libslvs leaked ~11.5 KiB per drag frame and more per rebuild (vendor/slvs/BUILD.md).
    for (let f = 0; f < 1500; f++) s.dragTo([{ pointId: 'rim', x: rim.x + Math.sin(f / 20), y: 0 }]);
    s.endDrag();
    for (let i = 0; i < 200; i++) { s.load(sample.sketch); s.solve({ mode: 'full' }); }
    expect(s.memoryBytes()).toBe(before);
  } finally { s.destroy(); }
}, 20000);

it('slvs adapters share one instance until a trap retires it', async () => {
  const a = await createSolver('slvs'), first = capture.slvs!;
  const b = await createSolver('slvs'); expect(capture.slvs).toBe(first);
  const trap = vi.spyOn(first, 'solveSketch').mockImplementation(() => { throw new WebAssembly.RuntimeError('unreachable'); });
  try {
    a.load(diagnosticCases[0].sketch);
    expect(() => a.solve({ mode: 'full' })).toThrow(WebAssembly.RuntimeError);
  } finally { trap.mockRestore(); a.destroy(); b.destroy(); }
  const c = await createSolver('slvs');
  try {
    expect(capture.slvs).not.toBe(first);
    c.load(diagnosticCases[0].sketch); expect(c.solve({ mode: 'full' }).status).toBe('ok');
  } finally { c.destroy(); }
});
