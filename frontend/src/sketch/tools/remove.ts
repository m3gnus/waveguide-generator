import type { Command } from '../commands';
import { constraintRefs, entityPoints, type Sketch } from '../model';
import { referencePointIds } from '../context';
import type { Reference } from '../adapter';

export function removeCommands(sketch: Sketch, selection: string[], references: Reference[] = []): Command[] {
  const fixedPoints = referencePointIds(sketch, references);
  const commands: Command[] = [];
  for (const id of selection) {
    if (sketch.constraints.some(c => c.id === id)) commands.push({ kind: 'removeConstraint', id });
    else {
      const entity = sketch.entities.find(e => e.id === id);
      if (entity && !entity.reference) commands.push({ kind: 'deleteEntity', id });
      else if (sketch.points.some(p => p.id === id) && !fixedPoints.has(id)) {
        if (!sketch.entities.some(e => selection.includes(e.id) && entityPoints(e).includes(id))) commands.push({ kind: 'deletePoint', id });
      }
    }
  }
  const unique = [...new Map(commands.map(c => ['id' in c ? c.id : '', c])).values()];
  // Geometry deletion also removes dangling constraints.
  const deleting = unique.filter(c => c.kind === 'deleteEntity');
  const removedRefs = new Set([...deleting.flatMap(c => c.kind === 'deleteEntity' ? [c.id, ...entityPoints(sketch.entities.find(e => e.id === c.id)!)] : []), ...unique.flatMap(c => c.kind === 'deletePoint' ? [c.id] : [])]);
  const filtered = unique.filter(c => c.kind !== 'removeConstraint' || !constraintRefs(sketch.constraints.find(v => v.id === c.id)!).some(id => removedRefs.has(id)));
  return filtered.sort((a, b) => Number(b.kind === 'removeConstraint') - Number(a.kind === 'removeConstraint'));
}
