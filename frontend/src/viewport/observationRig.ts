/**
 * Where the microphones are, in viewport space.
 *
 * The observation frame is inferred once, on the server, from the authoritative
 * Gmsh artifact, and handed to the backend as `frame_override` -- so the arc
 * built here is the arc the solve measured on, not a second guess at it. That
 * distinction is the whole reason the frame is transported rather than
 * re-derived: `_gmsh22_observation_frame` documents a horn in a deep cabinet
 * whose axis an extent-based guess flips, putting every polar two metres behind
 * the back panel. Two independent derivations would disagree exactly there, and
 * the viewport would be the one lying.
 *
 * The frame (origin, axis, u, v) therefore comes from the selected run. The rig
 * *on* it -- distance, angle range, planes, and whether it pivots at the mouth
 * or the throat -- comes from the settings on screen, so the arc answers those
 * fields as they are edited rather than only after another solve. Both centres
 * are published, which is what lets the mouth/throat choice move the pivot with
 * no solve at all.
 */
import type { ResultData } from '../api/results';

export type Vector3Tuple = [number, number, number];

export interface ObservationFrameBasis {
  axis: Vector3Tuple;
  u: Vector3Tuple;
  v: Vector3Tuple;
  origin_m: Vector3Tuple;
  mouth_center_m?: Vector3Tuple;
  source_center_m?: Vector3Tuple;
}

export type ObservationPlane = 'horizontal' | 'vertical' | 'diagonal';

export interface ObservationRigSpec {
  distanceM: number;
  angleStartDeg: number;
  angleEndDeg: number;
  angleCount: number;
  planes: ObservationPlane[];
  /** Degrees from horizontal for the diagonal plane. */
  inclinationDeg: number;
  origin: 'mouth' | 'throat';
}

export interface ObservationMicrophone {
  plane: ObservationPlane;
  angleDeg: number;
  /** Viewport-space position, already scaled out of metres. */
  position: Vector3Tuple;
}

export interface ObservationRig {
  /** The pivot, in viewport space. */
  origin: Vector3Tuple;
  microphones: ObservationMicrophone[];
  /** One flat position array per plane, ready for a line geometry. */
  arcs: Array<{ plane: ObservationPlane; positions: Float32Array }>;
}

function isVector(value: unknown): value is Vector3Tuple {
  return Array.isArray(value)
    && value.length === 3
    && value.every((component) => typeof component === 'number' && Number.isFinite(component));
}

/** The frame the selected run measured in, or null if it did not publish one. */
export function observationFrameBasisOf(result: ResultData | undefined): ObservationFrameBasis | null {
  const raw = result?.metadata?.observation_frame_basis;
  if (!raw || typeof raw !== 'object') return null;
  const record = raw as Record<string, unknown>;
  if (!isVector(record.axis) || !isVector(record.origin_m)) return null;
  // u and v are only absent on a frame built by something older than the field.
  // Without them there is no plane to sweep in, so this is not drawable.
  if (!isVector(record.u) || !isVector(record.v)) return null;
  return {
    axis: record.axis,
    u: record.u,
    v: record.v,
    origin_m: record.origin_m,
    ...(isVector(record.mouth_center_m) ? { mouth_center_m: record.mouth_center_m } : {}),
    ...(isVector(record.source_center_m) ? { source_center_m: record.source_center_m } : {}),
  };
}

function add(base: Vector3Tuple, axis: Vector3Tuple, axisScale: number, transverse: Vector3Tuple, transverseScale: number): Vector3Tuple {
  return [
    base[0] + axis[0] * axisScale + transverse[0] * transverseScale,
    base[1] + axis[1] * axisScale + transverse[1] * transverseScale,
    base[2] + axis[2] * axisScale + transverse[2] * transverseScale,
  ];
}

/**
 * The transverse direction each plane sweeps into.
 *
 * Mirrors `_custom_observation_points` exactly, inclination included, so the
 * diagonal drawn here is the diagonal that would be solved.
 */
export function transverseFor(basis: ObservationFrameBasis, plane: ObservationPlane, inclinationDeg: number): Vector3Tuple {
  if (plane === 'horizontal') return basis.u;
  if (plane === 'vertical') return basis.v;
  const inclination = (inclinationDeg * Math.PI) / 180;
  const cosine = Math.cos(inclination);
  const sine = Math.sin(inclination);
  return [
    basis.u[0] * cosine + basis.v[0] * sine,
    basis.u[1] * cosine + basis.v[1] * sine,
    basis.u[2] * cosine + basis.v[2] * sine,
  ];
}

/**
 * The pivot the rig turns about.
 *
 * `origin_m` is the one the run was solved with; the two named centres let the
 * choice be changed on screen without another solve. Falling back to
 * `origin_m` keeps a run that published only the one centre drawable.
 */
export function pivotFor(basis: ObservationFrameBasis, origin: ObservationRigSpec['origin']): Vector3Tuple {
  const named = origin === 'throat' ? basis.source_center_m : basis.mouth_center_m;
  return named ?? basis.origin_m;
}

/** How finely an arc is drawn, independent of how many angles were measured. */
const ARC_SEGMENTS = 96;

/**
 * Microphone positions and the arcs through them, in viewport units.
 *
 * `unitsPerMetre` is the viewport's own scale: a parametric preview is drawn in
 * millimetres while an imported solver mesh is in metres, and the frame is
 * always stated in metres.
 */
export function observationRig(
  basis: ObservationFrameBasis,
  spec: ObservationRigSpec,
  unitsPerMetre: number,
): ObservationRig {
  const pivot = pivotFor(basis, spec.origin);
  const origin: Vector3Tuple = [pivot[0] * unitsPerMetre, pivot[1] * unitsPerMetre, pivot[2] * unitsPerMetre];
  const radius = spec.distanceM * unitsPerMetre;
  const count = Math.max(1, Math.floor(spec.angleCount));
  const step = count > 1 ? (spec.angleEndDeg - spec.angleStartDeg) / (count - 1) : 0;
  const at = (transverse: Vector3Tuple, angleDeg: number): Vector3Tuple => {
    const angle = (angleDeg * Math.PI) / 180;
    return add(origin, basis.axis, radius * Math.cos(angle), transverse, radius * Math.sin(angle));
  };

  const microphones: ObservationMicrophone[] = [];
  const arcs: ObservationRig['arcs'] = [];
  spec.planes.forEach((plane) => {
    const transverse = transverseFor(basis, plane, spec.inclinationDeg);
    for (let index = 0; index < count; index += 1) {
      const angleDeg = spec.angleStartDeg + step * index;
      microphones.push({ plane, angleDeg, position: at(transverse, angleDeg) });
    }
    // The arc is drawn at its own resolution: a five-point sweep would
    // otherwise be a pentagon, and the shape being conveyed is a circle at a
    // fixed distance, not the sampling.
    const positions = new Float32Array((ARC_SEGMENTS + 1) * 3);
    for (let index = 0; index <= ARC_SEGMENTS; index += 1) {
      const angleDeg = spec.angleStartDeg + ((spec.angleEndDeg - spec.angleStartDeg) * index) / ARC_SEGMENTS;
      const point = at(transverse, angleDeg);
      positions[index * 3] = point[0];
      positions[index * 3 + 1] = point[1];
      positions[index * 3 + 2] = point[2];
    }
    arcs.push({ plane, positions });
  });
  return { origin, microphones, arcs };
}

/** The microphone nearest a point, for hover and selection. */
export function nearestMicrophone(rig: ObservationRig, point: Vector3Tuple): ObservationMicrophone | null {
  let best: ObservationMicrophone | null = null;
  let bestDistance = Infinity;
  rig.microphones.forEach((microphone) => {
    const [x, y, z] = microphone.position;
    const distance = (x - point[0]) ** 2 + (y - point[1]) ** 2 + (z - point[2]) ** 2;
    if (distance < bestDistance) {
      bestDistance = distance;
      best = microphone;
    }
  });
  return best;
}

/**
 * The plane and angle a viewport-space point maps to on the rig's sphere.
 *
 * This is the inverse of `observationRig`: given somewhere the user dragged a
 * microphone to, say which measured position it now names. Distance is not
 * recovered -- the rig has one radius, and a drag along it is a change of angle
 * rather than of distance.
 */
export function angleForPoint(
  basis: ObservationFrameBasis,
  spec: ObservationRigSpec,
  unitsPerMetre: number,
  point: Vector3Tuple,
): { plane: ObservationPlane; angleDeg: number } | null {
  const pivot = pivotFor(basis, spec.origin);
  const relative: Vector3Tuple = [
    point[0] / unitsPerMetre - pivot[0],
    point[1] / unitsPerMetre - pivot[1],
    point[2] / unitsPerMetre - pivot[2],
  ];
  const dot = (left: Vector3Tuple, right: Vector3Tuple) => left[0] * right[0] + left[1] * right[1] + left[2] * right[2];
  const axial = dot(relative, basis.axis);
  let best: { plane: ObservationPlane; angleDeg: number } | null = null;
  let bestOffPlane = Infinity;
  spec.planes.forEach((plane) => {
    const transverse = transverseFor(basis, plane, spec.inclinationDeg);
    const lateral = dot(relative, transverse);
    // How far out of this plane the point sits decides which plane it belongs
    // to: the component in neither the axis nor this plane's transverse.
    const offPlane = Math.sqrt(Math.max(0, dot(relative, relative) - axial * axial - lateral * lateral));
    if (offPlane < bestOffPlane) {
      bestOffPlane = offPlane;
      best = { plane, angleDeg: (Math.atan2(lateral, axial) * 180) / Math.PI };
    }
  });
  return best;
}
