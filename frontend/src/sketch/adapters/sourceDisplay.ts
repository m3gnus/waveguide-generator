import { sketchGeometry, selectedConstraintRefs } from '../sketchGeometry';
import type { Constraint } from '../model';
import type { DomainProblem, Reference } from '../adapter';
import { contextData, type ContextDrawingItem, type SketchContext } from '../context';
import { entityPoints, type Sketch } from '../model';
import { arcGeometry, onArc } from '../geometry';
import { dimensionLabel, dimensionLayouts, internalHelper } from '../dimensionGraphics';
import { displayName } from '../displayName';
import { formatValue } from '../formatValue';
import { contourScreenPoints, contourDimensionBounds } from '../displayGeometry';
import { fit, worldToScreen, type View } from '../view';
import type { DisplayItem, DisplayModel, DisplayUI } from '../workspace/types';

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

export function sourceBounds(sketch: Sketch, context?: SketchContext, all = false) {
  const ids = new Set(sketch.entities.filter(e => !e.reference && !internalHelper(e)).flatMap(e => e.kind === 'arc' ? [e.start, e.end] : entityPoints(e)));
  const pts = sketch.points.filter(p => ids.has(p.id));
  // Include only extrema on the finite arc, including major arcs.
  for (const e of sketch.entities) if (e.kind === 'arc' && !internalHelper(e)) {
    const g = arcGeometry(sketch, e);
    for (const angle of [0, Math.PI / 2, Math.PI, 3 * Math.PI / 2]) {
      const p = { id: '', x: g.center.x + g.radius * Math.cos(angle), y: g.center.y + g.radius * Math.sin(angle) };
      if (onArc(sketch, e, p)) pts.push(p);
    }
  }
  for (const e of sketch.entities) if (e.reference && !contextData(e) && e.kind === 'line') {
    const a = sketch.points.find(p => p.id === e.p1)!;
    if (a.x > 0) pts.push(a);
  }
  const extent = Math.max(1, ...pts.map(p => Math.abs(p.x)), ...pts.map(p => Math.abs(p.y)));
  const boxPoints = context?.items.filter(i => i.role === 'enclosure' || i.role === 'baffle').flatMap(i => i.geometry) ?? [];
  const nearbyBox = boxPoints.every(p => Math.max(Math.abs(p.x), Math.abs(p.y)) <= 3 * extent);
  for (const item of context?.items ?? []) if (all || ['throat', 'plug', 'rim-plane'].includes(item.role)
    || nearbyBox && ['enclosure', 'baffle', 'wall', 'mouth', 'aperture', 'collar'].includes(item.role)) pts.push(...item.geometry.map(p => ({ ...p, id: '' })));
  return { minX: Math.min(0, ...pts.map(p => p.x)), maxX: Math.max(1, ...pts.map(p => p.x)), minY: Math.min(0, ...pts.map(p => p.y)), maxY: Math.max(1, ...pts.map(p => p.y)) };
}
export function sourceInitialView(sketch: Sketch, width = 640, height = 360, context?: SketchContext, all = false): View {
  const named = sketch.constraints.filter(c => c.label && 'value' in c);
  const { bottom, top, right, offsetX, margin } = sourceFitPadding(named);
  const fitted = fit(sourceBounds(sketch, context, all), { width: Math.max(100, width - right), height: Math.max(100, height - bottom - top) }, margin);
  return { ...fitted, offsetX: fitted.offsetX + offsetX, offsetY: fitted.offsetY + top };
}

function sourceFitPadding(named: Constraint[]) {
  const bottom = named.filter(c => c.kind === 'distanceX').length * 24;
  const top = named.filter(c => c.kind === 'distanceY').length * 24;
  const right = named.length ? 170 : 0;
  return { bottom, top, right, offsetX: named.length ? 48 : 0, margin: 32 };
}

export function sourceDisplay(state: { sketch: Sketch; meta: { references: Reference[] } }, ui: DisplayUI, problems: DomainProblem[] = []): DisplayModel {
  const { sketch, meta } = state, { selection, hover, scale, context } = ui;
  const geometry = sketchGeometry(sketch, meta.references, selection, hover, hover, hover);
  const point = (id: string) => sketch.points.find(p => p.id === id)!;
  const highlighted = new Set([...selection, ...ui.highlighted ?? [], ...selectedConstraintRefs(sketch, [...selection, ...ui.highlighted ?? []])]);
  const flags = (id: string, ids: string[] = []) => [
    ...(highlighted.has(id) || ids.some(id => highlighted.has(id)) ? ['selected'] : []),
    ...(hover === id ? ['hovered'] : []),
    ...(problems.some(p => p.ids.includes(id) || ids.some(id => p.ids.includes(id))) ? ['problem'] : []),
  ];
  const items: DisplayItem[] = sketch.entities.flatMap((e): DisplayItem[] => {
    if (e.reference || internalHelper(e) || meta.references.some(r => r.id === e.id)) return [];
    const base = { id: e.id, role: e.data && typeof e.data === 'object' && !Array.isArray(e.data) && typeof e.data.role === 'string' ? e.data.role : 'geometry',
      editable: true, label: displayName(sketch, e.id), state: [...flags(e.id, entityPoints(e)), ...(e.construction ? ['construction'] : [])] };
    if (e.kind === 'line') return [{ ...base, kind: 'polyline' as const, points: [point(e.p1), point(e.p2)] }];
    if (e.kind === 'arc') return [{ ...base, kind: 'arc' as const, start: point(e.start), end: point(e.end), center: point(e.center), ccw: e.ccw }];
    return [];
  });
  items.push(...geometry.visiblePoints.filter(p => !ui.hiddenPoints?.includes(p.id)).map(p => ({
    kind: 'point' as const, id: p.id, role: 'point', editable: true, at: p, state: flags(p.id),
    glyph: !geometry.endpoints.has(p.id) && geometry.arcs.some(e => e.center === p.id) ? 'centre' as const : 'dot' as const,
  })));
  const view = ui.view ?? { scale, offsetX: 0, offsetY: 0 }, xy = (id: string) => worldToScreen(view, point(id));
  const dimensionBounds = contourDimensionBounds(contourScreenPoints(sketch, view), ui.viewport ?? { width: 640, height: 360 });
  const layouts = dimensionLayouts(sketch, xy, dimensionBounds, scale, c => dimensionLabel(c, 'name'));
  for (const c of sketch.constraints) {
    const layout = layouts.get(c.id);
    if ('value' in c && layout) items.push({ id: c.id, kind: 'dimension', role: 'dimension', editable: true, state: flags(c.id),
      layout, text: dimensionLabel(c, ui.names || selection.includes(c.id) || hover === c.id ? 'name' : 'value'), driving: c.driving });
  }
  const bounds = ui.bounds ?? sourceBounds(sketch, context);
  const named = sketch.constraints.filter(c => c.label && 'value' in c);
  return { items, context: referenceContext(meta.references, context, { low: bounds.minY, high: bounds.maxY, left: bounds.minX, right: bounds.maxX }),
    bounds: all => sourceBounds(sketch, context, all), fitPadding: sourceFitPadding(named) };
}
