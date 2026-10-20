import type { XY } from './solver/types';
export interface View { scale: number; offsetX: number; offsetY: number }
export interface Bounds { minX: number; minY: number; maxX: number; maxY: number }
export interface Viewport { width: number; height: number }
export const worldToScreen = (view: View, p: XY): XY => ({ x: view.offsetX + p.x * view.scale, y: view.offsetY - p.y * view.scale });
export const screenToWorld = (view: View, p: XY): XY => ({ x: (p.x - view.offsetX) / view.scale, y: (view.offsetY - p.y) / view.scale });
export function zoomAt(view: View, cursor: XY, factor: number): View {
  if (!(factor > 0) || !Number.isFinite(factor * view.scale)) throw new Error('Zoom must be positive and finite.');
  const p = screenToWorld(view, cursor), scale = view.scale * factor;
  return { scale, offsetX: cursor.x - p.x * scale, offsetY: cursor.y + p.y * scale };
}
export const pan = (view: View, dx: number, dy: number): View => ({ ...view, offsetX: view.offsetX + dx, offsetY: view.offsetY + dy });
export function fit(bounds: Bounds, viewport: Viewport, margin = 24): View {
  const { minX, minY, maxX, maxY } = bounds, { width, height } = viewport;
  if (![minX, minY, maxX, maxY, width, height, margin].every(Number.isFinite) || minX > maxX || minY > maxY
    || margin < 0 || width <= 2 * margin || height <= 2 * margin) throw new Error('Invalid fit bounds or viewport.');
  const scale = Math.min((width - 2 * margin) / Math.max(maxX - minX, 1e-9), (height - 2 * margin) / Math.max(maxY - minY, 1e-9));
  return { scale, offsetX: width / 2 - (minX + maxX) / 2 * scale, offsetY: height / 2 + (minY + maxY) / 2 * scale };
}
export function gridStep(scale: number): number {
  if (!(scale > 0) || !Number.isFinite(scale)) throw new Error('Scale must be positive and finite.');
  const minimum = 8 / scale, power = 10 ** Math.floor(Math.log10(minimum));
  return [1, 2, 5, 10].find(n => n * power >= minimum)! * power;
}

/** Visible segment midpoint, or the nearest visible point to a hover cursor. */
export function visibleAnchor(points: XY[], viewport: Viewport, near?: XY): XY | undefined {
  const inside = (p: XY) => p.x >= 0 && p.x <= viewport.width && p.y >= 0 && p.y <= viewport.height;
  if (points.length === 1) return inside(points[0]) ? points[0] : undefined;
  let nearest: XY | undefined, distance = Infinity;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i], delta = { x: b.x - a.x, y: b.y - a.y };
    let low = 0, high = 1;
    for (const [at, d, max] of [[a.x, delta.x, viewport.width], [a.y, delta.y, viewport.height]]) {
      if (d === 0) { if (at < 0 || at > max) high = -1; }
      else { const t0 = -at / d, t1 = (max - at) / d; low = Math.max(low, Math.min(t0, t1)); high = Math.min(high, Math.max(t0, t1)); }
    }
    if (low <= high) {
      const t = near ? Math.max(low, Math.min(high, ((near.x - a.x) * delta.x + (near.y - a.y) * delta.y) / (delta.x ** 2 + delta.y ** 2 || 1))) : (low + high) / 2;
      const p = { x: a.x + delta.x * t, y: a.y + delta.y * t };
      if (!near) return p;
      const d = Math.hypot(p.x - near.x, p.y - near.y);
      if (d < distance) { nearest = p; distance = d; }
    }
  }
  return nearest;
}

/** Pixel scroll gestures pan; discrete wheels zoom, and ctrl-wheel is a pinch. */
export function wheelGesture(event: Pick<WheelEvent, 'deltaX' | 'deltaY' | 'deltaMode' | 'ctrlKey'>):
  { kind: 'pan'; dx: number; dy: number } | { kind: 'zoom'; factor: number } {
  const { deltaX, deltaY, deltaMode, ctrlKey } = event;
  if (!ctrlKey && deltaMode === 0 && (deltaX !== 0 || Math.abs(deltaY) < 40))
    return { kind: 'pan', dx: -deltaX, dy: -deltaY };
  const pixels = deltaY * (deltaMode === 1 ? 16 : deltaMode === 2 ? 360 : 1);
  return { kind: 'zoom', factor: Math.exp(-pixels * (ctrlKey ? .01 : .001)) };
}
