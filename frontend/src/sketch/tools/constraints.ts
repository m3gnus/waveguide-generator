import { constraintProblem, type NewConstraint, type Sketch } from '../model';
export const selectionConstraints = ['horizontal', 'vertical', 'coincident', 'tangent', 'fix', 'equal', 'point-on'] as const;
export type SelectionConstraint = typeof selectionConstraints[number];
export function constraintFor(sketch: Sketch, ids: string[], kind: SelectionConstraint): NewConstraint | undefined {
  let c: NewConstraint | undefined;
  const [a, b] = ids;
  if (kind === 'fix' && ids.length === 1) c = { kind, point: a };
  else if ((kind === 'horizontal' || kind === 'vertical') && ids.length === 1) c = { kind, line: a };
  else if (kind === 'point-on' && ids.length === 2) {
    const point = ids.find(id => sketch.points.some(p => p.id === id));
    const entity = sketch.entities.find(e => ids.includes(e.id));
    if (point && entity?.kind === 'line') c = { kind: 'pointOnLine', point, line: entity.id };
    if (point && entity?.kind === 'arc') c = { kind: 'pointOnArc', point, arc: entity.id };
  } else if (kind !== 'point-on' && kind !== 'fix' && ids.length === 2 && a !== b) c = { kind, a, b } as NewConstraint;
  return c && !constraintProblem(sketch, c) ? c : undefined;
}
