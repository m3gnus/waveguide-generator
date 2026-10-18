import { describe, expect, it } from 'vitest';
import { Box3, MathUtils, Vector3 } from 'three';
import { calculateCameraFit, clippingRange, framingRadius, orthographicExtents, zoomedOrthographicValue } from './cameraMath';

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

// Imported and CAD meshes are in metres, parametric previews in millimetres.
// A 0.5-unit radius floor framed the 2026-10-18 restored FEM run -- a ported
// box 42 x 42 x 71 mm, i.e. 0.042 x 0.042 x 0.071 scene units -- as if it
// were a metre across, so it sat as a speck the user could not frame.
describe('framing is independent of scene units and model size', () => {
  const portedBoxMetres = new Box3(new Vector3(-0.001, -0.001, -0.001), new Vector3(0.041, 0.041, 0.07));
  const trueRadius = (box: Box3) => box.min.distanceTo(box.max) / 2;
  const scaled = (box: Box3, k: number) => new Box3(box.min.clone().multiplyScalar(k), box.max.clone().multiplyScalar(k));

  /** Fraction of the limiting viewport half-extent the bounding sphere covers. */
  function perspectiveFill(bounds: Box3, aspect: number): number {
    const fit = calculateCameraFit(bounds, 'three-quarter', 'perspective', aspect);
    const distance = fit.position.distanceTo(fit.center);
    const halfFov = MathUtils.degToRad(34 / 2);
    const limiting = Math.min(halfFov, Math.atan(Math.tan(halfFov) * aspect));
    return Math.asin(trueRadius(bounds) / distance) / limiting;
  }

  it.each([1e-3, 1, 1e3])('fills the view with the restored ported box at scale %s', (k) => {
    const bounds = scaled(portedBoxMetres, k);
    const fill = perspectiveFill(bounds, 16 / 9);
    expect(fill).toBeGreaterThan(0.6);
    expect(fill).toBeLessThan(1);
    const fit = calculateCameraFit(bounds, 'front', 'perspective', 16 / 9);
    expect(fit.center.toArray()).toEqual(bounds.getCenter(new Vector3()).toArray());
    // The whole model sits between the depth planes.
    expect(fit.near).toBeLessThan(fit.position.distanceTo(fit.center) - trueRadius(bounds));
    expect(fit.far).toBeGreaterThan(fit.position.distanceTo(fit.center) + trueRadius(bounds));
  });

  it('frames a metres model and its millimetre twin identically', () => {
    const metres = calculateCameraFit(portedBoxMetres, 'three-quarter', 'orthographic', 1.5);
    const millimetres = calculateCameraFit(scaled(portedBoxMetres, 1000), 'three-quarter', 'orthographic', 1.5);
    for (const key of ['near', 'far', 'left', 'right', 'top', 'bottom'] as const) {
      expect(millimetres[key]).toBeCloseTo(metres[key] * 1000, 6);
    }
    expect(millimetres.position.distanceTo(metres.position.clone().multiplyScalar(1000))).toBeLessThan(1e-9);
    // The orthographic window hugs the model rather than a 0.5-unit sphere.
    expect(metres.top).toBeLessThan(0.1);
  });

  it('still frames a degenerate single-point box', () => {
    const point = new Box3(new Vector3(0.02, 0.02, 0.07), new Vector3(0.02, 0.02, 0.07));
    const fit = calculateCameraFit(point, 'front', 'perspective', 1);
    expect(fit.position.distanceTo(fit.center)).toBeGreaterThan(0);
    expect(fit.near).toBeGreaterThan(0);
    expect(fit.far).toBeGreaterThan(fit.near);
  });
});
