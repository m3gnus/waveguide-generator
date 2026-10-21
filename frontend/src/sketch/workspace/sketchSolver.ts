import type { Reference } from '../adapter';
import { constraintError, diagnoseSketch, type Authoring } from '../controller';
import { createSession } from '../session';
import type { SolverBinding } from './types';

/** Adapt the existing SketchSession; transaction/history ownership stays in W3. */
export function sketchSolver<M>(references: (state: Authoring<M>) => Reference[],
  dimensionProblem?: (state: Authoring<M>, id: string, value: number) => string | undefined): SolverBinding<Authoring<M>> {
  return { async open(initial) {
    const session = await createSession(initial.sketch, { references: references(initial) });
    return {
      capabilities: session.capabilities,
      diagnose: state => diagnoseSketch(session, state),
      beginDrag: ids => session.beginDrag(ids),
      dragTo: targets => session.dragTo(targets),
      endDrag: () => session.endDrag(),
      async constraint(state, constraint, options) {
        const result = await session.tryAddConstraint(state.sketch, constraint, { ifRedundant: options.reference ? 'reference' : 'refuse' });
        if (!result.ok) throw constraintError(state.sketch, result);
        return { ...state, sketch: result.sketch };
      },
      async dimension(state, id, value) {
        const problem = dimensionProblem?.(state, id, value);
        if (problem) throw new Error(problem);
        session.sketch = state.sketch;
        const result = await session.setDimensionContinuation(id, value);
        if (!result.ok) throw new Error(result.reason);
        return (await diagnoseSketch(session, { ...state, sketch: result.sketch })).state;
      },
      destroy: () => session.destroy(),
    };
  } };
}
