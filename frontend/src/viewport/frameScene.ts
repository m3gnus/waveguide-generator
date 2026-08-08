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

export function curvatureColors(values: Float32Array): Float32Array {
  let limit = 0;
  for (const value of values) {
    if (Number.isFinite(value)) limit = Math.max(limit, Math.abs(value));
  }
  if (limit === 0) limit = 1;
  const colors = new Float32Array(values.length * 3);
  for (let index = 0; index < values.length; index += 1) {
    const normalized = Number.isFinite(values[index]) ? Math.max(-1, Math.min(1, values[index] / limit)) : 0;
    const warm = Math.max(0, normalized);
    const cool = Math.max(0, -normalized);
    const center = 1 - Math.abs(normalized);
    colors[index * 3] = 0.18 + warm * 0.82 + center * 0.42;
    colors[index * 3 + 1] = 0.2 + center * 0.62;
    colors[index * 3 + 2] = 0.2 + cool * 0.8 + center * 0.48;
  }
  return colors;
}

export function surfaceBoundaryPositions(surface: SceneSurface): Float32Array {
  const triangleCount = Math.floor(surface.indices.length / 3);
  if (triangleCount > MAX_EDGE_TRIANGLES) return new Float32Array();
  const vertexCount = Math.floor(surface.positions.length / 3);
  const weldedVertices = new Map<string, number>();
  const weldedIds = new Uint32Array(vertexCount);
  for (let vertex = 0; vertex < vertexCount; vertex += 1) {
    const offset = vertex * 3;
    const key = [
      Math.round(surface.positions[offset] / EDGE_VERTEX_GRID),
      Math.round(surface.positions[offset + 1] / EDGE_VERTEX_GRID),
      Math.round(surface.positions[offset + 2] / EDGE_VERTEX_GRID),
    ].join(',');
    let weldedId = weldedVertices.get(key);
    if (weldedId === undefined) {
      weldedId = weldedVertices.size;
      weldedVertices.set(key, weldedId);
    }
    weldedIds[vertex] = weldedId;
  }

  interface Edge {
    a: number;
    b: number;
    normals: Array<[number, number, number]>;
  }
  const edges = new Map<string, Edge>();
  const add = (weldedA: number, weldedB: number, sourceA: number, sourceB: number, normal: [number, number, number]) => {
    if (weldedA === weldedB) return;
    const low = Math.min(weldedA, weldedB);
    const high = Math.max(weldedA, weldedB);
    const key = `${low}:${high}`;
    const edge = edges.get(key);
    if (edge) {
      edge.normals.push(normal);
      return;
    }
    edges.set(key, { a: sourceA, b: sourceB, normals: [normal] });
  };
  for (let offset = 0; offset + 2 < surface.indices.length; offset += 3) {
    const originalA = surface.indices[offset];
    const originalB = surface.indices[offset + 1];
    const originalC = surface.indices[offset + 2];
    if (originalA >= vertexCount || originalB >= vertexCount || originalC >= vertexCount) continue;
    const a = originalA * 3;
    const b = originalB * 3;
    const c = originalC * 3;
    const abx = surface.positions[b] - surface.positions[a];
    const aby = surface.positions[b + 1] - surface.positions[a + 1];
    const abz = surface.positions[b + 2] - surface.positions[a + 2];
    const acx = surface.positions[c] - surface.positions[a];
    const acy = surface.positions[c + 1] - surface.positions[a + 1];
    const acz = surface.positions[c + 2] - surface.positions[a + 2];
    const nx = aby * acz - abz * acy;
    const ny = abz * acx - abx * acz;
    const nz = abx * acy - aby * acx;
    const length = Math.hypot(nx, ny, nz);
    if (!Number.isFinite(length) || length === 0) continue;
    const normal: [number, number, number] = [nx / length, ny / length, nz / length];
    const weldedA = weldedIds[originalA];
    const weldedB = weldedIds[originalB];
    const weldedC = weldedIds[originalC];
    add(weldedA, weldedB, originalA, originalB, normal);
    add(weldedB, weldedC, originalB, originalC, normal);
    add(weldedC, weldedA, originalC, originalA, normal);
  }

  const featureEdges = [...edges.values()].filter(({ normals }) => {
    if (normals.length === 1) return true;
    for (let first = 0; first < normals.length; first += 1) {
      for (let second = first + 1; second < normals.length; second += 1) {
        const dot = Math.abs(
          normals[first][0] * normals[second][0]
          + normals[first][1] * normals[second][1]
          + normals[first][2] * normals[second][2],
        );
        if (dot < FEATURE_EDGE_COSINE) return true;
      }
    }
    return false;
  });
  const positions = new Float32Array(featureEdges.length * 6);
  featureEdges.forEach(({ a, b }, index) => {
    const sourceA = a * 3;
    const sourceB = b * 3;
    const target = index * 6;
    positions[target] = surface.positions[sourceA];
    positions[target + 1] = surface.positions[sourceA + 1];
    positions[target + 2] = surface.positions[sourceA + 2];
    positions[target + 3] = surface.positions[sourceB];
    positions[target + 4] = surface.positions[sourceB + 1];
    positions[target + 5] = surface.positions[sourceB + 2];
  });
  return positions;
}

const EDGE_VERTEX_GRID = 1e-4;
const FEATURE_EDGE_COSINE = Math.cos(20 * Math.PI / 180);
export const MAX_EDGE_TRIANGLES = 250_000;
