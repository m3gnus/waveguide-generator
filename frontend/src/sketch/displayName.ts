import type { Sketch } from './model';

const names: Record<string, string> = {
  'dust-cap': 'Dust cap', cone: 'Cone', dome: 'Dome', surround: 'Surround', land: 'Land', piston: 'Piston',
  pole: 'Pole', 'cap.rim': 'Dust-cap edge', 'dome.rim': 'Dome edge', 'cone.rim': 'Cone edge',
  'surround.rim': 'Surround edge', rim: 'Rim',
};

/** Stable labels follow authoring order; identifiers remain available in tooltips. */
export function displayName(sketch: Sketch, id: string): string {
  if (names[id]) return names[id];
  const entity = sketch.entities.find(e => e.id === id);
  if (entity) {
    if (entity.construction) {
      const index = sketch.entities.filter(e => e.construction && e.kind === entity.kind).findIndex(e => e.id === id) + 1;
      return `Construction ${entity.kind} ${index}`;
    }
    const kind = entity.kind[0].toUpperCase() + entity.kind.slice(1);
    return `${kind} ${sketch.entities.filter(e => !e.construction && e.kind === entity.kind).findIndex(e => e.id === id) + 1}`;
  }
  const references = new Set(sketch.entities.filter(e => e.reference).flatMap(e => e.kind === 'line' ? [e.p1, e.p2] : []));
  return `Point ${sketch.points.filter(p => !references.has(p.id)).findIndex(p => p.id === id) + 1}`;
}
