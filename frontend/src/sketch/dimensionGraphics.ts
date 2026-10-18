import type { Constraint, Sketch } from './model';
import { arcGeometry } from './geometry';
import type { XY } from './solver/types';

export const dimensionHelper = (e: Sketch['entities'][number]) => !!e.data && typeof e.data === 'object' && !Array.isArray(e.data) && e.data.dimensionHelper === true;
export function dimensionName(c: Constraint): string {
  const names: Record<string, string> = { distance: 'Distance', distanceX: 'Horizontal distance', distanceY: 'Vertical distance',
    radius: 'Radius', angle: 'Angle', pointLineDistance: 'Point-line distance' };
  return c.label?.trim() || names[c.kind] || c.kind;
}
export function dimensionLabel(c: Constraint & { value: number; driving: boolean }, mode: 'value' | 'name' = 'value') {
  const value = (c.kind === 'angle' ? c.value * 180 / Math.PI : c.value).toFixed(2);
  return `${c.driving ? '' : '('}${mode === 'name' ? `${dimensionName(c)} ` : ''}${c.kind === 'radius' ? 'R ' : ''}${value}${c.kind === 'angle' ? '°' : ' mm'}${c.driving ? '' : ')'}`;
}
export interface DimensionLayout { at: XY; path: string; arrows: string }
/** Screen-space lanes keep named labels apart, independent of short measured spans. */
export function dimensionLayout(sketch: Sketch, c: Constraint, xy: (id: string) => XY, bounds: { right: number; bottom: number; top: number }, index: number): DimensionLayout | undefined {
  if (!('value' in c)) return;
  let a: XY, b: XY;
  if ('a' in c && ['distance', 'distanceX', 'distanceY'].includes(c.kind)) { a = xy(c.a); b = xy(c.b); }
  else if (c.kind === 'radius') {
    const arc = sketch.entities.find(e => e.id === c.arc);
    if (arc?.kind !== 'arc') return;
    const g = arcGeometry(sketch, arc), center = xy(arc.center), start = xy(arc.start);
    const scale = Math.hypot(start.x - center.x, start.y - center.y) / g.radius, angle = g.start + g.sweep / 2;
    a = center; b = { x: center.x + Math.cos(angle) * g.radius * scale, y: center.y - Math.sin(angle) * g.radius * scale };
  } else return;
  const segment = (p: XY, q: XY) => `M${p.x},${p.y} L${q.x},${q.y}`;
  const arrow = (p: XY, angle: number) => {
    const wing = (offset: number) => ({ x: p.x + 6 * Math.cos(angle + offset), y: p.y + 6 * Math.sin(angle + offset) });
    return `${segment(wing(.45), p)} ${segment(p, wing(-.45))}`;
  };
  if (c.kind === 'distanceX') {
    const y = bounds.bottom + 26 + index * 24, p = { x: a.x, y }, q = { x: b.x, y };
    const at = { x: (a.x + b.x) / 2, y: y - 6 };
    return { at, path: `${segment(a, { x: a.x, y: y + 6 })} ${segment(b, { x: b.x, y: y + 6 })} ${segment(p, q)}`,
      arrows: `${arrow(p, a.x < b.x ? 0 : Math.PI)} ${arrow(q, a.x < b.x ? Math.PI : 0)}` };
  }
  if (c.kind === 'distanceY') {
    const x = bounds.right + 22 + index * 14, p = { x, y: a.y }, q = { x, y: b.y };
    const at = { x: bounds.right + 80, y: bounds.top - 18 - index * 24 };
    return { at, path: `${segment(a, { x: x + 6, y: a.y })} ${segment(b, { x: x + 6, y: b.y })} ${segment(p, q)} ${segment({ x, y: (a.y + b.y) / 2 }, { x, y: at.y + 4 })}`,
      arrows: `${arrow(p, a.y < b.y ? Math.PI / 2 : -Math.PI / 2)} ${arrow(q, a.y < b.y ? -Math.PI / 2 : Math.PI / 2)}` };
  }
  const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy) || 1, offset = 24 + index * 24;
  const p = { x: a.x - dy / length * offset, y: a.y + dx / length * offset }, q = { x: b.x - dy / length * offset, y: b.y + dx / length * offset };
  return { at: { x: (p.x + q.x) / 2, y: (p.y + q.y) / 2 - 6 }, path: `${segment(a, p)} ${segment(b, q)} ${segment(p, q)}`,
    arrows: `${arrow(p, Math.atan2(dy, dx))} ${arrow(q, Math.atan2(-dy, -dx))}` };
}
