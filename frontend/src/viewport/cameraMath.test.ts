import { Box3, Vector3 } from 'three';
import { describe, expect, it } from 'vitest';
import { calculateCameraFit, clippingRange, orthographicExtents, zoomedOrthographicValue } from './cameraMath';

describe('depth clipping range', () => {
  it('brackets the model instead of spanning the universe', () => {
    // A 165 mm-radius waveguide viewed from ~680 mm — the everyday case that
    // used to resolve depth in ~28 mm steps because near pinned to 0.001.
    const { near, far } = clippingRange(680, 165);
    expect(near).toBeGreaterThan(1);
    expect(far / near).toBeLessThan(1_000);
    expect(far).toBeGreaterThan(680 + 165);
  });

  it('keeps a positive near plane when the camera is inside the model', () => {
    const { near, far } = clippingRange(12, 400);
    expect(near).toBeGreaterThan(0);
    expect(near).toBeLessThan(12);
    expect(far).toBeGreaterThan(near);
  });

  it('never divides by a zero distance', () => {
    const { near, far } = clippingRange(0, 0);
    expect(near).toBeGreaterThan(0);
    expect(far).toBeGreaterThan(near);
  });
});

describe('orthographic camera math', () => {
  it('fits wide and tall viewports without changing world proportions', () => {
    expect(orthographicExtents(10, 2)).toEqual({ left: -24, right: 24, top: 12, bottom: -12 });
    expect(orthographicExtents(10, 0.5)).toEqual({ left: -12, right: 12, top: 24, bottom: -24 });
  });

  it.each(['front', 'three-quarter', 'top'] as const)('centers and clips the %s preset in orthographic mode', (preset) => {
    const bounds = new Box3(new Vector3(-20, -10, 4), new Vector3(60, 30, 44));
    const fit = calculateCameraFit(bounds, preset, 'orthographic', 16 / 9);
    expect(fit.center.toArray()).toEqual([20, 10, 24]);
    expect(fit.left).toBeLessThan(0);
    expect(fit.right).toBeGreaterThan(0);
    expect(fit.near).toBeGreaterThan(0);
    expect(fit.far).toBeGreaterThan(fit.near);
    expect(fit.position.distanceTo(fit.center)).toBeGreaterThan(bounds.getSize(new Vector3()).length());
  });

  it('uses an explicit direction while still looking at the bounds center', () => {
    const bounds = new Box3(new Vector3(-20, -70, 4), new Vector3(60, 182, 44));
    const fit = calculateCameraFit(bounds, [-1, 0, 0], 'perspective', 16 / 9);
    expect(fit.center.toArray()).toEqual([20, 56, 24]);
    expect(fit.position.y).toBe(fit.center.y);
    expect(fit.position.z).toBe(fit.center.z);
    expect(fit.position.x).toBeLessThan(fit.center.x);
  });

  it('zooms orthographic cameras in and out symmetrically', () => {
    expect(zoomedOrthographicValue(1, 'in')).toBe(1.25);
    expect(zoomedOrthographicValue(1.25, 'out')).toBe(1);
  });
});
