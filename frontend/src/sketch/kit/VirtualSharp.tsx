import type { Sketch } from '../model';
import { virtualSharpData } from '../virtualSharp';
import { worldToScreen, type View } from '../view';

/** Extension strokes are presentation only; the existing point owns picking. */
export function VirtualSharpExtensions({ sketch, view }: { sketch: Sketch; view: View }) {
  return <g className="sketch-virtual-sharp-extensions" aria-hidden="true">
    {sketch.entities.map(e => {
      const v = virtualSharpData(e); if (!v) return null;
      const sharp = sketch.points.find(p => p.id === v.point), contact = sketch.points.find(p => p.id === v.contact);
      if (!sharp || !contact) return null;
      const a = worldToScreen(view, sharp), b = worldToScreen(view, contact);
      return <line key={e.id} x1={a.x} y1={a.y} x2={b.x} y2={b.y}/>;
    })}
  </g>;
}
