import type { Reference } from './adapter';
import { applyCommand } from './ops';
import { constraintProblem, validateSketch, type NewConstraint, type Sketch } from './model';
import { arcGeometry, distance, tau } from './geometry';
import { createSolver, type DragTarget, type Sketch as SolverSketch, type SketchSolver, type SolveResult } from './solver/types';

export function toSolverSketch(sketch: Sketch, references: Reference[] = [{ id: 'axis', kind: 'axis' }, { id: 'baffle', kind: 'baffle' }]): SolverSketch {
  const valid = validateSketch(sketch); if (!valid.ok) throw new Error(valid.reason);
  const fixed = new Set(references.flatMap(r => {
    const e = sketch.entities.find(e => e.id === r.id && e.construction);
    return e?.kind === 'line' ? [e.p1, e.p2] : [];
  }));
  return { entities: [
    ...sketch.points.map(p => ({ ...p, kind: 'point' as const, ...(fixed.has(p.id) ? { fixed: true } : {}) })),
    ...sketch.entities.map(e => e.kind === 'arc' ? { kind: 'arc' as const, id: e.id, center: e.center,
      start: e.ccw ? e.start : e.end, end: e.ccw ? e.end : e.start, direction: 'ccw' as const }
      : e.kind === 'line' ? { kind: 'line' as const, id: e.id, p1: e.p1, p2: e.p2 } : { kind: e.kind, id: e.id }),
  ], constraints: sketch.constraints.map(c => ({ ...c })) };
}
export interface Diagnosis extends SolveResult {
  sketch: Sketch; constraintStates: Record<string, 'ok' | 'redundant' | 'conflicting'>;
  pointDetermined: Record<string, boolean> | undefined;
}
export interface SessionStats { solves: number; slowSolves: number; maxSolveMs: number; recoveries: number }
export type SolverFactory = () => Promise<SketchSolver>;
export interface SessionOptions { solverFactory?: SolverFactory; references?: Reference[] }
export type DragResult = { sketch: Sketch; stopped: boolean; steps: number; result?: SolveResult; reason?: string };
export type ConstraintAttempt = { ok: true; sketch: Sketch; id: string; diagnosis: Diagnosis }
  | { ok: false; sketch: Sketch; reason: string; conflicting: string[]; redundant: string[] };
export class SketchSession {
  readonly stats: SessionStats = { solves: 0, slowSolves: 0, maxSolveMs: 0, recoveries: 0 };
  private dragIds?: string[];
  private destroyed = false;
  private constructor(public sketch: Sketch, private solver: SketchSolver, private factory: SolverFactory, private references?: Reference[]) { this.load(); }
  static async create(sketch: Sketch, options: SessionOptions = {}) {
    const factory = options.solverFactory ?? (() => createSolver('slvs'));
    const solver = await factory();
    try { return new SketchSession(sketch, solver, factory, options.references); }
    catch (e) { solver.destroy(); throw e; }
  }
  private load() { this.solver.load(toSolverSketch(this.sketch, this.references)); }
  private alive() { if (this.destroyed) throw new Error('Session has been destroyed.'); }
  private async call<T>(operation: () => T, resumeDrag = true, measure = true): Promise<T> {
    this.alive();
    for (let attempt = 0; ; attempt++) {
      const start = performance.now();
      try { return operation(); }
      catch (e) {
        if (attempt || !(e instanceof WebAssembly.RuntimeError || e instanceof RangeError || e instanceof Error && /retired/i.test(e.message))) throw e;
        try { this.solver.destroy(); } catch { /* A trapped module may refuse cleanup. */ }
        this.solver = await this.factory(); this.stats.recoveries++; this.load();
        if (resumeDrag && this.dragIds) this.solver.beginDrag(this.dragIds);
      } finally {
        const ms = performance.now() - start;
        if (measure) {
          this.stats.solves++; this.stats.maxSolveMs = Math.max(this.stats.maxSolveMs, ms);
          if (ms > 250) this.stats.slowSolves++;
        }
      }
    }
  }
  private read(result: SolveResult, base = this.sketch): Sketch {
    return { ...base, points: base.points.map(p => result.points[p.id] ? { ...p, ...result.points[p.id] } : p) };
  }
  private diagnosis(result: SolveResult): Diagnosis {
    const constraintStates = Object.fromEntries(this.sketch.constraints.map(c => [c.id,
      result.conflicting?.includes(c.id) ? 'conflicting' : result.redundant?.includes(c.id) ? 'redundant' : 'ok'])) as Diagnosis['constraintStates'];
    const pointDetermined = (result as SolveResult & { pointDetermined?: Record<string, boolean> }).pointDetermined;
    return { ...result, sketch: this.sketch, constraintStates, pointDetermined };
  }
  async solveFull(): Promise<Diagnosis> {
    this.alive(); if (this.dragIds) throw new Error('End the drag before full diagnosis.');
    const result = await this.call(() => { this.load(); return this.solver.solve({ mode: 'full' }); });
    if (result.status === 'ok') this.sketch = this.read(result);
    else this.load();
    return this.diagnosis(result);
  }
  async tryAddConstraint(sketch: Sketch, constraint: NewConstraint, options: { ifRedundant?: 'refuse' | 'reference' } = {}): Promise<ConstraintAttempt> {
    this.alive(); if (this.dragIds) throw new Error('End the drag before adding constraints.');
    const applied = applyCommand(sketch, { kind: 'addConstraint', constraint });
    const refuse = (reason: string, conflicting: string[] = [], redundant: string[] = []): ConstraintAttempt => {
      this.sketch = sketch; this.load(); return { ok: false, sketch, reason, conflicting, redundant };
    };
    if ('error' in applied) return refuse(applied.error);
    const id = applied.created[0]; this.sketch = applied.sketch;
    const diagnosis = await this.solveFull();
    if (diagnosis.status !== 'ok' || diagnosis.conflicting?.length) return refuse(diagnosis.reason ?? 'The constraint conflicts with the sketch.', diagnosis.conflicting ?? []);
    if (diagnosis.redundant?.length) {
      if (options.ifRedundant !== 'reference' || !('driving' in constraint)) return refuse('The constraint is redundant.', [], diagnosis.redundant);
      const reference = applyCommand(applied.sketch, { kind: 'setDriving', id, driving: false });
      if ('error' in reference) return refuse(reference.error);
      this.sketch = reference.sketch;
      const checked = await this.solveFull();
      if (checked.status !== 'ok') return refuse(checked.reason ?? 'Reference dimension could not be added.', checked.conflicting ?? []);
      return { ok: true, sketch: this.sketch, id, diagnosis: checked };
    }
    return { ok: true, sketch: this.sketch, id, diagnosis };
  }
  async beginDrag(pointIds: string[]) {
    this.alive(); if (this.dragIds) throw new Error('A drag is already active.');
    if (!pointIds.length || new Set(pointIds).size !== pointIds.length || pointIds.some(id => !this.sketch.points.some(p => p.id === id))) throw new Error('Drag needs unique existing point IDs.');
    this.dragIds = [...pointIds];
    try { await this.call(() => { this.load(); this.solver.beginDrag(pointIds); }, false, false); }
    catch (e) { this.dragIds = undefined; throw e; }
  }
  private stepSize(): number {
    const values = this.sketch.constraints.flatMap(c => 'value' in c && c.driving && c.kind !== 'angle' && Math.abs(c.value) > 1e-9 ? [Math.abs(c.value)] : []);
    if (!values.length) for (const e of this.sketch.entities) {
      if (e.construction) continue;
      if (e.kind === 'line') values.push(distance(this.sketch.points.find(p => p.id === e.p1)!, this.sketch.points.find(p => p.id === e.p2)!));
      if (e.kind === 'arc') values.push(arcGeometry(this.sketch, e).radius);
    }
    return .25 * Math.min(...values.filter(v => v > 1e-9), 100);
  }
  private guarded(before: Sketch, after: Sketch): boolean {
    if (after.points.some(p => !Number.isFinite(p.x + p.y))) return false;
    for (const a of before.entities) if (a.kind === 'arc') {
      const previous = arcGeometry(before, a).sweep, g = arcGeometry(after, a);
      const next = g.sweep + tau * Math.round((previous - g.sweep) / tau);
      if (g.radius <= 1e-9 || Math.abs(g.radius - g.endRadius) > 1e-9 * Math.max(1, g.radius)
        || previous * next <= 0 || Math.abs(previous) < Math.PI && Math.abs(next) >= Math.PI
        || Math.abs(previous) > Math.PI && Math.abs(next) <= Math.PI
        || Math.abs(next) >= tau || Math.abs(next) <= 1e-12) return false;
    }
    // Keep the side of consecutive directed edges, including all-line linkages.
    const cross = (s: Sketch, a: string, b: string, c: string) => {
      const p = s.points.find(p => p.id === a)!, q = s.points.find(p => p.id === b)!, r = s.points.find(p => p.id === c)!;
      return (q.x - p.x) * (r.y - q.y) - (q.y - p.y) * (r.x - q.x);
    };
    const ends = before.entities.flatMap(e => e.construction ? [] : e.kind === 'line' ? [[e.p1, e.p2]] : e.kind === 'arc' ? [[e.start, e.end]] : []);
    for (const [a, b] of ends) for (const [j, c] of ends) if (b === j && a !== c) {
      const old = cross(before, a, b, c), next = cross(after, a, b, c);
      if (Math.abs(old) > 1e-9 && old * next <= 0) return false;
    }
    return true;
  }
  private restartDrag() { this.load(); if (this.dragIds) this.solver.beginDrag(this.dragIds); }
  async dragTo(target: DragTarget | DragTarget[]): Promise<DragResult> {
    this.alive(); if (!this.dragIds) throw new Error('beginDrag() must precede dragTo().');
    const targets = Array.isArray(target) ? target : [target];
    if (targets.length !== this.dragIds.length || new Set(targets.map(t => t.pointId)).size !== targets.length
      || targets.some(t => !this.dragIds!.includes(t.pointId) || !Number.isFinite(t.x + t.y))) throw new Error('Targets must name every dragged point once with finite coordinates.');
    const maxStep = this.stepSize(); let steps = 0, result: SolveResult | undefined;
    while (steps < 64) {
      const from = targets.map(t => ({ pointId: t.pointId, ...this.sketch.points.find(p => p.id === t.pointId)! }));
      const remaining = Math.max(...targets.map((t, i) => distance(t, from[i])));
      if (remaining < 1e-8) return { sketch: this.sketch, stopped: false, steps, result };
      const before = this.sketch; let accepted = false, fraction = Math.min(1, maxStep / remaining);
      for (let half = 0; half <= 6; half++, fraction /= 2) {
        const part = targets.map((t, i) => ({ pointId: t.pointId, x: from[i].x + (t.x - from[i].x) * fraction, y: from[i].y + (t.y - from[i].y) * fraction }));
        result = await this.call(() => this.solver.dragTo(part));
        const next = this.read(result, before);
        if (result.status === 'ok' && this.guarded(before, next)) { this.sketch = next; accepted = true; break; }
        await this.call(() => this.restartDrag(), false, false);
      }
      steps++;
      if (!accepted) return { sketch: this.sketch, stopped: true, steps, result, reason: 'Stopped at the reachable branch boundary.' };
      if (targets.every((t, i) => distance(this.sketch.points.find(p => p.id === t.pointId)!, from[i]) < 1e-9)) break;
    }
    const stopped = targets.some(t => distance(t, this.sketch.points.find(p => p.id === t.pointId)!) > 1e-7);
    return { sketch: this.sketch, stopped, steps, result, ...(stopped ? { reason: 'Stopped at the reachable boundary or continuation step limit.' } : {}) };
  }
  async endDrag(): Promise<Diagnosis> {
    this.alive(); if (!this.dragIds) throw new Error('No active drag.');
    const before = this.sketch;
    const result = await this.call(() => this.solver.endDrag()); this.dragIds = undefined;
    const next = this.read(result);
    if (result.status === 'ok' && this.guarded(before, next)) this.sketch = next;
    else { this.load(); return this.diagnosis({ ...result, status: 'failed', reason: result.reason ?? 'Release would change branch.' }); }
    return this.diagnosis(result);
  }
  async setDimensionContinuation(id: string, value: number): Promise<{ ok: boolean; sketch: Sketch; reason?: string }> {
    this.alive(); if (this.dragIds) throw new Error('End the drag before editing a dimension.');
    const c = this.sketch.constraints.find(c => c.id === id);
    if (!c || !('value' in c) || !c.driving) return { ok: false, sketch: this.sketch, reason: 'Expected a driving dimension.' };
    const problem = constraintProblem(this.sketch, { ...c, value });
    if (problem) return { ok: false, sketch: this.sketch, reason: problem };
    const original = this.sketch;
    const maxStep = c.kind === 'angle' ? Math.PI / 16 : this.stepSize();
    const refuse = (reason: string) => { this.sketch = original; this.load(); return { ok: false, sketch: original, reason }; };
    let current = c.value;
    for (let step = 0; current !== value && step < 64; step++) {
      const before = this.sketch, remaining = value - current;
      let delta = Math.sign(remaining) * Math.min(Math.abs(remaining), maxStep), accepted = false;
      for (let half = 0; half <= 6; half++, delta /= 2) {
        const nextValue = Math.abs(delta) >= Math.abs(remaining) ? value : current + delta;
        const applied = applyCommand(before, { kind: 'setDimension', id, value: nextValue });
        if ('error' in applied) return refuse(applied.error);
        this.sketch = applied.sketch;
        const result = await this.call(() => { this.load(); return this.solver.solve({ mode: 'drag' }); });
        const next = this.read(result);
        if (result.status === 'ok' && this.guarded(before, next)) { this.sketch = next; current = nextValue; accepted = true; break; }
        this.sketch = before; this.load();
      }
      if (!accepted) return refuse('Dimension edit would fold the sketch or cannot be solved.');
    }
    if (current !== value) return refuse('Dimension edit exceeds the continuation step limit.');
    const beforeDiagnosis = this.sketch;
    const final = await this.solveFull();
    if (final.status !== 'ok' || !this.guarded(beforeDiagnosis, this.sketch)) return refuse('Dimension edit could not be diagnosed without folding.');
    return { ok: true, sketch: this.sketch };
  }
  destroy() { if (!this.destroyed) { this.solver.destroy(); this.destroyed = true; this.dragIds = undefined; } }
}
export const createSession = (sketch: Sketch, options?: SessionOptions) => SketchSession.create(sketch, options);
