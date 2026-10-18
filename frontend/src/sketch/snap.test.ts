// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { findSnap } from './snap';
import { seed } from './adapters/sourceContour';
import { INITIAL_SOURCE } from '../design/sourceContourEditing';
import { importedSketch } from './ops';

describe('snap inference', () => {
  it('prioritizes endpoints, then centers, axis, baffle, alignment and grid', () => {
    const { sketch, meta } = seed(INITIAL_SOURCE);
    expect(findSnap(sketch, meta.references, { x: .05, y: .05 }, .01)?.glyph).toBe('endpoint');
    expect(findSnap(sketch, meta.references, { x: .05, y: 3 }, .01)?.glyph).toBe('axis');
    expect(findSnap(sketch, meta.references, { x: 3, y: .05 }, .01)?.glyph).toBe('baffle');
    expect(findSnap(sketch, [], { x: 3, y: .05 }, .01)?.glyph).toBe('horizontal');
    expect(findSnap(sketch, [], { x: 8.05, y: 3 }, .01)?.glyph).toBe('vertical');
    const grid = findSnap(sketch, [], { x: 3.04, y: 2.04 }, .01);
    expect(grid).toEqual({ x: 3, y: 2, glyph: 'grid' });
    const arc = importedSketch([{ id: 'c', x: 0, y: 1 }, { id: 's', x: -2, y: 1 }, { id: 'e', x: 2, y: 1 }],
      [{ kind: 'arc', id: 'a', center: 'c', start: 's', end: 'e', ccw: true }]);
    expect(findSnap(arc, meta.references, { x: .02, y: 1.02 }, .01)).toMatchObject({ glyph: 'center', constraint: { kind: 'coincident', point: 'c' } });
  });
  it('holds a previous snap to 12px and releases it beyond that', () => {
    const { sketch, meta } = seed(INITIAL_SOURCE), previous = findSnap(sketch, meta.references, { x: .02, y: 0 }, .01)!;
    expect(findSnap(sketch, meta.references, { x: .11, y: 0 }, .01, previous)).toBeDefined();
    expect(findSnap(sketch, meta.references, { x: .11, y: 0 }, .01, previous)?.glyph).toBe('endpoint');
    expect(findSnap(sketch, meta.references, { x: .13, y: 0 }, .01, previous)?.glyph).toBe('baffle');
    const axis = findSnap(sketch, meta.references, { x: .02, y: 3 }, .01)!;
    expect(findSnap(sketch, meta.references, { x: .1, y: 9 }, .01, axis)).toMatchObject({ x: 0, y: 9, glyph: 'axis' });
    expect(findSnap(sketch, meta.references, { x: .01, y: 0 }, .01, previous, { suppress: true })).toBeNull();
  });
  it('ignores construction endpoints and permits no snap outside tolerance', () => {
    const { sketch, meta } = seed(INITIAL_SOURCE);
    expect(findSnap(sketch, meta.references, { x: 0, y: 1 }, .01)?.glyph).toBe('axis');
    expect(findSnap(sketch, [], { x: 3, y: 3 }, .13)).toBeNull();
    expect(() => findSnap(sketch, [], { x: 0, y: 0 }, 0)).toThrow();
  });
  it('refreshes held snap positions and releases a deleted target', () => {
    const { sketch } = seed(INITIAL_SOURCE), previous = findSnap(sketch, [], { x: 8, y: 0 }, .01)!;
    const moved = { ...sketch, points: sketch.points.map(p => p.id === 'rim' ? { ...p, x: 9 } : p) };
    expect(findSnap(moved, [], { x: 9.1, y: 0 }, .01, previous)).toMatchObject({ x: 9, glyph: 'endpoint' });
    const deleted = { ...moved, points: moved.points.filter(p => p.id !== 'rim'), entities: moved.entities.filter(e => e.id !== 'piston') };
    expect(findSnap(deleted, [], { x: 8.05, y: 0 }, .01, previous)?.glyph).not.toBe('endpoint');
  });
});
