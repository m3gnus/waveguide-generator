import { afterEach, describe, expect, it } from 'vitest';
import type { ImportedMeshScene } from './importedMesh';
import { importedMeshStore } from './importedMeshStore';

const cadScene = { name: 'Fusion assembly', source: 'cad', ingestId: 'wgi_example' } as ImportedMeshScene;
const fileScene = { name: 'inspection.msh', source: 'file', ingestId: null } as ImportedMeshScene;
const secondFileScene = { name: 'comparison.msh', source: 'file', ingestId: null } as ImportedMeshScene;

describe('importedMeshStore', () => {
  afterEach(() => importedMeshStore.clear());

  it('retains CAD and file geometry in independent slots', () => {
    importedMeshStore.setFile(fileScene);
    importedMeshStore.setCad(cadScene);

    expect(importedMeshStore.getSnapshot()).toEqual({
      cad: cadScene,
      file: fileScene,
      showing: 'cad',
    });

    importedMeshStore.setFile(secondFileScene);
    expect(importedMeshStore.getSnapshot()).toEqual({
      cad: cadScene,
      file: secondFileScene,
      showing: 'file',
    });
  });

  it('restores the scene held by each slot when showing changes', () => {
    importedMeshStore.setFile(fileScene);
    importedMeshStore.setCad(cadScene);

    importedMeshStore.showFile();
    expect(importedMeshStore.getSnapshot()).toMatchObject({ file: fileScene, showing: 'file' });

    importedMeshStore.showParametric();
    expect(importedMeshStore.getSnapshot()).toMatchObject({ showing: 'parametric' });

    importedMeshStore.showCad();
    expect(importedMeshStore.getSnapshot()).toMatchObject({ cad: cadScene, showing: 'cad' });
  });

  it('does not name an empty slot as showing', () => {
    importedMeshStore.showCad();
    expect(importedMeshStore.getSnapshot().showing).toBe('parametric');

    importedMeshStore.setFile(fileScene);
    importedMeshStore.showCad();
    expect(importedMeshStore.getSnapshot().showing).toBe('file');
  });

  it('rejects an async apply from a stale generation', () => {
    const generation = importedMeshStore.beginIntent();
    importedMeshStore.showParametric();

    expect(importedMeshStore.setCad(cadScene, generation)).toBe(false);
    expect(importedMeshStore.getSnapshot()).toEqual({ cad: null, file: null, showing: 'parametric' });
  });

  it('keeps the snapshot reference stable across no-op calls', () => {
    const empty = importedMeshStore.getSnapshot();
    importedMeshStore.showCad();
    importedMeshStore.clear('file');
    expect(importedMeshStore.getSnapshot()).toBe(empty);

    importedMeshStore.setFile(fileScene);
    const file = importedMeshStore.getSnapshot();
    importedMeshStore.showFile();
    expect(importedMeshStore.getSnapshot()).toBe(file);
  });

  it('clears one slot without disturbing the other, or clears both', () => {
    importedMeshStore.setFile(fileScene);
    importedMeshStore.setCad(cadScene);
    importedMeshStore.clear('cad');

    expect(importedMeshStore.getSnapshot()).toEqual({ cad: null, file: fileScene, showing: 'parametric' });
    importedMeshStore.showFile();
    expect(importedMeshStore.getSnapshot().showing).toBe('file');

    importedMeshStore.clear();
    expect(importedMeshStore.getSnapshot()).toEqual({ cad: null, file: null, showing: 'parametric' });
  });
});
