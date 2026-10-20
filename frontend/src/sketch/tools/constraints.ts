import { constraintProblem, type NewConstraint, type Sketch } from '../model';
import type { Capabilities, ConstraintKind } from '../solver/types';

type SelectionConstraint = Exclude<ConstraintKind, 'distance' | 'distanceX' | 'distanceY' | 'pointLineDistance' | 'angle' | 'radius'>;
type SelectionRule = { label: string; key?: string; match: (sketch: Sketch, ids: string[]) => NewConstraint | undefined };
const pair = (kind: 'coincident' | 'parallel' | 'perpendicular' | 'equal' | 'tangent'): SelectionRule['match'] =>
  (_, ids) => ids.length === 2 && ids[0] !== ids[1] ? { kind, a: ids[0], b: ids[1] } : undefined;
const pointCurve = (kind: 'midpoint' | 'pointOnLine' | 'pointOnArc'): SelectionRule['match'] => (sketch, ids) => {
  if (ids.length !== 2) return;
  const point = ids.find(id => sketch.points.some(p => p.id === id));
  const curve = sketch.entities.find(e => ids.includes(e.id) && e.kind === (kind === 'pointOnArc' ? 'arc' : 'line'));
  if (point && curve) return kind === 'pointOnArc' ? { kind, point, arc: curve.id } : { kind, point, line: curve.id };
};
/** The single selection/label policy; solver capabilities decide which entries are offered. */
const rules: Record<SelectionConstraint, SelectionRule> = {
  horizontal: { label: 'Horizontal', key: 'h', match: (_, ids) => ids.length === 1 ? { kind: 'horizontal', line: ids[0] } : ids.length === 2 ? { kind: 'horizontal', a: ids[0], b: ids[1] } : undefined },
  vertical: { label: 'Vertical', key: 'v', match: (_, ids) => ids.length === 1 ? { kind: 'vertical', line: ids[0] } : ids.length === 2 ? { kind: 'vertical', a: ids[0], b: ids[1] } : undefined },
  coincident: { label: 'Coincident', match: pair('coincident') },
  tangent: { label: 'Tangent', match: pair('tangent') },
  fix: { label: 'Fix', match: (_, ids) => ids.length === 1 ? { kind: 'fix', point: ids[0] } : undefined },
  equal: { label: 'Equal', match: pair('equal') },
  parallel: { label: 'Parallel', key: 'p', match: pair('parallel') },
  perpendicular: { label: 'Perpendicular', key: 'n', match: pair('perpendicular') },
  midpoint: { label: 'Midpoint', key: 'm', match: pointCurve('midpoint') },
  pointOnLine: { label: 'Point on', match: pointCurve('pointOnLine') },
  pointOnArc: { label: 'Point on', match: pointCurve('pointOnArc') },
};
export function constraintFor(sketch: Sketch, ids: string[], kind: SelectionConstraint): NewConstraint | undefined {
  const c = rules[kind].match(sketch, ids);
  return c && !constraintProblem(sketch, c) ? c : undefined;
}
export function selectionConstraints(sketch: Sketch, ids: string[], capabilities?: Capabilities) {
  return (Object.entries(rules) as [SelectionConstraint, SelectionRule][]).flatMap(([kind, rule]) => {
    if (!capabilities || capabilities.constraints[kind].support === 'unsupported') return [];
    const constraint = constraintFor(sketch, ids, kind);
    return constraint ? [{ ...rule, kind, constraint }] : [];
  });
}
