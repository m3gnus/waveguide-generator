import { beforeEach, describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { applyJobPreferences, CHART_TYPES, EXPORT_FORMATS, exportBaseName, jobBaseName, loadPreferences, MAP_REFERENCES, preferencesStore, readPreferences, STORAGE_VERSION } from './preferences';

function job(id: string, rating: number | null, created: string, completed = created): JobItem {
  return { id, rating, created_at: created, completed_at: completed, label: id, status: 'complete', progress: 1, stage: null, stage_message: null, queued_at: created, started_at: created, config_summary: {}, has_results: true, has_mesh_artifact: false, error_message: null, cancellation_requested: false, mesh_stats: null, script_snapshot: null, design_revision: 0, polar_grid: {}, exported_files: [], auto_export_completed_at: null, auto_export_formats: {}, raw_results_file: null, mesh_artifact_file: null, log_tail: [] };
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
    expect(JSON.parse(localStorage.getItem('waveguide-v2-g3-preferences') ?? '{}').version).toBe(STORAGE_VERSION);
  });
  it('builds a friendly versioned job name with an optional local-date prefix', () => {
    const now = new Date(2026, 7, 5, 12, 0, 0);
    expect(jobBaseName({ outputName: ' Tritonia mk2 ', jobVersion: 7, datePrefix: false }, now)).toBe('Tritonia_mk2_v07');
    expect(jobBaseName({ outputName: 'Tritonia', jobVersion: 103, datePrefix: true }, now)).toBe('2026-08-05_Tritonia_v103');
  });
  it('resets only the panel selection when migrating a v1 layout', () => {
    const stored = JSON.stringify({ version: 1, preferences: {
      chartTypes: ['frequency_response', 'directivity_map_h', 'balloon', 'beam_map', 'impedance', 'summary'],
      outputName: 'tritonia', exportFormats: ['csv'], mapReference: -9,
    } });
    const migrated = loadPreferences(stored);
    expect(migrated.chartTypes).not.toContain('balloon');
    expect(migrated.chartTypes).not.toContain('beam_map');
    expect(migrated.outputName).toBe('tritonia');
    expect(migrated.exportFormats).toEqual(['csv']);
    expect(migrated.mapReference).toBe(-9);
  });
  it('keeps a current-version layout exactly as stored', () => {
    const chartTypes = ['balloon', 'beam_map'];
    const stored = JSON.stringify({ version: STORAGE_VERSION, preferences: { chartTypes } });
    expect(loadPreferences(stored).chartTypes).toEqual(chartTypes);
    expect(readPreferences(stored).migrated).toBe(false);
  });
  it('migrates v2 chart choices to a variable list without resetting unrelated settings', () => {
    const chartTypes = ['directivity_map_h', 'summary', 'impedance'];
    const stored = JSON.stringify({ version: 2, preferences: {
      chartTypes,
      outputName: 'kept-name',
      exportFormats: ['csv', 'stl'],
      jobSort: 'name_asc',
      minRating: 4,
      mapReference: -12,
    } });
    const migrated = readPreferences(stored);
    expect(migrated.migrated).toBe(true);
    expect(migrated.value.chartTypes).toEqual(chartTypes);
    expect(migrated.value).toMatchObject({ outputName: 'kept-name', exportFormats: ['csv', 'stl'], jobSort: 'name_asc', minRating: 4, mapReference: -12 });
  });
  it('keeps an explicitly empty current chart list and can grow it again', () => {
    const stored = JSON.stringify({ version: STORAGE_VERSION, preferences: { chartTypes: [] } });
    expect(loadPreferences(stored).chartTypes).toEqual([]);
    preferencesStore.update({ chartTypes: [] });
    preferencesStore.addChart();
    expect(preferencesStore.getSnapshot().chartTypes).toHaveLength(1);
  });
  it('migrates once: a rewritten layout is not reset again on the next load', () => {
    const v1 = JSON.stringify({ version: 1, preferences: { chartTypes: ['balloon', 'beam_map', 'summary', 'impedance', 'frequency_response', 'directivity_map_v'] } });
    const first = readPreferences(v1);
    expect(first.migrated).toBe(true);
    const rewritten = JSON.stringify({ version: STORAGE_VERSION, preferences: first.value });
    const second = readPreferences(rewritten);
    expect(second.migrated).toBe(false);
    expect(second.value.chartTypes).toEqual(first.value.chartTypes);
  });
  it('filters by minimum rating and applies each persisted sort', () => {
    const jobs = [job('beta', 2, '2026-01-01T00:00:00Z'), job('alpha', 5, '2026-01-02T00:00:00Z')];
    expect(applyJobPreferences(jobs, 'rating_desc', 3).map(({ id }) => id)).toEqual(['alpha']);
    expect(applyJobPreferences(jobs, 'name_asc', 0).map(({ id }) => id)).toEqual(['alpha', 'beta']);
  });
});
