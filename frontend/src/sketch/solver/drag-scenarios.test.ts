// @vitest-environment node
import { writeFileSync } from 'node:fs';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { dragScenario, compareTrajectory, dragScenarioNames, maximum, measureDragScenario, type DragMeasurement } from './drag-scenarios';
import { createSolver, solverNames, type SolverName } from './types';

const nodeProcess = (globalThis as { process?: { env: Record<string, string | undefined>; version: string; platform: string; arch: string } }).process;
const measurements: { solver: SolverName; n: number; scenarios: DragMeasurement[] }[] = [];
afterAll(() => {
  if (nodeProcess?.env.SKETCH_DRAG_OUTPUT) writeFileSync(nodeProcess.env.SKETCH_DRAG_OUTPUT,
    JSON.stringify({ node: nodeProcess.version, platform: nodeProcess.platform, arch: nodeProcess.arch, measurements }, null, 2));
});

for (const n of [8, 24]) describe(`equivalent drag n=${n}`, () => {
  const results = new Map<SolverName, DragMeasurement[]>();
  beforeAll(async () => {
    for (const name of solverNames) {
      const s = await createSolver(name);
      try { results.set(name, dragScenarioNames.map(scenario => measureDragScenario(s, n, scenario))); }
      finally { s.destroy(); }
    }
    const reference = results.get('planegcs')!.find(r => r.name === 'trajectory')!;
    for (const name of solverNames) {
      const rows = results.get(name)!;
      compareTrajectory(rows.find(r => r.name === 'trajectory')!, reference);
      measurements.push({ solver: name, n, scenarios: rows });
      console.log('DRAG_RESULTS', JSON.stringify({ solver: name, n, scenarios: rows.map(({ frames, finalResiduals, ...r }) => ({ ...r, finalMaxResidual: maximum(Object.values(finalResiduals)),
        framesCompleted: frames.length, maxProjectionError: maximum(frames.flatMap(f => f.projectionError === undefined ? [] : [f.projectionError])),
      })) }));
    }
  }, 60000);
  for (const name of solverNames) {
    it(`${name}: reachable free chain end tracks every target within 1e-6 mm`, () => {
      const r = results.get(name)![0]; expect(r.frames).toHaveLength(60);
      expect(r.failedFrames).toBe(0); expect(r.maxCursorError).toBeLessThan(1e-6); expect(r.maxResidual).toBeLessThan(1e-5);
    });
    it(`${name}: unreachable drag keeps every frame valid on the constraint locus`, () => {
      const r = results.get(name)![1]; expect(r.frames).toHaveLength(60);
      expect(r.failedFrames).toBe(0); expect(r.maxResidual).toBeLessThan(1e-5);
    });
    const projectionTest = it;
    projectionTest(`${name}: unreachable targets project to rim locus with minimum cursor movement`, () => {
      const r = results.get(name)![1]; expect(r.frames).toHaveLength(60);
      expect(r.failedFrames).toBe(0); expect(r.maxResidual).toBeLessThan(1e-5);
      expect(maximum(r.frames.map(f => f.projectionError!))).toBeLessThan(1e-6); expect(r.finalCursorError).toBeCloseTo(5, 6);
    });
    it(`${name}: records the same 60-frame path and three interior-point deviations`, () => {
      const r = results.get(name)![2]; expect(r.frames).toHaveLength(60);
      expect(r.failedFrames).toBe(0); expect(r.maxResidual).toBeLessThan(1e-5); expect(Object.keys(r.maxDeviationFromPlanegcs!)).toHaveLength(3);
      for (const f of r.frames) expect(Object.values(f.interior).flatMap(p => [p.x, p.y]).every(Number.isFinite)).toBe(true);
      if (name === 'planegcs') expect(maximum(Object.values(r.maxDeviationFromPlanegcs!))).toBe(0);
    });
  }
});

it('slvs: pointOnLine reachable locus drag tracks cursor within 1e-6 mm', async () => {
  const solver = await createSolver('slvs');
  try {
    const sample = dragScenario(8, 'trajectory');
    sample.sketch.entities.push({ kind: 'point', id: 'baffle-end', x: 1, y: 0, fixed: true },
      { kind: 'line', id: 'baffle', p1: 'origin', p2: 'baffle-end' });
    sample.sketch.constraints = sample.sketch.constraints.map(c => c.id === 'rim-baffle'
      ? { kind: 'pointOnLine', id: c.id, point: 'rim', line: 'baffle' } : c);
    solver.load(sample.sketch); solver.beginDrag(['rim']);
    let error = 0;
    for (const target of sample.targets) {
      const r = solver.dragTo([target]); expect(r.status).toBe('ok');
      error = Math.max(error, Math.hypot(r.points.rim.x - target.x, r.points.rim.y - target.y));
    }
    const output = nodeProcess?.env.SKETCH_FIX_OUTPUT;
    if (output) writeFileSync(`${output}.point-on-line.json`, JSON.stringify({ frames: sample.targets.length, maxCursorError: error }));
    expect(error).toBeLessThanOrEqual(1e-6); expect(solver.endDrag().status).toBe('ok');
  } finally { solver.destroy(); }
});
