import { describe, expect, it, vi } from 'vitest';
import { SurfaceBufferManager, type SurfaceBuffers } from './bufferManager';

function buffers(positionCount = 3, normalCount = positionCount, indexCount = 3): SurfaceBuffers {
  return {
    positions: new Float32Array(positionCount * 3),
    normals: new Float32Array(normalCount * 3),
    indices: new Uint32Array(indexCount),
  };
}

function attributes(manager: SurfaceBufferManager) {
  return {
    position: manager.geometry.getAttribute('position'),
    normal: manager.geometry.getAttribute('normal'),
    index: manager.geometry.getIndex(),
  };
}

describe('SurfaceBufferManager', () => {
  it('reuses all three GPU attribute objects and swaps in zero-copy views when byte lengths match', () => {
    const manager = new SurfaceBufferManager();
    manager.update(buffers());
    const before = attributes(manager);
    const next = buffers();
    expect(manager.update(next)).toBe('reused');
    const after = attributes(manager);
    expect(after).toEqual(before);
    expect(after.position.array).toBe(next.positions);
    expect(after.normal.array).toBe(next.normals);
    expect(after.index?.array).toBe(next.indices);
  });

  it.each([
    ['position grow', buffers(4, 3, 3)],
    ['normal grow', buffers(3, 4, 3)],
    ['index grow', buffers(3, 3, 6)],
    ['position shrink', buffers(2, 3, 3)],
    ['normal shrink', buffers(3, 2, 3)],
    ['index shrink', buffers(3, 3, 2)],
  ])('replaces position, normal, and index together on %s', (_label, next) => {
    const manager = new SurfaceBufferManager();
    manager.update(buffers());
    const before = attributes(manager);
    const dispose = vi.spyOn(manager.geometry, 'dispose');
    expect(manager.update(next)).toBe('replaced');
    const after = attributes(manager);
    expect(after.position).not.toBe(before.position);
    expect(after.normal).not.toBe(before.normal);
    expect(after.index).not.toBe(before.index);
    expect(after.position.array).toBe(next.positions);
    expect(after.normal.array).toBe(next.normals);
    expect(after.index?.array).toBe(next.indices);
    expect(dispose).toHaveBeenCalledOnce();
  });

  it('retains a color attribute while curvature is off and disposes GPU state before resizing it', () => {
    const manager = new SurfaceBufferManager();
    manager.update({ ...buffers(), colors: new Float32Array(9) });
    const color = manager.geometry.getAttribute('color');
    manager.update(buffers());
    expect(manager.geometry.getAttribute('color')).toBe(color);

    const dispose = vi.spyOn(manager.geometry, 'dispose');
    manager.update({ ...buffers(), colors: new Float32Array(12) });
    expect(dispose).toHaveBeenCalledOnce();
    expect(manager.geometry.getAttribute('color')).not.toBe(color);
  });
});
