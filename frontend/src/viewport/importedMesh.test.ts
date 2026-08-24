import { describe, expect, it } from 'vitest';
import { createImportedMeshScene } from './importedMesh';
import type { ParsedMSH } from './mshParser';
import type { SceneSurface } from './types';

function mesh(vertices: number[], triangles: number[][]): ParsedMSH {
  return {
    vertices: Float32Array.from(vertices),
    indices: Uint32Array.from(triangles.flat()),
    physicalNames: new Map([[7, 'imported-surface']]),
    physicalTags: Uint32Array.from(triangles.map(() => 7)),
  };
}

function normalAt(surface: SceneSurface, vertex: number): [number, number, number] {
  return [surface.normals[vertex * 3], surface.normals[vertex * 3 + 1], surface.normals[vertex * 3 + 2]];
}

function verticesAt(surface: SceneSurface, position: [number, number, number]): number[] {
  const matches: number[] = [];
  for (let vertex = 0; vertex * 3 < surface.positions.length; vertex += 1) {
    const offset = vertex * 3;
    if (surface.positions[offset] === position[0]
      && surface.positions[offset + 1] === position[1]
      && surface.positions[offset + 2] === position[2]) matches.push(vertex);
  }
  return matches;
}

describe('imported mesh shading', () => {
  it('marks imported solver and CAD meshes as metres', () => {
    const imported = createImportedMeshScene('metres.msh', mesh(
      [0, 0, 0, 1, 0, 0, 0, 1, 0],
      [[0, 1, 2]],
    ));
    expect(imported.scene.unitsPerMetre).toBe(1);
  });

  it('splits normals across a 90 degree crease instead of averaging over it', () => {
    // A horizontal face (normal +z) meeting a vertical one (normal +x) along
    // the x = z = 0 edge, wound as one consistent surface.
    const imported = createImportedMeshScene('crease.msh', mesh(
      [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1],
      [[0, 1, 2], [0, 2, 3], [0, 3, 4], [0, 4, 5]],
    ));
    const [surface] = imported.scene.surfaces;

    // Six welded positions, two of which sit on the crease and so carry one
    // normal per side.
    expect(surface.positions.length / 3).toBe(8);
    for (let vertex = 0; vertex * 3 < surface.normals.length; vertex += 1) {
      const [x, y, z] = normalAt(surface, vertex);
      expect(y).toBeCloseTo(0, 6);
      // Either face normal exactly, never the 45 degree average of the two.
      expect(Math.max(Math.abs(x), Math.abs(z))).toBeCloseTo(1, 6);
      expect(Math.min(Math.abs(x), Math.abs(z))).toBeCloseTo(0, 6);
    }

    const onCrease = verticesAt(surface, [0, 0, 0]);
    expect(onCrease).toHaveLength(2);
    const creaseNormals = onCrease.map((vertex) => normalAt(surface, vertex).map(Math.round));
    expect(creaseNormals).toContainEqual([0, 0, 1]);
    expect(creaseNormals).toContainEqual([1, 0, 0]);
  });

  it('still averages across a shallow fold whose seam vertices are duplicated', () => {
    const fold = 10 * Math.PI / 180;
    const imported = createImportedMeshScene('fold.msh', mesh(
      [
        0, 0, 0, 1, 0, 0, 0.5, 1, 0,
        1, 0, 0, 0, 0, 0, 0.5, -Math.cos(fold), Math.sin(fold),
      ],
      [[0, 1, 2], [3, 4, 5]],
    ));
    const [surface] = imported.scene.surfaces;

    // The seam is duplicated in the file, so index equality would read it as a
    // crease; welding positions keeps the join smooth.
    expect(surface.positions.length / 3).toBe(4);
    const [seam] = verticesAt(surface, [0, 0, 0]);
    const [, y, z] = normalAt(surface, seam);
    expect(y).toBeGreaterThan(0);
    expect(z).toBeLessThan(1);
  });
});

describe('independent CAD viewport artifacts', () => {
  it('keeps a full-domain artifact unmirrored while marking the solver half by centroid', () => {
    const imported = createImportedMeshScene('full-cad.msh', mesh(
      [
        1, 0, 0, 2, 0, 0, 1, 1, 0,
        -1, 0, 0, -1, 1, 0, -2, 0, 0,
      ],
      [[0, 1, 2], [3, 4, 5]],
    ), 'cad', 'wgi_full', ['x0'], {
      fullDomain: true,
      solvedTriangleCount: 17,
      artifactToken: 'sha256:visual',
    });

    expect(imported.triangleCount).toBe(2);
    expect(imported.solvedTriangleCount).toBe(17);
    expect(imported.artifactToken).toBe('sha256:visual');
    expect(imported.scene.surfaces.some((surface) => surface.key.includes(':mirror-'))).toBe(false);
    expect(imported.scene.surfaces.map((surface) => surface.solvedDomain).sort())
      .toEqual([false, true]);
  });
});

describe('imported source roles', () => {
  function tagged(names: Array<[number, string]>): ParsedMSH {
    // One degenerate-free triangle per tag, sharing a vertex pool.
    const triangles = names.map((_entry, index) => [index * 3, index * 3 + 1, index * 3 + 2]);
    const vertices = names.flatMap((_entry, index) => [
      index, 0, 0, index + 1, 0, 0, index, 1, 0,
    ]);
    return {
      vertices: Float32Array.from(vertices),
      indices: Uint32Array.from(triangles.flat()),
      physicalNames: new Map(names),
      physicalTags: Uint32Array.from(names.map(([tag]) => tag)),
    };
  }

  it('reads the Fusion paint role out of a WG ingest physical name', () => {
    const imported = createImportedMeshScene('return.msh', tagged([
      [1, 'wg-import-v1|rigid'],
      [2, 'wg-import-v1|tag=2|source_id=source-hf|instance_id=01J|role=HF'],
      [3, 'wg-import-v1|tag=3|source_id=source-lf|instance_id=01J|role=LF'],
      [4, 'wg-import-v1|tag=4|source_id=source-port-exit|instance_id=01J|role=PORT_EXIT'],
    ]));
    expect(imported.scene.surfaces.map((surface) => surface.role))
      .toEqual(['imported.rigid', 'imported.HF', 'imported.LF', 'imported.PORT_EXIT']);
    expect(imported.scene.surfaces.map((surface) => surface.sourceRole))
      .toEqual([null, 'HF', 'LF', 'PORT_EXIT']);
    expect(imported.scene.surfaces.map((surface) => surface.materialClass))
      .toEqual(['horn-smooth', 'hf-smooth', 'lf-smooth', 'port-smooth']);
  });

  it('keeps two sources of the same role apart on screen', () => {
    const imported = createImportedMeshScene('two-hf.msh', tagged([
      [2, 'wg-import-v1|tag=2|source_id=source-hf|instance_id=01J|role=HF'],
      [3, 'wg-import-v1|tag=3|source_id=source-hf-2|instance_id=01K|role=HF'],
    ]));
    expect(imported.scene.surfaces.map((surface) => surface.role))
      .toEqual(['imported.HF', 'imported.HF-2']);
    // Same role, so the same colour: the suffix distinguishes them by name.
    expect(imported.scene.surfaces.map((surface) => surface.materialClass))
      .toEqual(['hf-smooth', 'hf-smooth']);
  });

  it('colours the parametric mesher\'s own source groups too', () => {
    const imported = createImportedMeshScene('solver.msh', tagged([
      [1, 'SD1G0'],
      [2, 'SD1D1001'],
      [10, 'mid_port_exit_left'],
    ]));
    expect(imported.scene.surfaces.map((surface) => surface.sourceRole))
      .toEqual([null, 'HF', 'PORT_EXIT']);
  });

  it('leaves an unrecognised group neutral rather than guessing a role', () => {
    const imported = createImportedMeshScene('plain.msh', tagged([[7, 'imported surface']]));
    expect(imported.scene.surfaces[0].role).toBe('imported.imported-surface');
    expect(imported.scene.surfaces[0].sourceRole).toBeNull();
    expect(imported.scene.surfaces[0].materialClass).toBe('horn-smooth');
  });
});
