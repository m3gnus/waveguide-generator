import { afterEach, describe, expect, it } from 'vitest';
import type { ImportedMeshScene } from './importedMesh';
import { importedMeshStore } from './importedMeshStore';

const cadScene = { name: 'Fusion assembly', source: 'cad', ingestId: 'wgi_example' } as ImportedMeshScene;
const fileScene = { name: 'inspection.msh', source: 'file', ingestId: null } as ImportedMeshScene;
const secondFileScene = { name: 'comparison.msh', source: 'file', ingestId: null } as ImportedMeshScene;
const solverScene = { name: 'Simulation mesh', source: 'solver', ingestId: null, artifactToken: 'solver:a' } as ImportedMeshScene;
const secondSolverScene = { name: 'Simulation mesh', source: 'solver', ingestId: null, artifactToken: 'solver:b' } as ImportedMeshScene;

describe('importedMeshStore', () => {
  afterEach(() => importedMeshStore.clear());

  it('retains CAD and file geometry in independent slots', () => {
    importedMeshStore.setFile(fileScene);
    importedMeshStore.setCad(cadScene);

    expect(importedMeshStore.getSnapshot()).toEqual({
      cad: cadScene,
      cadSolver: null,
      file: fileScene,
      solver: null,
      showing: 'cad',
    });

    importedMeshStore.setFile(secondFileScene);
    expect(importedMeshStore.getSnapshot()).toEqual({
      cad: cadScene,
      cadSolver: null,
      file: secondFileScene,
      solver: null,
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
    expect(importedMeshStore.getSnapshot()).toEqual({ cad: null, cadSolver: null, file: null, solver: null, showing: 'parametric' });
  });

  it('can fill the CAD slot without stealing a parametric viewport', () => {
    const generation = importedMeshStore.beginIntent();
    expect(importedMeshStore.setCad(cadScene, generation, false)).toBe(true);
    expect(importedMeshStore.getSnapshot()).toEqual({ cad: cadScene, cadSolver: null, file: null, solver: null, showing: 'parametric' });
  });

  it('drops the CAD solve mesh with the CAD geometry it belongs to', () => {
    const cadSolverScene = { ...cadScene, name: 'ingested solve mesh' };
    importedMeshStore.setFile(fileScene);
    importedMeshStore.setCad(cadScene);
    importedMeshStore.setCadSolver(cadSolverScene);
    expect(importedMeshStore.getSnapshot().showing).toBe('cadSolver');

    // Both artifacts describe one ingestion; a superseded return must not
    // leave its solve mesh behind claiming to describe the new geometry.
    importedMeshStore.clear('cad');
    expect(importedMeshStore.getSnapshot()).toEqual({
      cad: null, cadSolver: null, file: fileScene, solver: null, showing: 'parametric',
    });
  });

  it('lets the CAD mesh view be selected before its artifact is fetched', () => {
    importedMeshStore.showCadSolver();
    expect(importedMeshStore.getSnapshot().showing).toBe('cadSolver');
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

    expect(importedMeshStore.getSnapshot()).toEqual({ cad: null, cadSolver: null, file: fileScene, solver: null, showing: 'parametric' });
    importedMeshStore.showFile();
    expect(importedMeshStore.getSnapshot().showing).toBe('file');

    importedMeshStore.clear();
    expect(importedMeshStore.getSnapshot()).toEqual({ cad: null, cadSolver: null, file: null, solver: null, showing: 'parametric' });
  });

  it('lets the solver view be selected before its scene exists', () => {
    // Activation drives the first build, so `showing` moves ahead of the slot.
    importedMeshStore.showSolver();
    expect(importedMeshStore.getSnapshot()).toMatchObject({ solver: null, showing: 'solver' });

    importedMeshStore.setSolver(solverScene);
    expect(importedMeshStore.getSnapshot()).toMatchObject({ solver: solverScene, showing: 'solver' });
  });

  it('refreshes the solver slot in place without stealing another view', () => {
    importedMeshStore.setSolver(solverScene);
    importedMeshStore.showParametric();

    const generation = importedMeshStore.beginIntent();
    expect(importedMeshStore.setSolver(secondSolverScene, generation, false)).toBe(true);
    expect(importedMeshStore.getSnapshot()).toMatchObject({ solver: secondSolverScene, showing: 'parametric' });
  });

  it('returns to parametric when the visible solver slot is cleared', () => {
    importedMeshStore.setSolver(solverScene);
    expect(importedMeshStore.getSnapshot().showing).toBe('solver');

    importedMeshStore.clear('solver');
    expect(importedMeshStore.getSnapshot()).toMatchObject({ solver: null, showing: 'parametric' });
  });

  it('rejects a solver scene from a stale generation', () => {
    const generation = importedMeshStore.beginIntent();
    importedMeshStore.showParametric();

    expect(importedMeshStore.setSolver(solverScene, generation)).toBe(false);
    expect(importedMeshStore.getSnapshot().solver).toBeNull();
  });
});
