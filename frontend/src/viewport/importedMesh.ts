import { Box3, Vector3 } from 'three';
import type { FrameScene } from './frameScene';
import type { ParsedMSH } from './mshParser';
import type { SceneSurface } from './types';

export interface ImportedMeshScene {
  name: string;
  scene: FrameScene;
  triangleCount: number;
  physicalGroupCount: number;
}

function calculateNormals(positions: Float32Array, indices: Uint32Array): Float32Array {
  const normals = new Float32Array(positions.length);
  for (let offset = 0; offset + 2 < indices.length; offset += 3) {
    const ia = indices[offset] * 3;
    const ib = indices[offset + 1] * 3;
    const ic = indices[offset + 2] * 3;
    const abx = positions[ib] - positions[ia];
    const aby = positions[ib + 1] - positions[ia + 1];
    const abz = positions[ib + 2] - positions[ia + 2];
    const acx = positions[ic] - positions[ia];
    const acy = positions[ic + 1] - positions[ia + 1];
    const acz = positions[ic + 2] - positions[ia + 2];
    const nx = aby * acz - abz * acy;
    const ny = abz * acx - abx * acz;
    const nz = abx * acy - aby * acx;
    for (const target of [ia, ib, ic]) {
      normals[target] += nx;
      normals[target + 1] += ny;
      normals[target + 2] += nz;
    }
  }
  for (let offset = 0; offset < normals.length; offset += 3) {
    const length = Math.hypot(normals[offset], normals[offset + 1], normals[offset + 2]) || 1;
    normals[offset] /= length;
    normals[offset + 1] /= length;
    normals[offset + 2] /= length;
  }
  return normals;
}

function roleForTag(tag: number, physicalNames: Map<number, string>): string {
  const name = physicalNames.get(tag)?.trim().replace(/[^a-zA-Z0-9_.-]+/g, '-') || `tag-${tag}`;
  return `imported.${name}`;
}

export function createImportedMeshScene(name: string, mesh: ParsedMSH): ImportedMeshScene {
  if (mesh.indices.length === 0) throw new Error('The MSH file contains no triangle elements');
  const grouped = new Map<number, number[]>();
  for (let triangle = 0; triangle < mesh.physicalTags.length; triangle += 1) {
    const tag = mesh.physicalTags[triangle];
    const indices = grouped.get(tag) ?? [];
    indices.push(mesh.indices[triangle * 3], mesh.indices[triangle * 3 + 1], mesh.indices[triangle * 3 + 2]);
    grouped.set(tag, indices);
  }
  const surfaces: SceneSurface[] = [...grouped.entries()].map(([tag, values]) => {
    const indices = Uint32Array.from(values);
    return {
      key: `msh:${name}:${tag}`,
      role: roleForTag(tag, mesh.physicalNames),
      shading: 'smooth',
      materialClass: 'boundary-smooth',
      enclosure: false,
      positions: mesh.vertices,
      normals: calculateNormals(mesh.vertices, indices),
      indices,
      curvature: null,
    };
  });
  const bounds = new Box3();
  const point = new Vector3();
  for (let offset = 0; offset < mesh.vertices.length; offset += 3) {
    bounds.expandByPoint(point.set(mesh.vertices[offset], mesh.vertices[offset + 1], mesh.vertices[offset + 2]));
  }
  if (bounds.isEmpty()) bounds.set(new Vector3(-1, -1, -1), new Vector3(1, 1, 1));
  return {
    name,
    scene: { surfaces, bounds, hasCurvature: false },
    triangleCount: mesh.indices.length / 3,
    physicalGroupCount: grouped.size,
  };
}
