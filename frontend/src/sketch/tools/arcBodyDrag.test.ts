// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { ArcBodyDrag, bendableArc } from './arcBodyDrag';
import { arcBendTargets } from '../bend';
import { distance } from '../geometry';
import { importedSketch } from '../ops';
import type { Sketch } from '../model';
import type { DragTarget } from '../solver/types';

const sketch = () => importedSketch([{ id: 'c', x: 0, y: 0 }, { id: 'a', x: 5, y: 0 }, { id: 'b', x: 0, y: 5 }],
  [{ id: 'arc', kind: 'arc', center: 'c', start: 'a', end: 'b', ccw: true }]);
const moved = (s: Sketch, targets: DragTarget[]): Sketch => ({ ...s, points: s.points.map(p => ({ ...p, ...targets.find(t => t.pointId === p.id) })) });
function expectUnchanged(s: Sketch, targets: DragTarget[]) {
  for (const t of targets) expect(distance(t, s.points.find(p => p.id === t.pointId)!)).toBeLessThan(1e-8);
}

describe('arc body drag mode anchors', () => {
  it('starts with the groundwork targets in either mode', () => {
    const s = sketch(), cursor = { x: 4, y: 4 };
    for (const mode of ['bend', 'radius'] as const) expect(new ArcBodyDrag(s, 'arc', mode).targets(s, cursor, mode === 'radius')).toEqual(arcBendTargets(s, 'arc', cursor, mode));
  });
  it.each([{ x: -5, y: 0 }, { x: 0, y: 5 }, { x: 5, y: 1e-8 }, { x: 0, y: 0 }])('rebases radius to bend without a jump at %j', cursor => {
    const s = sketch(), drag = new ArcBodyDrag(s, 'arc', 'radius');
    expectUnchanged(s, drag.targets(s, cursor, false));
    const targets = drag.targets(s, { x: cursor.x + .2, y: cursor.y + .2 }, false);
    expect(targets[1]).toEqual({ pointId: 'a', x: 5, y: 0 }); expect(targets[2]).toEqual({ pointId: 'b', x: 0, y: 5 });
    expect(distance(targets[0], s.points[0])).toBeGreaterThan(.01);
  });
  it('rebases from the solved shape when constraints held it away from the requested shape', () => {
    const s = sketch(), drag = new ArcBodyDrag(s, 'arc', 'bend'), cursor = { x: 10, y: 10 };
    drag.targets(s, cursor, false);
    const solved = moved(s, arcBendTargets(s, 'arc', { x: 3.8, y: 3.8 }, 'bend'));
    expectUnchanged(solved, drag.targets(solved, cursor, true));
    const targets = drag.targets(solved, { x: 11, y: 11 }, true);
    expect(distance(targets[0], solved.points[0])).toBeLessThan(1e-8);
    expect(distance(targets[1], solved.points[1])).toBeGreaterThan(.01);
  });
  it('only offers bending for a single authored arc', () => {
    const s = sketch();
    expect(bendableArc(s, 'arc', [])).toBe('arc');
    expect(bendableArc(s, 'arc', ['arc'])).toBe('arc');
    expect(bendableArc(s, 'arc', ['arc', 'a'])).toBeUndefined();
    expect(bendableArc(s, 'a', ['a'])).toBeUndefined();
    s.entities[0].reference = true;
    expect(bendableArc(s, 'arc', [])).toBeUndefined();
  });
});
