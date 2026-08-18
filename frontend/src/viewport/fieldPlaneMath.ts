import type { FieldPlaneSpec, Vector3Tuple } from '../api/fieldPlane';

export type FieldPlanePreset = 'h' | 'v' | 'mouth';
export type FieldPlaneRotationAxis = 'u' | 'v';

export interface FieldPlaneBounds {
  min: Vector3Tuple;
  max: Vector3Tuple;
  unitsPerMetre: number;
}

export interface FieldPlaneRay {
  origin: Vector3Tuple;
  direction: Vector3Tuple;
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
