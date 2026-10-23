import type { SolveBlocker } from './actions';
/** Non-engine refusals (including MeshTooLarge) need only a message. */
export type SolveReadinessState = Pick<SolveBlocker, 'code' | 'message'> & Partial<Pick<SolveBlocker, 'engine' | 'remedies'>>;
export const messageBlocker = (message: string): SolveReadinessState => ({ code: 'solve_blocked', message, remedies: [] });
