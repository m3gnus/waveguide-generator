import { describe, expect, it, vi } from 'vitest';
import {
  listCadProjects,
  placeRunCadDocument,
  runProjectLineage,
  runReturnStateHash,
  runsForProject,
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

describe('listing CAD projects', () => {
  it('reads the server canonical-head contract including its archive stem', async () => {
    const serverRow = {
      designId: 'wgd_newest',
      lineageId: 'wgl_project',
      editVersion: 2,
      designHash: 'sha256:newest',
      filename: 'newest.cfg',
      archiveStem: 'stable-project-folder',
      branchedFromDesignId: 'wgd_older',
      branchedFromEditVersion: 1,
      exportCount: 0,
      lastExportedAt: null,
      createdAt: '2026-08-20T10:00:00Z',
      updatedAt: '2026-08-22T10:00:00Z',
    };
    const fetcher = async () => new Response(
      JSON.stringify({ items: [serverRow] }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );

    const projects = await listCadProjects(fetcher as typeof fetch);

    expect(projects).toEqual([{
      designId: 'wgd_newest',
      lineageId: 'wgl_project',
      filename: 'newest.cfg',
      documentName: null,
      archiveStem: 'stable-project-folder',
      exportCount: 0,
      createdAt: '2026-08-20T10:00:00Z',
      updatedAt: '2026-08-22T10:00:00Z',
    }]);
  });
});

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

describe('a run’s model provenance', () => {
  it('reads a run’s model state from its CAD provenance', () => {
    expect(runReturnStateHash(run({ id: '1', returnStateHash: 'sha256:a' }))).toBe('sha256:a');
    expect(runReturnStateHash(run({ id: '2', imported: false }))).toBeNull();
  });
});

describe('filing a run’s CAD document', () => {
  function cadRun(archiveStem: string | null, returnStateHash: string | null): JobItem {
    return {
      id: 'run-1',
      config_summary: { geometry_type: 'imported' },
      cad_source: {
        ingest_id: 'wgi_1',
        lineage_id: 'wgl_a',
        archive_stem: archiveStem,
        return_state_hash: returnStateHash,
      },
    } as unknown as JobItem;
  }

  it('reports the copy the server did not make, instead of discarding the answer', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
      placed: false,
      retryable: false,
      reason: 'A different file already occupies the run’s CAD document name.',
    }), { status: 200 }));

    const outcome = await placeRunCadDocument(
      cadRun('Tritonia', 'sha256:aaa'), 'Tritonia/14_Tritonia', '14_Tritonia', fetcher, async () => {},
    );

    expect(outcome.requested).toBe(true);
    expect(outcome.placed).toBe(false);
    expect(outcome.reason).toContain('already occupies');
    // Not retryable: asking again would only repeat the refusal.
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('asks again for a copy the server says may still be coming', async () => {
    const answers = [
      { placed: false, retryable: true, reason: 'not in the project archive' },
      { placed: true, relativePath: '14_Tritonia/14_Tritonia.f3d' },
    ];
    const fetcher = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify(answers.shift()), { status: 200 }),
    );

    const outcome = await placeRunCadDocument(
      cadRun('Tritonia', 'sha256:aaa'), 'Tritonia/14_Tritonia', '14_Tritonia', fetcher, async () => {},
    );

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(outcome).toEqual({
      placed: true, relativePath: '14_Tritonia/14_Tritonia.f3d', requested: true,
    });
  });

  it('gives up after a bounded number of attempts', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
      placed: false, retryable: true, reason: 'not in the project archive',
    }), { status: 200 }));

    const outcome = await placeRunCadDocument(
      cadRun('Tritonia', 'sha256:aaa'), 'Tritonia/14_Tritonia', '14_Tritonia', fetcher, async () => {},
    );

    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(outcome.placed).toBe(false);
    expect(outcome.requested).toBe(true);
  });

  it('asks for nothing when the run has no CAD model state', async () => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response('{}', { status: 200 }));

    const outcome = await placeRunCadDocument(
      cadRun('Tritonia', null), 'Tritonia/14_Tritonia', '14_Tritonia', fetcher, async () => {},
    );

    expect(fetcher).not.toHaveBeenCalled();
    expect(outcome).toEqual({ placed: false, requested: false });
  });
});
