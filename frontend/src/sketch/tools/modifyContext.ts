import { materialiseContext, type SketchContext } from '../context';
import type { Sketch } from '../model';

/** The preview and the edit use identical, persistent reference cutters. */
export function modifyContext(sketch: Sketch, context?: SketchContext): Sketch {
  if (!context) return sketch;
  for (const segment of modifyContextSegments(context)) {
    if (segment.geometry.length < 2 || segment.geometry[0].x === segment.geometry[1].x && segment.geometry[0].y === segment.geometry[1].y) continue;
    sketch = materialiseContext(sketch, { ...context, items: [{ ...segment, pickable: true }] }, segment.id).sketch;
  }
  return sketch;
}

/** Keep polyline support identities stable when the enclosing context refreshes. */
export function modifyContextSegments(context: SketchContext) {
  return context.items.flatMap(item => item.kind === 'point' ? [] : item.kind === 'segment' ? [item] : item.geometry.slice(1).map((end, i) => ({
    ...item, id: `${item.id}/segment/${i}`, kind: 'segment' as const, geometry: [item.geometry[i], end],
  })));
}
