import type { Reference } from './adapter';
import { entityPoints, type Line, type Sketch } from './model';
import { applyCommand } from './ops';
import { formatValue } from './formatValue';
import type { XY } from './solver/types';

export interface ContextItem {
  id: string;
  kind: 'point' | 'segment' | 'polyline';
  geometry: XY[];
  role: 'axis' | 'rim-plane' | 'throat' | 'wall' | 'mouth' | 'baffle' | 'enclosure' | 'plug' | 'aperture' | 'collar' | 'keepout' | 'limit';
  label: string;
  pickable: boolean;
  emphasis?: 'near' | 'far';
  /** A semantic point uses an endpoint of its owning segment. */
  owner?: string;
}
export interface ContextDrawingItem extends ContextItem { referenceId?: string; sourceLimit?: { radius: number; locked: boolean } }
export interface SketchContext { items: ContextItem[]; rimPlaneLabel: string; fingerprint: string }
export function moveReferenceLine(sketch: Sketch, line: Line, geometry: XY[]): Sketch {
  return { ...sketch, points: sketch.points.map(p => p.id === line.p1 ? { ...p, ...geometry[0] }
    : p.id === line.p2 ? { ...p, ...geometry[1] } : p) };
}
export function contextData(e: { data?: unknown }): { context: string; fingerprint: string; label?: string } | undefined {
  const d = e.data as { context?: unknown; fingerprint?: unknown; label?: unknown } | undefined;
  return d && typeof d.context === 'string' && typeof d.fingerprint === 'string'
    ? { context: d.context, fingerprint: d.fingerprint, ...(typeof d.label === 'string' ? { label: d.label } : {}) } : undefined;
}
function ownedSegment(item: ContextItem, context: SketchContext): { id: string; geometry: XY[]; endpoint?: number; label: string } {
  if (item.kind === 'segment') return item;
  const owner = context.items.find(i => i.id === item.owner);
  if (item.kind !== 'point' || !owner || owner.geometry.length < 2) throw new Error('Context item has no straight pick target.');
  const p = item.geometry[0], index = owner.geometry.findIndex(q => q.x === p.x && q.y === p.y);
  if (index < 0) throw new Error('Context point is not on its owner.');
  const last = index === owner.geometry.length - 1;
  // A polyline is display-only: only the endpoint's adjacent segment enters the solver.
  return { id: owner.kind === 'polyline' ? `${item.id}/support` : owner.id, label: owner.kind === 'polyline' ? item.label : owner.label,
    geometry: last ? owner.geometry.slice(-2) : owner.geometry.slice(index, index + 2), endpoint: last ? 1 : 0 };
}
export function materialiseContext(sketch: Sketch, context: SketchContext, itemId: string): { sketch: Sketch; id: string } {
  const item = context.items.find(i => i.id === itemId && i.pickable);
  if (!item || item.kind === 'polyline') throw new Error('Context item is display-only.');
  const target = ownedSegment(item, context);
  let line = sketch.entities.find(e => contextData(e)?.context === target.id);
  if (!line) {
    const result = applyCommand(sketch, { kind: 'addLine', from: target.geometry[0], to: target.geometry[1] });
    if ('error' in result) throw new Error(result.error);
    line = result.sketch.entities.find(e => !sketch.entities.some(old => old.id === e.id))!;
    line.reference = true; line.construction = true;
    line.data = { context: target.id, fingerprint: geometryFingerprint(target.geometry), label: target.label };
    sketch = result.sketch;
  }
  if (line.kind !== 'line') throw new Error('Expected a reference line.');
  return { sketch, id: target.endpoint === undefined ? line.id : target.endpoint === 0 ? line.p1 : line.p2 };
}
export function contextFingerprint(value: unknown): string {
  let hash = 0xcbf29ce484222325n;
  for (const c of JSON.stringify(value)) hash = BigInt.asUintN(64, (hash ^ BigInt(c.codePointAt(0)!)) * 0x100000001b3n);
  return hash.toString(16).padStart(16, '0');
}
export const geometryFingerprint = (geometry: XY[]) => contextFingerprint(geometry);
export function refreshContext(sketch: Sketch, context: SketchContext): { sketch: Sketch; missing: { id: string; label: string }[]; moved: boolean } {
  const missing: { id: string; label: string }[] = [];
  let moved = false;
  for (const e of sketch.entities) {
    const data = contextData(e);
    if (!data || e.kind !== 'line') continue;
    let target = context.items.find(i => i.id === data.context && i.kind === 'segment');
    if (!target && data.context.endsWith('/support')) {
      const point = context.items.find(i => `${i.id}/support` === data.context);
      if (point) target = { ...point, ...ownedSegment(point, context), kind: 'segment' };
    }
    if (!target) { missing.push({ id: data.context, label: data.label ?? 'External reference' }); continue; }
    const geometry = target.geometry, fingerprint = geometryFingerprint(geometry);
    const previous = entityPoints(e).map(id => sketch.points.find(p => p.id === id)!);
    const changed = previous.some((p, i) => p.x !== geometry[i].x || p.y !== geometry[i].y);
    if (!changed && data.fingerprint === fingerprint && data.label === target.label) continue;
    moved ||= changed;
    sketch = moveReferenceLine(sketch, e, target.geometry);
    sketch = { ...sketch, entities: sketch.entities.map(line => line.id === e.id ? { ...line, data: { context: data.context, fingerprint, label: target.label } } : line) };
  }
  return { sketch, missing, moved };
}
/** Convert the legacy solver references once, then render every datum with the same layer. */
export function referenceContext(references: Reference[], context: SketchContext | undefined, bounds: { low: number; high: number; left: number; right: number }): ContextDrawingItem[] {
  const items = [...context?.items ?? []];
  for (const r of references) {
    if (r.kind === 'axis') items.push({ id: 'axis', kind: 'segment', geometry: [{ x: 0, y: bounds.low }, { x: 0, y: bounds.high }], role: 'axis', label: `Axis (r = ${formatValue(0)})`, pickable: true });
    else if (r.kind === 'baffle' && !items.some(i => i.role === 'rim-plane')) items.push({ id: 'rim-plane', kind: 'segment', geometry: [{ x: bounds.left, y: 0 }, { x: bounds.right, y: 0 }], role: 'rim-plane', label: context?.rimPlaneLabel ?? `Rim plane (z = ${formatValue(0)})`, pickable: true });
    else if (r.kind === 'limit' && !items.some(i => i.role === (r.locked ? 'throat' : r.label.startsWith('Mouth') ? 'mouth' : 'aperture'))) {
      items.push({ id: 'limit', kind: 'point', geometry: [{ x: r.radius, y: 0 }], role: 'limit', label: `${r.label}${r.locked ? ' 🔒' : ''}`, pickable: false });
    }
  }
  return items.map(item => {
    const reference = references.find(r => item.role === 'axis' ? r.kind === 'axis' : item.role === 'rim-plane' ? r.kind === 'baffle'
      : item.kind === 'point' && r.kind === 'limit' && (r.locked ? item.role === 'throat' || item.role === 'limit' : item.role === 'mouth' || item.role === 'aperture' || item.role === 'limit'));
    return { ...item, referenceId: reference?.id, ...(reference?.kind === 'limit' ? {
      label: `${reference.label}${reference.locked ? ' 🔒' : ''}`, sourceLimit: { radius: reference.radius, locked: reference.locked },
    } : {}) };
  });
}
export const referencePointIds = (sketch: Sketch, references: Reference[] = []) => new Set(sketch.entities.filter(e => e.reference || references.some(r => r.id === e.id)).flatMap(entityPoints));
