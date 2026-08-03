import { describe, expect, it } from 'vitest';
import { createImportedMeshScene } from './importedMesh';
import { parseMSH } from './mshParser';
import fixture from './test-fixtures/tagged_sources-small.msh?raw';

describe('Gmsh 2.2 import', () => {
  it('matches the golden extracted from v1 output/tagged_sources.msh', () => {
    const parsed = parseMSH(fixture);
    expect([...parsed.vertices.slice(0, 6)]).toEqual([
      160.2053985595703, 412.9870910644531, 310,
      4.233309268951416, 0, 0,
    ]);
    expect([...parsed.indices]).toEqual([2, 0, 3, 4, 5, 1]);
    expect([...parsed.physicalTags]).toEqual([2, 4]);
    expect([...parsed.physicalNames]).toEqual([[1, 'rigid'], [2, 'LF'], [4, 'HF']]);
  });

  it('routes physical groups through the regular scene-surface path', () => {
    const imported = createImportedMeshScene('tagged_sources-small.msh', parseMSH(fixture));
    expect(imported.scene.surfaces.map((surface) => surface.role)).toEqual(['imported.LF', 'imported.HF']);
    expect(imported.scene.surfaces.every((surface) => surface.normals.every(Number.isFinite))).toBe(true);
    expect(imported.triangleCount).toBe(2);
  });

  it('rejects binary, newer, and dangling-node meshes with actionable errors', () => {
    expect(() => parseMSH('$MeshFormat\n2.2 1 8\n$EndMeshFormat')).toThrow('Only ASCII');
    expect(() => parseMSH('$MeshFormat\n4.1 0 8\n$EndMeshFormat')).toThrow('Unsupported');
    const dangling = '$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$Nodes\n1\n1 0 0 0\n$EndNodes\n$Elements\n1\n1 2 0 1 2 3\n$EndElements';
    expect(() => parseMSH(dangling)).toThrow('Unknown node id 2');
  });
});
