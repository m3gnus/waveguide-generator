import { describe, expect, it } from 'vitest';
import { commandNotice } from './notices';
import { importedSketch } from './ops';
import { seedPreset, presetDefaults } from './adapters/sourcePreset';
import { expandedPreset } from './adapters/presetTestSupport';
import { applySourceCommand } from './adapters/sourceContour';

const before = () => {
  const s = importedSketch([{ id: 'a', x: 0, y: 0 }, { id: 'b', x: 2, y: 0 }, { id: 'c', x: 0, y: 2 }],
    [{ id: 'arc', kind: 'arc', center: 'a', start: 'b', end: 'c', ccw: true }]);
  s.constraints = [{ id: 'c1', kind: 'radius', arc: 'arc', value: 2, driving: true },
    { id: 'c2', kind: 'distanceY', a: 'a', b: 'c', value: 2, driving: true, label: 'Cone depth' },
    { id: 'c3', kind: 'horizontal', a: 'a', b: 'b' }, { id: 'c4', kind: 'fix', point: 'a' }];
  return s;
};

describe('user-facing edit notices', () => {
  it('reports preset dimensions by their names for Make line', () => {
    const s = seedPreset(expandedPreset('cone'), 'cone', presetDefaults.cone);
    const result = applySourceCommand(s.sketch, s.meta, { kind: 'convertEntity', id: 'dust-cap', to: 'line' });
    if ('error' in result) throw new Error(result.error);
    expect(result.notice).toBe('Make line removed 2 dimensions: Dust-cap radius, Dust-cap height.');
  });
  it.each([['fillet', 'Fillet'], ['trim', 'Trim'], ['extend', 'Extend'], ['splitAt', 'Split'], ['deleteEntity', 'Delete'], ['deletePoint', 'Delete'], ['removeConstraint', 'Delete']])('uses the action name for %s without ids or geometry lists', (kind, name) => {
    const s = before();
    expect(commandNotice(s, { ...s, constraints: s.constraints.slice(2) }, kind)).toBe(`${name} removed 2 dimensions: Radius, Cone depth.`);
    expect(commandNotice(s, { ...s, constraints: s.constraints.slice(1, 2) }, kind)).toBe(`${name} removed 1 dimension and 2 relations: Radius, Horizontal, Fix.`);
    expect(commandNotice(s, { ...s, constraints: s.constraints.slice(0, 3) }, kind)).toBe(`${name} removed 1 relation: Fix.`);
  });
  it('names Make arc and preserves custom dimension names containing parentheses', () => {
    const s = before(); s.entities = [{ id: 'arc', kind: 'line', p1: 'b', p2: 'c' }];
    s.constraints[0].label = 'Size (outer)';
    expect(commandNotice(s, { ...s, entities: before().entities, constraints: s.constraints.slice(1) }, 'convertEntity')).toBe('Make arc removed 1 dimension: Size (outer).');
  });
  it('explains frozen expressions in terms of the user action and dimension names', () => {
    const s = before(); s.constraints[1].expression = 'c1';
    const next = { ...s, constraints: s.constraints.slice(1).map(c => ({ ...c, expression: undefined })) };
    expect(commandNotice(s, next, 'deleteEntity')).toBe('Delete removed 1 dimension: Radius. Delete kept Cone depth at its last value because Radius was removed; its expression was cleared.');
  });
});
