import { Box3, Matrix4, Plane, Vector3 } from 'three';
import type { FieldPlaneSpec } from '../api/fieldPlane';

export type ModelClipMode = 'off' | 'section' | 'field-plane';

/** Which world axis the section cut's plane normal points along. X and Y are
 * the model's two symmetry planes; Z is the axial direction, throat to mouth. */
export type SectionAxis = 'x' | 'y' | 'z';

/** The order one press of the section button walks. Off is the fourth step, so
 * the control is still a way to switch the cut off and not only to change it. */
export const SECTION_AXES: readonly SectionAxis[] = ['x', 'y', 'z'];

export interface SectionCutState {
  clipMode: ModelClipMode;
  sectionAxis: SectionAxis;
}

/**
 * One press of the section-cut button: X, Y, Z, off.
 *
 * Any other clip mode — off, or the field-plane clip — enters the cycle at its
 * first axis rather than resuming where it left off, so a press from a state
 * the user can see is uncut always produces a visible cut. Turning the cut off
 * also rewinds the axis, which is what makes the next press predictable.
 */
export function nextSectionCut({ clipMode, sectionAxis }: SectionCutState): SectionCutState {
  if (clipMode !== 'section') return { clipMode: 'section', sectionAxis: SECTION_AXES[0] };
  const upcoming = SECTION_AXES[SECTION_AXES.indexOf(sectionAxis) + 1];
  return upcoming
    ? { clipMode: 'section', sectionAxis: upcoming }
    : { clipMode: 'off', sectionAxis: SECTION_AXES[0] };
}

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

const SECTION_NORMALS: Record<SectionAxis, [number, number, number]> = {
  x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1],
};

/**
 * The plane one axis of the section cut clips against, keeping the positive
 * half of the model.
 *
 * The cut goes through the model origin when the origin is inside the model on
 * that axis — which is exactly the case on X and Y, the two symmetry planes,
 * and is the plane the single fixed cut has always used. On an axis where the
 * origin sits at or outside an end, cutting there would remove nothing and the
 * press would look like it did nothing at all; those cut through the middle of
 * the model's extent instead. Z, throat to mouth, is normally that axis.
 */
export function sectionClipPlane(axis: SectionAxis, bounds?: Box3, target = new Plane()): Plane {
  const normal = new Vector3(...SECTION_NORMALS[axis]);
  let offset = 0;
  if (bounds && !bounds.isEmpty()) {
    // `SectionAxis` is spelled as the Vector3 component it names, so the bounds
    // read directly off it.
    const minimum = bounds.min[axis];
    const maximum = bounds.max[axis];
    const span = maximum - minimum;
    // A hair inside each end, so a cut plane resting on the model's own face
    // still counts as outside it.
    const margin = span * 1e-3;
    if (!(minimum + margin < 0 && 0 < maximum - margin)) offset = (minimum + maximum) / 2;
  }
  // `-0` is a plane equation nobody wants to read in a debugger, and negating a
  // zero offset is the only way to produce one here.
  return target.set(normal, offset ? -offset : 0);
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
