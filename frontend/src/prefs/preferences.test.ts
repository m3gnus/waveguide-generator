import { beforeEach, describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { applyJobPreferences, CHART_TYPES, EXPORT_FORMATS, exportBaseName, MAP_REFERENCES, preferencesStore } from './preferences';

function job(id: string, rating: number | null, created: string, completed = created): JobItem {
  return { id, rating, created_at: created, completed_at: completed, label: id, status: 'complete', progress: 1, stage: null, stage_message: null, queued_at: created, started_at: created, config_summary: {}, has_results: true, has_mesh_artifact: false, error_message: null, cancellation_requested: false, mesh_stats: null, script_snapshot: null, exported_files: [], auto_export_completed_at: null, raw_results_file: null, mesh_artifact_file: null, log_tail: [] };
}

describe('client preferences', () => {
  beforeEach(() => { localStorage.clear(); preferencesStore.resetForTests(); });
  it('persists the eleven-format selection and clamps naming state', () => {
    expect(EXPORT_FORMATS).toHaveLength(11);
    expect(CHART_TYPES).toHaveLength(10);
    expect(MAP_REFERENCES).toEqual([-3, -6, -9, -12]);
    preferencesStore.toggleFormat('csv');
    preferencesStore.toggleFormat('stl');
    preferencesStore.update({ outputName: ' horn / alpha ', counter: 2_000_000 });
    expect(preferencesStore.getSnapshot().exportFormats).toEqual(['csv', 'stl']);
    expect(exportBaseName(preferencesStore.getSnapshot())).toBe('horn_alpha_999999');
    expect(JSON.parse(localStorage.getItem('waveguide-v2-g3-preferences') ?? '{}').version).toBe(1);
  });
  it('filters by minimum rating and applies each persisted sort', () => {
    const jobs = [job('beta', 2, '2026-01-01T00:00:00Z'), job('alpha', 5, '2026-01-02T00:00:00Z')];
    expect(applyJobPreferences(jobs, 'rating_desc', 3).map(({ id }) => id)).toEqual(['alpha']);
    expect(applyJobPreferences(jobs, 'name_asc', 0).map(({ id }) => id)).toEqual(['alpha', 'beta']);
  });
});
