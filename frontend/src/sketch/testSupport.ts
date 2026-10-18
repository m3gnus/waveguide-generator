import type { Sketch } from './model';
import { createSession, type Diagnosis } from './session';
import { emptySketch } from './model';
/** Load the real WASM before DOM tests start fake timers or make assertions. */
export async function realSessionReady() { const session = await createSession(emptySketch(), { references: [] }); session.destroy(); }
export const initialDiagnosis = (sketch: Sketch): Diagnosis => ({ sketch, status: 'ok', dof: 2, points: {}, constraintStates: {}, pointDetermined: undefined,
  timings: { totalMs: 0, updateMs: 0, solveMs: 0, readbackMs: 0, marshalMs: 0 } });
