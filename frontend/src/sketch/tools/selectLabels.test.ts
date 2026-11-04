// @vitest-environment node
import { expect, it } from 'vitest';
import { selectLabels } from './selectLabels';
import { finishSelect, moveSelect, startSelect } from './select';
import { importedSketch } from '../ops';
const labels = [{ id: 'dimension', box: { x: 10, y: 20, width: 40, height: 28 } }];
it('windows contain the entire label, including its border', () => {
  expect(selectLabels(labels, { x: 10, y: 20 }, { x: 50, y: 48 }, false)).toEqual(['dimension']);
  expect(selectLabels(labels, { x: 11, y: 20 }, { x: 50, y: 48 }, false)).toEqual([]);
});
it('crossings include labels whose edges touch the box and exclude separated boxes', () => {
  expect(selectLabels(labels, { x: 60, y: 10 }, { x: 50, y: 20 })).toEqual(['dimension']);
  expect(selectLabels(labels, { x: 60, y: 10 }, { x: 51, y: 20 })).toEqual([]);
});
it('point picking uses the same label bounds as crossing', () => {
  expect(selectLabels(labels, { x: 10, y: 20 })).toEqual(['dimension']);
  expect(selectLabels(labels, { x: 9, y: 20 })).toEqual([]);
});
it.each([{}, { shift: true }, { toggle: true }])('combines label and geometry hits once with modifiers %j', modifiers => {
  const sketch = importedSketch([{ id: 'p', x: 1, y: 1 }], []);
  const state = moveSelect(startSelect({ x: 0, y: 0 }, undefined, ['dimension', 'old'], modifiers), { x: 20, y: 20 });
  const actual = finishSelect(state, sketch, state.start, state.current, [], ['dimension']);
  expect(actual).toEqual('toggle' in modifiers ? ['old', 'p'] : 'shift' in modifiers ? ['dimension', 'old', 'p'] : ['p', 'dimension']);
});
