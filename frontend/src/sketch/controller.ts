import type { Command } from './commands';
import type { Reference } from './adapter';
import type { NewConstraint, Sketch } from './model';
import { EditCore } from './workspace/editCore';
import { LocalHistoryAuthority, type HistoryAuthority, type HistoryCause } from './workspace/historyAuthority';
import type { Diagnosis } from './session';
import { openSketchHandle, type SketchSolveHandle } from './workspace/sketchSolver';
import { materialiseContext, referencePointIds, type SketchContext } from './context';
import type { XY } from './solver/types';
import { displayName } from './displayName';

import { constraintError, isConstraintConflict, type Authoring } from './workspace/sketchSolvePolicy';
export { isConstraintConflict, type Authoring } from './workspace/sketchSolvePolicy';
export type Apply<T> = (state: Authoring<T>, command: Command) => Authoring<T> | { error: string };

/** Serialises gestures and keeps geometry and consumer metadata in the same history. */
export interface SketchControllerOptions<T> {
  initial: Authoring<T>;
  authority?: HistoryAuthority<Authoring<T>>;
  references: Reference[] | ((state: Authoring<T>) => Reference[]);
  apply: Apply<T>;
  changed: (state: Authoring<T>, diagnosis?: Diagnosis) => void;
  failed?: (reason: unknown) => void;
  activity?: (busy: boolean) => void;
  preview?: (state: Authoring<T>, diagnosis?: Diagnosis) => void;
  dimensionProblem?: (state: Authoring<T>, id: string, value: number) => string | undefined;
  historyNavigation?: () => void;
}
export class SketchController<T> {
  private readonly core: EditCore<Authoring<T>, SketchSolveHandle<T>>;
  readonly authority: HistoryAuthority<Authoring<T>>;
  get historyKind() { return this.authority.kind; }
  subscribeHistory = (listener: (cause: HistoryCause) => void) => this.core.subscribeHistory(listener);
  get current() { return this.core.current; }
  get busy() { return this.core.busy; }
  get ready() { return this.core.ready; }
  get notice() { return this.core.notice; }
  set notice(notice: string | undefined) { this.core.notice = notice; }
  get rejection() { return this.core.rejection; }
  set rejection(reason: unknown) { this.core.rejection = reason; }
  dismissRejection() { this.core.dismissRejection(); }
  get history() {
    if (!(this.authority instanceof LocalHistoryAuthority)) throw new Error('Project history is owned by the design store.');
    return this.authority.history;
  }
  diagnosis?: Diagnosis;
  lastSolveMs?: number;
  private readonly measureDrag = typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('sketchPerf') === '1';
  private placedDimension?: { id: string; state: Authoring<T> };
  private context?: { replace: (state: Authoring<T>) => Authoring<T>; conflictPrefix?: string };
  constructor(private config: SketchControllerOptions<T>) {
    const { initial, activity } = config;
    const authority = config.authority ?? new LocalHistoryAuthority(initial, (state, accept) => this.restoreHistory(state, accept));
    this.authority = authority;
    this.core = new EditCore({ initial, authority, activity,
      restore: { cancelGesture: () => this.cancelGesture(), restoreState: state => this.restoreState(state) },
      changed: state => this.publish(state), preview: state => this.showPreview(state) });
    this.core.ready = openSketchHandle<T>(initial.sketch, { references: this.getReferences(initial) }, this.config.dimensionProblem).then(async session => {
      if (this.core.destroyed) { session.destroy(); return; }
      this.core.session = session;
      this.diagnosis = await session.solveFull();
      // Opening is a diagnosis, never an edit to the exact imported document.
      session.sketch = initial.sketch;
      if (!this.core.destroyed && (this.authority.kind === 'local' || this.current === initial)) this.config.changed(initial, this.diagnosis);
    });
    void this.core.ready.catch(reason => { if (!this.core.destroyed) this.config.failed?.(reason); });
  }
  private getReferences(state: Authoring<T>) { return typeof this.config.references === 'function' ? this.config.references(state) : this.config.references; }
  get capabilities() { return this.core.session?.capabilities; }
  private async diagnose(session: SketchSolveHandle<T>, state: Authoring<T>) {
    const result = await session.diagnose(state);
    this.diagnosis = result.diagnosis;
    return result.state;
  }
  private publish(state: Authoring<T>) { this.dismissRejection(); if (!this.core.destroyed) this.config.changed(state, this.diagnosis); }
  private showPreview(state: Authoring<T>) { if (!this.core.destroyed) this.config.preview?.(state, this.diagnosis); }
  private async transaction(session: SketchSolveHandle<T>, label: string, edit: () => Promise<Authoring<T>>, mergePrevious = false, reportRejection = true) {
    const diagnosis = this.diagnosis;
    try { return (await this.core.transact(label, edit, mergePrevious, reportRejection)).sketch; }
    catch (reason) { session.sketch = this.current.sketch; this.diagnosis = diagnosis; throw reason; }
  }
  /** A function is resolved against the state the queued command actually runs on, so fields
   *  it does not change (e.g. the other coordinate of a point) are never stale. */
  command(commands: Command | Command[] | ((state: Authoring<T>) => Command | Command[]), dimensions?: { length?: number; angle?: number }) {
    return this.core.enqueue(session => this.transaction(session, 'Edit sketch', async () => {
      let state = this.current;
      const removed = new Set<string>();
      const resolved = typeof commands === 'function' ? commands(state) : commands;
      for (const command of Array.isArray(resolved) ? resolved : [resolved]) {
        if (command.kind === 'movePoint' && state.sketch.constraints.some(c => c.kind === 'fix' && c.point === command.id)) throw new Error('Point is held by constraints; remove its fix before editing coordinates.');
        const next = this.config.apply(state, command); if ('error' in next) throw new Error(next.error);
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
              const line = this.config.apply(state, { kind: 'addLine', from: { x: 0, y: 0 }, to: { x: 1, y: 0 } });
              if ('error' in line) throw new Error(line.error);
              baseline = line.sketch.entities.find(e => !state.sketch.entities.some(old => old.id === e.id));
              if (baseline?.kind !== 'line') throw new Error('Could not create an angle reference.');
              const construction = this.config.apply(line, { kind: 'setConstruction', id: baseline.id, construction: true });
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
    return this.core.enqueue(async session => {
      const before = this.current;
      const sketch = await this.transaction(session, 'Constraint', async () => {
        const result = await session.constraint(this.current, constraint, { reference });
        this.diagnosis = result.diagnosis; return result.state;
      }, false, !placement);
      const added = sketch.constraints.find(c => !before.sketch.constraints.some(old => old.id === c.id));
      this.placedDimension = placement && added ? { id: added.id, state: this.current } : undefined;
      return sketch;
    });
  }
  dimension(id: string, value: number, mergePlacement = false) {
    return this.core.enqueue(async session => {
      // Identity also prevents merging across any intervening authoring edit.
      const merge = mergePlacement && this.placedDimension?.id === id && this.placedDimension.state === this.current;
      const sketch = await this.transaction(session, 'Dimension', async () => {
        const result = await session.dimension(this.current, id, value);
        this.diagnosis = result.diagnosis; return result.state;
      }, merge);
      this.placedDimension = undefined;
      return sketch;
    });
  }
  /** Resolve offers when queued work starts, so intervening edits can invalidate them. */
  adopt(state: Authoring<T> | ((current: Authoring<T>) => Authoring<T> | undefined), label = 'Load contour', requireSolved = false) {
    return this.core.enqueue(async session => {
      const candidate = typeof state === 'function' ? state(this.current) : state;
      if (!candidate) return;
      return this.transaction(session, label, async () => {
        const next = await openSketchHandle<T>(candidate.sketch, { references: this.getReferences(candidate) }, this.config.dimensionProblem);
        try { const diagnosis = await next.solveFull(); if (requireSolved && diagnosis.status !== 'ok') throw constraintError(candidate.sketch, diagnosis); this.diagnosis = diagnosis; }
        catch (e) { next.destroy(); throw e; }
        next.sketch = candidate.sketch; session.destroy(); this.core.session = next; return candidate;
      });
    });
  }
  materialiseContext(context: SketchContext, itemId: string) {
    return this.core.enqueue(async session => {
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
    return this.core.enqueue(session => this.installContext(session, replace, notice, conflictPrefix));
  }
  private async installContext(session: SketchSolveHandle<T>, replace: (state: Authoring<T>) => Authoring<T>, notice?: string, conflictPrefix?: string) {
    if (this.authority.kind === 'project') this.authority.begin('Refresh context');
    const current = this.current;
    let next: SketchSolveHandle<T> | undefined;
    let state: Authoring<T>;
    try {
      state = replace(current);
      next = await openSketchHandle<T>(state.sketch, { references: this.getReferences(state) }, this.config.dimensionProblem);
      const diagnosis = await next.solveFull();
      if (conflictPrefix && diagnosis.status !== 'ok') {
        const cause = constraintError(state.sketch, diagnosis);
        const constraints = state.sketch.constraints.filter(c => diagnosis.constraintStates[c.id] === 'conflicting');
        const named = constraints.length ? constraints : state.sketch.constraints.filter(c => 'driving' in c && c.driving);
        const conflict = Object.assign(cause, { message: `${conflictPrefix}${named.map(c => displayName(state.sketch, c.id)).join('; ') || cause.message}`, overconstrained: true, constraints: named, conflicting: named.map(c => c.id) });
        this.rejection = conflict; throw conflict;
      }
      this.diagnosis = diagnosis;
    } catch (e) {
      next?.destroy();
      if (this.authority.kind === 'project') this.authority.cancel();
      throw e;
    }
    const referenceMoved = (before: Sketch, after: Sketch) => [...referencePointIds(after)].some(id => {
      const a = before.points.find(p => p.id === id), b = after.points.find(p => p.id === id)!;
      return !a || a.x !== b.x || a.y !== b.y;
    });
    // A display/clearance refresh must not perturb exact imported contour coordinates.
    const solved = { ...state, sketch: this.diagnosis.status === 'ok' && referenceMoved(current.sketch, state.sketch) ? next.sketch : state.sketch };
    session.destroy(); this.core.session = next;
    next.sketch = solved.sketch;
    this.notice = this.diagnosis.status === 'ok' ? notice ?? (referenceMoved(current.sketch, state.sketch) ? 'Reference context changed; contour updated' : undefined) : 'Context changed; contour could not be solved';
    if (this.authority.kind === 'project') {
      this.authority.update(solved); this.authority.commit({ amend: true });
    } else this.authority.replace(solved);
    if (this.core.dragStart) this.core.dragStart = replace(this.core.dragStart);
    this.publish(this.current);
  }
  metadata(meta: T | ((current: T) => T)) {
    return this.core.enqueue(session => this.transaction(session, 'Metadata', async () => ({ ...this.current, meta: typeof meta === 'function' ? (meta as (current: T) => T)(this.current.meta) : meta })));
  }
  beginDrag(id: string | string[]) {
    return this.core.enqueue(async session => {
      this.core.startDrag(); session.sketch = this.current.sketch;
      try { await session.beginDrag(typeof id === 'string' ? [id] : id); this.core.dragging = true; }
      catch (e) { this.authority.cancel(); this.core.dragStart = undefined; throw e; }
    });
  }
  dragTo(id: string, target: XY, targets?: { pointId: string; x: number; y: number }[]) {
    return this.core.enqueue(async session => {
      if (!this.core.dragging) return { sketch: this.current.sketch, stopped: true, steps: 0 };
      const start = this.measureDrag ? performance.now() : 0;
      const result = await session.dragTo(targets ?? { pointId: id, ...target });
      if (this.measureDrag) this.lastSolveMs = performance.now() - start;
      this.core.updateDrag({ ...this.current, sketch: result.sketch }); return result;
    });
  }
  endDrag(cancel = false) {
    return this.core.enqueue(async session => {
      if (!this.core.dragging) return;
      const diagnosis = this.diagnosis;
      try {
        this.diagnosis = await session.endDrag();
        const rejected = this.diagnosis.status !== 'ok';
        const unchanged = this.current === this.core.dragStart || JSON.stringify(session.sketch) === JSON.stringify(this.core.dragStart!.sketch);
        if (cancel || rejected || unchanged) {
          this.diagnosis = diagnosis;
          this.core.finishDrag(cancel || unchanged && !rejected, this.current, rejected);
          session.sketch = this.current.sketch;
        } else this.core.finishDrag(false, { ...this.current, sketch: session.sketch });
      } finally { this.core.dragging = false; this.core.dragStart = undefined; }
    });
  }
  undo(redo = false) {
    if (this.authority.kind === 'project') return this.authority.undo(redo);
    return this.core.enqueue(async () => {
      this.config.historyNavigation?.();
      this.notice = undefined; this.dismissRejection();
      await this.authority.undo(redo);
    });
  }
  private async restoreHistory(state: Authoring<T>, accept: () => void) {
    const session = this.core.session!;
    const next = await openSketchHandle<T>(state.sketch, { references: this.getReferences(state) }, this.config.dimensionProblem);
    let diagnosis: Diagnosis;
    try {
      diagnosis = await next.solveFull();
      if (diagnosis.status !== 'ok') throw new Error(diagnosis.reason ?? 'The history state cannot be solved.');
    } catch (e) { next.destroy(); throw e; }
    const previousDiagnosis = this.diagnosis;
    next.sketch = state.sketch; this.core.session = next; this.diagnosis = diagnosis;
    accept();
    this.authority.replace(state);
    // Remember even a rejected host update. Historical sketches try the current
    // attachment before publishing; an infeasible one stays rolled back for repair.
    if (this.context) {
      const { replace, conflictPrefix } = this.context;
      try { await this.installContext(next, replace, undefined, conflictPrefix); }
      catch (reason) {
        if (!isConstraintConflict(reason)) {
          // The host could not be installed: keep the published state and history aligned.
          this.core.session = session; this.diagnosis = previousDiagnosis; next.destroy();
          throw reason;
        }
        if (!this.core.destroyed) this.config.changed(state, this.diagnosis);
      }
    } else this.publish(state);
    session.destroy();
  }
  private async cancelGesture() {
    if (this.core.dragging) { await this.core.session!.endDrag(); this.core.finishDrag(true); }
  }
  private async restoreState(state: Authoring<T>) {
    const next = await openSketchHandle<T>(state.sketch, { references: this.getReferences(state) }, this.config.dimensionProblem);
    try { this.diagnosis = await next.solveFull(); }
    catch (reason) { next.destroy(); throw reason; }
    // External history is authoritative, including an unsolvable historic sketch.
    next.sketch = state.sketch; this.core.session?.destroy(); this.core.session = next;
    this.placedDimension = undefined;
    return state;
  }
  rehydrate(state: Authoring<T>) {
    return this.core.rehydrate(state, { cancelGesture: () => this.cancelGesture(), restoreState: state => this.restoreState(state) });
  }
  destroy() { this.core.close(() => this.core.session?.destroy()); }
}
