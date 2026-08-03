import { Box3, Vector3 } from 'three';
import { describe, expect, it } from 'vitest';
import { calculateCameraFit, orthographicExtents, zoomedOrthographicValue } from './cameraMath';

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

  it('zooms orthographic cameras in and out symmetrically', () => {
    expect(zoomedOrthographicValue(1, 'in')).toBe(1.25);
    expect(zoomedOrthographicValue(1.25, 'out')).toBe(1);
  });
});
