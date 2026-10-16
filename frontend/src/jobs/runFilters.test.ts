import { describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { runModelKey, showProjectRuns, useRunFilters } from './runFilters';

const run = (id: string, lineage: string | null, hash: string | null): JobItem => ({ id,
  config_summary: { geometry_type: 'imported' }, cad_source: { lineage_id: lineage,
    return_state_hash: hash, ingest_id: `wgi_${id}` } } as unknown as JobItem);

describe('shared Runs identity', () => {
  it('groups actual CAD snapshots, across preparations, without joining unrelated projects', () => {
    expect(runModelKey(run('first', 'project-a', 'sha256:same'))).toBe(runModelKey(run('second', 'project-a', 'sha256:same')));
    expect(runModelKey(run('first', 'project-a', 'sha256:same'))).not.toBe(runModelKey(run('second', 'project-b', 'sha256:same')));
  });
  it('keeps unknown model states and same-named file imports separate', () => {
    expect(runModelKey(run('first', 'project-a', null))).not.toBe(runModelKey(run('second', 'project-a', null)));
    const first = { ...run('first', null, null), label: 'speaker', config_summary: { geometry_type: 'imported', import_origin: 'mesh-file' } };
    expect(runModelKey(first)).not.toBe(runModelKey({ ...first, cad_source: { ...first.cad_source!, ingest_id: 'wgi_other' } }));
  });
  it('opens a project by stable identity and clears stale source, model and text filters', () => {
    useRunFilters.setState({ query: 'old', source: 'mesh-file', project: 'old', model: 'old' });
    showProjectRuns('stable-lineage');
    expect(useRunFilters.getState()).toEqual({ query: '', source: 'all', project: 'stable-lineage', model: '' });
  });
});
