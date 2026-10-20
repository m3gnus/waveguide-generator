import { describe, expect, it } from 'vitest';
import { displayName } from './displayName';
import { seed, withSourceContext, withSourceLimit } from './adapters/sourceContour';
import { expandedPreset } from './adapters/presetTestSupport';
import { importedSketch } from './ops';
import { materialiseContext, type SketchContext } from './context';

describe('displayName', () => {
  it('names every preset patch and contour point without changing its identifier', () => {
    const { sketch } = seed(expandedPreset('cone'));
    expect(['dust-cap', 'cone', 'surround', 'land'].map(id => displayName(sketch, id))).toEqual(['Dust cap', 'Cone', 'Surround', 'Land']);
    expect(['pole', 'cap.rim', 'cone.rim', 'surround.rim', 'rim'].map(id => displayName(sketch, id))).toEqual(['Pole', 'Dust-cap edge', 'Cone edge', 'Surround edge', 'Rim']);
    expect(displayName(seed(expandedPreset('flat')).sketch, 'piston')).toBe('Piston');
    expect(displayName(seed(expandedPreset('dome')).sketch, 'dome.rim')).toBe('Dome edge');
    expect(displayName(seed(expandedPreset('dome')).sketch, 'dome')).toBe('Dome');
  });
  it('numbers construction lines separately with positive accessible names', () => {
    const sketch = importedSketch([{ id: 'a', x: 0, y: 0 }, { id: 'b', x: 1, y: 0 }], [
      { kind: 'line', id: 'one', p1: 'a', p2: 'b', construction: true },
      { kind: 'line', id: 'physical', p1: 'a', p2: 'b' },
      { kind: 'line', id: 'two', p1: 'a', p2: 'b', construction: true },
    ]);
    expect(displayName(sketch, 'one')).toBe('Construction line 1');
    expect(displayName(sketch, 'two')).toBe('Construction line 2');
    expect(displayName(sketch, 'physical')).toBe('Line 1');
  });
  it('numbers imported hex and UUID geometry by kind and authoring order', () => {
    const sketch = importedSketch([
      { id: 'point.abc', x: 0, y: 0 }, { id: 'point.def', x: 1, y: 0 }, { id: '18fc-e143-0ab9', x: 2, y: 1 }, { id: 'center', x: 1, y: 1 },
    ], [
      { kind: 'line', id: 'patch.aaa', p1: 'point.abc', p2: 'point.def' },
      { kind: 'arc', id: 'patch.bbb', start: 'point.def', end: '18fc-e143-0ab9', center: 'center', ccw: true },
      { kind: 'line', id: 'patch.ccc', p1: 'point.abc', p2: '18fc-e143-0ab9' },
    ]);
    expect(displayName(sketch, 'patch.aaa')).toBe('Line 1');
    expect(displayName(sketch, 'patch.ccc')).toBe('Line 2');
    expect(displayName(sketch, 'patch.bbb')).toBe('Arc 1');
    expect(displayName(sketch, '18fc-e143-0ab9')).toBe('Point 3');
  });
});


it('names constraints with their geometry and preserves custom labels', () => {
  const { sketch } = seed(expandedPreset('cone'));
  sketch.constraints.push({ id: 'opaque', kind: 'parallel', a: 'cone', b: 'axis' });
  expect(displayName(sketch, 'opaque')).toBe('Parallel (Cone, Axis of revolution)');
  sketch.constraints[sketch.constraints.length - 1].label = 'Cone direction';
  expect(displayName(sketch, 'opaque')).toBe('Cone direction (Cone, Axis of revolution)');
});

it.each([
  { kind: 'throat' as const, hard: true, label: 'Throat lock' },
  { kind: 'aperture' as const, hard: false, label: 'Aperture limit' },
  { kind: 'mouth' as const, hard: false, label: 'Mouth limit' },
])('names $label geometry, endpoints and constraints through displayName', ({ kind, hard, label }) => {
  const state = withSourceLimit(seed(expandedPreset('cone')), { kind, hard, radius: 12 });
  const id = state.meta.references.find(r => r.kind === 'limit')!.id;
  const line = state.sketch.entities.find(e => e.id === id)!;
  if (line.kind !== 'line') throw new Error('Expected line');
  expect(displayName(state.sketch, id)).toBe(label);
  expect(displayName(state.sketch, line.p1)).toBe(label);
  state.sketch.constraints.push({ id: 'test-ref', kind: 'pointOnLine', point: 'rim', line: id });
  expect(displayName(state.sketch, 'test-ref')).toBe(`Point on line (Rim, ${label})`);
});
it('names materialised context lines and the host rim plane', () => {
  const context: SketchContext = { fingerprint: '1', rimPlaneLabel: 'Throat plane (z = 0)', items: [{ id: 'box.back', kind: 'segment', geometry: [{ x: 0, y: -20 }, { x: 20, y: -20 }], role: 'enclosure', label: 'Enclosure back', pickable: true }] };
  const state = withSourceContext(seed(expandedPreset('cone')), context);
  expect(displayName(state.sketch, 'axis')).toBe('Axis of revolution');
  expect(displayName(state.sketch, 'baffle')).toBe(context.rimPlaneLabel);
  const picked = materialiseContext(state.sketch, context, 'box.back');
  expect(displayName(picked.sketch, picked.id)).toBe('Enclosure back');
  const line = picked.sketch.entities.find(e => e.id === picked.id)!;
  if (line.kind !== 'line') throw new Error('Expected line');
  expect(displayName(picked.sketch, line.p1)).toBe('Enclosure back');
});
