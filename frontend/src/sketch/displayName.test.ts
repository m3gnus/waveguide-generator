import { describe, expect, it } from 'vitest';
import { displayName } from './displayName';
import { seed } from './adapters/sourceContour';
import { expandedPreset } from './adapters/presetTestSupport';
import { importedSketch } from './ops';

describe('displayName', () => {
  it('names every preset patch and contour point without changing its identifier', () => {
    const { sketch } = seed(expandedPreset('cone'));
    expect(['dust-cap', 'cone', 'surround', 'land'].map(id => displayName(sketch, id))).toEqual(['Dust cap', 'Cone', 'Surround', 'Land']);
    expect(['pole', 'cap.rim', 'cone.rim', 'surround.rim', 'rim'].map(id => displayName(sketch, id))).toEqual(['Pole', 'Dust-cap edge', 'Cone edge', 'Surround edge', 'Rim']);
    expect(displayName(seed(expandedPreset('flat')).sketch, 'piston')).toBe('Piston');
    expect(displayName(seed(expandedPreset('dome')).sketch, 'dome.rim')).toBe('Dome edge');
    expect(displayName(seed(expandedPreset('dome')).sketch, 'dome')).toBe('Dome');
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
