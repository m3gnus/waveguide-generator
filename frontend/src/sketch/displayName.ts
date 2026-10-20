import { contextData } from './context';
import { constraintRefs, type Sketch } from './model';

const names: Record<string, string> = {
  'dust-cap': 'Dust cap', cone: 'Cone', dome: 'Dome', surround: 'Surround', land: 'Land', piston: 'Piston',
  axis: 'Axis of revolution', baffle: 'Rim plane',
  pole: 'Pole', 'cap.rim': 'Dust-cap edge', 'dome.rim': 'Dome edge', 'cone.rim': 'Cone edge',
  'surround.rim': 'Surround edge', rim: 'Rim',
};

/** Stable labels follow authoring order; identifiers remain available in tooltips. */
export function displayName(sketch: Sketch, id: string): string {
  const constraint = sketch.constraints.find(c => c.id === id);
  if (constraint) {
    const kind = constraint.kind.replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase();
    return `${constraint.label?.trim() || kind[0].toUpperCase() + kind.slice(1)} (${constraintRefs(constraint).map(ref => displayName(sketch, ref)).join(', ')})`;
  }
  const entity = sketch.entities.find(e => e.id === id);
  const reference = entity ?? sketch.entities.find(e => e.reference && e.kind === 'line' && [e.p1, e.p2].includes(id));
  const data = reference?.data;
  if (data && typeof data === 'object' && !Array.isArray(data) && typeof data.name === 'string' && data.name.trim()) return data.name.trim();
  const label = reference && contextData(reference)?.label;
  if (label) return label;
  if (names[id]) return names[id];
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
