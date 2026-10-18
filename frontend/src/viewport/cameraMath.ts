import { Box3, MathUtils, Vector3 } from 'three';
import type { CameraProjection } from '../viewerprefs/viewerPreferences';
import type { CameraPreset } from './types';

export type CameraDirection = readonly [number, number, number];
export type CameraView = CameraPreset | CameraDirection | Vector3;

export interface CameraFit {
  center: Vector3;
  position: Vector3;
  near: number;
  far: number;
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export function presetDirection(preset: CameraPreset): Vector3 {
  if (preset === 'front') return new Vector3(0, 0, 1);
  if (preset === 'top') return new Vector3(0, 1, 0.0001).normalize();
  return new Vector3(0.72, 0.52, 0.72).normalize();
}

export function viewDirection(view: CameraView): Vector3 {
  const direction = typeof view === 'string'
    ? presetDirection(view)
    : view instanceof Vector3
      ? view.clone()
      : new Vector3(...view);
  return direction.lengthSq() === 0 ? presetDirection('front') : direction.normalize();
}

/**
 * The bounding-sphere radius a camera frames, in scene units.
 *
 * Scenes are not all in one unit: parametric previews are millimetres, while
 * imported and CAD meshes are metres. An absolute floor (this used to be 0.5)
 * is half a millimetre in one and half a metre in the other, so a 70 mm
 * imported box was framed as if it were a metre across and sat as a speck in
 * the middle of the view. The floor therefore only guards a degenerate
 * (single-point) box, and scales with where that point is so it stays well
 * above float32 position resolution.
 */
export function framingRadius(bounds: Box3): number {
  const halfDiagonal = bounds.min.distanceTo(bounds.max) / 2;
  const magnitude = Math.max(...bounds.min.toArray().map(Math.abs), ...bounds.max.toArray().map(Math.abs));
  return Math.max(halfDiagonal, magnitude * 1e-3, 1e-6);
}

export function orthographicExtents(radius: number, aspect: number, padding = 1.2): Pick<CameraFit, 'left' | 'right' | 'top' | 'bottom'> {
  const safeAspect = Math.max(aspect, 0.01);
  const halfHeight = Math.max(radius, 1e-6) * padding * Math.max(1, 1 / safeAspect);
  const halfWidth = halfHeight * safeAspect;
  return { left: -halfWidth, right: halfWidth, top: halfHeight, bottom: -halfHeight };
}

export function calculateCameraFit(
  bounds: Box3,
  view: CameraView,
  projection: CameraProjection,
  aspect: number,
  verticalFovDegrees = 34,
): CameraFit {
  const center = bounds.getCenter(new Vector3());
  const radius = framingRadius(bounds);
  const verticalHalfFov = MathUtils.degToRad(verticalFovDegrees / 2);
  const horizontalHalfFov = Math.atan(Math.tan(verticalHalfFov) * Math.max(aspect, 0.01));
  const limitingHalfFov = Math.max(0.01, Math.min(verticalHalfFov, horizontalHalfFov));
  const perspectiveDistance = radius * 1.2 / Math.sin(limitingHalfFov);
  const distance = projection === 'perspective' ? perspectiveDistance : radius * 4;
  const position = center.clone().addScaledVector(viewDirection(view), distance);
  return {
    center,
    position,
    ...clippingRange(distance, radius),
    ...orthographicExtents(radius, aspect),
  };
}

/**
 * Depth planes that bracket the model instead of the universe.
 *
 * The previous rule always collapsed to `near = 0.001` and `far ≥ 2000`, and
 * since preview geometry is in millimetres that is a 2,000,000:1 depth range —
 * about 28 mm of depth resolution at a typical viewing distance, on a model
 * whose wall is a few millimetres thick. Bracketing the model keeps the ratio
 * near 1000:1, which a 24-bit buffer resolves to well under a hundredth of a
 * millimetre.
 *
 * `near` is floored proportionally rather than absolutely so that dollying
 * inside the bounding sphere shrinks it too, instead of clipping away whatever
 * the camera has moved close to.
 */
export function clippingRange(distance: number, radius: number): Pick<CameraFit, 'near' | 'far'> {
  const safeDistance = Math.max(distance, radius * 1e-3, 1e-9);
  const near = Math.max(safeDistance - radius * 1.5, safeDistance * 0.005);
  return { near, far: Math.max(near * 10, safeDistance + radius * 4) };
}

export function zoomedOrthographicValue(current: number, direction: 'in' | 'out'): number {
  return Math.min(1_000, Math.max(0.001, current * (direction === 'in' ? 1.25 : 0.8)));
}
