import type { Reference } from '../adapter';
import { constraintError, diagnoseSketch, type Authoring } from './sketchSolvePolicy';
import { createSession, type SketchSession } from '../session';
import type { SolverBinding, SolveHandle } from './types';

/** Solver operations remain in SketchSession; the handle adds authoring-state binding. */
export class SketchSolveHandle<M> implements SolveHandle<Authoring<M>> {
  constructor(private session: SketchSession,
    private dimensionProblem?: (state: Authoring<M>, id: string, value: number) => string | undefined) {}
  get capabilities() { return this.session.capabilities; }
  get sketch() { return this.session.sketch; }
  set sketch(sketch) { this.session.sketch = sketch; }
  solveFull() { return this.session.solveFull(); }
  diagnose(state: Authoring<M>) { return diagnoseSketch(this.session, state); }
  beginDrag(ids: string[]) { return this.session.beginDrag(ids); }
  dragTo(targets: Parameters<SketchSession['dragTo']>[0]) { return this.session.dragTo(targets); }
  endDrag() { return this.session.endDrag(); }
  tryAddConstraint(...args: Parameters<SketchSession['tryAddConstraint']>) { return this.session.tryAddConstraint(...args); }
  checkConstraintChange(...args: Parameters<SketchSession['checkConstraintChange']>) { return this.session.checkConstraintChange(...args); }
  setDimensionContinuation(...args: Parameters<SketchSession['setDimensionContinuation']>) { return this.session.setDimensionContinuation(...args); }
  async constraint(state: Authoring<M>, constraint: Parameters<SketchSession['tryAddConstraint']>[1], options: { reference: boolean }) {
    const result = await this.tryAddConstraint(state.sketch, constraint, { ifRedundant: options.reference ? 'reference' : 'refuse' });
    if (!result.ok) throw constraintError(state.sketch, result);
    return { state: { ...state, sketch: result.sketch }, diagnosis: result.diagnosis };
  }
  async dimension(state: Authoring<M>, id: string, value: number) {
    const problem = this.dimensionProblem?.(state, id, value);
    if (problem) throw new Error(problem);
    this.sketch = state.sketch;
    const result = await this.setDimensionContinuation(id, value);
    if (!result.ok) throw new Error(result.reason);
    return this.diagnose({ ...state, sketch: result.sketch });
  }
  destroy() { this.session.destroy(); }
}
export async function openSketchHandle<M>(sketch: Authoring<M>['sketch'], options: Parameters<typeof createSession>[1],
  dimensionProblem?: (state: Authoring<M>, id: string, value: number) => string | undefined) {
  return new SketchSolveHandle<M>(await createSession(sketch, options), dimensionProblem);
}
export function sketchSolver<M>(references: (state: Authoring<M>) => Reference[],
  dimensionProblem?: (state: Authoring<M>, id: string, value: number) => string | undefined): SolverBinding<Authoring<M>> {
  return { async open(initial) {
    return openSketchHandle(initial.sketch, { references: references(initial) }, dimensionProblem);
  } };
}
