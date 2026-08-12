import { Box3, Vector3 } from 'three';
import type { DecodedFrame, FrameArray, FrameSurface } from '../api/frame';
import type { SceneSurface, SurfaceMaterialClass } from './types';

export interface FrameScene {
  surfaces: SceneSurface[];
  bounds: Box3;
  hasCurvature: boolean;
  edgeModeUnavailable?: boolean;
}

export function hasRenderableSurfaces(scene: FrameScene | null): boolean {
  return Boolean(scene?.surfaces.length);
}

function section<T extends FrameArray>(frame: DecodedFrame, name: string, constructor: { new (...args: never[]): T }): T {
  const value = frame.sections[name];
  if (!(value instanceof constructor)) throw new Error(`Surface section ${name} has the wrong type`);
  return value;
}

export function isEnclosureRole(role: string): boolean {
  return role === 'enclosure' || role.startsWith('enclosure.');
}

/**
 * The radiating surface gets its own colour; everything structural shares one.
 * The rim and the rear cap are the same material as the horn wall, so they read
 * as the horn — only the source cap and the enclosure are visually distinct.
 */
export function materialClassForSurface(surface: Pick<FrameSurface, 'role' | 'shading'>): SurfaceMaterialClass {
  const family = isEnclosureRole(surface.role)
    ? 'enclosure'
    : surface.role === 'source_cap'
      ? 'source'
      : 'horn';
  return `${family}-${surface.shading}` as SurfaceMaterialClass;
}

function curvatureName(frame: DecodedFrame, surface: FrameSurface): string | null {
  const declared = surface.curvatureMean ?? surface.curvaturePrincipal;
  if (declared && frame.sections[declared] instanceof Float32Array) return declared;
  const prefix = surface.positions.endsWith('.positions')
    ? surface.positions.slice(0, -'.positions'.length)
    : surface.positions;
  for (const candidate of [`${prefix}.curvatureMean`, `${prefix}.curvaturePrincipal`]) {
    if (frame.sections[candidate] instanceof Float32Array) return candidate;
  }
  if ((frame.header.surfaces?.length ?? 0) === 1) {
    if (frame.sections.curvatureMean instanceof Float32Array) return 'curvatureMean';
    if (frame.sections.curvaturePrincipal instanceof Float32Array) return 'curvaturePrincipal';
  }
  return null;
}

export function frameToScene(frame: DecodedFrame): FrameScene {
  // Scalar min/max rather than Box3.expandByPoint. This runs over every vertex
  // of every surface on every decoded frame -- up to 30 a second while a
  // control is being dragged -- and expandByPoint costs a Vector3.set plus two
  // component-wise Vector3 calls per vertex where six comparisons will do.
  let minX = Infinity;
  let minY = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let maxZ = -Infinity;
  const surfaces = (frame.header.surfaces ?? []).map((surface, index): SceneSurface => {
    const positions = section(frame, surface.positions, Float32Array);
    const normals = section(frame, surface.normals, Float32Array);
    const indices = section(frame, surface.indices, Uint32Array);
    const curvatureSection = curvatureName(frame, surface);
    const curvatureValues = curvatureSection ? section(frame, curvatureSection, Float32Array) : null;
    const curvature = curvatureValues?.length === positions.length / 3 ? curvatureValues : null;
    for (let offset = 0; offset < positions.length; offset += 3) {
      const x = positions[offset];
      const y = positions[offset + 1];
      const z = positions[offset + 2];
      if (x < minX) minX = x;
      if (y < minY) minY = y;
      if (z < minZ) minZ = z;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;
      if (z > maxZ) maxZ = z;
    }
    return {
      key: `${index}:${surface.role}:${surface.positions}`,
      role: surface.role,
      shading: surface.shading,
      materialClass: materialClassForSurface(surface),
      enclosure: isEnclosureRole(surface.role),
      positions,
      normals,
      indices,
      curvature,
    };
  });
  const bounds = new Box3();
  if (minX <= maxX) bounds.set(new Vector3(minX, minY, minZ), new Vector3(maxX, maxY, maxZ));
  if (bounds.isEmpty()) bounds.set(new Vector3(-50, -50, -50), new Vector3(50, 50, 50));
  return {
    surfaces,
    bounds,
    hasCurvature: surfaces.some((surface) => surface.curvature !== null),
    edgeModeUnavailable: surfaces.some((surface) => Math.floor(surface.indices.length / 3) > MAX_EDGE_TRIANGLES),
  };
}

/**
 * Curvature is a signed quantity, so this is a diverging ramp: two poles and a
 * neutral middle, never a hue at the midpoint. The poles are the interface's
 * own accent and blue, and flat regions land on a warm grey rather than on the
 * green that a red-to-cyan blend used to pass through -- a hue that read as a
 * third state the data does not have.
 */
const CURVATURE_CONVEX: readonly [number, number, number] = [0.878, 0.404, 0.247]; // --acc
const CURVATURE_CONCAVE: readonly [number, number, number] = [0.435, 0.616, 1.0]; // --blue
const CURVATURE_FLAT: readonly [number, number, number] = [0.58, 0.56, 0.53];

export function curvatureColors(values: Float32Array): Float32Array {
  let limit = 0;
  for (const value of values) {
    if (Number.isFinite(value)) limit = Math.max(limit, Math.abs(value));
  }
  if (limit === 0) limit = 1;
  const colors = new Float32Array(values.length * 3);
  for (let index = 0; index < values.length; index += 1) {
    const normalized = Number.isFinite(values[index]) ? Math.max(-1, Math.min(1, values[index] / limit)) : 0;
    const magnitude = Math.abs(normalized);
    const pole = normalized >= 0 ? CURVATURE_CONVEX : CURVATURE_CONCAVE;
    for (let channel = 0; channel < 3; channel += 1) {
      colors[index * 3 + channel] = CURVATURE_FLAT[channel] * (1 - magnitude) + pole[channel] * magnitude;
    }
  }
  return colors;
}

/** Smallest power of two that leaves an open-addressed table under ~50 % full. */
function tableSize(entries: number): number {
  let size = 16;
  while (size < entries * 2) size *= 2;
  return size;
}

/**
 * Feature edges of one surface, as a line-segment position buffer.
 *
 * This is the most expensive thing the viewport does per frame — measured at
 * 34.9 ms for a 59,844-triangle scene and 66.76 ms for a single 79,202-triangle
 * surface — and now that in-flight frames are actually rendered it runs on
 * every one of them in edges mode. It used to build a `Map` keyed on a string
 * per vertex (`"12345,678,-90"`) and another keyed on a string per edge, then
 * an array of freshly allocated three-element normal tuples per edge: roughly
 * 4N string allocations and 3T array allocations per surface, all of which the
 * garbage collector then has to take back at the frame rate.
 *
 * The algorithm is unchanged. Both maps became open-addressed tables over
 * typed arrays, the per-edge normal list became a chain of triangle indices
 * into one shared normal buffer, and edges are kept in insertion order so the
 * output segments come out in the same order as before.
 */
export function surfaceBoundaryPositions(surface: SceneSurface): Float32Array {
  const { positions: coordinates, indices } = surface;
  const triangleCount = Math.floor(indices.length / 3);
  if (triangleCount > MAX_EDGE_TRIANGLES) return new Float32Array();
  const vertexCount = Math.floor(coordinates.length / 3);

  // Weld vertices that share a grid cell. Open addressing on the quantised
  // integer coordinates, which is what the old string key encoded.
  const weldSlots = tableSize(vertexCount);
  const weldMask = weldSlots - 1;
  const weldX = new Int32Array(weldSlots);
  const weldY = new Int32Array(weldSlots);
  const weldZ = new Int32Array(weldSlots);
  const weldId = new Int32Array(weldSlots).fill(-1);
  const weldedIds = new Uint32Array(vertexCount);
  let weldedCount = 0;
  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    const offset = vertex * 3;
    const qx = Math.round(coordinates[offset] / EDGE_VERTEX_GRID);
    const qy = Math.round(coordinates[offset + 1] / EDGE_VERTEX_GRID);
    const qz = Math.round(coordinates[offset + 2] / EDGE_VERTEX_GRID);
    let slot = (Math.imul(qx, 0x9e3779b1) ^ Math.imul(qy, 0x85ebca6b) ^ Math.imul(qz, 0xc2b2ae35)) & weldMask;
    for (;;) {
      const existing = weldId[slot];
      if (existing === -1) {
        weldX[slot] = qx;
        weldY[slot] = qy;
        weldZ[slot] = qz;
        weldId[slot] = weldedCount;
        weldedIds[vertex] = weldedCount;
        weldedCount += 1;
        break;
      }
      if (weldX[slot] === qx && weldY[slot] === qy && weldZ[slot] === qz) {
        weldedIds[vertex] = existing;
        break;
      }
      slot = (slot + 1) & weldMask;
    }
  }

  // One normal per triangle, and one edge record per welded vertex pair. Each
  // record holds the source vertices of its first sighting (the old Map kept
  // the first insertion) and the head of a chain of contributing triangles.
  const maxEdges = triangleCount * 3;
  const edgeSlots = tableSize(maxEdges);
  const edgeMask = edgeSlots - 1;
  const edgeSlotIndex = new Int32Array(edgeSlots).fill(-1);
  const edgeLow = new Int32Array(maxEdges);
  const edgeHigh = new Int32Array(maxEdges);
  const edgeSourceA = new Uint32Array(maxEdges);
  const edgeSourceB = new Uint32Array(maxEdges);
  const edgeHead = new Int32Array(maxEdges);
  const chainTriangle = new Int32Array(maxEdges);
  const chainNext = new Int32Array(maxEdges);
  const normals = new Float32Array(triangleCount * 3);
  let edgeCount = 0;
  let chainCount = 0;

  const add = (weldedA: number, weldedB: number, sourceA: number, sourceB: number, triangle: number) => {
    if (weldedA === weldedB) return;
    const low = weldedA < weldedB ? weldedA : weldedB;
    const high = weldedA < weldedB ? weldedB : weldedA;
    let slot = (Math.imul(low, 0x9e3779b1) ^ Math.imul(high, 0x85ebca6b)) & edgeMask;
    for (;;) {
      const existing = edgeSlotIndex[slot];
      if (existing === -1) {
        edgeSlotIndex[slot] = edgeCount;
        edgeLow[edgeCount] = low;
        edgeHigh[edgeCount] = high;
        edgeSourceA[edgeCount] = sourceA;
        edgeSourceB[edgeCount] = sourceB;
        chainTriangle[chainCount] = triangle;
        chainNext[chainCount] = -1;
        edgeHead[edgeCount] = chainCount;
        chainCount += 1;
        edgeCount += 1;
        return;
      }
      if (edgeLow[existing] === low && edgeHigh[existing] === high) {
        chainTriangle[chainCount] = triangle;
        chainNext[chainCount] = edgeHead[existing];
        edgeHead[existing] = chainCount;
        chainCount += 1;
        return;
      }
      slot = (slot + 1) & edgeMask;
    }
  };

  for (let triangle = 0; triangle < triangleCount; triangle += 1) {
    const offset = triangle * 3;
    const originalA = indices[offset];
    const originalB = indices[offset + 1];
    const originalC = indices[offset + 2];
    if (originalA >= vertexCount || originalB >= vertexCount || originalC >= vertexCount) continue;
    const a = originalA * 3;
    const b = originalB * 3;
    const c = originalC * 3;
    const ax = coordinates[a];
    const ay = coordinates[a + 1];
    const az = coordinates[a + 2];
    const abx = coordinates[b] - ax;
    const aby = coordinates[b + 1] - ay;
    const abz = coordinates[b + 2] - az;
    const acx = coordinates[c] - ax;
    const acy = coordinates[c + 1] - ay;
    const acz = coordinates[c + 2] - az;
    const nx = aby * acz - abz * acy;
    const ny = abz * acx - abx * acz;
    const nz = abx * acy - aby * acx;
    const length = Math.hypot(nx, ny, nz);
    if (!Number.isFinite(length) || length === 0) continue;
    normals[offset] = nx / length;
    normals[offset + 1] = ny / length;
    normals[offset + 2] = nz / length;
    add(weldedIds[originalA], weldedIds[originalB], originalA, originalB, triangle);
    add(weldedIds[originalB], weldedIds[originalC], originalB, originalC, triangle);
    add(weldedIds[originalC], weldedIds[originalA], originalC, originalA, triangle);
  }

  // An edge is a feature if nothing shares it, or if any two of the triangles
  // that do differ in normal direction by more than the tangent-break angle.
  const featureEdges = new Uint32Array(edgeCount);
  let featureCount = 0;
  for (let edge = 0; edge < edgeCount; edge += 1) {
    const head = edgeHead[edge];
    if (chainNext[head] === -1) {
      featureEdges[featureCount] = edge;
      featureCount += 1;
      continue;
    }
    let feature = false;
    for (let first = head; first !== -1 && !feature; first = chainNext[first]) {
      const firstOffset = chainTriangle[first] * 3;
      const fx = normals[firstOffset];
      const fy = normals[firstOffset + 1];
      const fz = normals[firstOffset + 2];
      for (let second = chainNext[first]; second !== -1; second = chainNext[second]) {
        const secondOffset = chainTriangle[second] * 3;
        const dot = Math.abs(
          fx * normals[secondOffset]
          + fy * normals[secondOffset + 1]
          + fz * normals[secondOffset + 2],
        );
        if (dot < FEATURE_EDGE_COSINE) {
          feature = true;
          break;
        }
      }
    }
    if (feature) {
      featureEdges[featureCount] = edge;
      featureCount += 1;
    }
  }

  const positions = new Float32Array(featureCount * 6);
  for (let index = 0; index < featureCount; index += 1) {
    const edge = featureEdges[index];
    const sourceA = edgeSourceA[edge] * 3;
    const sourceB = edgeSourceB[edge] * 3;
    const target = index * 6;
    positions[target] = coordinates[sourceA];
    positions[target + 1] = coordinates[sourceA + 1];
    positions[target + 2] = coordinates[sourceA + 2];
    positions[target + 3] = coordinates[sourceB];
    positions[target + 4] = coordinates[sourceB + 1];
    positions[target + 5] = coordinates[sourceB + 2];
  }
  return positions;
}

const EDGE_VERTEX_GRID = 1e-4;
const FEATURE_EDGE_COSINE = Math.cos(20 * Math.PI / 180);
export const MAX_EDGE_TRIANGLES = 250_000;
