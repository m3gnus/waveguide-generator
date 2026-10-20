// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { importedSketch } from '../ops';
import { materialiseContext } from '../context';
import { capabilities } from '../solver/common';
import { constraintPalette, constraintFor, type SelectionConstraint } from './constraints';
import { acceptsConstraintPick, constraintHint, constraintTool, escapeConstraint, hitConstraint, pickConstraint } from './constraintTool';

const sketch = () => importedSketch([
  { id: 'a', x: 0, y: 0 }, { id: 'b', x: 10, y: 0 },
  { id: 'c', x: 4, y: 8 }, { id: 'd', x: 14, y: 12 },
  { id: 'o', x: 20, y: 20 }, { id: 'start', x: 25, y: 20 }, { id: 'end', x: 20, y: 25 },
], [
  { id: 'l', kind: 'line', p1: 'a', p2: 'b' }, { id: 'other', kind: 'line', p1: 'c', p2: 'd' },
  { id: 'arc', kind: 'arc', center: 'o', start: 'start', end: 'end', ccw: true },
]);
const sequences: [SelectionConstraint, string[]][] = [
  ['coincident', ['a', 'c']], ['horizontal', ['l']], ['vertical', ['l']], ['parallel', ['l', 'other']],
  ['perpendicular', ['l', 'other']], ['tangent', ['l', 'arc']], ['equal', ['l', 'other']],
  ['midpoint', ['a', 'other']], ['fix', ['a']], ['pointOnLine', ['a', 'other']], ['pointOnArc', ['a', 'arc']],
];
describe('pure constraint pick tool', () => {
  it.each(sequences)('%s ignores invalid picks, emits the rule constraint and stays active', (kind, ids) => {
    const s = sketch();
    let state = constraintTool([kind]);
    expect(pickConstraint(s, state, 'missing').state).toBe(state);
    // A wrong geometry kind is ignored at every step, including the first.
    const invalid = ['l', 'arc', 'a'].find(id => !acceptsConstraintPick(s, state, id));
    expect(invalid).toBeDefined();
    expect(pickConstraint(s, state, invalid!)).toEqual({ state });
    for (const [i, id] of ids.entries()) {
      const next = pickConstraint(s, state, id);
      if (i === ids.length - 1) {
        expect(next.constraint).toEqual(constraintFor(s, ids, kind));
        expect(next.constraint).toBeDefined();
        expect(next.state).toEqual(constraintTool([kind]));
        expect(acceptsConstraintPick(s, next.state, ids[0])).toBe(true);
      } else {
        expect(next.constraint).toBeUndefined();
        expect(pickConstraint(s, next.state, id).state).toBe(next.state); // duplicate
        const wrong = kind === 'tangent' ? 'other' : 'arc';
        if (!acceptsConstraintPick(s, next.state, wrong)) expect(pickConstraint(s, next.state, wrong).state).toBe(next.state);
      }
      state = next.state;
    }
  });
  it.each(['horizontal', 'vertical'] as const)('also supports the two-point %s pattern', kind => {
    const s = sketch(), first = pickConstraint(s, constraintTool([kind]), 'a');
    expect(first.constraint).toBeUndefined();
    expect(pickConstraint(s, first.state, 'l').state).toBe(first.state);
    expect(pickConstraint(s, first.state, 'b').constraint).toEqual({ kind, a: 'a', b: 'b' });
  });
  it.each(['midpoint', 'pointOnLine', 'pointOnArc'] as const)('accepts curve-first %s', kind => {
    const s = sketch(), curve = kind === 'pointOnArc' ? 'arc' : 'other';
    const first = pickConstraint(s, constraintTool([kind]), curve);
    expect(first.state.ids).toEqual([curve]);
    expect(pickConstraint(s, first.state, 'a').constraint).toEqual(constraintFor(s, [curve, 'a'], kind));
  });
  it('cancels pending picks on Escape, then exits, with rule-derived hints', () => {
    const s = sketch(), initial = constraintTool(['parallel']);
    expect(constraintHint(s, initial)).toBe('Parallel: select the first line');
    const first = pickConstraint(s, initial, 'l').state;
    expect(constraintHint(s, first)).toBe('Parallel: select the second line');
    const cancelled = escapeConstraint(first)!;
    expect(cancelled).toEqual(initial); expect(escapeConstraint(cancelled)).toBeUndefined();
  });
  it.each(['parallel', 'perpendicular', 'pointOnLine', 'coincident'] as const)('accepts %s against context in either order', kind => {
    const context = { fingerprint: '1', rimPlaneLabel: '', items: [{ id: 'wall', kind: 'segment' as const, role: 'wall' as const, label: 'Wall', pickable: true, geometry: [{ x: 30, y: 0 }, { x: 30, y: 10 }] }] };
    const result = materialiseContext(sketch(), context, 'wall');
    const line = result.sketch.entities.find(e => e.id === result.id)!;
    if (line.kind !== 'line') throw new Error('Expected line');
    const ids = kind === 'coincident' ? ['a', line.p1] : [kind === 'pointOnLine' ? 'a' : 'l', result.id];
    for (const order of [ids, [...ids].reverse()]) {
      const first = pickConstraint(result.sketch, constraintTool([kind]), order[0]);
      expect(first.state.ids).toEqual([order[0]]);
      expect(pickConstraint(result.sketch, first.state, order[1]).constraint).toEqual(constraintFor(result.sketch, order, kind));
    }
    for (const rule of ['equal', 'midpoint'] as const) {
      expect(acceptsConstraintPick(result.sketch, constraintTool([rule]), result.id)).toBe(false);
      const first = pickConstraint(result.sketch, constraintTool([rule]), rule === 'equal' ? 'l' : 'a');
      expect(pickConstraint(result.sketch, first.state, result.id).state).toBe(first.state);
    }
  });
  it('filters hit targets before nearest-hit priority and hits infinite datum lines', () => {
    const s = sketch(), state = constraintTool(['parallel']);
    expect(hitConstraint(s, [], state, { x: 0, y: 0 }, 1)).toBe('l'); // point a is invalid
    const first = pickConstraint(s, state, 'l').state;
    expect(hitConstraint(s, [], first, { x: 5, y: 0 }, 1)).toBeUndefined();
    expect(hitConstraint(s, [{ kind: 'axis', id: 'other' }], first, { x: 104, y: 48 }, 1)).toBe('other');
  });
  it('offers supported rules only, with one Point on entry and the existing keys', () => {
    const c = capabilities();
    expect(constraintPalette()).toEqual([]);
    c.constraints.pointOnArc.support = 'native';
    const on = constraintPalette(c).filter(e => e.label === 'Point on');
    expect(on).toHaveLength(1); expect(on[0].kinds).toEqual(['pointOnLine', 'pointOnArc']);
    for (const entry of constraintPalette(c)) {
      for (const kind of entry.kinds) c.constraints[kind].support = 'unsupported';
      expect(constraintPalette(c).some(e => e.label === entry.label)).toBe(false);
    }
  });
});
