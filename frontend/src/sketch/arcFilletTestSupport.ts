import { importedSketch } from './ops';

/** Quarter-circle corners exercise both signs of each circular offset. */
export function arcFilletCorner(first: 'line' | 'internal' | 'external', second: 'internal' | 'external' = 'internal') {
  const x = first === 'external' ? -10 : 10, y = second === 'internal' ? 10 : -10;
  return importedSketch([
    { id: 'corner', x: 0, y: 0 }, { id: 'farA', x: first === 'line' ? 0 : x, y: 10 },
    { id: 'farB', x: 10, y }, { id: 'centerA', x, y: 0 }, { id: 'centerB', x: 0, y },
  ], [first === 'line' ? { id: 'first', kind: 'line', p1: 'farA', p2: 'corner' }
    : { id: 'first', kind: 'arc', center: 'centerA', start: 'farA', end: 'corner', ccw: first === 'internal' },
  { id: 'second', kind: 'arc', center: 'centerB', start: 'corner', end: 'farB', ccw: second === 'internal' }]);
}
