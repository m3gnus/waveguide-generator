import { constraintProblem, type NewConstraint, type Sketch } from '../model';
import type { Capabilities, ConstraintKind } from '../solver/types';

export type SelectionConstraint = Exclude<ConstraintKind, 'distance' | 'distanceX' | 'distanceY' | 'pointLineDistance' | 'angle' | 'radius'>;
type PickKind = 'point' | 'line' | 'arc';
type SelectionRule = { label: string; key?: string; symbol: string; patterns: PickKind[][]; match: (sketch: Sketch, ids: string[]) => NewConstraint | undefined };
const pair = (kind: 'coincident' | 'parallel' | 'perpendicular' | 'equal' | 'tangent'): SelectionRule['match'] =>
  (_, ids) => ids.length === 2 && ids[0] !== ids[1] ? { kind, a: ids[0], b: ids[1] } : undefined;
const pointCurve = (kind: 'midpoint' | 'pointOnLine' | 'pointOnArc'): SelectionRule['match'] => (sketch, ids) => {
  if (ids.length !== 2) return;
  const point = ids.find(id => sketch.points.some(p => p.id === id));
  const curve = sketch.entities.find(e => ids.includes(e.id) && e.kind === (kind === 'pointOnArc' ? 'arc' : 'line'));
  if (point && curve) return kind === 'pointOnArc' ? { kind, point, arc: curve.id } : { kind, point, line: curve.id };
};
const editableCurves = (match: SelectionRule['match']): SelectionRule['match'] => (sketch, ids) =>
  sketch.entities.some(e => ids.includes(e.id) && e.reference) ? undefined : match(sketch, ids);
/** The single selection/label policy; solver capabilities decide which entries are offered. */
export const rules: Record<SelectionConstraint, SelectionRule> = {
  horizontal: { label: 'Horizontal', symbol: 'H', patterns: [['line'], ['point', 'point']], key: 'h', match: (_, ids) => ids.length === 1 ? { kind: 'horizontal', line: ids[0] } : ids.length === 2 ? { kind: 'horizontal', a: ids[0], b: ids[1] } : undefined },
  vertical: { label: 'Vertical', symbol: 'V', patterns: [['line'], ['point', 'point']], key: 'v', match: (_, ids) => ids.length === 1 ? { kind: 'vertical', line: ids[0] } : ids.length === 2 ? { kind: 'vertical', a: ids[0], b: ids[1] } : undefined },
  coincident: { symbol: '●', patterns: [['point', 'point']], label: 'Coincident', match: pair('coincident') },
  tangent: { symbol: '⌒', patterns: [['line', 'arc'], ['arc', 'line'], ['arc', 'arc']], label: 'Tangent', match: pair('tangent') },
  fix: { symbol: '▣', patterns: [['point']], label: 'Fix', match: (_, ids) => ids.length === 1 ? { kind: 'fix', point: ids[0] } : undefined },
  equal: { symbol: '=', patterns: [['line', 'line'], ['arc', 'arc']], label: 'Equal', match: editableCurves(pair('equal')) },
  parallel: { symbol: '∥', patterns: [['line', 'line']], label: 'Parallel', key: 'p', match: pair('parallel') },
  perpendicular: { symbol: '⊥', patterns: [['line', 'line']], label: 'Perpendicular', key: 'n', match: pair('perpendicular') },
  midpoint: { symbol: 'M', patterns: [['point', 'line'], ['line', 'point']], label: 'Midpoint', key: 'm', match: editableCurves(pointCurve('midpoint')) },
  pointOnLine: { symbol: '⊙', patterns: [['point', 'line'], ['line', 'point']], label: 'Point on', match: pointCurve('pointOnLine') },
  pointOnArc: { symbol: '⊙', patterns: [['point', 'arc'], ['arc', 'point']], label: 'Point on', match: pointCurve('pointOnArc') },
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

/** Palette entries come only from supported rules; Point on combines line/arc support. */
export function constraintPalette(capabilities?: Capabilities) {
  const entries: { kind: SelectionConstraint; kinds: SelectionConstraint[]; label: string; key?: string; symbol: string }[] = [];
  for (const [kind, rule] of Object.entries(rules) as [SelectionConstraint, SelectionRule][]) {
    if (!capabilities || capabilities.constraints[kind].support === 'unsupported') continue;
    const existing = entries.find(entry => entry.label === rule.label);
    if (existing) existing.kinds.push(kind);
    else entries.push({ kind, kinds: [kind], label: rule.label, key: rule.key, symbol: rule.symbol });
  }
  return entries;
}
export function pickPatterns(sketch: Sketch, ids: string[], kind: SelectionConstraint) {
  const types = ids.map(id => sketch.points.some(p => p.id === id) ? 'point' : sketch.entities.find(e => e.id === id)?.kind);
  if (new Set(ids).size !== ids.length) return [];
  // Reference restrictions remain in the rule's matcher for both selection and picks.
  const candidates = [...sketch.points, ...sketch.entities].map(e => e.id);
  return rules[kind].patterns.filter(pattern => ids.length <= pattern.length && types.every((type, i) => type === pattern[i])
    && (ids.length === pattern.length ? !!constraintFor(sketch, ids, kind)
      : !ids.length || candidates.some(id => !ids.includes(id) && !!constraintFor(sketch, [...ids, id], kind))));
}
