import { describe, expect, it } from 'vitest';
import type { DecodedFrame, FrameSurface } from '../api/frame';
import { curvatureColors, frameToScene, hasRenderableSurfaces, materialClassForSurface, MAX_EDGE_TRIANGLES, surfaceBoundaryPositions } from './frameScene';

function surface(role: string, prefix: string, shading: 'smooth' | 'flat' = 'smooth'): FrameSurface {
  return {
    role,
    positions: `${prefix}.positions`,
    normals: `${prefix}.normals`,
    indices: `${prefix}.indices`,
    shading,
    normalMethod: shading === 'flat' ? 'exact-planar' : 'analytic-parametric',
  };
}

function fixture(): DecodedFrame {
  const hornPositions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
  const rimPositions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 0, 1]);
  const hornNormals = new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]);
  const rimNormals = new Float32Array([0, 1, 0, 0, 1, 0, 0, 1, 0]);
  const hornIndices = new Uint32Array([0, 1, 2]);
  const rimIndices = new Uint32Array([0, 1, 2]);
  const surfaces = [surface('horn.inner', 'horn'), surface('mouth_rim', 'rim', 'flat')];
  return {
    header: {
      v: 1, kind: 'preview', designRevision: 2, lod: 'fine', surfaces,
      sections: [
        { name: 'horn.positions', dtype: 'f32', shape: [3, 3], byteOffset: 0, byteLength: hornPositions.byteLength },
        { name: 'horn.normals', dtype: 'f32', shape: [3, 3], byteOffset: 0, byteLength: hornNormals.byteLength },
        { name: 'horn.indices', dtype: 'u32', shape: [3], byteOffset: 0, byteLength: hornIndices.byteLength },
        { name: 'rim.positions', dtype: 'f32', shape: [3, 3], byteOffset: 0, byteLength: rimPositions.byteLength },
        { name: 'rim.normals', dtype: 'f32', shape: [3, 3], byteOffset: 0, byteLength: rimNormals.byteLength },
        { name: 'rim.indices', dtype: 'u32', shape: [3], byteOffset: 0, byteLength: rimIndices.byteLength },
      ],
    },
    sections: {
      'horn.positions': hornPositions, 'horn.normals': hornNormals, 'horn.indices': hornIndices,
      'rim.positions': rimPositions, 'rim.normals': rimNormals, 'rim.indices': rimIndices,
    },
  };
}

function sceneSurface(positions: number[], indices: number[]): ReturnType<typeof frameToScene>['surfaces'][number] {
  return {
    ...frameToScene(fixture()).surfaces[0],
    positions: new Float32Array(positions),
    normals: new Float32Array(positions.length),
    indices: new Uint32Array(indices),
  };
}

function hasSegment(lines: Float32Array, a: [number, number, number], b: [number, number, number]): boolean {
  for (let offset = 0; offset < lines.length; offset += 6) {
    const forward = lines[offset] === a[0] && lines[offset + 1] === a[1] && lines[offset + 2] === a[2]
      && lines[offset + 3] === b[0] && lines[offset + 4] === b[1] && lines[offset + 5] === b[2];
    const reverse = lines[offset] === b[0] && lines[offset + 1] === b[1] && lines[offset + 2] === b[2]
      && lines[offset + 3] === a[0] && lines[offset + 4] === a[1] && lines[offset + 5] === a[2];
    if (forward || reverse) return true;
  }
  return false;
}

const EDGE_VERTEX_GRID = 1e-4;
const FEATURE_EDGE_COSINE = Math.cos(20 * Math.PI / 180);

/**
 * The string-keyed Map implementation `surfaceBoundaryPositions` replaced,
 * retained verbatim as a differential oracle. It is 5-6x slower and allocates
 * ~4N strings and 3T tuples per surface, which is why it is no longer shipped;
 * it is still the definition of the answer.
 */
function boundaryPositionsOracle(surface: { positions: Float32Array; indices: Uint32Array }): Float32Array {
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
  interface Edge { a: number; b: number; normals: Array<[number, number, number]> }
  const edges = new Map<string, Edge>();
  const add = (weldedA: number, weldedB: number, sourceA: number, sourceB: number, normal: [number, number, number]) => {
    if (weldedA === weldedB) return;
    const low = Math.min(weldedA, weldedB);
    const high = Math.max(weldedA, weldedB);
    const key = `${low}:${high}`;
    const edge = edges.get(key);
    if (edge) { edge.normals.push(normal); return; }
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
    add(weldedIds[originalA], weldedIds[originalB], originalA, originalB, normal);
    add(weldedIds[originalB], weldedIds[originalC], originalB, originalC, normal);
    add(weldedIds[originalC], weldedIds[originalA], originalC, originalA, normal);
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

/** A deterministic pseudo-random source, so a failure is reproducible. */
function randomSource(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

describe('surfaceBoundaryPositions equals the string-keyed implementation it replaced', () => {
  it('agrees on the hand-built cases: seams, tangent breaks, open boundaries, degenerates', () => {
    const cases: Array<[number[], number[]]> = [
      // a quad split into two triangles: one internal diagonal
      [[0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0], [0, 1, 2, 0, 2, 3]],
      // two quads meeting on an unwelded but coplanar seam
      [[0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 1, 0, 0, 2, 0, 0, 2, 1, 0, 1, 1, 0],
        [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7]],
      // a welded 90-degree fold
      [[0, 0, 0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 0, 0, 1, 0, 1, 1], [0, 3, 2, 0, 2, 1, 0, 1, 5, 0, 5, 4]],
      // a lone triangle: every edge is an open boundary
      [[0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2]],
      // a zero-area triangle contributes nothing at all
      [[0, 0, 0, 1, 0, 0, 2, 0, 0, 0, 1, 0], [0, 1, 2, 0, 1, 3]],
      // repeated indices, and an index past the end of the position array
      [[0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 0, 1, 0, 1, 2, 0, 1, 9]],
      // three triangles sharing one edge: non-manifold, and only one pair folds
      [[0, 0, 0, 0, 1, 0, 1, 0, 0, -1, 0, 0, 0, 0, 1], [0, 1, 2, 0, 1, 3, 0, 1, 4]],
      // an empty surface
      [[], []],
    ];
    for (const [positions, indices] of cases) {
      const surface = { positions: new Float32Array(positions), indices: new Uint32Array(indices) };
      expect([...surfaceBoundaryPositions(sceneSurface(positions, indices))]).toEqual([...boundaryPositionsOracle(surface)]);
    }
  });

  it('agrees on randomised meshes with duplicated, welded and folded geometry', () => {
    for (let trial = 0; trial < 24; trial += 1) {
      const random = randomSource(trial * 7919 + 13);
      // Snap to a coarse lattice so vertices genuinely coincide and weld.
      const vertexCount = 6 + Math.floor(random() * 40);
      const positions: number[] = [];
      for (let vertex = 0; vertex < vertexCount; vertex += 1) {
        positions.push(
          Math.round(random() * 4) / 2,
          Math.round(random() * 4) / 2,
          Math.round(random() * 4) / 2,
        );
      }
      const indices: number[] = [];
      const triangles = 4 + Math.floor(random() * 60);
      for (let triangle = 0; triangle < triangles; triangle += 1) {
        indices.push(
          Math.floor(random() * vertexCount),
          Math.floor(random() * vertexCount),
          Math.floor(random() * vertexCount),
        );
      }
      const surface = { positions: new Float32Array(positions), indices: new Uint32Array(indices) };
      expect([...surfaceBoundaryPositions(sceneSurface(positions, indices))])
        .toEqual([...boundaryPositionsOracle(surface)]);
    }
  });
});

describe('frameToScene', () => {
  it('maps roles to material classes while retaining every declared hard-boundary surface', () => {
    const frame = fixture();
    const scene = frameToScene(frame);
    expect(scene.surfaces.map(({ role, materialClass }) => ({ role, materialClass }))).toEqual([
      { role: 'horn.inner', materialClass: 'horn-smooth' },
      { role: 'mouth_rim', materialClass: 'horn-flat' },
    ]);
    expect(scene.surfaces).toHaveLength(2);
    expect(scene.surfaces[0].positions).toBe(frame.sections['horn.positions']);
    expect(scene.surfaces[1].normals).toBe(frame.sections['rim.normals']);
  });

  it('maps enclosure roles to their role-visible material class', () => {
    expect(materialClassForSurface(surface('enclosure.roundover', 'box', 'flat'))).toBe('enclosure-flat');
  });

  it('colours only the radiating surface apart from the structure', () => {
    expect(materialClassForSurface(surface('source_cap', 'cap', 'flat'))).toBe('source-flat');
    for (const role of ['horn.inner', 'horn.outer', 'mouth_rim', 'wall.rear_cap']) {
      expect(materialClassForSurface(surface(role, role, 'smooth'))).toBe('horn-smooth');
    }
  });

  it('extracts only topology borders and omits the internal triangle diagonal', () => {
    expect(surfaceBoundaryPositions(sceneSurface(
      [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0],
      [0, 1, 2, 0, 2, 3],
    ))).toHaveLength(4 * 2 * 3);
  });

  it('welds a coplanar unwelded seam while retaining the true open boundary', () => {
    const lines = surfaceBoundaryPositions(sceneSurface(
      [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 1, 0, 0, 2, 0, 0, 2, 1, 0, 1, 1, 0],
      [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7],
    ));
    expect(lines).toHaveLength(6 * 2 * 3);
    expect(hasSegment(lines, [1, 0, 0], [1, 1, 0])).toBe(false);
  });

  it('draws a welded 90-degree tangent break', () => {
    const lines = surfaceBoundaryPositions(sceneSurface(
      [0, 0, 0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 0, 0, 1, 0, 1, 1],
      [0, 3, 2, 0, 2, 1, 0, 1, 5, 0, 5, 4],
    ));
    expect(hasSegment(lines, [0, 0, 0], [0, 1, 0])).toBe(true);
  });

  it('draws a true open boundary', () => {
    const lines = surfaceBoundaryPositions(sceneSurface(
      [0, 0, 0, 1, 0, 0, 0, 1, 0],
      [0, 1, 2],
    ));
    expect(lines).toHaveLength(3 * 2 * 3);
  });

  it('maps non-finite curvature samples to the finite neutral color', () => {
    const colors = curvatureColors(new Float32Array([Number.NaN, Number.POSITIVE_INFINITY, -2, 2]));
    expect([...colors].every(Number.isFinite)).toBe(true);
    expect([...colors.slice(0, 3)]).toEqual([...curvatureColors(new Float32Array([0])).slice(0, 3)]);
    expect([...colors.slice(3, 6)]).toEqual([...colors.slice(0, 3)]);
  });

  it('bounds edge extraction before allocating per-edge storage', () => {
    const sceneSurface = frameToScene(fixture()).surfaces[0];
    sceneSurface.indices = new Uint32Array((MAX_EDGE_TRIANGLES + 1) * 3);
    expect(surfaceBoundaryPositions(sceneSurface)).toHaveLength(0);
  });

  it('keeps over-cap fill geometry and marks edge mode unavailable', () => {
    const frame = fixture();
    frame.sections['horn.indices'] = new Uint32Array((MAX_EDGE_TRIANGLES + 1) * 3);
    const scene = frameToScene(frame);
    expect(scene.edgeModeUnavailable).toBe(true);
    expect(scene.surfaces[0].positions).toHaveLength(9);
    expect(scene.surfaces[0].indices).toHaveLength((MAX_EDGE_TRIANGLES + 1) * 3);
  });

  it('distinguishes valid empty-surface frames from renderable scenes', () => {
    expect(hasRenderableSurfaces(frameToScene({ ...fixture(), header: { ...fixture().header, surfaces: [] } }))).toBe(false);
    expect(hasRenderableSurfaces(frameToScene(fixture()))).toBe(true);
  });
});
