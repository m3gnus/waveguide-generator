import { Box3, Vector3 } from 'three';
import type { DecodedFrame, FrameArray, FrameSurface } from '../api/frame';
import type { SceneSurface, SurfaceMaterialClass } from './types';

export interface FrameScene {
  surfaces: SceneSurface[];
  bounds: Box3;
  hasCurvature: boolean;
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

export function materialClassForSurface(surface: Pick<FrameSurface, 'role' | 'shading'>): SurfaceMaterialClass {
  const family = isEnclosureRole(surface.role)
    ? 'enclosure'
    : surface.role === 'horn.inner' || surface.role === 'horn.outer'
      ? 'horn'
      : 'boundary';
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
  const bounds = new Box3();
  const point = new Vector3();
  const surfaces = (frame.header.surfaces ?? []).map((surface, index): SceneSurface => {
    const positions = section(frame, surface.positions, Float32Array);
    const normals = section(frame, surface.normals, Float32Array);
    const indices = section(frame, surface.indices, Uint32Array);
    const curvatureSection = curvatureName(frame, surface);
    const curvatureValues = curvatureSection ? section(frame, curvatureSection, Float32Array) : null;
    const curvature = curvatureValues?.length === positions.length / 3 ? curvatureValues : null;
    for (let offset = 0; offset < positions.length; offset += 3) {
      point.set(positions[offset], positions[offset + 1], positions[offset + 2]);
      bounds.expandByPoint(point);
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
  if (bounds.isEmpty()) bounds.set(new Vector3(-50, -50, -50), new Vector3(50, 50, 50));
  return { surfaces, bounds, hasCurvature: surfaces.some((surface) => surface.curvature !== null) };
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
  const keys = new BigUint64Array(triangleCount * 3);
  let edgeCount = 0;
  const add = (a: number, b: number) => {
    if (a >= vertexCount || b >= vertexCount) return;
    const low = Math.min(a, b);
    const high = Math.max(a, b);
    keys[edgeCount] = (BigInt(low) << 32n) | BigInt(high);
    edgeCount += 1;
  };
  for (let offset = 0; offset + 2 < surface.indices.length; offset += 3) {
    const a = surface.indices[offset];
    const b = surface.indices[offset + 1];
    const c = surface.indices[offset + 2];
    add(a, b);
    add(b, c);
    add(c, a);
  }
  const sorted = keys.subarray(0, edgeCount);
  sorted.sort();
  let boundaryCount = 0;
  for (let index = 0; index < sorted.length;) {
    let end = index + 1;
    while (end < sorted.length && sorted[end] === sorted[index]) end += 1;
    if (end === index + 1) boundaryCount += 1;
    index = end;
  }
  const positions = new Float32Array(boundaryCount * 6);
  let target = 0;
  for (let index = 0; index < sorted.length;) {
    let end = index + 1;
    while (end < sorted.length && sorted[end] === sorted[index]) end += 1;
    if (end !== index + 1) {
      index = end;
      continue;
    }
    const key = sorted[index];
    const a = Number(key >> 32n) * 3;
    const b = Number(key & 0xffff_ffffn) * 3;
    positions[target] = surface.positions[a];
    positions[target + 1] = surface.positions[a + 1];
    positions[target + 2] = surface.positions[a + 2];
    positions[target + 3] = surface.positions[b];
    positions[target + 4] = surface.positions[b + 1];
    positions[target + 5] = surface.positions[b + 2];
    target += 6;
    index = end;
  }
  return positions;
}

export const MAX_EDGE_TRIANGLES = 250_000;
