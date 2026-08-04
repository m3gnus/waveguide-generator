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
    const sceneSurface = {
      ...frameToScene(fixture()).surfaces[0],
      positions: new Float32Array([0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0]),
      normals: new Float32Array(12),
      indices: new Uint32Array([0, 1, 2, 0, 2, 3]),
    };
    expect(surfaceBoundaryPositions(sceneSurface)).toHaveLength(4 * 2 * 3);
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

  it('distinguishes valid empty-surface frames from renderable scenes', () => {
    expect(hasRenderableSurfaces(frameToScene({ ...fixture(), header: { ...fixture().header, surfaces: [] } }))).toBe(false);
    expect(hasRenderableSurfaces(frameToScene(fixture()))).toBe(true);
  });
});
