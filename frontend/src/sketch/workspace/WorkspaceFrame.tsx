import { createPortal } from 'react-dom';
import type { HTMLAttributes, ReactNode, RefObject, SVGProps } from 'react';
import type { WorkspaceLabels } from './types';
import type { SelectState } from '../tools/select';
import { gridStep, screenToWorld, worldToScreen, type View, type Viewport } from '../view';

export interface WorkspaceSlots { toolbar?: HTMLElement | null; status?: HTMLElement | null; properties?: HTMLElement | null }
interface Props {
  slots?: WorkspaceSlots; labels?: WorkspaceLabels; pending: boolean; view: View; size: Viewport; svg: RefObject<SVGSVGElement | null>;
  rootProps: HTMLAttributes<HTMLDivElement>; svgProps: SVGProps<SVGSVGElement>; marquee?: SelectState;
  toolbar: ReactNode; banners?: ReactNode; properties?: ReactNode; overlays?: ReactNode; status: ReactNode; footer?: ReactNode; children: ReactNode;
}
/** Shared DOM shell: portals never add wrappers or change SVG paint order. */
export function WorkspaceFrame({ slots, labels, pending, view, size, svg, rootProps, svgProps, marquee, toolbar, banners, properties, overlays, status, footer, children }: Props) {
  const step = gridStep(view.scale), topLeft = screenToWorld(view, { x: 0, y: 0 }), bottomRight = screenToWorld(view, { x: size.width, y: size.height });
  const gridX = Array.from({ length: Math.min(200, Math.ceil((bottomRight.x - topLeft.x) / step) + 1) }, (_, i) => (Math.ceil(topLeft.x / step) + i) * step);
  const gridY = Array.from({ length: Math.min(200, Math.ceil((topLeft.y - bottomRight.y) / step) + 1) }, (_, i) => (Math.ceil(bottomRight.y / step) + i) * step);
  const slot = (node: ReactNode, host?: HTMLElement | null) => host ? createPortal(node, host) : node;
  return <div className="sketch-canvas" aria-busy={pending} {...rootProps}>
    {slot(toolbar, slots?.toolbar)}
    {banners}
    <div className="sketch-surface">
      {slot(properties, slots?.properties)}
      <svg ref={svg} viewBox={`0 0 ${size.width} ${size.height}`} preserveAspectRatio="xMidYMid meet" tabIndex={0} role="application" aria-label={labels?.drawing ?? 'Sketch drawing'} {...svgProps}>
        <g className="sketch-grid" aria-hidden="true">{gridX.map(x => <line key={`x${x}`} x1={worldToScreen(view, { x, y: 0 }).x} x2={worldToScreen(view, { x, y: 0 }).x} y1={0} y2={size.height}/>)}{gridY.map(y => <line key={`y${y}`} x1={0} x2={size.width} y1={worldToScreen(view, { x: 0, y }).y} y2={worldToScreen(view, { x: 0, y }).y}/>)}</g>
        {children}
        {marquee && <rect className={`sketch-marquee ${marquee.current.x < marquee.start.x ? 'crossing' : 'window'}`} x={Math.min(marquee.start.x,marquee.current.x)} y={Math.min(marquee.start.y,marquee.current.y)} width={Math.abs(marquee.current.x-marquee.start.x)} height={Math.abs(marquee.current.y-marquee.start.y)}/>}
      </svg>
      {overlays}
    </div>
    {slot(status, slots?.status)}
    {slot(footer, slots?.properties)}
  </div>;
}
