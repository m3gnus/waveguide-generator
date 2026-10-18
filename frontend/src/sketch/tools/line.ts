import type { Command, PointRef } from '../commands';
import type { Sketch } from '../model';
import type { SnapResult } from '../snap';
import type { XY } from '../solver/types';
import { tangentIncoming } from '../geometry';

export interface LineState { from?: PointRef; end?: PointRef; mode: 'line' | 'tangent' | 'arc3' }
export const lineState = (): LineState => ({ mode: 'line' });
export const cancelLine = (): LineState => lineState();
export function placeLine(state: LineState, cursor: XY, snap: SnapResult | null, sketch?: Sketch): { state: LineState; command?: Command; reason?: string } {
  const to = { ...cursor, snap };
  if (state.mode === 'tangent' && typeof state.from !== 'string') {
    const c = snap?.constraint;
    if (c?.kind === 'coincident') {
      try { if (sketch) tangentIncoming(sketch, c.point); }
      catch (reason) { return { state, reason: reason instanceof Error ? reason.message : String(reason) }; }
      return { state: { ...state, from: c.point } };
    }
    return { state: { mode: 'arc3', from: to }, reason: 'No chain end here: started a 3-point arc. Choose end 2 of 3.' };
  }
  if (!state.from) return { state: { ...state, from: to } };
  if (state.mode === 'arc3' && !state.end) return { state: { ...state, end: to } };
  const command: Command = state.mode === 'arc3'
    ? { kind: 'addArc3', start: state.from, end: state.end!, through: cursor }
    : state.mode === 'tangent' && typeof state.from === 'string'
      ? { kind: 'addTangentArc', from: state.from, to }
      : { kind: 'addLine', from: state.from, to };
  return { state, command };
}
export function continueLine(state: LineState, before: Sketch, after: Sketch): LineState {
  const added = after.entities.find(e => !before.entities.some(old => old.id === e.id) && !e.construction);
  const from = added?.kind === 'line' ? added.p2 : added?.kind === 'arc' ? added.end : state.from;
  return { mode: state.mode, from };
}
export function typedTarget(from: XY, cursor: XY, length: number, degrees?: number): XY {
  const angle = degrees === undefined ? Math.atan2(cursor.y - from.y, cursor.x - from.x) : degrees * Math.PI / 180;
  return { x: from.x + length * Math.cos(angle), y: from.y + length * Math.sin(angle) };
}
