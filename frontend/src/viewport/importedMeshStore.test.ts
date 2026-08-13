import { afterEach, describe, expect, it } from 'vitest';
import type { ImportedMeshScene } from './importedMesh';
import { importedMeshStore } from './importedMeshStore';

describe('importedMeshStore', () => {
  afterEach(() => importedMeshStore.clear());

  it('retains imported geometry while switching to the parametric viewport', () => {
    const scene = { name: 'Fusion assembly', source: 'cad', ingestId: 'wgi_example' } as ImportedMeshScene;

    importedMeshStore.set(scene);
    expect(importedMeshStore.getSnapshot()).toEqual({ scene, active: true });

    importedMeshStore.showParametric();
    expect(importedMeshStore.getSnapshot()).toEqual({ scene, active: false });

    importedMeshStore.showImported();
    expect(importedMeshStore.getSnapshot()).toEqual({ scene, active: true });
  });
});
