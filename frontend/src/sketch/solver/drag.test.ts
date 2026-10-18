// @vitest-environment node
import { describe, expect, it, vi } from 'vitest';
import type { SlvsModule } from 'slvs';
import { GcsWrapper } from '@salusoft89/planegcs';
import * as acs from 'acs-solver';
import { createSolver, solverNames } from './types';
import { branchCases, contourChainConstrained, diagnosticCases, residual } from './corpus';

const capture = vi.hoisted(() => ({ slvs: undefined as SlvsModule | undefined }));
vi.mock('slvs', async importOriginal => {
  const actual = await importOriginal<typeof import('slvs')>();
  return { ...actual, default: async () => { capture.slvs = await actual.default(); return capture.slvs; } };
});
vi.mock('acs-solver', async importOriginal => {
  const actual = await importOriginal<typeof import('acs-solver')>();
  return { ...actual, acsSolveSketch: vi.fn(actual.acsSolveSketch) };
});

for (const name of solverNames) describe(`${name} drag session`, () => {
  it('updates goals without rebuilding; release removes goals and diagnoses', async () => {
    const s = await createSolver(name);
    const push = vi.spyOn(GcsWrapper.prototype, 'push_primitives_and_params');
    const clear = capture.slvs && vi.spyOn(capture.slvs, 'clearSketch');
    const nativeAcs = vi.mocked(acs.acsSolveSketch); nativeAcs.mockClear();
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
      if (name === 'acs') {
        const requests = nativeAcs.mock.calls.map(([json]) => JSON.parse(json));
        expect(requests).toHaveLength(3);
        expect(requests.every(r => r.analyze === false)).toBe(true);
        expect(requests[1].primitives.find((p: { id: string }) => p.id === 'e0').x).toBeCloseTo(1, 5);
        expect(requests[2].primitives.find((p: { id: string }) => p.id === 'e1').x).toBeCloseTo(15, 5);
      }
      const final = s.endDrag(); expect(final.status).toBe('ok'); expect(final.dof).toBe(4);
      expect(final.conflicting).toEqual([]);
      if (name === 'acs') {
        const request = JSON.parse(nativeAcs.mock.calls.at(-1)![0]);
        expect(request.analyze).toBe(true); expect(request.primitives.some((p: { temporary?: boolean }) => p.temporary)).toBe(false);
      }
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

it('ACS honors top-level analysis and iteration options', async () => {
  const initialized = await createSolver('acs'); initialized.destroy();
  const primitives = [{ type: 'point', id: 'a', x: 0, y: 0, fixed: true },
    { type: 'point', id: 'b', x: 10, y: 2 }, { type: 'distance', id: 'd', a: 'a', b: 'b', value: 7 }];
  const solve = (options: object) => JSON.parse(acs.acsSolveSketch(JSON.stringify({ version: 1, primitives, ...options })));
  expect(solve({ analyze: false }).dof).toBeUndefined(); expect(solve({ analyze: true }).dof).toBe(1);
  const limited = solve({ analyze: false, maxIterations: 1 });
  expect(limited.status).toBe('failed'); expect(limited.stats.iterations).toBe(1);
  const nested = solve({ options: { analyze: false, maxIterations: 1 } });
  expect(nested.status).toBe('converged'); expect(nested.dof).toBe(1); expect(nested.stats.iterations).toBeGreaterThan(1);
});
