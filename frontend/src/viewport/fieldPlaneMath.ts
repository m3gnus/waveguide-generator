import type { FieldPlaneSpec, Vector3Tuple } from '../api/fieldPlane';

export type FieldPlanePreset = 'h' | 'v' | 'mouth';
export type FieldPlaneRotationAxis = 'u' | 'v';
export type FieldPlaneTranslationAxis = 'u' | 'v' | 'n';
export type FieldPlaneResizeAxis = 'u' | 'v' | 'both';

/** The request validator refuses extents outside (0, 100] metres, so the
 * interactive handles clamp to a slightly narrower band that always survives
 * the round trip. */
export const FIELD_PLANE_MIN_EXTENT_M = 0.005;
export const FIELD_PLANE_MAX_EXTENT_M = 100;

export interface FieldPlaneBounds {
  min: Vector3Tuple;
  max: Vector3Tuple;
  unitsPerMetre: number;
}

export interface FieldPlaneRay {
  origin: Vector3Tuple;
  direction: Vector3Tuple;
}

export interface FieldPlaneProbeHit {
  /** Normalised plane coordinates, (0,0) at the -u/-v corner, matching the
   * texture coordinates the field shader samples with. */
  u: number;
  v: number;
  /** Signed distance from the plane centre along each in-plane axis, metres. */
  offsetU_m: number;
  offsetV_m: number;
  /** Hit point in solver metres. */
  point_m: Vector3Tuple;
}

const SNAP_RADIANS = 5 * Math.PI / 180;
const EPSILON = 1e-10;

function add(a: Vector3Tuple, b: Vector3Tuple): Vector3Tuple {
  return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
}

function subtract(a: Vector3Tuple, b: Vector3Tuple): Vector3Tuple {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

function scale(vector: Vector3Tuple, scalar: number): Vector3Tuple {
  return [vector[0] * scalar, vector[1] * scalar, vector[2] * scalar];
}

function dot(a: Vector3Tuple, b: Vector3Tuple): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function cross(a: Vector3Tuple, b: Vector3Tuple): Vector3Tuple {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
}

function normalize(vector: Vector3Tuple): Vector3Tuple {
  const length = Math.hypot(...vector);
  if (length < EPSILON) throw new Error('Field-plane direction must be non-zero');
  return scale(vector, 1 / length);
}

export function fieldPlaneNormal(plane: Pick<FieldPlaneSpec, 'axis_u' | 'axis_v'>): Vector3Tuple {
  return normalize(cross(plane.axis_u, plane.axis_v));
}

export function fieldPlaneOffsetMetres(plane: Pick<FieldPlaneSpec, 'origin_m' | 'axis_u' | 'axis_v'>): number {
  return dot(plane.origin_m, fieldPlaneNormal(plane));
}

export function withFieldPlaneOffset(plane: FieldPlaneSpec, offsetMetres: number): FieldPlaneSpec {
  const normal = fieldPlaneNormal(plane);
  const delta = offsetMetres - dot(plane.origin_m, normal);
  return { ...plane, origin_m: add(plane.origin_m, scale(normal, delta)) };
}

function planeSpan(minimum: number, maximum: number, unitsPerMetre: number): number {
  const size = Math.max(0, maximum - minimum);
  const extentFromOrigin = Math.max(Math.abs(minimum), Math.abs(maximum));
  const sceneSpan = Math.max(size * 2, extentFromOrigin * 2, unitsPerMetre * 0.01);
  return Math.min(100, sceneSpan / unitsPerMetre);
}

export function fieldPlanePreset(
  plane: FieldPlaneSpec,
  preset: FieldPlanePreset,
  bounds: FieldPlaneBounds,
): FieldPlaneSpec {
  const span = (axis: 0 | 1 | 2) => planeSpan(bounds.min[axis], bounds.max[axis], bounds.unitsPerMetre);
  if (preset === 'h') {
    return {
      ...plane,
      origin_m: [0, 0, 0],
      axis_u: [1, 0, 0],
      axis_v: [0, 0, 1],
      width_m: span(0),
      height_m: span(2),
    };
  }
  if (preset === 'v') {
    return {
      ...plane,
      origin_m: [0, 0, 0],
      axis_u: [0, 1, 0],
      axis_v: [0, 0, 1],
      width_m: span(1),
      height_m: span(2),
    };
  }
  return {
    ...plane,
    origin_m: [0, 0, bounds.max[2] / bounds.unitsPerMetre],
    axis_u: [1, 0, 0],
    axis_v: [0, 1, 0],
    width_m: span(0),
    height_m: span(1),
  };
}

function rotateAroundAxis(vector: Vector3Tuple, axis: Vector3Tuple, angle: number): Vector3Tuple {
  const unitAxis = normalize(axis);
  const cosine = Math.cos(angle);
  const sine = Math.sin(angle);
  return add(
    add(scale(vector, cosine), scale(cross(unitAxis, vector), sine)),
    scale(unitAxis, dot(unitAxis, vector) * (1 - cosine)),
  );
}

export function rotateFieldPlane(
  plane: FieldPlaneSpec,
  rotationAxis: FieldPlaneRotationAxis,
  angleRadians: number,
): FieldPlaneSpec {
  if (rotationAxis === 'u') {
    return { ...plane, axis_v: normalize(rotateAroundAxis(plane.axis_v, plane.axis_u, angleRadians)) };
  }
  return { ...plane, axis_u: normalize(rotateAroundAxis(plane.axis_u, plane.axis_v, angleRadians)) };
}

export function snapFieldPlaneRotation(angleRadians: number, freeRotation: boolean): number {
  return freeRotation ? angleRadians : Math.round(angleRadians / SNAP_RADIANS) * SNAP_RADIANS;
}

function rayAxisParameter(ray: FieldPlaneRay, axisOrigin: Vector3Tuple, axis: Vector3Tuple): number | null {
  const rayDirection = normalize(ray.direction);
  const axisDirection = normalize(axis);
  const offset = subtract(ray.origin, axisOrigin);
  const parallel = dot(rayDirection, axisDirection);
  const denominator = 1 - parallel * parallel;
  if (Math.abs(denominator) < EPSILON) return null;
  return (dot(axisDirection, offset) - parallel * dot(rayDirection, offset)) / denominator;
}

/** Closest-line projection used by the normal-arrow drag. Values are in the
 * same units as the supplied rays and axis origin. */
export function translationDeltaAlongNormal(
  startRay: FieldPlaneRay,
  currentRay: FieldPlaneRay,
  axisOrigin: Vector3Tuple,
  normal: Vector3Tuple,
): number | null {
  const start = rayAxisParameter(startRay, axisOrigin, normal);
  const current = rayAxisParameter(currentRay, axisOrigin, normal);
  return start === null || current === null ? null : current - start;
}

function intersectRayPlane(ray: FieldPlaneRay, planeOrigin: Vector3Tuple, planeNormal: Vector3Tuple): Vector3Tuple | null {
  const direction = normalize(ray.direction);
  const normal = normalize(planeNormal);
  const denominator = dot(direction, normal);
  if (Math.abs(denominator) < EPSILON) return null;
  const distance = dot(subtract(planeOrigin, ray.origin), normal) / denominator;
  if (!Number.isFinite(distance)) return null;
  return add(ray.origin, scale(direction, distance));
}

/** Intersects both pointer rays with the rotation ring's plane and returns the
 * signed angle about the selected in-plane axis. */
export function rotationAngleFromRays(
  startRay: FieldPlaneRay,
  currentRay: FieldPlaneRay,
  center: Vector3Tuple,
  rotationAxis: Vector3Tuple,
): number | null {
  const startPoint = intersectRayPlane(startRay, center, rotationAxis);
  const currentPoint = intersectRayPlane(currentRay, center, rotationAxis);
  if (!startPoint || !currentPoint) return null;
  const startOffset = subtract(startPoint, center);
  const currentOffset = subtract(currentPoint, center);
  if (Math.hypot(...startOffset) < EPSILON || Math.hypot(...currentOffset) < EPSILON) return null;
  const startDirection = normalize(startOffset);
  const currentDirection = normalize(currentOffset);
  const axis = normalize(rotationAxis);
  return Math.atan2(dot(axis, cross(startDirection, currentDirection)), dot(startDirection, currentDirection));
}

export function fieldPlaneAxisVector(
  plane: Pick<FieldPlaneSpec, 'axis_u' | 'axis_v'>,
  axis: FieldPlaneTranslationAxis,
): Vector3Tuple {
  if (axis === 'u') return normalize(plane.axis_u);
  if (axis === 'v') return normalize(plane.axis_v);
  return fieldPlaneNormal(plane);
}

export function withFieldPlaneOrigin(plane: FieldPlaneSpec, origin: Vector3Tuple): FieldPlaneSpec {
  if (origin.some((value) => !Number.isFinite(value))) return plane;
  return { ...plane, origin_m: [origin[0], origin[1], origin[2]] };
}

/** Slides the plane bodily along one of its own axes. `u` and `v` keep the
 * plane coplanar; `n` is the same motion the normal arrow already performed. */
export function translateFieldPlane(
  plane: FieldPlaneSpec,
  axis: FieldPlaneTranslationAxis,
  deltaMetres: number,
): FieldPlaneSpec {
  if (!Number.isFinite(deltaMetres) || deltaMetres === 0) return plane;
  return { ...plane, origin_m: add(plane.origin_m, scale(fieldPlaneAxisVector(plane, axis), deltaMetres)) };
}

export function clampFieldPlaneExtent(extentMetres: number): number {
  if (!Number.isFinite(extentMetres)) return FIELD_PLANE_MIN_EXTENT_M;
  return Math.min(FIELD_PLANE_MAX_EXTENT_M, Math.max(FIELD_PLANE_MIN_EXTENT_M, extentMetres));
}

export function withFieldPlaneExtents(
  plane: FieldPlaneSpec,
  widthMetres: number,
  heightMetres: number,
): FieldPlaneSpec {
  return {
    ...plane,
    width_m: clampFieldPlaneExtent(widthMetres),
    height_m: clampFieldPlaneExtent(heightMetres),
  };
}

/** Grows the plane symmetrically about its centre, so resizing never drags the
 * observation origin off the model. Deltas are the extent change, not the
 * handle travel — the caller doubles the handle motion. */
export function resizeFieldPlane(
  plane: FieldPlaneSpec,
  widthDeltaMetres: number,
  heightDeltaMetres: number,
): FieldPlaneSpec {
  return withFieldPlaneExtents(
    plane,
    plane.width_m + (Number.isFinite(widthDeltaMetres) ? widthDeltaMetres : 0),
    plane.height_m + (Number.isFinite(heightDeltaMetres) ? heightDeltaMetres : 0),
  );
}

/** Closest-line projection along an arbitrary plane axis, shared by the
 * translate arrows and the resize grips. */
export const translationDeltaAlongAxis = translationDeltaAlongNormal;

/** Where a pointer ray meets the plane quad, in the same normalised
 * coordinates the fragment shader samples with. `ray` is in viewport units;
 * the plane is in solver metres. Returns null when the ray misses the quad. */
export function probeFieldPlaneRay(
  plane: FieldPlaneSpec,
  ray: FieldPlaneRay,
  unitsPerMetre: number,
): FieldPlaneProbeHit | null {
  if (!(unitsPerMetre > 0) || !Number.isFinite(unitsPerMetre)) return null;
  const center = scale(plane.origin_m, unitsPerMetre);
  const point = intersectRayPlane(ray, center, fieldPlaneNormal(plane));
  if (!point) return null;
  const offset = subtract(point, center);
  const offsetU_m = dot(offset, normalize(plane.axis_u)) / unitsPerMetre;
  const offsetV_m = dot(offset, normalize(plane.axis_v)) / unitsPerMetre;
  const u = offsetU_m / plane.width_m + 0.5;
  const v = offsetV_m / plane.height_m + 0.5;
  if (u < 0 || u > 1 || v < 0 || v > 1) return null;
  return { u, v, offsetU_m, offsetV_m, point_m: scale(point, 1 / unitsPerMetre) };
}
