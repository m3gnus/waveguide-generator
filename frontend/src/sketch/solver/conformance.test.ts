// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { arcResidual, branchCases, constraintCase, contourChain, contourChainConstrained, diagnosticCases, residual, arc, point } from './corpus';
import { pointsOf } from './common';
import { constraintKinds, createSolver, solverNames, type Sketch, type SketchSolver } from './types';

for (const name of solverNames) {
  const probe = await createSolver(name), caps = probe.capabilities(); probe.destroy();
  const withSolver = async (sketch: Sketch, fn: (solver: SketchSolver) => void) => {
    const solver = await createSolver(name);
    try { solver.load(sketch); fn(solver); } finally { solver.destroy(); }
  };
  const supported = (kind: typeof constraintKinds[number], label: string, fn: () => Promise<void>) => {
    const c = caps.constraints[kind];
    if (c.support === 'unsupported') it.skip(`${label} — ${c.detail}`, fn); else it(label, fn);
  };
  describe(name, () => {
    for (const kind of constraintKinds) supported(kind, `${kind} residual`, () => {
      const sketch = constraintCase(kind);
      return withSolver(sketch, s => {
        const r = s.solve({ mode: 'full' });
        expect(r.status, r.reason).toBe('ok');
        for (const c of sketch.constraints) expect(residual(sketch, c, r.points)).toBeLessThan(1e-5);
        for (const a of sketch.entities) if (a.kind === 'arc') expect(arcResidual(sketch, a, r.points)).toBeLessThan(1e-5);
        for (const e of sketch.entities) if (e.kind === 'point' && e.fixed) expect(r.points[e.id]).toEqual({ x: e.x, y: e.y });
        expect(r.timings.totalMs).toBeGreaterThanOrEqual(r.timings.solveMs);
      });
    });
    for (const kind of ['horizontal', 'vertical'] as const) supported(kind, `${kind} point pair`, () => {
      const sketch = constraintCase(kind, 'pair');
      return withSolver(sketch, s => { const r = s.solve({ mode: 'full' }); expect(r.status).toBe('ok');
        expect(residual(sketch, sketch.constraints[0], r.points)).toBeLessThan(1e-5); });
    });
    for (const kind of ['distanceX', 'distanceY'] as const) supported(kind, `${kind} signed negative`, () => {
      const sketch = constraintCase(kind, 'negative');
      return withSolver(sketch, s => { const r = s.solve({ mode: 'full' }); expect(r.status).toBe('ok');
        expect(residual(sketch, sketch.constraints[0], r.points)).toBeLessThan(1e-5); });
    });
    supported('equal', 'equal arc radii', () => {
      const sketch: Sketch = { entities: [point('c', 0, 0, true), point('s', 10, 0), point('e', 0, 10), arc('a', 'c', 's', 'e'),
        point('c2', 30, 0, true), point('s2', 35, 0), point('e2', 30, 5), arc('b', 'c2', 's2', 'e2')],
        constraints: [{ id: 'radii', kind: 'equal', a: 'a', b: 'b' }] };
      return withSolver(sketch, s => { const r = s.solve({ mode: 'full' }); expect(r.status).toBe('ok');
        expect(residual(sketch, sketch.constraints[0], r.points)).toBeLessThan(1e-5); });
    });
    supported('tangent', 'arc–arc tangency at a shared endpoint', () => {
      const sketch: Sketch = { entities: [point('c', 0, 0, true), point('s', 10, 0, true), point('e', 0, 10),
        point('c2', 20, 1), point('e2', 20, -10), arc('a', 'c', 'e', 's'), arc('b', 'c2', 's', 'e2')],
        constraints: [{ id: 'tangent', kind: 'tangent', a: 'a', b: 'b' }] };
      return withSolver(sketch, s => { const r = s.solve({ mode: 'full' }); expect(r.status).toBe('ok');
        expect(residual(sketch, sketch.constraints[0], r.points)).toBeLessThan(1e-5); });
    });
    supported('pointOnArc', 'pointOnArc excludes the complementary circle span', () => {
      const sketch: Sketch = { entities: [point('c', 0, 0, true), point('s', 10, 0, true), point('e', 0, 10, true),
        point('p', -9, -1), arc('a', 'c', 's', 'e')], constraints: [{ id: 'span', kind: 'pointOnArc', point: 'p', arc: 'a' }] };
      return withSolver(sketch, s => { const r = s.solve({ mode: 'full' }); expect(r.status).toBe('ok');
        expect(residual(sketch, sketch.constraints[0], r.points)).toBeLessThan(1e-5); });
    });
    for (const sample of diagnosticCases) {
      const missing = sample.sketch.constraints.find(c => caps.constraints[c.kind].support === 'unsupported');
      const run = () => withSolver(sample.sketch, s => {
        const r = s.solve({ mode: 'full' });
        if (!sample.expected.valid) expect(r.status).toBe('failed');
        if (sample.expected.dof !== undefined) expect(r.dof, sample.expected.note).toBe(sample.expected.dof);
        const allowed = [...sample.sketch.entities, ...sample.sketch.constraints].map(e => e.id);
        for (const id of [...r.conflicting ?? [], ...r.redundant ?? [], ...r.partiallyRedundant ?? []]) expect(allowed).toContain(id);
      });
      if (missing) it.skip(`${sample.name} DOF — ${caps.constraints[missing.kind].detail}`, run);
      // Four coordinates minus the rank-one equation y_a - y_b = 0 = 3 DOF.
      // slvs 3.2.0 reports 2 for two copies; retain the native rank defect as an expected failure.
      else if (name === 'slvs' && sample.name === 'duplicate constraint') it.fails(`${sample.name} DOF`, run);
      else it(`${sample.name} DOF`, run);
      if (sample.expected.conflicting.length) {
        const test = () => withSolver(sample.sketch, s => {
          const r = s.solve({ mode: 'full' });
          // Native conflict sets need not contain every member; each independent group must be represented.
          expect(r.conflicting?.length).toBeGreaterThan(0);
          for (const id of r.conflicting ?? []) expect(sample.expected.conflicting).toContain(id);
          if (sample.name === 'two independent conflicts') {
            expect(r.conflicting?.some(id => id.startsWith('ab'))).toBe(true);
            expect(r.conflicting?.some(id => id.startsWith('cd'))).toBe(true);
          }
        });
        if (caps.diagnostics.conflicting.support === 'unsupported') it.skip(`${sample.name} conflicts — ${caps.diagnostics.conflicting.detail}`, test);
        else it(`${sample.name} conflicting user IDs`, test);
      }
      for (const key of ['redundant', 'partiallyRedundant'] as const) if (sample.expected[key]) {
        const test = () => withSolver(sample.sketch, s => {
          const r = s.solve({ mode: 'full' });
          // A rank basis can discard the scalar horizontal equation entirely, or mark
          // the two-equation coincidence partially redundant. Both diagnoses are exact.
          if (sample.name === 'partial redundancy' && key === 'partiallyRedundant' && !r[key]?.length) {
            expect(r.redundant).toContain('h');
          } else {
            expect(r[key]?.length).toBeGreaterThan(0);
            for (const id of r[key] ?? []) expect(sample.expected[key]).toContain(id);
          }
        });
        if (caps.diagnostics[key].support === 'unsupported') it.skip(`${sample.name} ${key} — ${caps.diagnostics[key].detail}`, test);
        else it(`${sample.name} ${key} user IDs`, test);
      }
    }
    for (const sample of branchCases) it(`${sample.name}: CCW endpoint order and radius survive drag`, () => withSolver(sample.sketch, s => {
      for (const target of sample.targets) {
        const r = s.solve({ mode: 'drag', dragTargets: [target] }); expect(r.status).toBe('ok');
        for (const a of sample.sketch.entities) if (a.kind === 'arc') expect(arcResidual(sample.sketch, a, r.points)).toBeLessThan(1e-5);
        for (const c of sample.sketch.constraints) expect(residual(sample.sketch, c, r.points)).toBeLessThan(1e-5);
        for (const e of sample.sketch.entities) if (e.kind === 'point' && e.fixed) expect(r.points[e.id]).toEqual({ x: e.x, y: e.y });
        // First three cases pin the cursor on the original branch, on either side of pi.
        if (sample.targets.length === 1) expect(Math.hypot(r.points.e.x - target.x, r.points.e.y - target.y)).toBeLessThan(1e-4);
      }
    }));
    it('8 segment contour is initially exact and solves', () => {
      const sample = contourChain(8), pts = pointsOf(sample.sketch);
      for (const c of sample.sketch.constraints) expect(residual(sample.sketch, c, pts)).toBeLessThan(1e-6);
      return withSolver(sample.sketch, s => { const r = s.solve({ mode: 'full' }); expect(r.status, r.reason).toBe('ok');
        expect(r.dof).toBe(sample.expected.dof);
        for (const c of sample.sketch.constraints) expect(residual(sample.sketch, c, r.points)).toBeLessThan(1e-5); });
    });
    for (const n of [8, 60]) it(`mostly dimensioned ${n}-segment contour has four DOF`, () => {
      const sample = contourChainConstrained(n);
      return withSolver(sample.sketch, s => {
        const r = s.solve({ mode: 'full' }); expect(r.status).toBe('ok'); expect(r.dof).toBe(4);
        for (const c of sample.sketch.constraints) expect(residual(sample.sketch, c, r.points)).toBeLessThan(1e-5);
      });
    });
    it('reference dimensions do not change DOF or points', () => {
      const sketch = constraintCase('distance');
      sketch.constraints = [ { id: 'reference', kind: 'distance', a: 'a', b: 'b', value: 100, driving: false } ];
      return withSolver(sketch, s => { const r = s.solve({ mode: 'full' }); expect(r.status).toBe('ok');
        expect(r.dof).toBe(4); expect(r.points).toEqual(pointsOf(sketch)); });
    });
    it('drag diagnosis omitted, reference release restores diagnosis', () => withSolver(diagnosticCases[0].sketch, s => {
      const r = s.solve({ mode: 'drag', dragTargets: [{ pointId: 'b', x: 40, y: 20 }] });
      expect(r.status).toBe('ok'); expect(r.dof).toBeUndefined(); expect(r.conflicting).toBeUndefined();
      expect(r.points.b.x).toBeCloseTo(40, 4); expect(r.points.b.y).toBeCloseTo(20, 4);
      expect(s.solve({ mode: 'full' }).dof).toBe(4);
    }));
    it('implicit arc-rule diagnostics use the public arc ID', () => {
      const sketch: Sketch = { entities: [point('c', 0, 0, true), point('s', 10, 0, true), point('e', 0, 20, true),
        arc('arc-owner', 'c', 's', 'e')], constraints: [] };
      return withSolver(sketch, s => { const r = s.solve({ mode: 'full' }); expect(r.status).toBe('failed');
        expect(r.conflicting).toContain('arc-owner'); });
    });
    it('reserved entity returns unsupported', () => withSolver({ entities: [{ kind: 'circle', id: 'future' }], constraints: [] }, s => {
      expect(s.solve({ mode: 'full' }).status).toBe('unsupported');
    }));
    it('load replaces the previous sketch and preserves caller data', async () => {
      const s = await createSolver(name), original = constraintCase('distance'), saved = structuredClone(original);
      try { s.load(original); s.solve({ mode: 'full' }); expect(original).toEqual(saved);
        s.load(diagnosticCases[0].sketch); expect(s.solve({ mode: 'full' }).dof).toBe(4); } finally { s.destroy(); }
    });
    it('destroy and recreate 50 times; double destroy is harmless', async () => {
      for (let i = 0; i < 50; i++) {
        const s = await createSolver(name); s.load(diagnosticCases[0].sketch);
        expect(s.solve({ mode: 'full' }).dof).toBe(4); s.destroy(); s.destroy();
        expect(() => s.solve({ mode: 'full' })).toThrow('destroyed');
      }
    }, 20000);
  });
}

describe('corpus', () => {
  for (const generate of [contourChain, contourChainConstrained]) for (const n of [8, 60, 256]) it(`${generate.name}(${n}) is deterministic and analytically satisfied`, () => {
    const sample = generate(n), pts = pointsOf(sample.sketch);
    expect(generate(n)).toEqual(sample);
    expect(sample.sketch.entities.filter(e => e.kind === 'line' || e.kind === 'arc')).toHaveLength(n);
    expect(pts.pole.x).toBe(0); expect(pts.rim.y).toBe(0);
    for (const c of sample.sketch.constraints) expect(residual(sample.sketch, c, pts)).toBeLessThan(1e-6);
    for (const e of sample.sketch.entities) if (e.kind === 'arc') expect(arcResidual(sample.sketch, e, pts)).toBeLessThan(1e-6);
  });
});
