import { describe, expect, it, vi } from 'vitest';
import { exportProfileArtifacts, reportText } from './DesignFileMenu';
import type { GeometryExportFile, ImportReport } from '../api/designIo';

describe('profile artifact export', () => {
  const file = (kind: string) => ({
    filename: `horn_${kind}.csv`,
    blob: new Blob([kind], { type: 'text/csv' }),
  });

  it('builds both halves and writes them as one set', async () => {
    // One write, so the pair asks about its destination once and about
    // replacing existing files once. Two writes raced for a dialog that
    // answers one question at a time, and the loser was auto-declined.
    const build = vi.fn(async (kind: 'profiles' | 'slices') => file(kind));
    const written: GeometryExportFile[][] = [];
    const write = vi.fn(async (files: GeometryExportFile[]) => {
      written.push(files);
      return { directory: 'C:/Output/horn' };
    });

    const message = await exportProfileArtifacts(build, write, 7);

    expect(build).toHaveBeenCalledTimes(2);
    expect(write).toHaveBeenCalledTimes(1);
    expect(written[0].map(({ filename }) => filename))
      .toEqual(['horn_profiles.csv', 'horn_slices.csv']);
    expect(message).toBe('Exported profiles and slices CSV from revision 7 to C:/Output/horn');
  });

  it('reports partial success with the completed and failed artifact names', async () => {
    const build = vi.fn(async (kind: 'profiles' | 'slices') => {
      if (kind === 'slices') throw new Error('disk full');
      return file(kind);
    });
    const written: GeometryExportFile[][] = [];
    const write = vi.fn(async (files: GeometryExportFile[]) => {
      written.push(files);
      return { directory: 'C:/Output/horn' };
    });

    await expect(exportProfileArtifacts(build, write, 7))
      .rejects.toThrow('Exported profiles CSV to C:/Output/horn; failed slices: disk full');
    expect(build).toHaveBeenCalledTimes(2);
    // The half that built is still written: a reported failure must not also
    // lose the work that succeeded.
    expect(written[0].map(({ filename }) => filename)).toEqual(['horn_profiles.csv']);
  });

  it('writes nothing when neither half builds', async () => {
    const build = vi.fn(async (): Promise<GeometryExportFile> => { throw new Error('no geometry'); });
    const write = vi.fn(async () => ({ directory: 'C:/Output/horn' }));

    await expect(exportProfileArtifacts(build, write, 7)).rejects.toThrow('failed profiles');
    expect(write).not.toHaveBeenCalled();
  });
});

describe('opened-file report line', () => {
  const base: ImportReport = {
    dialect: 'ath',
    migrationsApplied: [],
    passthrough: { keysPreserved: [], blocksPreserved: [], keyCount: 0, blockCount: 0 },
  };

  it('says what a migration did, not which identifier it has', () => {
    const text = reportText({
      ...base,
      migrationsApplied: [{
        name: '006_machine_solver_mode_not_portable',
        note: 'Dropped Simulation.SolverMode. Set the solver path in Solve options.',
      }],
    });
    expect(text).toContain('Dropped Simulation.SolverMode. Set the solver path in Solve options.');
    // The identifier is server-side vocabulary; showing it was the defect.
    expect(text).not.toContain('006_machine_solver_mode_not_portable');
    expect(text).toContain('migrations: 1');
  });

  it('keeps the counts and says none when nothing was migrated', () => {
    expect(reportText({ ...base, dialect: 'mwg', passthrough: { keysPreserved: [], blocksPreserved: [], keyCount: 3, blockCount: 1 } }))
      .toBe('MWG · migrations: none · passthrough: 1 blocks, 3 keys preserved');
  });

  it('carries an ignored-setting note alongside the migration explanation', () => {
    const text = reportText({
      ...base,
      migrationsApplied: [{
        name: '006_machine_solver_mode_not_portable',
        note: 'Dropped Simulation.SolverMode. Set the solver path in Solve options.',
      }],
      ignoredSettings: [{
        key: 'WG.Solve.Engine',
        value: 'metal',
        note: "WG.Solve.Engine states 'metal'. Which backend runs a solve depends on the host, so the solve ignores it. Choose the engine in Solve options.",
      }],
    });
    expect(text).toContain('Dropped Simulation.SolverMode.');
    expect(text).toContain("WG.Solve.Engine states 'metal'.");
    // The two are different things: one key was migrated away, the other is
    // still in the file and simply not read.
    expect(text).toContain('migrations: 1');
  });

  it('is unchanged by a server that sends no ignoredSettings at all', () => {
    const withField = reportText({ ...base, ignoredSettings: [] });
    const withoutField = reportText(base);
    expect(withField).toBe(withoutField);
    expect(withoutField).toBe('ATH · migrations: none · passthrough: 0 blocks, 0 keys preserved');
  });

  it('reports every note when more than one migration applied', () => {
    const text = reportText({
      ...base,
      migrationsApplied: [
        { name: '001_a', note: 'First thing changed.' },
        { name: '002_b', note: 'Second thing changed.' },
      ],
    });
    expect(text).toContain('First thing changed. Second thing changed.');
    expect(text).toContain('migrations: 2');
  });
});
