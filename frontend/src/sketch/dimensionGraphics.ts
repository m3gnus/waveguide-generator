import type { Constraint, DimensionPlacement, Sketch } from './model';
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
export interface LabelBox { x: number; y: number; width: number; height: number }
export interface DimensionLayout {
  at: XY; path: string; arrows: string; rotation: number; placement: DimensionPlacement;
}
const segment = (p: XY, q: XY) => `M${p.x},${p.y} L${q.x},${q.y}`;
const arrow = (p: XY, angle: number) => {
  const wing = (offset: number) => ({ x: p.x + 6 * Math.cos(angle + offset), y: p.y + 6 * Math.sin(angle + offset) });
  return `${segment(wing(.45), p)} ${segment(p, wing(-.45))}`;
};
export function dimensionLabelBox(layout: DimensionLayout, label: string): LabelBox {
  const width = label.length * 6.4 + 12;
  return layout.rotation === -90
    ? { x: layout.at.x - 17, y: layout.at.y - width / 2, width: 28, height: width }
    : { x: layout.at.x - width / 2, y: layout.at.y - 17, width, height: 28 };
}
export const labelsOverlap = (a: LabelBox, b: LabelBox) => a.x < b.x + b.width + 4 && b.x < a.x + a.width + 4
  && a.y < b.y + b.height + 4 && b.y < a.y + a.height + 4;
type DimensionBounds = { left: number; right: number; bottom: number; top: number; height?: number };
function distancePoints(sketch: Sketch, c: Constraint): [XY, XY] | undefined {
  const point = (id: string) => sketch.points.find(p => p.id === id)!;
  if ('a' in c && ['distance', 'distanceX', 'distanceY'].includes(c.kind)) return [point(c.a), point(c.b)];
  if (c.kind !== 'pointLineDistance') return;
  const line = sketch.entities.find(e => e.id === c.line);
  if (line?.kind !== 'line') return;
  const a = point(c.point), p = point(line.p1), q = point(line.p2), dx = q.x - p.x, dy = q.y - p.y;
  const t = ((a.x - p.x) * dx + (a.y - p.y) * dy) / (dx * dx + dy * dy || 1);
  return [a, { x: p.x + dx * t, y: p.y + dy * t }];
}
function angleFrame(sketch: Sketch, c: Constraint) {
  if (c.kind !== 'angle') return;
  const a = sketch.entities.find(e => e.id === c.a), b = sketch.entities.find(e => e.id === c.b);
  if (a?.kind !== 'line' || b?.kind !== 'line') return;
  const point = (id: string) => sketch.points.find(p => p.id === id)!;
  const p = point(a.p1), q = point(a.p2), r = point(b.p1), s = point(b.p2);
  const dx = q.x - p.x, dy = q.y - p.y, ex = s.x - r.x, ey = s.y - r.y, cross = dx * ey - dy * ex;
  const t = Math.abs(cross) > 1e-12 ? ((r.x - p.x) * ey - (r.y - p.y) * ex) / cross : 0;
  return { center: { x: p.x + t * dx, y: p.y + t * dy }, start: Math.atan2(dy, dx), sweep: c.value };
}
/** Geometry-relative defaults; only colliding labels advance to an outward lane. */
export function dimensionLayout(sketch: Sketch, c: Constraint, xy: (id: string) => XY, bounds: DimensionBounds, lane = 0, scale = 1): DimensionLayout | undefined {
  if (!('value' in c)) return;
  const origin = sketch.points[0], screenOrigin = origin && xy(origin.id);
  const screen = (p: XY): XY => ({ x: screenOrigin.x + (p.x - origin.x) * scale, y: screenOrigin.y - (p.y - origin.y) * scale });
  if (c.kind === 'angle') {
    const frame = angleFrame(sketch, c); if (!frame) return;
    const placement = c.placement && 'along' in c.placement ? c.placement : { offset: (28 + lane * 32) / scale, along: .5 };
    const center = screen(frame.center), radius = placement.offset * scale;
    const at = (along: number, extra = 0): XY => {
      const angle = frame.start + frame.sweep * along;
      return { x: center.x + (radius + extra) * Math.cos(angle), y: center.y - (radius + extra) * Math.sin(angle) };
    };
    const lo = Math.min(0, placement.along), hi = Math.max(1, placement.along);
    const count = Math.max(1, Math.min(64, Math.ceil(Math.abs(frame.sweep * (hi - lo)) / Math.PI)));
    let path = `M${at(lo).x},${at(lo).y}`;
    for (let i = 1; i <= count; i++) { const p = at(lo + (hi - lo) * i / count); path += ` A${Math.abs(radius)},${Math.abs(radius)} 0 0 ${frame.sweep > 0 ? 0 : 1} ${p.x},${p.y}`; }
    const direction = frame.sweep >= 0 ? 1 : -1;
    return { at: at(placement.along), placement, rotation: 0,
      path: `${segment(center, at(0, 6))} ${segment(center, at(1, 6))} ${path}`,
      arrows: `${arrow(at(0), -frame.start - direction * Math.PI / 2)} ${arrow(at(1), -frame.start - frame.sweep + direction * Math.PI / 2)}` };
  }
  if (c.kind === 'radius') {
    const arc = sketch.entities.find(e => e.id === c.arc);
    if (arc?.kind !== 'arc') return;
    const g = arcGeometry(sketch, arc), center = xy(arc.center);
    const placement = c.placement && 'angle' in c.placement ? c.placement : { angle: g.start + g.sweep / 2, offset: (24 + lane * 32) / scale };
    const direction = { x: Math.cos(placement.angle), y: -Math.sin(placement.angle) };
    const b = { x: center.x + direction.x * g.radius * scale, y: center.y + direction.y * g.radius * scale };
    const at = { x: center.x + direction.x * (g.radius + placement.offset) * scale, y: center.y + direction.y * (g.radius + placement.offset) * scale };
    return { at, path: segment(center, at), arrows: arrow(b, Math.atan2(direction.y, direction.x) + Math.PI), rotation: 0, placement };
  }
  const measured = distancePoints(sketch, c); if (!measured) return;
  const [a, b] = measured.map(screen), middle = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  let tangent: XY, normal: XY, offset: number;
  if (c.kind === 'distanceX') {
    tangent = { x: b.x >= a.x ? 1 : -1, y: 0 }; normal = { x: 0, y: -1 };
    const below = (middle.y >= (bounds.top + bounds.bottom) / 2) !== (lane < 0), outward = lane < 0 ? -lane - 1 : lane;
    const y = below ? Math.max(a.y, b.y) + 26 + outward * 32 : Math.min(a.y, b.y) - 26 - outward * 32;
    offset = (middle.y - y) / scale;
  } else if (c.kind === 'distanceY') {
    tangent = { x: 0, y: b.y >= a.y ? 1 : -1 }; normal = { x: 1, y: 0 };
    const right = middle.x > (bounds.left + bounds.right) / 2;
    const x = right ? Math.max(a.x, b.x) + 24 + lane * 32 : Math.min(a.x, b.x) - 24 - lane * 32;
    offset = (x - middle.x) / scale;
  } else {
    const length = Math.hypot(b.x - a.x, b.y - a.y) || 1;
    tangent = { x: (b.x - a.x) / length, y: (b.y - a.y) / length };
    // Left-hand perpendicular in world coordinates (screen y is inverted).
    normal = { x: tangent.y, y: -tangent.x }; offset = (24 + lane * 32) / scale;
  }
  const placement = c.placement && 'along' in c.placement ? c.placement : { offset, along: .5 };
  const base = { x: middle.x + normal.x * placement.offset * scale, y: middle.y + normal.y * placement.offset * scale };
  const span = (b.x - a.x) * tangent.x + (b.y - a.y) * tangent.y;
  const onLine = (along: number) => ({ x: base.x + tangent.x * span * (along - .5), y: base.y + tangent.y * span * (along - .5) });
  const p = onLine(0), q = onLine(1), text = onLine(placement.along);
  const sign = Math.sign(placement.offset) || 1;
  const at = c.kind === 'distanceY' ? { x: text.x - 10, y: text.y } : { x: text.x, y: text.y - 6 };
  const extend = (p: XY) => ({ x: p.x + normal.x * sign * 6, y: p.y + normal.y * sign * 6 });
  return { at, placement, rotation: c.kind === 'distanceY' ? -90 : 0,
    path: `${segment(a, extend(p))} ${segment(b, extend(q))} ${segment(onLine(Math.min(0, placement.along)), onLine(Math.max(1, placement.along)))}`,
    arrows: `${arrow(p, Math.atan2(tangent.y, tangent.x))} ${arrow(q, Math.atan2(-tangent.y, -tangent.x))}` };
}
export function dimensionLayouts(sketch: Sketch, xy: (id: string) => XY, bounds: DimensionBounds, scale: number, label: (c: Constraint & { value: number; driving: boolean }) => string) {
  const result = new Map<string, DimensionLayout>(), boxes: LabelBox[] = [];
  // Reserve manual positions first, then nearby vertical measurements before horizontal lanes.
  const dimensions = sketch.constraints.filter(c => 'value' in c).sort((a, b) => Number(!!b.placement) - Number(!!a.placement)
    || Number(b.kind === 'distanceY') - Number(a.kind === 'distanceY'));
  for (const c of dimensions) {
    if (!('value' in c)) continue;
    let layout = dimensionLayout(sketch, c, xy, bounds, 0, scale);
    if (!layout) continue;
    let flipped = false, lane = 0;
    for (let attempt = 0; !c.placement && attempt < 100; attempt++) {
      const box = dimensionLabelBox(layout, label(c));
      const outside = c.kind === 'distanceX' && (box.y < 4 || box.y + box.height > (bounds.height ?? Infinity) - 4);
      if (!boxes.some(other => labelsOverlap(other, box)) && !outside) break;
      // Once an outward horizontal lane leaves the canvas, try the other side.
      if (outside && c.kind === 'distanceX' && !flipped) { flipped = true; lane = 0; }
      else lane++;
      layout = dimensionLayout(sketch, c, xy, bounds, flipped ? -lane - 1 : lane, scale)!;
    }
    result.set(c.id, layout); boxes.push(dimensionLabelBox(layout, label(c)));
  }
  return result;
}
/** Convert a text drag into world-mm placement without changing measured geometry. */
export function dragDimensionPlacement(sketch: Sketch, c: Constraint, initial: DimensionPlacement, delta: XY): DimensionPlacement {
  if (c.kind === 'radius' && 'angle' in initial) {
    const arc = sketch.entities.find(e => e.id === c.arc);
    if (arc?.kind !== 'arc') return initial;
    const radius = arcGeometry(sketch, arc).radius;
    const x = Math.cos(initial.angle) * (radius + initial.offset) + delta.x;
    const y = Math.sin(initial.angle) * (radius + initial.offset) + delta.y;
    return { angle: Math.atan2(y, x), offset: Math.hypot(x, y) - radius };
  }
  if (!('along' in initial)) return initial;
  if (c.kind === 'angle') {
    const frame = angleFrame(sketch, c); if (!frame) return initial;
    const angle = frame.start + frame.sweep * initial.along;
    const x = initial.offset * Math.cos(angle) + delta.x, y = initial.offset * Math.sin(angle) + delta.y;
    const turn = Math.atan2(Math.sin(Math.atan2(y, x) - angle), Math.cos(Math.atan2(y, x) - angle));
    return { offset: Math.hypot(x, y), along: initial.along + turn / (frame.sweep || 1) };
  }
  const points = distancePoints(sketch, c); if (!points) return initial;
  const [a, b] = points;
  if (c.kind === 'distanceX') return { offset: initial.offset + delta.y, along: initial.along + delta.x / (b.x - a.x || 1) };
  if (c.kind === 'distanceY') return { offset: initial.offset + delta.x, along: initial.along + delta.y / (b.y - a.y || 1) };
  const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy) || 1;
  return { offset: initial.offset + (-dy * delta.x + dx * delta.y) / length, along: initial.along + (dx * delta.x + dy * delta.y) / (length * length) };
}
