// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { SketchController, type Authoring } from './controller';
import { applyCommand, importedSketch } from './ops';
import { seed, applySourceCommand } from './adapters/sourceContour';
import { INITIAL_SOURCE } from '../design/sourceContourEditing';
const base = () => importedSketch([{ id: 'p', x: 0, y: 0 }, { id: 'q', x: 10, y: 0 }], [{ kind: 'line', id: 'line', p1: 'p', p2: 'q' }]);
function generic(sketch = base()) {
  return new SketchController({ sketch, meta: null }, [], (s, c) => {
    const r = applyCommand(s.sketch, c); return 'error' in r ? r : { ...s, sketch: r.sketch };
  }, () => {});
}
describe('authoring session transactions', () => {
  it('groups typed length and absolute angle with a segment, including a generic reference baseline', async () => {
    const initial = base(), controller = generic(initial);
    try {
      await controller.command({ kind: 'addLine', from: 'q', to: { x: 13, y: 3 } }, { length: Math.sqrt(18), angle: 45 });
      expect(controller.current.sketch.constraints).toContainEqual(expect.objectContaining({ kind: 'distance', driving: true, value: Math.sqrt(18) }));
      expect(controller.current.sketch.constraints).toContainEqual(expect.objectContaining({ kind: 'angle', value: Math.PI / 4 }));
      expect(controller.current.sketch.entities.some(e => e.construction)).toBe(true);
      await controller.undo(); expect(controller.current.sketch).toBe(initial);
      await controller.undo(true); expect(controller.current.sketch.entities.filter(e => !e.construction)).toHaveLength(2);
    } finally { controller.destroy(); }
  });
  it('refuses held coordinate edits and conflicting dimensions without creating undo entries', async () => {
    const controller = generic();
    try {
      await controller.constraint({ kind: 'fix', point: 'q' });
      const before = controller.current;
      await expect(controller.command({ kind: 'movePoint', id: 'q', x: 15, y: 0 })).rejects.toThrow('held by constraints');
      expect(controller.current).toBe(before);
      await controller.constraint({ kind: 'distance', a: 'p', b: 'q', value: 10, driving: true });
      const length = controller.current;
      await expect(controller.constraint({ kind: 'distance', a: 'p', b: 'q', value: 20, driving: true })).rejects.toThrow('Conflicts with');
      expect(controller.current).toBe(length);
      await controller.constraint({ kind: 'distance', a: 'p', b: 'q', value: 10, driving: true }, true);
      expect(controller.current.sketch.constraints.at(-1)).toMatchObject({ driving: false });
    } finally { controller.destroy(); }
  });
  it('continues processing unreachable drag cursors, and restores the exact start on cancel', async () => {
    const controller = generic();
    try {
      await controller.constraint({ kind: 'fix', point: 'p' });
      await controller.constraint({ kind: 'distance', a: 'p', b: 'q', value: 10, driving: true });
      const before = controller.current;
      await controller.beginDrag('q');
      const first = await controller.dragTo('q', { x: 20, y: 0 });
      const second = await controller.dragTo('q', { x: 0, y: 20 });
      expect(first.stopped).toBe(true); expect(second.stopped).toBe(true);
      expect(first.sketch.points.find(p => p.id === 'q')!.x).toBeCloseTo(10);
      expect(second.sketch.points.find(p => p.id === 'q')!.y).toBeCloseTo(10, 2);
      await controller.endDrag(true); expect(controller.current).toBe(before);
    } finally { controller.destroy(); }
  });
  it('merges a deleted line join with the left identity, reports dangling constraints, and refuses unequal source weights', async () => {
    const initial = seed(INITIAL_SOURCE), controller = new SketchController(initial, initial.meta.references, (s, c) => {
      const r = applySourceCommand(s.sketch, s.meta, c); return 'error' in r ? r : { sketch: r.sketch, meta: r.meta };
    }, () => {});
    try {
      await controller.command({ kind: 'splitAt', entity: 'piston', x: 4, y: 0 });
      const first = controller.current.sketch.entities.find(e => e.id === 'piston');
      if (first?.kind !== 'line') throw new Error('Expected line');
      const join = first.p2, right = controller.current.sketch.entities.find(e => !e.construction && e.id !== 'piston')!;
      await controller.metadata({ ...controller.current.meta, weights: { piston: 1, [right.id]: 2 } });
      await expect(controller.command({ kind: 'deletePoint', id: join })).rejects.toThrow('same role and literal weight');
      await controller.undo();
      await controller.constraint({ kind: 'fix', point: join });
      await controller.command({ kind: 'deletePoint', id: join });
      expect(controller.current.sketch.entities.filter(e => !e.construction)).toEqual([initial.sketch.entities[0]]);
      expect(controller.notice).toContain('Removed constraints');
      expect(controller.current.meta.weights).toEqual({ piston: 1 });
      await controller.undo(); expect(controller.current.sketch.points.some(p => p.id === join)).toBe(true);
    } finally { controller.destroy(); }
  });
  it('keeps source weights with geometry through split and undo, and records metadata separately', async () => {
    const initial = seed(INITIAL_SOURCE); initial.meta.weights.piston = -.5;
    let published: Authoring<typeof initial.meta> | undefined;
    const controller = new SketchController(initial, initial.meta.references, (s, c) => {
      const r = applySourceCommand(s.sketch, s.meta, c); return 'error' in r ? r : { sketch: r.sketch, meta: r.meta };
    }, state => { published = state; });
    try {
      await controller.command({ kind: 'splitAt', entity: 'piston', x: 4, y: 0 });
      expect(Object.values(controller.current.meta.weights)).toEqual([-.5, -.5]);
      await controller.undo(); expect(controller.current).toBe(initial); expect(published).toBe(initial);
      await controller.metadata({ ...initial.meta, motion: 'axial' });
      expect(controller.current.meta.motion).toBe('axial'); await controller.undo(); expect(controller.current).toBe(initial);
    } finally { controller.destroy(); }
  });
});
