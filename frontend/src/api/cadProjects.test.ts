import { describe, expect, it } from 'vitest';
import {
  groupRunsByModelState,
  runProjectLineage,
  runReturnStateHash,
  runsForProject,
  type CadProjectDocument,
} from './cadProjects';
import type { JobItem } from './jobsSocket';

function run(overrides: {
  id: string;
  lineageId?: string | null;
  returnStateHash?: string | null;
  imported?: boolean;
}): JobItem {
  const imported = overrides.imported ?? true;
  return {
    id: overrides.id,
    config_summary: { geometry_type: imported ? 'imported' : 'parametric' },
    cad_source: imported ? {
      ingest_id: `wgi_${overrides.id}`,
      lineage_id: overrides.lineageId ?? null,
      return_state_hash: overrides.returnStateHash ?? null,
    } : null,
  } as unknown as JobItem;
}

function document(hash: string, capturedAt: string): CadProjectDocument {
  return {
    returnStateHash: hash,
    documentName: 'Tritonia',
    ingestId: null,
    returnId: null,
    capturedAt,
    filename: `${hash}.f3d`,
    bytes: 12,
  };
}

describe('which project a run belongs to', () => {
  it('is the lineage, and only for imported runs', () => {
    expect(runProjectLineage(run({ id: '1', lineageId: 'wgl_a' }))).toBe('wgl_a');
    expect(runProjectLineage(run({ id: '2', lineageId: 'wgl_a', imported: false }))).toBeNull();
  });

  it('claims no project for a run that carries no lineage', () => {
    // Guessing from a name would file someone else's run into this project.
    expect(runProjectLineage(run({ id: '3', lineageId: null }))).toBeNull();
  });

  it('selects only this project’s runs', () => {
    const jobs = [
      run({ id: 'a', lineageId: 'wgl_a' }),
      run({ id: 'b', lineageId: 'wgl_b' }),
      run({ id: 'c', lineageId: 'wgl_a' }),
      run({ id: 'd', lineageId: 'wgl_a', imported: false }),
    ];
    expect(runsForProject(jobs, 'wgl_a').map((job) => job.id)).toEqual(['a', 'c']);
    expect(runsForProject(jobs, null)).toEqual([]);
  });
});

describe('splitting a project’s runs where the model changed', () => {
  it('keeps consecutive runs of one model together', () => {
    const jobs = [
      run({ id: 'new1', lineageId: 'wgl_a', returnStateHash: 'sha256:b' }),
      run({ id: 'new2', lineageId: 'wgl_a', returnStateHash: 'sha256:b' }),
      run({ id: 'old1', lineageId: 'wgl_a', returnStateHash: 'sha256:a' }),
    ];

    const groups = groupRunsByModelState(jobs, [
      document('sha256:b', '2026-08-21T09:00:00Z'),
      document('sha256:a', '2026-08-20T09:00:00Z'),
    ]);

    expect(groups.map((group) => group.runs.map((job) => job.id))).toEqual([
      ['new1', 'new2'],
      ['old1'],
    ]);
    expect(groups[0].document?.capturedAt).toBe('2026-08-21T09:00:00Z');
  });

  it('starts a new group when the model changes back and forth', () => {
    // Returning to an earlier geometry is a real thing to do, and it is a new
    // stretch of runs even though the model is one already seen.
    const jobs = [
      run({ id: '3', lineageId: 'wgl_a', returnStateHash: 'sha256:a' }),
      run({ id: '2', lineageId: 'wgl_a', returnStateHash: 'sha256:b' }),
      run({ id: '1', lineageId: 'wgl_a', returnStateHash: 'sha256:a' }),
    ];

    const groups = groupRunsByModelState(jobs, [document('sha256:a', '2026-08-20T09:00:00Z')]);

    expect(groups.map((group) => group.returnStateHash)).toEqual([
      'sha256:a', 'sha256:b', 'sha256:a',
    ]);
  });

  it('groups runs whose model was never captured, without a document', () => {
    const jobs = [run({ id: '1', lineageId: 'wgl_a', returnStateHash: null })];

    const [group] = groupRunsByModelState(jobs, []);

    expect(group.returnStateHash).toBeNull();
    expect(group.document).toBeNull();
  });

  it('reports no document when the model state has no archived file', () => {
    const jobs = [run({ id: '1', lineageId: 'wgl_a', returnStateHash: 'sha256:gone' })];

    const [group] = groupRunsByModelState(jobs, [document('sha256:a', '2026-08-20T09:00:00Z')]);

    expect(group.returnStateHash).toBe('sha256:gone');
    expect(group.document).toBeNull();
  });

  it('reads a run’s model state from its CAD provenance', () => {
    expect(runReturnStateHash(run({ id: '1', returnStateHash: 'sha256:a' }))).toBe('sha256:a');
    expect(runReturnStateHash(run({ id: '2', imported: false }))).toBeNull();
  });
});
