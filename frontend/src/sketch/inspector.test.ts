import { describe, expect, it } from 'vitest';
import { inspectConstraints } from './inspector';
import { importedSketch } from './ops';
import type { Sketch } from './model';

function sample(): Sketch {
  const s = importedSketch([{ id: 'p1', x: 0, y: 0 }, { id: 'p2', x: 5, y: 0 }, { id: 'p3', x: 0, y: 5 }], [{ id: 'a4', kind: 'arc', center: 'p1', start: 'p2', end: 'p3', ccw: true }]);
  s.constraints = [{ id: 'c5', kind: 'fix', point: 'p1' }, { id: 'c6', kind: 'radius', arc: 'a4', value: 5, driving: true, diameter: true, label: 'Size' },
    { id: 'c7', kind: 'distance', a: 'p1', b: 'p2', value: 10, driving: true, expression: 'Size' }, { id: 'c8', kind: 'distance', a: 'p1', b: 'p3', value: 5, driving: false }]; s.nextId = 9; return s;
}
describe('constraint inspector', () => {
  it('sorts dimensions first, uses names and display units, and reports dependencies', () => {
    const rows = inspectConstraints(sample()); expect(rows.map(r => r.id)).toEqual(['c6', 'c7', 'c8', 'c5']);
    expect(rows[0]).toMatchObject({ name: 'Size (Arc 1)', refs: [{ id: 'a4', name: 'Arc 1' }], value: 'Ø10.00 mm', diameter: true, dependents: ['c7'], status: 'notAnalysed' });
    expect(rows[1].expression).toBe('Size'); expect(rows[2].status).toBe('reference');
  });
  it('maps conflict, redundancy and partial redundancy without inventing diagnostics', () => {
    expect(inspectConstraints(sample(), { conflicting: ['c6'], redundant: ['c5'], partiallyRedundant: ['c7'] }).map(r => r.status)).toEqual(['conflicting', 'redundant', 'reference', 'redundant']);
    expect(inspectConstraints(sample(), {})[0].status).toBe('ok');
  });
  it('filters by selected constraint, curve or point and respects an empty selection', () => {
    expect(inspectConstraints(sample(), undefined, ['c6']).map(r => r.id)).toEqual(['c6']);
    expect(inspectConstraints(sample(), undefined, ['a4']).map(r => r.id)).toEqual(['c6', 'c7', 'c8', 'c5']);
    expect(inspectConstraints(sample(), undefined, ['p2']).map(r => r.id)).toEqual(['c6', 'c7']);
    expect(inspectConstraints(sample(), undefined, [])).toEqual([]);
  });
  it('formats angles in degrees', () => {
    const s = sample(); s.constraints = [{ id: 'c5', kind: 'angle', a: 'a4', b: 'a4', value: Math.PI / 2, driving: true }];
    expect(inspectConstraints(s)[0].value).toBe('90.00°');
  });
});
