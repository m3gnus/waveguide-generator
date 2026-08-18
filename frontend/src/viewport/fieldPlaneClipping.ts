import { Box3, Matrix4, Plane, Vector3 } from 'three';
import type { FieldPlaneSpec } from '../api/fieldPlane';

export type ModelClipMode = 'off' | 'section' | 'field-plane';

export interface CapQuad {
  matrix: Matrix4;
  center: Vector3;
  axisU: Vector3;
  axisV: Vector3;
  width: number;
  height: number;
}

interface CapQuadOptions {
  preferredAxisU?: Vector3;
  padding?: number;
  minimumSize?: number;
}

export function fieldPlaneToClipPlane(
  fieldPlane: FieldPlaneSpec,
  unitsPerMetre: number,
  invert = false,
  target = new Plane(),
): Plane {
  const normal = new Vector3(...fieldPlane.axis_u).cross(new Vector3(...fieldPlane.axis_v)).normalize();
  const origin = new Vector3(...fieldPlane.origin_m).multiplyScalar(unitsPerMetre);
  target.setFromNormalAndCoplanarPoint(normal, origin);
  if (invert) target.negate();
  return target;
}

export function staticSectionClipPlane(target = new Plane()): Plane {
  return target.set(new Vector3(1, 0, 0), 0);
}

function capBasis(normal: Vector3, preferredAxisU?: Vector3): [Vector3, Vector3] {
  let axisU = preferredAxisU?.clone().addScaledVector(normal, -preferredAxisU.dot(normal));
  if (!axisU || axisU.lengthSq() < 1e-12) {
    const reference = Math.abs(normal.y) < 0.9 ? new Vector3(0, 1, 0) : new Vector3(1, 0, 0);
    axisU = reference.cross(normal);
  }
  axisU.normalize();
  return [axisU, normal.clone().cross(axisU).normalize()];
}

/** Orient a unit plane onto the clip plane and scale it to the scene bounds
 * projected into that plane's local basis. */
export function deriveCapQuad(plane: Plane, bounds: Box3, options: CapQuadOptions = {}): CapQuad {
  const normal = plane.normal.clone();
  if (normal.lengthSq() < 1e-12) throw new Error('Cap plane normal must be non-zero');
  normal.normalize();
  const [axisU, axisV] = capBasis(normal, options.preferredAxisU);
  const planeOrigin = plane.coplanarPoint(new Vector3());
  let minU = Infinity;
  let maxU = -Infinity;
  let minV = Infinity;
  let maxV = -Infinity;
  const relative = new Vector3();
  for (const x of [bounds.min.x, bounds.max.x]) {
    for (const y of [bounds.min.y, bounds.max.y]) {
      for (const z of [bounds.min.z, bounds.max.z]) {
        relative.set(x, y, z).sub(planeOrigin);
        const u = relative.dot(axisU);
        const v = relative.dot(axisV);
        minU = Math.min(minU, u);
        maxU = Math.max(maxU, u);
        minV = Math.min(minV, v);
        maxV = Math.max(maxV, v);
      }
    }
  }
  const padding = options.padding ?? 1.05;
  const minimumSize = options.minimumSize ?? 1;
  const width = Math.max((maxU - minU) * padding, minimumSize);
  const height = Math.max((maxV - minV) * padding, minimumSize);
  const center = planeOrigin.clone()
    .addScaledVector(axisU, (minU + maxU) / 2)
    .addScaledVector(axisV, (minV + maxV) / 2);
  const matrix = new Matrix4().makeBasis(
    axisU.clone().multiplyScalar(width),
    axisV.clone().multiplyScalar(height),
    normal,
  );
  matrix.setPosition(center);
  return { matrix, center, axisU, axisV, width, height };
}
