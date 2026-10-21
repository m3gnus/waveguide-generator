import type { Command } from './commands';
import type { Reference } from './adapter';
import type { Constraint, NewConstraint, Sketch } from './model';
import { History } from './history';
import { createSession, type Diagnosis, type SketchSession } from './session';
import { materialiseContext, referencePointIds, type SketchContext } from './context';
import { displayName } from './displayName';
import type { XY } from './solver/types';

/** Carry the rejected candidate too: it is rolled back and has no live glyph to name. */
function constraintError(sketch: Sketch, result: { reason?: string; conflicting?: string[]; redundant?: string[]; constraints?: Constraint[]; hasRedundancy?: boolean }) {
  const ids = [...result.conflicting ?? [], ...result.redundant ?? []];
  const constraints = result.constraints ?? sketch.constraints.filter(c => ids.includes(c.id));
  const named = { ...sketch, constraints: [...sketch.constraints.filter(c => !constraints.some(v => v.id === c.id)), ...constraints] };
  if (!ids.length && !result.hasRedundancy) return new Error(`Could not solve this change${result.reason ? `: ${result.reason}` : '.'}`);
  const reason = result.reason ?? 'The solver reports conflicting or redundant constraints.';
  const error = new Error(`Conflicts with ${constraints.map(c => displayName(named, c.id)).join('; ') || 'the sketch'}: ${reason}`);
  return Object.assign(error, { conflicting: constraints.map(c => c.id), constraints,
    overconstrained: true });
}

export function isConstraintConflict(reason: unknown): reason is Error & { overconstrained: true; conflicting: string[]; constraints: Constraint[] } {
  return !!reason && typeof reason === 'object' && 'overconstrained' in reason && reason.overconstrained === true;
}

export interface Authoring<T> { sketch: Sketch; meta: T }
export type Apply<T> = (state: Authoring<T>, command: Command) => Authoring<T> | { error: string };
/** Serialises gestures and keeps geometry and consumer metadata in the same history. */
export class SketchController<T> {
  readonly history: History<Authoring<T>>;
  diagnosis?: Diagnosis;
  notice?: string;
  rejection?: unknown;
  lastSolveMs?: number;
  private readonly measureDrag = typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('sketchPerf') === '1';
  private session?: SketchSession;
  private ready: Promise<void>;
  private work: Promise<unknown> = Promise.resolve();
  private destroyed = false;
  private dragging = false;
  private dragStart?: Authoring<T>;
  private pending = 0;
  private placedDimension?: { id: string; state: Authoring<T> };
  private busy = false;
  private context?: { replace: (state: Authoring<T>) => Authoring<T>; conflictPrefix?: string };
  constructor(initial: Authoring<T>, private references: Reference[] | ((state: Authoring<T>) => Reference[]), private apply: Apply<T>, private changed: (state: Authoring<T>, diagnosis?: Diagnosis) => void, failed?: (reason: unknown) => void, private activity?: (busy: boolean) => void, private preview?: (state: Authoring<T>, diagnosis?: Diagnosis) => void, private dimensionProblem?: (state: Authoring<T>, id: string, value: number) => string | undefined) {
    this.history = new History(initial);
    this.ready = createSession(initial.sketch, { references: this.getReferences(initial) }).then(async session => {
      if (this.destroyed) { session.destroy(); return; }
      this.session = session;
      this.diagnosis = await session.solveFull();
      // Opening is a diagnosis, never an edit to the exact imported document.
      session.sketch = initial.sketch;
      if (!this.destroyed) this.changed(initial, this.diagnosis);
    });
    void this.ready.catch(reason => { if (!this.destroyed) failed?.(reason); });
  }
  private getReferences(state: Authoring<T>) { return typeof this.references === 'function' ? this.references(state) : this.references; }
  get capabilities() { return this.session?.capabilities; }
  get current() { return this.history.current; }
  private setActivity(busy: boolean) { if (busy !== this.busy) { this.busy = busy; if (!this.destroyed) this.activity?.(busy); } }
  private queue<R>(fn: (session: SketchSession) => Promise<R>): Promise<R> {
    this.pending++; this.setActivity(true);
    const result = this.work.then(async () => {
      await this.ready;
      if (this.destroyed || !this.session) throw new Error('Sketch session is closed.');
      return fn(this.session);
    }).finally(() => { this.pending--; this.setActivity(this.pending > 0 || this.dragging); });
    this.work = result.catch(() => {}); return result;
  }
  private async diagnose(session: SketchSession, state: Authoring<T>) {
    session.sketch = state.sketch;
    const diagnosis = await session.solveFull();
    if (diagnosis.status !== 'ok') throw constraintError(state.sketch, diagnosis);
    this.diagnosis = diagnosis;
    return { ...state, sketch: session.sketch };
  }
  dismissRejection() { this.rejection = undefined; }
  private publish(state: Authoring<T>) { this.dismissRejection(); if (!this.destroyed) this.changed(state, this.diagnosis); }
  private async transaction(session: SketchSession, label: string, edit: () => Promise<Authoring<T>>, mergePrevious = false, reportRejection = true) {
    const diagnosis = this.diagnosis; this.notice = undefined;
    this.history.begin(label);
    try {
      const next = await edit(); this.history.update(next); this.history.commit(mergePrevious); this.publish(next); return next.sketch;
    } catch (e) { this.rejection = reportRejection ? e : undefined; session.sketch = this.history.cancel().sketch; this.diagnosis = diagnosis; throw e; }
  }
  /** A function is resolved against the state the queued command actually runs on, so fields
   *  it does not change (e.g. the other coordinate of a point) are never stale. */
  command(commands: Command | Command[] | ((state: Authoring<T>) => Command | Command[]), dimensions?: { length?: number; angle?: number }) {
    return this.queue(session => this.transaction(session, 'Edit sketch', async () => {
      let state = this.current;
      const removed = new Set<string>();
      const resolved = typeof commands === 'function' ? commands(state) : commands;
      for (const command of Array.isArray(resolved) ? resolved : [resolved]) {
        if (command.kind === 'movePoint' && state.sketch.constraints.some(c => c.kind === 'fix' && c.point === command.id)) throw new Error('Point is held by constraints; remove its fix before editing coordinates.');
        const next = this.apply(state, command); if ('error' in next) throw new Error(next.error);
        if (['splitAt', 'deleteEntity', 'deletePoint', 'convertEntity'].includes(command.kind)) for (const c of state.sketch.constraints) if (!next.sketch.constraints.some(v => v.id === c.id)) removed.add(c.id);
        if (command.kind === 'setDriving') {
          const result = await session.checkConstraintChange(state.sketch, next.sketch, command.id);
          if (!result.ok) throw constraintError(next.sketch, result);
          this.diagnosis = result.diagnosis;
          state = { ...next, sketch: result.sketch };
        } else state = next;
        if (command.kind === 'movePoint') {
          const solved = await this.diagnose(session, state), p = solved.sketch.points.find(p => p.id === command.id)!;
          if (Math.hypot(p.x - command.x, p.y - command.y) > 1e-7) throw new Error('Point is held by constraints; this coordinate cannot be reached.');
          state = solved;
        }
      }
      // Names and dimension positions are authoring metadata. Solving them could perturb exact imported coordinates
      // or refuse a rename on a sketch that is already in conflict.
      const annotationsOnly = (Array.isArray(resolved) ? resolved : [resolved]).every(c => c.kind === 'setConstraintLabel' || c.kind === 'setEntityName' || c.kind === 'setDimensionPlacement');
      if (annotationsOnly) session.sketch = state.sketch;
      else state = await this.diagnose(session, state);
      if (dimensions) {
        const added = state.sketch.entities.find(e => !this.current.sketch.entities.some(old => old.id === e.id));
        if (added?.kind === 'line') {
          const constraints: NewConstraint[] = dimensions.length === undefined ? [] : [{ kind: 'distance', a: added.p1, b: added.p2, value: dimensions.length, driving: true }];
          if (dimensions.angle !== undefined) {
            // A fixed construction baseline provides a generic absolute angle reference.
            let baseline = state.sketch.entities.find(e => e.reference && e.kind === 'line' && (() => {
              const a = state.sketch.points.find(p => p.id === e.p1)!, b = state.sketch.points.find(p => p.id === e.p2)!;
              return a.y === b.y && b.x > a.x;
            })());
            if (!baseline) {
              const line = this.apply(state, { kind: 'addLine', from: { x: 0, y: 0 }, to: { x: 1, y: 0 } });
              if ('error' in line) throw new Error(line.error);
              baseline = line.sketch.entities.find(e => !state.sketch.entities.some(old => old.id === e.id));
              if (baseline?.kind !== 'line') throw new Error('Could not create an angle reference.');
              const construction = this.apply(line, { kind: 'setConstruction', id: baseline.id, construction: true });
              if ('error' in construction) throw new Error(construction.error);
              state = { ...construction, sketch: { ...construction.sketch, entities: construction.sketch.entities.map(e => e.id === baseline!.id ? { ...e, data: { angleBaseline: true } } : e) } };
              for (const point of [baseline.p1, baseline.p2]) {
                const result = await session.tryAddConstraint(state.sketch, { kind: 'fix', point });
                if (!result.ok) throw new Error(result.reason);
                state = { ...state, sketch: result.sketch };
              }
            }
            constraints.push({ kind: 'angle', a: baseline.id, b: added.id, value: dimensions.angle * Math.PI / 180, driving: true });
          }
          for (const constraint of constraints) {
            const result = await session.tryAddConstraint(state.sketch, constraint, { ifRedundant: 'reference' });
            if (!result.ok) throw new Error(result.reason);
            state = { ...state, sketch: result.sketch }; this.diagnosis = result.diagnosis;
          }
        }
      }
      this.notice = removed.size ? `Removed constraints: ${[...removed].join(', ')}.` : undefined;
      return state;
    }));
  }
  /** A placed dimension can merge its first submitted value into the add's undo step. */
  constraint(constraint: NewConstraint, reference = false, placement = false) {
    return this.queue(async session => {
      const before = this.current;
      const sketch = await this.transaction(session, 'Constraint', async () => {
        const result = await session.tryAddConstraint(this.current.sketch, constraint, { ifRedundant: reference ? 'reference' : 'refuse' });
        if (!result.ok) throw constraintError(this.current.sketch, result);
        this.diagnosis = result.diagnosis; return { ...this.current, sketch: result.sketch };
      }, false, !placement);
      const added = sketch.constraints.find(c => !before.sketch.constraints.some(old => old.id === c.id));
      this.placedDimension = placement && added ? { id: added.id, state: this.current } : undefined;
      return sketch;
    });
  }
  dimension(id: string, value: number, mergePlacement = false) {
    return this.queue(async session => {
      // Identity also prevents merging across any intervening authoring edit.
      const merge = mergePlacement && this.placedDimension?.id === id && this.placedDimension.state === this.current;
      const sketch = await this.transaction(session, 'Dimension', async () => {
        const problem = this.dimensionProblem?.(this.current, id, value);
        if (problem) throw new Error(problem);
        session.sketch = this.current.sketch;
        const result = await session.setDimensionContinuation(id, value);
        if (!result.ok) throw new Error(result.reason);
        return this.diagnose(session, { ...this.current, sketch: result.sketch });
      }, merge);
      this.placedDimension = undefined;
      return sketch;
    });
  }
  adopt(state: Authoring<T>, label = 'Load contour', requireSolved = false) {
    return this.queue(session => this.transaction(session, label, async () => {
      const next = await createSession(state.sketch, { references: this.getReferences(state) });
      try { const diagnosis = await next.solveFull(); if (requireSolved && diagnosis.status !== 'ok') throw constraintError(state.sketch, diagnosis); this.diagnosis = diagnosis; }
      catch (e) { next.destroy(); throw e; }
      next.sketch = state.sketch; session.destroy(); this.session = next; return state;
    }));
  }
  materialiseContext(context: SketchContext, itemId: string) {
    return this.queue(async session => {
      let picked = '';
      const sketch = await this.transaction(session, 'Attach context reference', async () => {
        const next = materialiseContext(this.current.sketch, context, itemId); picked = next.id;
        // Fixed reference construction cannot change the contour on first attachment.
        session.sketch = next.sketch;
        return { ...this.current, sketch: next.sketch };
      });
      return { sketch, id: picked };
    });
  }
  /** External attachment context belongs to every undo/redo state, not sketch history. */
  replaceContext(replace: (state: Authoring<T>) => Authoring<T>, notice?: string, conflictPrefix?: string) {
    this.context = { replace, conflictPrefix };
    return this.queue(session => this.installContext(session, replace, notice, conflictPrefix));
  }
  private async installContext(session: SketchSession, replace: (state: Authoring<T>) => Authoring<T>, notice?: string, conflictPrefix?: string) {
    const current = this.current, state = replace(current);
    const next = await createSession(state.sketch, { references: this.getReferences(state) });
    try {
      const diagnosis = await next.solveFull();
      if (conflictPrefix && diagnosis.status !== 'ok') {
        const cause = constraintError(state.sketch, diagnosis);
        const constraints = state.sketch.constraints.filter(c => diagnosis.constraintStates[c.id] === 'conflicting');
        const named = constraints.length ? constraints : state.sketch.constraints.filter(c => 'driving' in c && c.driving);
        const conflict = Object.assign(cause, { message: `${conflictPrefix}${named.map(c => displayName(state.sketch, c.id)).join('; ') || cause.message}`, overconstrained: true, constraints: named, conflicting: named.map(c => c.id) });
        this.rejection = conflict; throw conflict;
      }
      this.diagnosis = diagnosis;
    } catch (e) { next.destroy(); throw e; }
    const referenceMoved = (before: Sketch, after: Sketch) => [...referencePointIds(after)].some(id => {
      const a = before.points.find(p => p.id === id), b = after.points.find(p => p.id === id)!;
      return !a || a.x !== b.x || a.y !== b.y;
    });
    // A display/clearance refresh must not perturb exact imported contour coordinates.
    const solved = { ...state, sketch: this.diagnosis.status === 'ok' && referenceMoved(current.sketch, state.sketch) ? next.sketch : state.sketch };
    session.destroy(); this.session = next;
    next.sketch = solved.sketch;
    this.notice = this.diagnosis.status === 'ok' ? notice ?? (referenceMoved(current.sketch, state.sketch) ? 'Reference context changed; contour updated' : undefined) : 'Context changed; contour could not be solved';
    this.history.current = solved;
    if (this.dragStart) this.dragStart = replace(this.dragStart);
    this.publish(this.current);
  }
  metadata(meta: T | ((current: T) => T)) {
    return this.queue(session => this.transaction(session, 'Metadata', async () => ({ ...this.current, meta: typeof meta === 'function' ? (meta as (current: T) => T)(this.current.meta) : meta })));
  }
  beginDrag(id: string | string[]) {
    return this.queue(async session => {
      this.notice = undefined; this.dragStart = this.current; this.history.begin('Drag'); session.sketch = this.current.sketch;
      try { await session.beginDrag(typeof id === 'string' ? [id] : id); this.dragging = true; }
      catch (e) { this.history.cancel(); throw e; }
    });
  }
  dragTo(id: string, target: XY, targets?: { pointId: string; x: number; y: number }[]) {
    return this.queue(async session => {
      const start = this.measureDrag ? performance.now() : 0;
      const result = await session.dragTo(targets ?? { pointId: id, ...target });
      if (this.measureDrag) this.lastSolveMs = performance.now() - start;
      this.history.update({ ...this.current, sketch: result.sketch });
      if (!this.destroyed) this.preview?.(this.current, this.diagnosis); return result;
    });
  }
  endDrag(cancel = false) {
    return this.queue(async session => {
      if (!this.dragging) return;
      try {
        this.diagnosis = await session.endDrag();
        const unchanged = this.current === this.dragStart || JSON.stringify(session.sketch) === JSON.stringify(this.dragStart!.sketch);
        if (cancel || this.diagnosis.status !== 'ok' || unchanged) {
          session.sketch = this.history.cancel().sketch;
          if (!this.destroyed) this.preview?.(this.current, this.diagnosis);
        } else {
          this.history.update({ ...this.current, sketch: session.sketch }); this.history.commit(); this.publish(this.current);
        }
      } finally { this.dragging = false; this.dragStart = undefined; }
    });
  }
  undo(redo = false) {
    return this.queue(async session => {
      this.notice = undefined; this.dismissRejection();
      const state = this.history.peek(redo);
      if (state === this.current) return;
      const next = await createSession(state.sketch, { references: this.getReferences(state) });
      let diagnosis: Diagnosis;
      try {
        diagnosis = await next.solveFull();
        if (diagnosis.status !== 'ok') throw new Error(diagnosis.reason ?? 'The history state cannot be solved.');
      } catch (e) { next.destroy(); throw e; }
      next.sketch = state.sketch; session.destroy(); this.session = next; this.diagnosis = diagnosis;
      if (redo) this.history.redo(); else this.history.undo();
      this.history.current = state;
      // Remember even a rejected host update. Historical sketches try the current
      // attachment before publishing; an infeasible one stays rolled back for repair.
      if (this.context) {
        const { replace, conflictPrefix } = this.context;
        try { await this.installContext(next, replace, undefined, conflictPrefix); }
        catch (reason) {
          if (!isConstraintConflict(reason)) throw reason;
          if (!this.destroyed) this.changed(state, this.diagnosis);
        }
      } else this.publish(state);
    });
  }
  destroy() { this.destroyed = true; void this.ready.catch(() => {}).then(() => this.work).finally(() => this.session?.destroy()); }
}
