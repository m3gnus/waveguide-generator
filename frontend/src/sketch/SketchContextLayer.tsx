import type { ContextDrawingItem } from './context';
import { contextData } from './context';
import { entityPoints, type Sketch } from './model';
import type { XY } from './solver/types';
import { worldToScreen, visibleAnchor, type View } from './view';
import { contextLabelLayout } from './contextLabels';
import { labelsOverlap, type LabelBox } from './dimensionGraphics';
import { geometryHit } from './geometryHit';

/** One layer and one geometry path for structural datums, limits and host geometry. */
export function ContextLayer({ names, cursor, contour, viewport, reserved, canPick, validTargets, isPanning, sketch, items, hoveredId, highlighted, stale, view, onPick, onEditLimit, canEditLimit }: {
  names: boolean; cursor?: XY; contour: XY[]; viewport: { width: number; height: number }; reserved: LabelBox[]; canPick: boolean; validTargets?: Set<string>; isPanning: () => boolean; sketch: Sketch; items: ContextDrawingItem[]; hoveredId?: string; highlighted: Set<string>; stale: boolean; view: View;
  onPick: (item: ContextDrawingItem, additive: boolean) => void; onEditLimit: (radius: number) => void; canEditLimit: boolean;
}) {
  const hatch = 'sketch-context-hatch';
  const entries = items.map(item => {
    const points = item.geometry.map(p => worldToScreen(view, p));
    const materialised = sketch.entities.find(e => contextData(e)?.context === item.id || item.owner && contextData(e)?.context === item.owner || contextData(e)?.context === `${item.id}/support`);
    const active = !!(item.referenceId && highlighted.has(item.referenceId) || materialised && (highlighted.has(materialised.id) || item.kind === 'point' && entityPoints(materialised).some(id => highlighted.has(id) && sketch.points.some(p => p.id === id && p.x === item.geometry[0].x && p.y === item.geometry[0].y))));
    const near = cursor && visibleAnchor(points, viewport, cursor);
    return { item, kind: item.kind, role: item.role, points, active, near, gap: near && cursor ? Math.hypot(near.x - cursor.x, near.y - cursor.y) : Infinity };
  });
  // Hover uses geometry, so display-only paths keep passing clicks through to the canvas.
  const hovered = geometryHit(validTargets ? entries.filter(e => validTargets.has(e.item.id)) : entries, 7);
  const priority = (e: typeof entries[number]) => e === hovered ? 3 : e.item.sourceLimit || e.item.role === 'throat' || e.item.role === 'limit' ? 2 : e.active || e.item.referenceId === hoveredId && !!hoveredId ? 1 : 0;
  const occupied = [...reserved];
  return <g className={`sketch-context-layer${stale ? ' stale' : ''}`} aria-label="Reference context">
    <defs><pattern id={hatch} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" className="sketch-keepout-hatch"/></pattern></defs>
    {entries.sort((a, b) => priority(b) - priority(a)).map(entry => {
      const { item, points, active } = entry;
      if (!points.length) return null;
      const at = points[0];
      const limit = item.sourceLimit, label = item.label, reference = item.referenceId;
      const editable = !!limit && !limit.locked && canEditLimit && canPick && !validTargets;
      const pickable = item.pickable && canPick && !stale && (!validTargets || validTargets.has(item.id));
      const path = item.kind === 'point' ? `M${at.x},${at.y - 4} l4,4 -4,4 -4,-4 Z`
        : points.map((p, i) => `${i ? 'L' : 'M'}${p.x},${p.y}`).join(' ');
      const anchor = entry === hovered ? entry.near : visibleAnchor(points, viewport);
      const candidate = (names || priority(entry) > 0) && anchor ? contextLabelLayout(anchor, points, contour, label, viewport, entry === hovered) : undefined;
      const labelAt = candidate && (entry === hovered || !occupied.some(box => labelsOverlap(box, candidate.box))) ? candidate : undefined;
      if (labelAt) occupied.push(labelAt.box);
      const edit = () => { if (editable && limit) onEditLimit(limit.radius); };
      return <g key={item.id} data-context-id={item.id} data-reference-id={reference} className={`sketch-context-item role-${item.role} ${item.emphasis ?? ''}${limit ? ' sketch-limit' : ''}${entry === hovered || reference && reference === hoveredId ? ' hovered' : ''}${active ? ' selected' : ''}`}
        style={{ pointerEvents: pickable || editable ? 'auto' : 'none' }}
        onPointerDown={event => { if (pickable && event.button === 0 && !isPanning()) { event.preventDefault(); event.stopPropagation(); onPick(item, event.shiftKey); } }} onDoubleClick={event => { if (editable) { event.stopPropagation(); edit(); } }}>
        <title>{label}{limit?.locked ? ' · Set by the horn design' : editable ? ' · Double-click to edit limit' : ''}</title>
        {item.pickable && <path d={path} className="sketch-context-hit" style={{ pointerEvents: pickable ? 'stroke' : 'none' }}/>}
        <path d={path} fill={item.role === 'keepout' ? `url(#${hatch})` : undefined}/>
        {labelAt && <text x={labelAt.at.x} y={labelAt.at.y} textAnchor={labelAt.textAnchor} dominantBaseline={labelAt.baseline} data-anchor-x={anchor!.x} data-anchor-y={anchor!.y}
          role={editable ? 'button' : undefined} tabIndex={editable ? 0 : undefined}
          onKeyDown={event => { if (event.key === 'Enter') { event.stopPropagation(); edit(); } }}>
          <title>{limit ? limit.locked ? 'Set by the horn design' : 'Double-click to edit limit' : item.label}</title>{label}</text>}
      </g>;
    })}
  </g>;
}
