import {
  BufferAttribute,
  BufferGeometry,
  DoubleSide,
  Ray,
  Vector3,
  type Intersection,
} from 'three';
import { MeshBVH, type HitPointInfo } from 'three-mesh-bvh';
import type { FieldPlaneSpec, Vector3Tuple } from '../api/fieldPlane';

const PARITY_DIRECTION = new Vector3(1, 0.3713906763541037, 0.6949718377497182).normalize();
const MIN_RAY_DISTANCE_M = 1e-9;
const COINCIDENT_HIT_EPSILON_M = 1e-7;

export interface FieldPlaneMaskMesh {
  geometry: BufferGeometry;
  bvh: MeshBVH;
  watertight: boolean;
}

function edgeKey(first: number, second: number): string {
  return first < second ? `${first}:${second}` : `${second}:${first}`;
}

/** A closed manifold triangle shell has exactly two incident triangles at
 * every undirected edge. Degenerate triangles cannot form a valid shell. */
export function isWatertightTriangleMesh(indices: Uint32Array): boolean {
  if (indices.length === 0 || indices.length % 3 !== 0) return false;
  const edgeCounts = new Map<string, number>();
  for (let offset = 0; offset < indices.length; offset += 3) {
    const a = indices[offset];
    const b = indices[offset + 1];
    const c = indices[offset + 2];
    if (a === b || b === c || c === a) return false;
    for (const [first, second] of [[a, b], [b, c], [c, a]] as const) {
      const key = edgeKey(first, second);
      edgeCounts.set(key, (edgeCounts.get(key) ?? 0) + 1);
    }
  }
  return [...edgeCounts.values()].every((count) => count === 2);
}

export function createFieldPlaneMaskMesh(
  vertices: Float32Array,
  indices: Uint32Array,
): FieldPlaneMaskMesh {
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(vertices, 3));
  geometry.setIndex(new BufferAttribute(indices, 1));
  return {
    geometry,
    bvh: new MeshBVH(geometry),
    watertight: isWatertightTriangleMesh(indices),
  };
}

function uniqueForwardHits(intersections: Intersection[]): number {
  const distances = intersections
    .map((intersection) => intersection.distance)
    .filter((distance) => Number.isFinite(distance) && distance > MIN_RAY_DISTANCE_M)
    .sort((first, second) => first - second);
  let count = 0;
  let previous = -Infinity;
  for (const distance of distances) {
    if (distance - previous <= COINCIDENT_HIT_EPSILON_M) continue;
    previous = distance;
    count += 1;
  }
  return count;
}

/** Odd/even ray parity against the worker's BVH. The fixed oblique ray avoids
 * the common axis-aligned face diagonals, and coincident triangle hits on one
 * face are collapsed before parity is evaluated. */
export function isPointInsideMaskMesh(mesh: FieldPlaneMaskMesh, point: Vector3Tuple): boolean {
  if (!mesh.watertight) return false;
  const ray = new Ray(new Vector3(...point), PARITY_DIRECTION);
  return uniqueForwardHits(mesh.bvh.raycast(ray, DoubleSide)) % 2 === 1;
}

export function fieldPlaneGridSpacingMetres(plane: FieldPlaneSpec): number {
  return Math.max(plane.width_m / (plane.nx - 1), plane.height_m / (plane.ny - 1));
}

export function fieldPlaneMaskDistanceMetres(plane: FieldPlaneSpec): number {
  return Math.max(fieldPlaneGridSpacingMetres(plane) / 4, 1e-4);
}

/** Classify in solver metres. 1 means transparent: either near the surface or,
 * only for a validated closed manifold shell, inside it. */
export function classifyFieldPlaneMask(mesh: FieldPlaneMaskMesh, plane: FieldPlaneSpec): Uint8Array {
  const mask = new Uint8Array(plane.nx * plane.ny);
  const threshold = fieldPlaneMaskDistanceMetres(plane);
  const origin = new Vector3(...plane.origin_m);
  const axisU = new Vector3(...plane.axis_u);
  const axisV = new Vector3(...plane.axis_v);
  const point = new Vector3();
  const closest: HitPointInfo = { point: new Vector3(), distance: Infinity, faceIndex: -1 };
  for (let y = 0; y < plane.ny; y += 1) {
    for (let x = 0; x < plane.nx; x += 1) {
      point.copy(origin)
        .addScaledVector(axisU, (x / (plane.nx - 1) - 0.5) * plane.width_m)
        .addScaledVector(axisV, (y / (plane.ny - 1) - 0.5) * plane.height_m);
      const nearest = mesh.bvh.closestPointToPoint(point, closest, 0, threshold);
      const nearSurface = nearest !== null && nearest.distance < threshold;
      const inside = !nearSurface && mesh.watertight
        ? isPointInsideMaskMesh(mesh, point.toArray())
        : false;
      if (nearSurface || inside) mask[y * plane.nx + x] = 1;
    }
  }
  return mask;
}
