import type { XY } from '../solver/types';

export interface Modifiers { shift?: boolean; toggle?: boolean }
export function combineSelection(selection: string[], ids: string[], modifiers: Modifiers = {}): string[] {
  if (modifiers.toggle) return [...selection.filter(id => !ids.includes(id)), ...ids.filter(id => !selection.includes(id))];
  return modifiers.shift ? [...new Set([...selection, ...ids])] : ids;
}
export interface SelectState {
  start: XY; current: XY; hit?: string; selection: string[]; modifiers: Modifiers;
  phase: 'pending' | 'marquee' | 'drag';
}
export function startSelect(start: XY, hit: string | undefined, selection: string[], modifiers: Modifiers): SelectState {
  return { start, current: start, hit, selection, modifiers, phase: 'pending' };
}
export function moveSelect(state: SelectState, current: XY): SelectState {
  return { ...state, current, phase: state.phase === 'pending' && Math.hypot(current.x - state.start.x, current.y - state.start.y) >= 4 ? state.hit ? 'drag' : 'marquee' : state.phase };
}
export interface Cycle { at: XY; time: number; ids: string[]; index: number }
export function cycleHit(previous: Cycle | undefined, at: XY, time: number, ids: string[]): Cycle {
  const repeat = previous && time >= previous.time && time - previous.time <= 600 && Math.hypot(at.x - previous.at.x, at.y - previous.at.y) <= 3 && ids.join() === previous.ids.join();
  return { at, time, ids, index: repeat ? (previous.index + 1) % Math.max(1, ids.length) : 0 };
}
/** Resolve a selection gesture using a domain-supplied marquee query. */
export function finishWorkspaceSelection(state: SelectState, marquee: (state: SelectState) => string[]): string[] {
  if (state.phase === 'marquee') return marquee(state);
  if (state.phase === 'pending' && !state.modifiers.shift && !state.modifiers.toggle) return state.hit ? [state.hit] : [];
  return state.selection;
}

/** One pending target per animation frame. A flush on release preserves the final target. */
export function coalesceTargets<T>(schedule: (callback: () => void) => number, cancel: (id: number) => void, consume: (target: T) => void) {
  let frame: number | undefined, latest: T | undefined;
  const flush = () => {
    if (frame !== undefined) cancel(frame);
    frame = undefined;
    if (latest !== undefined) { const target = latest; latest = undefined; consume(target); }
  };
  return {
    push(target: T) { latest = target; frame ??= schedule(flush); },
    flush,
    cancel() { if (frame !== undefined) cancel(frame); frame = undefined; latest = undefined; },
  };
}

export const select = (selection: string[], id: string, extend = false): string[] => combineSelection(selection, [id], { toggle: extend });
