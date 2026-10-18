import { entity, SweepTracker } from './common';
import { arcResidual, contourChain, residual } from './corpus';
import type { DragTarget, Sketch, SketchSolver, XY } from './types';
export const dragScenarioNames = ['reachable', 'unreachable', 'trajectory'] as const;
export type DragScenarioName = typeof dragScenarioNames[number];
export interface DragScenario {
  name: DragScenarioName; sketch: Sketch; targets: DragTarget[]; interiorIds: string[];
}
/** Identical 60-frame targets for every adapter. Axial translation makes the free end reachable. */
export function dragScenario(n: number, name: DragScenarioName): DragScenario {
  const { sketch } = contourChain(n), rim = entity(sketch, 'rim', 'point');
  if (name === 'reachable') sketch.constraints = sketch.constraints.filter(c => c.id !== 'rim-baffle');
  const targets = Array.from({ length: 60 }, (_, i) => {
    const theta = (i + 1) / 60 * 2 * Math.PI;
    return { pointId: 'rim', x: rim.x + Math.sin(theta),
      y: name === 'unreachable' ? 5 : name === 'reachable' ? .5 * Math.sin(theta) : 0 };
  });
  return { name, sketch, targets, interiorIds: [1, 2, 3].map(i => `start${n * i / 4}`) };
}
export function residuals(sketch: Sketch, points: Record<string, XY>) {
  return Object.fromEntries([
    ...sketch.constraints.filter(c => !('driving' in c) || c.driving).map(c => [c.id, residual(sketch, c, points)] as const),
    ...sketch.entities.flatMap(e => e.kind === 'arc' ? [[e.id, arcResidual(sketch, e, points)] as const]
      : e.kind === 'point' && e.fixed ? [[e.id, Math.hypot(points[e.id].x - e.x, points[e.id].y - e.y)] as const] : []),
  ]);
}
export function maximum(values: number[]) { return values.length ? Math.max(...values) : 0; }
export interface DragFrame {
  frame: number; target: DragTarget; cursor: XY; cursorError: number; projectionError?: number;
  maxResidual: number; status: string; interior: Record<string, XY>;
  updateMs: number; solveMs: number; readbackMs: number; totalMs: number;
}
export interface DragMeasurement {
  name: DragScenarioName; n: number; frames: DragFrame[]; plannedFrames: number; failedFrames: number;
  maxCursorError: number; finalCursorError: number; maxResidual: number; finalResiduals: Record<string, number>;
  branchFlips: number; maxDeviationFromPlanegcs?: Record<string, number>;
}
export function measureDragScenario(solver: SketchSolver, n: number, name: DragScenarioName): DragMeasurement {
  const sample = dragScenario(n, name), tracker = new SweepTracker(sample.sketch);
  const out: DragMeasurement = { name, n, frames: [], plannedFrames: 60, failedFrames: 0,
    maxCursorError: 0, finalCursorError: 0, maxResidual: 0, finalResiduals: {}, branchFlips: 0 };
  solver.load(sample.sketch); solver.beginDrag(['rim']);
  for (const [frame, target] of sample.targets.entries()) {
    const r = solver.dragTo([target]), cursor = r.points.rim;
    const errors = residuals(sample.sketch, r.points), maxResidual = maximum(Object.values(errors));
    const cursorError = Math.hypot(cursor.x - target.x, cursor.y - target.y);
    out.frames.push({ frame, target, cursor: { ...cursor }, cursorError, maxResidual, status: r.status,
      ...(name === 'unreachable' ? { projectionError: Math.hypot(cursor.x - target.x, cursor.y) } : {}),
      interior: Object.fromEntries(sample.interiorIds.map(id => [id, { ...r.points[id] }])), ...r.timings });
    if (r.status !== 'ok' || !Number.isFinite(maxResidual) || maxResidual >= 1e-4) out.failedFrames++;
    out.branchFlips += tracker.update(r.points);
    out.maxCursorError = Math.max(out.maxCursorError, cursorError); out.finalCursorError = cursorError;
    out.maxResidual = Math.max(out.maxResidual, maxResidual); out.finalResiduals = errors;
  }
  solver.endDrag(); return out;
}
/** Compare each of three interior points at corresponding frames, never align by nearest position. */
export function compareTrajectory(measured: DragMeasurement, reference: DragMeasurement) {
  if (measured.name !== 'trajectory' || reference.name !== 'trajectory' || measured.n !== reference.n
    || measured.frames.length !== 60 || reference.frames.length !== 60) return;
  const ids = Object.keys(reference.frames[0].interior);
  measured.maxDeviationFromPlanegcs = Object.fromEntries(ids.map(id => [id, maximum(measured.frames.map((f, i) =>
    Math.hypot(f.interior[id].x - reference.frames[i].interior[id].x, f.interior[id].y - reference.frames[i].interior[id].y)))]));
}
