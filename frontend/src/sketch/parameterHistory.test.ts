// @vitest-environment node
import { expect, it } from 'vitest';
import { SketchController } from './controller';
import { emptySketch } from './model';
import { applyCommand } from './ops';
import type { Command } from './commands';

it('uses one existing history transaction for each parameter command and restores exact states on undo/redo', async () => {
  const controller = new SketchController({ initial: { sketch: emptySketch(), meta: null }, references: [], apply: (state, command) => {
    const next = applyCommand(state.sketch, command); return 'error' in next ? next : { ...state, sketch: next.sketch };
  }, changed: () => {} });
  const commands: Command[] = [
    { kind: 'addParameter', name: 'pitch', expression: '2', unit: 'mm' },
    { kind: 'setParameter', name: 'pitch', expression: '3' },
    { kind: 'setParameter', name: 'pitch', expression: '3', unit: 'deg' },
    { kind: 'renameParameter', from: 'pitch', to: 'angle' },
    { kind: 'removeParameter', name: 'angle' },
  ];
  try {
    for (const command of commands) {
      const before = controller.current;
      await controller.command(command); const after = controller.current;
      expect(after).not.toBe(before);
      await controller.undo(); expect(controller.current).toBe(before);
      await controller.undo(true); expect(controller.current).toBe(after);
    }
    await controller.undo();
    const before = controller.current;
    await expect(controller.command({ kind: 'setParameter', name: 'angle', expression: 'angle + 1' })).rejects.toThrow('cycle');
    expect(controller.current).toBe(before);
    await controller.undo(); expect(controller.current.sketch.parameters?.[0].name).toBe('pitch');
  } finally { controller.destroy(); }
});

it('solves dimension consumers when a parameter changes and restores both geometry and expressions with history', async () => {
  const initial = emptySketch();
  initial.points = [{ id: 'a', x: 0, y: 0 }, { id: 'b', x: 10, y: 0 }];
  initial.entities = [{ id: 'line', kind: 'line', p1: 'a', p2: 'b' }];
  initial.constraints = [{ id: 'origin', kind: 'fix', point: 'a' }, { id: 'horizontal', kind: 'horizontal', line: 'line' },
    { id: 'width', kind: 'distance', a: 'a', b: 'b', driving: true, value: 10 }];
  const controller = new SketchController({ initial: { sketch: initial, meta: null }, references: [], apply: (state, command) => {
    const next = applyCommand(state.sketch, command); return 'error' in next ? next : { ...state, sketch: next.sketch };
  }, changed: () => {} });
  try {
    await controller.command({ kind: 'addParameter', name: 'pitch', expression: '10', unit: 'mm' });
    await controller.command({ kind: 'setDimensionExpression', id: 'width', expression: 'pitch' });
    const before = controller.current;
    await controller.command({ kind: 'setParameter', name: 'pitch', expression: '20' });
    const after = controller.current;
    expect(after.sketch.points.find(p => p.id === 'b')!.x).toBeCloseTo(20, 8);
    expect(after.sketch.constraints.find(c => c.id === 'width')).toMatchObject({ expression: 'pitch', value: 20 });
    await controller.undo(); expect(controller.current).toBe(before);
    await controller.undo(true); expect(controller.current).toBe(after);
  } finally { controller.destroy(); }
});
