import type { ReactNode } from 'react';
import type { DisplayItem } from './types';
import { positiveAngle, tau } from '../geometry';
import type { XY } from '../solver/types';
import { worldToScreen, type View } from '../view';

/** SVG path generation has no knowledge of Sketch entities or solver state. */
export function displayPath(item: DisplayItem, view: View): string {
  if (item.kind === 'polyline') return item.points.map((p, i) => { const at = worldToScreen(view, p); return `${i ? 'L' : 'M'}${at.x},${at.y}`; }).join(' ') + (item.closed ? ' Z' : '');
  if (item.kind === 'arc') {
    const a = worldToScreen(view, item.start), b = worldToScreen(view, item.end);
    const radius = Math.hypot(item.start.x - item.center.x, item.start.y - item.center.y) * view.scale;
    const start = Math.atan2(item.start.y - item.center.y, item.start.x - item.center.x), end = Math.atan2(item.end.y - item.center.y, item.end.x - item.center.x);
    const sweep = positiveAngle(end - start) - (item.ccw ? 0 : tau);
    return `M${a.x},${a.y} A${radius},${radius} 0 ${Math.abs(sweep) > Math.PI ? 1 : 0} ${item.ccw ? 0 : 1} ${b.x},${b.y}`;
  }
  return '';
}
/** Shared point glyph; callers supply domain styling and accessible text. */
export function WorkspacePoint({ id, at, glyph, className, label, title, validStart = false, editable = true }: {
  id: string; at: XY; glyph?: 'dot' | 'centre' | 'corner' | 'smooth'; className: string;
  label: string; title?: ReactNode; validStart?: boolean; editable?: boolean;
}) {
  const data = editable ? { 'data-id': id } : {};
  const interactive = editable ? { tabIndex: -1, role: 'button', 'aria-label': label } : {};
  const caption = title === undefined ? undefined : <title>{title}</title>;
  return <g className="sketch-handle">
    {editable && <circle data-id={id} cx={at.x} cy={at.y} r={10} className="sketch-point-hit"/>}
    {validStart && <circle cx={at.x} cy={at.y} r={8} className="sketch-arc-start"/>}
    {glyph === 'centre' ? <path {...data} d={`M${at.x - 5},${at.y} h10 M${at.x},${at.y - 5} v10`} {...interactive} className={`${className} sketch-centre`}>{caption}</path>
      : glyph === 'corner' ? <rect {...data} x={at.x - 4} y={at.y - 4} width={8} height={8} {...interactive} className={className}>{caption}</rect>
      : <circle {...data} cx={at.x} cy={at.y} r={4} {...interactive} className={className}>{caption}</circle>}
  </g>;
}
export function WorkspaceItems({ items, view, render }: { items: DisplayItem[]; view: View; render?: (item: DisplayItem, path: string) => ReactNode }) {
  return <>{items.map(item => <WorkspaceItem key={item.id} item={item} view={view} render={render}/>)}</>;
}
function WorkspaceItem({ item, view, render }: { item: DisplayItem; view: View; render?: (item: DisplayItem, path: string) => ReactNode }) {
  const path = displayPath(item, view);
  if (render) return render(item, path);
  const state = ['workspace-item', ...(item.editable ? [] : ['display-only']), ...item.state ?? [], ...(item.kind === 'polyline' && item.stale ? ['stale'] : [])].join(' ');
  const interactive = item.editable ? { 'data-id': item.id, role: 'button', tabIndex: -1, 'aria-label': `Select ${item.label ?? item.id}` } : {};
  if (item.kind === 'point') {
    const at = worldToScreen(view, item.at), className = `sketch-point ${item.role} ${state}`;
    return <WorkspacePoint id={item.id} at={at} glyph={item.glyph} className={className} editable={item.editable}
      label={`Select ${item.label ?? item.id}`} title={item.glyph === 'centre' || item.glyph === 'corner' ? undefined : item.label ?? item.id}/>;
  }
  if (item.kind === 'circle') {
    const at = worldToScreen(view, item.center);
    return <circle {...interactive} cx={at.x} cy={at.y} r={item.radius * view.scale} className={`sketch-entity ${item.role} ${state}`}/>;
  }
  if (item.kind === 'handle') {
    const at = worldToScreen(view, item.at), anchor = worldToScreen(view, item.anchor);
    return <g className={`sketch-tangent-handle ${item.role} ${state}`}><line x1={anchor.x} y1={anchor.y} x2={at.x} y2={at.y}/><circle {...interactive} cx={at.x} cy={at.y} r={3}/></g>;
  }
  if (item.kind === 'dimension') {
    const { layout } = item;
    return <g className={`sketch-dimension ${item.role} ${state}`}><g className="sketch-dimension-lines" aria-hidden="true"><path className="sketch-extension-lines" d={layout.path}/><path className="sketch-dimension-arrows" d={layout.arrows}/></g><text {...interactive} x={layout.at.x} y={layout.at.y} textAnchor="middle" transform={layout.rotation ? `rotate(${layout.rotation} ${layout.at.x} ${layout.at.y})` : undefined} className={`sketch-glyph ${item.driving ? '' : 'reference'}`}>{item.text}</text></g>;
  }
  return <g className="sketch-curve">{item.editable && <path data-id={item.id} d={path} className="sketch-curve-hit"/>}<path {...interactive} d={path} className={`sketch-entity ${item.role} ${state}`}><title>{item.label ?? item.id}</title></path></g>;
}
