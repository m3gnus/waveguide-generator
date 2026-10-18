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
