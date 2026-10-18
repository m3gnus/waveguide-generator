import { Unsupported, constraintKinds, type Arc, type Capabilities, type Capability, type Entity,
  type DragTarget, type Point, type Sketch, type SketchSolver, type SolveOptions, type SolveResult, type XY } from './types';
export const native = (detail = ''): Capability => ({ support: 'native', detail });
export const emulated = (detail: string): Capability => ({ support: 'emulated', detail });
export const unsupported = (detail: string): Capability => ({ support: 'unsupported', detail });
export function capabilities(): Capabilities {
  return { constraints: Object.fromEntries(constraintKinds.map(k => [k, native()])) as Capabilities['constraints'],
    diagnostics: { dof: native(), conflicting: native(), redundant: native(), partiallyRedundant: native() },
    drag: '', referenceDimensions: 'Omitted from the equation system; measured from returned points.' };
}
export function entity<T extends Entity['kind']>(sketch: Sketch, id: string, kind: T): Extract<Entity, { kind: T }> {
  const e = sketch.entities.find(e => e.id === id);
  if (!e || e.kind !== kind) throw new Error(`Expected ${kind}: ${id}`);
  return e as Extract<Entity, { kind: T }>;
}
export const pointsOf = (s: Sketch): Record<string, XY> => Object.fromEntries(s.entities
  .filter((e): e is Point => e.kind === 'point').map(p => [p.id, { x: p.x, y: p.y }]));
export const tau = 2 * Math.PI;
export const positiveAngle = (v: number) => ((v % tau) + tau) % tau;
export function arcValues(s: Sketch, a: Arc) {
  const c = entity(s, a.center, 'point'), p = entity(s, a.start, 'point'), q = entity(s, a.end, 'point');
  const start = Math.atan2(p.y - c.y, p.x - c.x);
  return { radius: Math.hypot(p.x - c.x, p.y - c.y), start,
    end: start + positiveAngle(Math.atan2(q.y - c.y, q.x - c.x) - start) };
}
/** Signed endpoint sweep, unwrapped against the previous frame (sampling must be < pi). */
export function signedSweep(a: Arc, pts: Record<string, XY>, previous?: number): number {
  const c = pts[a.center], s = pts[a.start], e = pts[a.end];
  const raw = Math.atan2((s.x - c.x) * (e.y - c.y) - (s.y - c.y) * (e.x - c.x),
    (s.x - c.x) * (e.x - c.x) + (s.y - c.y) * (e.y - c.y));
  return previous === undefined ? positiveAngle(raw) : raw + tau * Math.round((previous - raw) / tau);
}
export class SweepTracker {
  readonly sweeps: Record<string, number> = {};
  constructor(private sketch: Sketch, points = pointsOf(sketch)) {
    for (const a of sketch.entities) if (a.kind === 'arc') this.sweeps[a.id] = signedSweep(a, points);
  }
  update(points: Record<string, XY>) {
    let flips = 0;
    for (const a of this.sketch.entities) if (a.kind === 'arc') {
      const prev = this.sweeps[a.id], next = signedSweep(a, points, prev);
      // Includes exact-zero samples: entering a collapsed/full-turn sweep is a crossing.
      if ((prev > 0 && next <= 0) || (prev < tau && next >= tau)
        || (prev <= 0 && next > 0) || (prev >= tau && next < tau)) flips++;
      this.sweeps[a.id] = next;
    }
    return flips;
  }
}
export type NativeResult = Omit<SolveResult, 'timings'> & { updateMs: number; solveMs: number };
/** Shared validation, successful continuation seeds and explicit drag-session lifecycle. */
export abstract class Adapter implements SketchSolver {
  protected sketch: Sketch = { entities: [], constraints: [] };
  protected destroyed = false;
  private loaded = false;
  protected dragPointIds?: string[];
  protected abstract prepareDrag(targets: DragTarget[]): void;
  protected abstract invalidatePrepared(): void;
  abstract capabilities(): Capabilities;
  abstract memoryBytes(): number | undefined;
  protected abstract run(options: SolveOptions): NativeResult;
  protected abstract release(): void;
  load(s: Sketch) {
    this.alive();
    const ids = [...s.entities, ...s.constraints].map(e => e.id);
    if (new Set(ids).size !== ids.length) throw new Error('IDs must be unique across entities and constraints');
    this.dragPointIds = undefined; this.invalidatePrepared();
    this.sketch = structuredClone(s); this.loaded = true;
  }
  protected alive() { if (this.destroyed) throw new Error('Solver has been destroyed'); }
  private validate() {
    this.alive(); if (!this.loaded) throw new Error('load() must precede solve() or beginDrag()');
    for (const e of this.sketch.entities) {
      if (!['point', 'line', 'arc'].includes(e.kind)) throw new Unsupported(`Reserved entity: ${e.kind}`);
    }
    const caps = this.capabilities();
    for (const c of this.sketch.constraints) {
      if ('driving' in c && !c.driving) continue;
      const cap = caps.constraints[c.kind];
      if (cap.support === 'unsupported') throw new Unsupported(`${c.kind}: ${cap.detail}`);
    }
  }
  beginDrag(pointIds: string[]) {
    this.validate();
    if (this.dragPointIds) throw new Error('A drag session is already active');
    if (!pointIds.length || new Set(pointIds).size !== pointIds.length) throw new Error('Drag point IDs must be nonempty and unique');
    const targets = pointIds.map(pointId => ({ pointId, ...entity(this.sketch, pointId, 'point') }));
    this.dragPointIds = [...pointIds];
    try { this.prepareDrag(targets); }
    catch (e) { this.dragPointIds = undefined; this.invalidatePrepared(); throw e; }
  }
  dragTo(targets: DragTarget[], options: { tolerance?: number } = {}): SolveResult {
    this.alive();
    if (!this.dragPointIds) throw new Error('beginDrag() must precede dragTo()');
    if (targets.length !== this.dragPointIds.length || new Set(targets.map(t => t.pointId)).size !== targets.length
      || targets.some(t => !this.dragPointIds!.includes(t.pointId) || !Number.isFinite(t.x) || !Number.isFinite(t.y))) {
      throw new Error('Targets must contain each session point once, with finite coordinates');
    }
    return this.execute({ mode: 'drag', dragTargets: targets, tolerance: options.tolerance });
  }
  endDrag(): SolveResult {
    this.alive(); if (!this.dragPointIds) throw new Error('No active drag session');
    return this.solve({ mode: 'full' });
  }
  solve(options: SolveOptions): SolveResult {
    this.alive(); this.dragPointIds = undefined; this.invalidatePrepared();
    return this.execute(options);
  }
  private execute(options: SolveOptions): SolveResult {
    this.alive(); if (!this.loaded) throw new Error('load() must precede solve()');
    const t = performance.now();
    let result: NativeResult;
    try {
      this.validate();
      for (const target of options.dragTargets ?? []) entity(this.sketch, target.pointId, 'point');
      const beforeRun = performance.now();
      result = this.run(options);
      result.updateMs += beforeRun - t;
    } catch (e) {
      if (!(e instanceof Unsupported)) throw e;
      result = { status: 'unsupported', reason: e.message, points: pointsOf(this.sketch), updateMs: performance.now() - t, solveMs: 0 };
    }
    // Some native APIs omit implicit arc-rule failures (or call them redundant).
    // This exact geometric invariant catches them without manufacturing constraint IDs.
    const invalidArcs = this.sketch.entities.filter((e): e is Arc => e.kind === 'arc').filter(a => {
      const c = result.points[a.center], p = result.points[a.start], q = result.points[a.end];
      const r1 = Math.hypot(p.x - c.x, p.y - c.y), r2 = Math.hypot(q.x - c.x, q.y - c.y);
      return !Number.isFinite(r1 + r2) || Math.abs(r1 - r2) > 1e-7 * Math.max(1, r1, r2);
    }).map(a => a.id);
    if (invalidArcs.length && result.status !== 'unsupported') {
      result.status = 'failed';
      result.reason = 'Arc endpoints do not lie on a common circle';
      if (options.mode === 'full') result.conflicting = [...new Set([...result.conflicting ?? [], ...invalidArcs])];
    }
    // Native failures must not poison continuation seeds. No artificial branch correction is applied:
    // the harness measures native branch changes, retaining the explicit CCW endpoint order.
    if (result.status === 'ok') for (const e of this.sketch.entities) {
      if (e.kind === 'point' && result.points[e.id]) Object.assign(e, result.points[e.id]);
    }
    if (result.status !== 'ok') this.invalidatePrepared();
    const totalMs = performance.now() - t;
    const { updateMs, solveMs, ...rest } = result;
    const readbackMs = totalMs - updateMs - solveMs;
    return { ...rest, timings: { totalMs, updateMs, solveMs, readbackMs, marshalMs: totalMs - solveMs } };
  }
  destroy() { if (!this.destroyed) { this.release(); this.dragPointIds = undefined; this.invalidatePrepared(); this.sketch = { entities: [], constraints: [] }; this.destroyed = true; } }
}
/** Internal equations map to public constraint IDs, or to the arc entity that owns arc rules. */
export function publicIds(ids: string[], map: Map<string, string>): string[] {
  return [...new Set(ids.flatMap(id => map.has(id) ? [map.get(id)!] : []))];
}
/** Endpoint equivalence is established by topology or explicit coincidences, never a distance tolerance. */
export function equivalent(s: Sketch, a: string, b: string): boolean {
  const reached = new Set([a]); let changed = true;
  while (changed) { changed = false; for (const c of s.constraints) if (c.kind === 'coincident') {
    if (reached.has(c.a) && !reached.has(c.b)) { reached.add(c.b); changed = true; }
    if (reached.has(c.b) && !reached.has(c.a)) { reached.add(c.a); changed = true; }
  } }
  return reached.has(b);
}
/** Exact endpoint-tangent composition: perpendicular line/radius, or parallel radii. */
export function tangentRadii(s: Sketch, aId: string, bId: string) {
  const a = s.entities.find(e => e.id === aId), b = s.entities.find(e => e.id === bId);
  const curve = a?.kind === 'arc' ? a : b?.kind === 'arc' ? b : undefined;
  if (!curve) throw new Error('Tangency requires an arc');
  const other = curve === a ? b : a;
  if (other?.kind !== 'line' && other?.kind !== 'arc') throw new Error('Tangency requires line–arc or arc–arc');
  const ends = other.kind === 'line' ? [other.p1, other.p2] : [other.start, other.end];
  const contact = [curve.start, curve.end].find(p => ends.some(q => equivalent(s, p, q)));
  if (!contact) return undefined;
  const second = ends.find(p => equivalent(s, contact, p))!;
  return { curve, other, contact, second };
}
