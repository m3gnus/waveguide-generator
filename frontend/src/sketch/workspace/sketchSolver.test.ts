// @vitest-environment node
import { expect, it, vi } from 'vitest';
import { SketchController } from '../controller';
import { applyCommand, importedSketch } from '../ops';
import { openSketchHandle, SketchSolveHandle } from './sketchSolver';

function initial() {
  return { sketch: importedSketch([{ id: 'p', x: 0, y: 0 }, { id: 'q', x: 10, y: 0 }],
    [{ kind: 'line', id: 'line', p1: 'p', p2: 'q' }]), meta: null };
}
it('returns the constraint and dimension diagnosis with the solved authoring', async () => {
  const state = initial(), handle = await openSketchHandle<null>(state.sketch, { references: [] });
  try {
    const constrained = await handle.constraint(state, { kind: 'distance', a: 'p', b: 'q', value: 10, driving: true }, { reference: false });
    expect(constrained.diagnosis.status).toBe('ok');
    expect(constrained.diagnosis.sketch).toEqual(constrained.state.sketch);
    const id = constrained.state.sketch.constraints.at(-1)!.id;
    const resized = await handle.dimension(constrained.state, id, 12);
    expect(resized.diagnosis.status).toBe('ok');
    expect(resized.diagnosis.sketch).toEqual(resized.state.sketch);
    expect(resized.state.sketch.constraints.find(c => c.id === id)).toHaveProperty('value', 12);
  } finally { handle.destroy(); }
});
it('delegates controller edits to the handle and keeps dimension guards after replacing sessions', async () => {
  const constraint = vi.spyOn(SketchSolveHandle.prototype, 'constraint');
  const dimension = vi.spyOn(SketchSolveHandle.prototype, 'dimension');
  const changed = vi.fn(), problem = vi.fn((_state, _id, value) => value === 99 ? 'Reserved value' : undefined);
  const controller = new SketchController({ initial: initial(), references: [], changed, dimensionProblem: problem,
    apply: (state, command) => { const result = applyCommand(state.sketch, command); return 'error' in result ? result : { ...state, sketch: result.sketch }; } });
  try {
    await controller.constraint({ kind: 'distance', a: 'p', b: 'q', value: 10, driving: true });
    expect(constraint).toHaveBeenCalledTimes(1);
    const constrained = await constraint.mock.results[0].value;
    expect(controller.diagnosis).toBe(constrained.diagnosis);
    const id = controller.current.sketch.constraints.at(-1)!.id;
    await controller.dimension(id, 12);
    expect(dimension).toHaveBeenCalledTimes(1);
    const resized = await dimension.mock.results[0].value;
    expect(changed).toHaveBeenLastCalledWith(resized.state, resized.diagnosis);
    const reject = async () => {
      const before = controller.current, diagnosis = controller.diagnosis;
      changed.mockClear();
      await expect(controller.dimension(id, 99)).rejects.toThrow('Reserved value');
      expect(controller.current).toBe(before); expect(controller.diagnosis).toBe(diagnosis);
      expect(changed).not.toHaveBeenCalled();
    };
    await reject();
    await controller.undo(); await reject();
    await controller.adopt(controller.current); await reject();
    await controller.replaceContext(state => state); await reject();
    await controller.rehydrate(controller.current); await reject();
    expect(problem).toHaveBeenCalledTimes(6);
  } finally { controller.destroy(); constraint.mockRestore(); dimension.mockRestore(); }
});
