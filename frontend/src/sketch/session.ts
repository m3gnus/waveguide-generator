import type { Reference } from './adapter';
import { applyCommand } from './ops';
import { constraintProblem, validateSketch, type NewConstraint, type Sketch } from './model';
import { arcGeometry, distance, projectToLoci, refreshReferenceDimensions, tau, type Locus } from './geometry';
import { createSolver, type DragTarget, type Sketch as SolverSketch, type SketchSolver, type SolveResult } from './solver/types';

export function toSolverSketch(sketch: Sketch, references: Reference[]): SolverSketch {
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
/** dragTolerance: projected cursor error (mm) accepted on interactive drag frames. */
export interface SessionOptions { solverFactory?: SolverFactory; references: Reference[]; dragTolerance?: number }
export type DragResult = { sketch: Sketch; stopped: boolean; steps: number; result?: SolveResult; reason?: string };
export type ConstraintAttempt = { ok: true; sketch: Sketch; id: string; diagnosis: Diagnosis }
  | { ok: false; sketch: Sketch; reason: string; conflicting: string[]; redundant: string[] };
type DimensionResult = { ok: boolean; sketch: Sketch; reason?: string };
type Pending<T, R> = { value: T; waiting: { resolve: (result: R) => void; reject: (error: unknown) => void }[] };
export class SketchSession {
  readonly stats: SessionStats = { solves: 0, slowSolves: 0, maxSolveMs: 0, recoveries: 0 };
  private dragIds?: string[];
  private destroyed = false;
  private lastTargets?: DragTarget[];
  private work?: Promise<void>;
  private enqueue<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.work ? this.work.then(operation) : operation();
    const settled = result.then(() => {}, () => {}); this.work = settled;
    void settled.then(() => { if (this.work === settled) this.work = undefined; }); return result;
  }
  private constructor(public sketch: Sketch, private solver: SketchSolver, private factory: SolverFactory, private references: Reference[], private dragTolerance = 1e-3) { this.load(); }
  static async create(sketch: Sketch, options: SessionOptions) {
    if (!Array.isArray(options?.references)) throw new Error('Session requires adapter references.');
    const factory = options.solverFactory ?? (() => createSolver('slvs'));
    const solver = await factory();
    try { return new SketchSession(sketch, solver, factory, options.references, options.dragTolerance); }
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
    return refreshReferenceDimensions({ ...base, points: base.points.map(p => result.points[p.id] ? { ...p, ...result.points[p.id] } : p) });
  }
  private diagnosis(result: SolveResult): Diagnosis {
    const constraintStates = Object.fromEntries(this.sketch.constraints.map(c => [c.id,
      result.conflicting?.includes(c.id) ? 'conflicting' : result.redundant?.includes(c.id) ? 'redundant' : 'ok'])) as Diagnosis['constraintStates'];
    const pointDetermined = (result as SolveResult & { pointDetermined?: Record<string, boolean> }).pointDetermined;
    return { ...result, sketch: this.sketch, constraintStates, pointDetermined };
  }
  async solveFull(): Promise<Diagnosis> {
    return this.enqueue(() => this.performFull());
  }
  private async performFull(): Promise<Diagnosis> {
    this.alive(); if (this.dragIds) throw new Error('End the drag before full diagnosis.');
    const result = await this.call(() => { this.load(); return this.solver.solve({ mode: 'full' }); });
    if (result.status === 'ok') this.sketch = this.read(result);
    else this.load();
    return this.diagnosis(result);
  }
  async tryAddConstraint(sketch: Sketch, constraint: NewConstraint, options: { ifRedundant?: 'refuse' | 'reference' } = {}): Promise<ConstraintAttempt> {
    return this.enqueue(() => this.performAddConstraint(sketch, constraint, options));
  }
  private async performAddConstraint(sketch: Sketch, constraint: NewConstraint, options: { ifRedundant?: 'refuse' | 'reference' }): Promise<ConstraintAttempt> {
    this.alive(); if (this.dragIds) throw new Error('End the drag before adding constraints.');
    const applied = applyCommand(sketch, { kind: 'addConstraint', constraint });
    const refuse = (reason: string, conflicting: string[] = [], redundant: string[] = []): ConstraintAttempt => {
      this.sketch = sketch; this.load(); return { ok: false, sketch, reason, conflicting, redundant };
    };
    if ('error' in applied) return refuse(applied.error);
    const id = applied.created[0];
    if ('driving' in constraint && !constraint.driving) {
      this.sketch = applied.sketch;
      const diagnosis = await this.performFull();
      if (diagnosis.status !== 'ok') return refuse(diagnosis.reason ?? 'Reference dimension could not be added.', diagnosis.conflicting ?? []);
      return { ok: true, sketch: this.sketch, id, diagnosis };
    }
    // Of mutually redundant constraints, SolveSpace may flag either one, and its choice is not
    // stable. So the new constraint is implicated when it is flagged or when any redundancy
    // appears that the sketch did not have before.
    this.sketch = sketch;
    const before = await this.performFull(), baseline = new Set(before.redundant ?? []);
    this.sketch = applied.sketch;
    const diagnosis = await this.performFull();
    if (diagnosis.status !== 'ok' || diagnosis.conflicting?.includes(id)) return refuse(diagnosis.reason ?? 'The constraint conflicts with the sketch.', diagnosis.conflicting ?? []);
    // SolveSpace can also report redundancy against an internal equation only, so use the
    // solver's flag and the DOF as well: a constraint that removes no freedom is redundant.
    const implicated = diagnosis.redundant?.some(r => r === id || !baseline.has(r))
      || (diagnosis.hasRedundancy === true && before.hasRedundancy === false)
      || (diagnosis.dof !== undefined && before.dof !== undefined && diagnosis.dof >= before.dof);
    if (implicated) {
      if (options.ifRedundant !== 'reference' || !('driving' in constraint)) return refuse('The constraint is redundant.', [], diagnosis.redundant?.length ? diagnosis.redundant : [id]);
      const reference = applyCommand(applied.sketch, { kind: 'setDriving', id, driving: false });
      if ('error' in reference) return refuse(reference.error);
      this.sketch = reference.sketch;
      const checked = await this.performFull();
      if (checked.status !== 'ok') return refuse(checked.reason ?? 'Reference dimension could not be added.', checked.conflicting ?? []);
      return { ok: true, sketch: this.sketch, id, diagnosis: checked };
    }
    return { ok: true, sketch: this.sketch, id, diagnosis };
  }
  async beginDrag(pointIds: string[]) {
    return this.enqueue(() => this.performBeginDrag(pointIds));
  }
  private async performBeginDrag(pointIds: string[]) {
    this.alive(); if (this.dragIds) throw new Error('A drag is already active.');
    if (!pointIds.length || new Set(pointIds).size !== pointIds.length || pointIds.some(id => !this.sketch.points.some(p => p.id === id))) throw new Error('Drag needs unique existing point IDs.');
    this.dragIds = [...pointIds];
    this.lastTargets = undefined;
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
  private guarded(before: Sketch, after: Sketch, dimension = false): boolean {
    const oldPoints = new Map(before.points.map(p => [p.id, p])), newPoints = new Map(after.points.map(p => [p.id, p]));
    if (after.points.some(p => !Number.isFinite(p.x + p.y))) return false;
    for (const a of before.entities) if (a.kind === 'arc') {
      const previous = arcGeometry(before, a, oldPoints).sweep, g = arcGeometry(after, a, newPoints);
      const next = g.sweep + tau * Math.round((previous - g.sweep) / tau);
      if (g.radius <= 1e-9 || Math.abs(g.radius - g.endRadius) > 1e-9 * Math.max(1, g.radius)
        || previous * next <= 0 || Math.abs(previous) < Math.PI && Math.abs(next) >= Math.PI
        || Math.abs(previous) > Math.PI && Math.abs(next) <= Math.PI
        || Math.abs(next) >= tau || Math.abs(next) <= 1e-12) return false;
    }
    if (!dimension) return true;
    // Only constrained joints participate in dimension fold refusal.
    const cross = (s: Sketch, a: string, b: string, c: string) => {
      const points = s === before ? oldPoints : newPoints, p = points.get(a)!, q = points.get(b)!, r = points.get(c)!;
      return (q.x - p.x) * (r.y - q.y) - (q.y - p.y) * (r.x - q.x);
    };
    const ends = before.entities.flatMap(e => e.construction ? [] : e.kind === 'line' ? [[e.p1, e.p2]] : e.kind === 'arc' ? [[e.start, e.end]] : []);
    const pair = (a: string, b: string) => JSON.stringify([a, b].sort());
    const lengths = new Set(before.constraints.flatMap(c => c.kind === 'distance' && c.driving ? [pair(c.a, c.b)] : []));
    const fixed = new Set(before.constraints.flatMap(c => c.kind === 'fix' ? [c.point] : []));
    for (const r of this.references) {
      const e = before.entities.find(e => e.id === r.id);
      if (e?.kind === 'line') { fixed.add(e.p1); fixed.add(e.p2); }
    }
    const outgoing = new Map<string, string[]>();
    for (const [a, b] of ends) outgoing.set(a, [...outgoing.get(a) ?? [], b]);
    for (const [a, b] of ends) for (const c of outgoing.get(b) ?? []) if (a !== c && (lengths.has(pair(a, b)) || lengths.has(pair(b, c)))
      && (lengths.has(pair(a, c)) || fixed.has(a) && fixed.has(c))) {
      const old = cross(before, a, b, c), next = cross(after, a, b, c);
      if (Math.abs(old) > 1e-9 && old * next <= 0) return false;
    }
    return true;
  }
  private restartDrag() { this.load(); if (this.dragIds) this.solver.beginDrag(this.dragIds); }
  private referencePoints(): Set<string> {
    return new Set(this.references.flatMap(r => {
      const e = this.sketch.entities.find(e => e.id === r.id);
      return e?.kind === 'line' ? [e.p1, e.p2] : [];
    }));
  }
  private projectTargets(targets: DragTarget[]): DragTarget[] {
    const points = new Map(this.sketch.points.map(p => [p.id, p])), fixed = this.referencePoints();
    for (const c of this.sketch.constraints) if (c.kind === 'fix') fixed.add(c.point);
    return targets.map(target => {
      const loci: Locus[] = [];
      if (fixed.has(target.pointId)) return { pointId: target.pointId, x: points.get(target.pointId)!.x, y: points.get(target.pointId)!.y };
      for (const c of this.sketch.constraints) {
        if (c.kind === 'pointOnLine' && c.point === target.pointId) {
          const e = this.sketch.entities.find(e => e.id === c.line);
          if (e?.kind === 'line') {
            const a = points.get(e.p1)!, b = points.get(e.p2)!;
            loci.push({ kind: 'line', origin: a, direction: { x: b.x - a.x, y: b.y - a.y } });
          }
        } else if (c.kind === 'pointOnArc' && c.point === target.pointId) {
          const e = this.sketch.entities.find(e => e.id === c.arc);
          if (e?.kind === 'arc') loci.push({ kind: 'circle', center: points.get(e.center)!, radius: distance(points.get(e.center)!, points.get(e.start)!) });
        } else if ((c.kind === 'horizontal' || c.kind === 'vertical') && 'a' in c && (c.a === target.pointId || c.b === target.pointId)) {
          const other = c.a === target.pointId ? c.b : c.a;
          if (fixed.has(other) || !this.dragIds!.includes(other)) loci.push({ kind: 'line', origin: points.get(other)!,
            direction: c.kind === 'horizontal' ? { x: 1, y: 0 } : { x: 0, y: 1 } });
        } else if (c.kind === 'distance' && c.driving && (c.a === target.pointId || c.b === target.pointId)) {
          const other = c.a === target.pointId ? c.b : c.a;
          if (fixed.has(other)) loci.push({ kind: 'circle', center: points.get(other)!, radius: c.value });
        }
      }
      return { pointId: target.pointId, ...projectToLoci(target, loci) };
    });
  }
  private driftGuard(before: Sketch, after: Sketch, targets: DragTarget[], tolerance: number): boolean {
    const old = new Map(before.points.map(p => [p.id, p])), next = new Map(after.points.map(p => [p.id, p]));
    const ignored = this.referencePoints(); for (const id of this.dragIds!) ignored.add(id);
    return targets.every(t => {
      const p = next.get(t.pointId)!;
      if (distance(p, t) <= tolerance) return true;
      const limit = Math.max(1e-6, 2 * distance(old.get(t.pointId)!, p));
      return after.points.every(p => ignored.has(p.id) || distance(old.get(p.id)!, p) <= limit);
    });
  }
  private targets(target: DragTarget | DragTarget[]): DragTarget[] {
    this.alive(); if (!this.dragIds) throw new Error('beginDrag() must precede dragTo().');
    const targets = Array.isArray(target) ? target : [target];
    if (targets.length !== this.dragIds.length || new Set(targets.map(t => t.pointId)).size !== targets.length
      || targets.some(t => !this.dragIds!.includes(t.pointId) || !Number.isFinite(t.x + t.y))) throw new Error('Targets must name every dragged point once with finite coordinates.');
    return targets;
  }
  private async performDrag(target: DragTarget | DragTarget[]): Promise<DragResult> {
    const requested = this.targets(target), targets = this.projectTargets(requested); this.lastTargets = targets;
    const tolerance = this.dragTolerance;
    const maxStep = this.stepSize(); let steps = 0, result: SolveResult | undefined;
    while (steps < 64) {
      const from = targets.map(t => ({ pointId: t.pointId, ...this.sketch.points.find(p => p.id === t.pointId)! }));
      const remaining = Math.max(...targets.map((t, i) => distance(t, from[i])));
      if (remaining <= Math.max(1e-8, tolerance)) {
        const fixed = this.referencePoints();
        for (const c of this.sketch.constraints) if (c.kind === 'fix') fixed.add(c.point);
        const stopped = requested.some((t, i) => fixed.has(t.pointId) && distance(t, targets[i]) > tolerance);
        return { sketch: this.sketch, stopped, steps, result };
      }
      const before = this.sketch; let accepted = false, fraction = Math.min(1, maxStep / remaining);
      for (let half = 0; half <= 6; half++, fraction /= 2) {
        const part = this.projectTargets(targets.map((t, i) => ({ pointId: t.pointId, x: from[i].x + (t.x - from[i].x) * fraction, y: from[i].y + (t.y - from[i].y) * fraction })));
        result = await this.call(() => this.solver.dragTo(part, { tolerance }));
        let next = this.read(result, before);
        // An unreachable target leaves a residual that the solver spreads into other points,
        // even when no direct locus exists to project onto. Restart from the step's start and
        // drag to where the point actually got: that target is reachable, so nothing spreads.
        if (result.status === 'ok' && part.some(t => distance(next.points.find(p => p.id === t.pointId)!, t) > tolerance)) {
          const reached = part.map(t => ({ pointId: t.pointId, x: next.points.find(p => p.id === t.pointId)!.x, y: next.points.find(p => p.id === t.pointId)!.y }));
          this.sketch = before; await this.call(() => this.restartDrag(), false, false);
          const second = await this.call(() => this.solver.dragTo(reached, { tolerance }));
          if (second.status === 'ok') { result = second; next = this.read(second, before); }
        }
        if (result.status === 'ok' && this.guarded(before, next) && this.driftGuard(before, next, part, tolerance) && part.every((t, i) => {
          const error = distance(next.points.find(p => p.id === t.pointId)!, t);
          return error <= Math.max(1e-6, tolerance) || error <= distance(from[i], t) + 1e-12;
        })) {
          // At the reachable boundary a step no longer moves the dragged point; accepting such
          // micro-steps repeatedly lets tiny solver adjustments accumulate. Stop there instead.
          if (part.every(t => distance(next.points.find(p => p.id === t.pointId)!, before.points.find(p => p.id === t.pointId)!) <= tolerance / 2)) {
            this.sketch = before; await this.call(() => this.restartDrag(), false, false);
            return { sketch: this.sketch, stopped: true, steps: steps + 1, result, reason: 'Stopped at the reachable boundary.' };
          }
          this.sketch = next; accepted = true; break;
        }
        await this.call(() => this.restartDrag(), false, false);
        if (this.pendingDrag) break;
      }
      steps++;
      if (this.pendingDrag) return { sketch: this.sketch, stopped: true, steps, result };
      if (!accepted) return { sketch: this.sketch, stopped: true, steps, result, reason: 'Stopped at the reachable branch boundary.' };
      if (targets.every((t, i) => distance(this.sketch.points.find(p => p.id === t.pointId)!, from[i]) < 1e-9)) break;
    }
    const stopped = targets.some(t => distance(t, this.sketch.points.find(p => p.id === t.pointId)!) > Math.max(1e-7, 2 * tolerance));
    return { sketch: this.sketch, stopped, steps, result, ...(stopped ? { reason: 'Stopped at the reachable boundary or continuation step limit.' } : {}) };
  }
  async endDrag(): Promise<Diagnosis> {
    return this.enqueue(() => this.performEndDrag());
  }
  private async performEndDrag(): Promise<Diagnosis> {
    this.alive(); if (!this.dragIds) throw new Error('No active drag.');
    // Interactive frames accept dragTolerance; refine the release at the last target to exact.
    if (this.lastTargets && this.dragTolerance > 1e-8 && this.lastTargets.every(t => distance(t, this.sketch.points.find(p => p.id === t.pointId)!) <= this.dragTolerance)) {
      const targets = this.lastTargets, at = this.sketch;
      const exact = await this.call(() => this.solver.dragTo(targets, { tolerance: 0 }));
      const next = this.read(exact, at);
      if (exact.status === 'ok' && this.guarded(at, next) && this.driftGuard(at, next, targets, 1e-8)) this.sketch = next;
      // A rejected refinement must not leave its solution as the solver's seed.
      else await this.call(() => this.restartDrag(), false, false);
    }
    this.lastTargets = undefined;
    const before = this.sketch;
    const result = await this.call(() => this.solver.endDrag()); this.dragIds = undefined;
    const next = this.read(result);
    if (result.status === 'ok' && this.guarded(before, next)) this.sketch = next;
    else { this.load(); return this.diagnosis({ ...result, status: 'failed', reason: result.reason ?? 'Release would change branch.' }); }
    return this.diagnosis(result);
  }
  private async performDimension(id: string, value: number): Promise<{ ok: boolean; sketch: Sketch; reason?: string }> {
    this.alive(); if (this.dragIds) throw new Error('End the drag before editing a dimension.');
    const c = this.sketch.constraints.find(c => c.id === id);
    if (!c || !('value' in c) || !c.driving) return { ok: false, sketch: this.sketch, reason: 'Expected a driving dimension.' };
    const problem = constraintProblem(this.sketch, { ...c, value });
    if (problem) return { ok: false, sketch: this.sketch, reason: problem };
    const original = this.sketch;
    const maxStep = .25 * Math.max(Math.abs(c.value), Math.abs(value - c.value));
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
        if (result.status === 'ok' && this.guarded(before, next, true)) { this.sketch = next; current = nextValue; accepted = true; break; }
        this.sketch = before; this.load();
        if (this.pendingDimensions.some(p => p.value.id === id)) break;
      }
      if (this.pendingDimensions.some(p => p.value.id === id)) return { ok: true, sketch: this.sketch };
      if (!accepted) return refuse('Dimension edit would fold the sketch or cannot be solved.');
    }
    if (current !== value) return refuse('Dimension edit exceeds the continuation step limit.');
    const beforeDiagnosis = this.sketch;
    const final = await this.performFull();
    if (final.status !== 'ok' || !this.guarded(beforeDiagnosis, this.sketch, true)) return refuse('Dimension edit could not be diagnosed without folding.');
    return { ok: true, sketch: this.sketch };
  }
  private dragWork?: Promise<void>;
  private dimensionWork?: Promise<void>;
  private pendingDrag?: Pending<DragTarget | DragTarget[], DragResult>;
  private pendingDimensions: Pending<{ id: string; value: number }, DimensionResult>[] = [];
  async dragTo(target: DragTarget | DragTarget[]): Promise<DragResult> {
    this.targets(target);
    return new Promise((resolve, reject) => {
      const waiting = this.pendingDrag?.waiting ?? [];
      waiting.push({ resolve, reject }); this.pendingDrag = { value: structuredClone(target), waiting };
      this.dragWork ??= this.enqueue(() => this.drainDrag());
    });
  }
  private queuedDrag() { return this.pendingDrag; }
  private async drainDrag() {
    try {
      while (this.pendingDrag) {
        const pending = this.pendingDrag; this.pendingDrag = undefined;
        try {
          const result = await this.performDrag(pending.value);
          const next = this.queuedDrag();
          if (next) next.waiting.unshift(...pending.waiting);
          else for (const w of pending.waiting) w.resolve(result);
        }
        catch (e) { for (const w of pending.waiting) w.reject(e); }
      }
    } finally { this.dragWork = undefined; }
  }
  async setDimensionContinuation(id: string, value: number): Promise<DimensionResult> {
    this.alive(); if (this.dragIds) throw new Error('End the drag before editing a dimension.');
    const c = this.sketch.constraints.find(c => c.id === id);
    if (!c || !('value' in c) || !c.driving) return { ok: false, sketch: this.sketch, reason: 'Expected a driving dimension.' };
    const problem = constraintProblem(this.sketch, { ...c, value });
    if (problem) return { ok: false, sketch: this.sketch, reason: problem };
    return new Promise((resolve, reject) => {
      const pending = this.pendingDimensions.find(p => p.value.id === id);
      if (pending) { pending.value = { id, value }; pending.waiting.push({ resolve, reject }); }
      else this.pendingDimensions.push({ value: { id, value }, waiting: [{ resolve, reject }] });
      this.dimensionWork ??= this.enqueue(() => this.drainDimensions());
    });
  }
  private async drainDimensions() {
    try {
      while (this.pendingDimensions.length) {
        const pending = this.pendingDimensions.shift()!;
        try {
          const result = await this.performDimension(pending.value.id, pending.value.value);
          const next = this.pendingDimensions.find(p => p.value.id === pending.value.id);
          if (next) next.waiting.unshift(...pending.waiting);
          else for (const w of pending.waiting) w.resolve(result);
        }
        catch (e) { for (const w of pending.waiting) w.reject(e); }
      }
    } finally { this.dimensionWork = undefined; }
  }
  destroy() { if (!this.destroyed) { this.solver.destroy(); this.destroyed = true; this.dragIds = undefined; } }
}
export const createSession = (sketch: Sketch, options: SessionOptions) => SketchSession.create(sketch, options);
