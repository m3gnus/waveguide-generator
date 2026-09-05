import { describe, expect, it, vi } from 'vitest';
import { exportProfileArtifacts, reportText } from './DesignFileMenu';
import type { ImportReport } from '../api/designIo';

describe('profile artifact export', () => {
  it('reports partial success with the completed and failed artifact names', async () => {
    const exporter = vi.fn(async (kind: 'profiles' | 'slices') => {
      if (kind === 'slices') throw new Error('disk full');
      return { directory: 'C:/Output/horn' };
    });
    await expect(exportProfileArtifacts(exporter, 7)).rejects.toThrow('Exported profiles CSV; failed slices: disk full');
    expect(exporter).toHaveBeenCalledTimes(2);
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
