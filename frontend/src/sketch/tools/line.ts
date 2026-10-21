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

/** One prospective segment for rendering and either confirmation gesture. Locks override snaps
 * only in the coordinates they drive; retain a snap relationship when it still fits. */
export function prospectiveLine(from: XY | undefined, cursor: XY, snap: SnapResult | null,
  typed?: { length: string; angle: string }): { endpoint: XY; snap: SnapResult | null; dimensions?: { length?: number; angle?: number }; reason?: string } {
  const target = snap ?? cursor;
  if (!from || !typed) return { endpoint: target, snap };
  const length = typed.length.trim() ? Number(typed.length) : undefined;
  const angle = typed.angle.trim() ? Number(typed.angle) : undefined;
  if (length !== undefined && (!(length > 0) || !Number.isFinite(length)) || angle !== undefined && !Number.isFinite(angle))
    return { endpoint: target, snap: null, reason: 'Enter a positive length and a finite angle.' };
  if (length === undefined && angle === undefined) return { endpoint: target, snap };
  const endpoint = typedTarget(from, target, length ?? Math.hypot(target.x - from.x, target.y - from.y), angle);
  const c = snap?.constraint;
  const compatible = snap && (c?.kind === 'horizontal' ? Math.abs(endpoint.y - snap.y) < 1e-8
    : c?.kind === 'vertical' || snap.glyph === 'axis' ? Math.abs(endpoint.x - snap.x) < 1e-8
      : snap.glyph === 'baffle' ? Math.abs(endpoint.y - snap.y) < 1e-8 : Math.hypot(endpoint.x - snap.x, endpoint.y - snap.y) < 1e-8);
  return { endpoint, snap: compatible ? { ...snap, ...endpoint } : null, dimensions: { length, angle } };
}
