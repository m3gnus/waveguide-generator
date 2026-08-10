import { beforeEach, describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { applyJobPreferences, CHART_TYPES, EXPORT_FORMATS, exportBaseName, jobBaseName, loadPreferences, MAP_REFERENCES, nextFileJobNaming, nextJobNaming, nextVersionFor, parseJobName, preferencesStore, readPreferences, runDisplayName, STORAGE_VERSION } from './preferences';

function job(id: string, rating: number | null, created: string, completed = created): JobItem {
  return { id, run_number: 1, parent_job_id: null, rating, created_at: created, completed_at: completed, label: id, status: 'complete', progress: 1, stage: null, stage_message: null, queued_at: created, started_at: created, config_summary: {}, solve_options: {} as JobItem['solve_options'], has_results: true, has_mesh_artifact: false, error_message: null, cancellation_requested: false, mesh_stats: null, script_snapshot: null, design_revision: 0, polar_grid: {}, exported_files: [], auto_export_completed_at: null, auto_export_formats: {}, raw_results_file: null, mesh_artifact_file: null, log_tail: [] };
}

describe('client preferences', () => {
  beforeEach(() => { localStorage.clear(); preferencesStore.resetForTests(); });
  it('persists the eleven-format selection and clamps naming state', () => {
    expect(EXPORT_FORMATS).toHaveLength(11);
    expect(CHART_TYPES).toHaveLength(10);
    expect(MAP_REFERENCES).toEqual([-3, -6, -9, -12]);
    preferencesStore.update({ exportFormats: [] });
    preferencesStore.toggleFormat('csv');
    preferencesStore.toggleFormat('stl');
    preferencesStore.update({ outputName: ' horn / alpha ', counter: 2_000_000 });
    expect(preferencesStore.getSnapshot().exportFormats).toEqual(['csv', 'stl']);
    expect(exportBaseName(preferencesStore.getSnapshot())).toBe('horn_alpha_999999');
    expect(JSON.parse(localStorage.getItem('waveguide-v2-g3-preferences') ?? '{}').version).toBe(STORAGE_VERSION);
  });
  it('uses useful manual defaults without opting into automatic file writing', () => {
    expect(loadPreferences(null)).toMatchObject({
      exportFormats: ['csv', 'png'],
      autoExportFormats: [],
      autoExportOnComplete: false,
    });
  });
  it('copies a pre-split selection to both lists when auto-export was enabled', () => {
    const stored = JSON.stringify({ version: 5, preferences: {
      exportFormats: ['csv', 'json'], autoExportOnComplete: true,
    } });
    expect(loadPreferences(stored)).toMatchObject({
      exportFormats: ['csv', 'json'],
      autoExportFormats: ['csv', 'json'],
    });
  });
  it('keeps a pre-split selection manual-only when auto-export was disabled', () => {
    const stored = JSON.stringify({ version: 5, preferences: {
      exportFormats: ['json'], autoExportOnComplete: false,
    } });
    expect(loadPreferences(stored)).toMatchObject({
      exportFormats: ['json'],
      autoExportFormats: [],
    });
  });
  it('preserves an explicitly empty pre-split selection instead of applying the new default', () => {
    const stored = JSON.stringify({ version: 5, preferences: {
      exportFormats: [], autoExportOnComplete: true,
    } });
    expect(loadPreferences(stored)).toMatchObject({ exportFormats: [], autoExportFormats: [] });
  });
  it('applies the new manual default only when the pre-split key was absent', () => {
    const stored = JSON.stringify({ version: 5, preferences: { autoExportOnComplete: false } });
    expect(loadPreferences(stored)).toMatchObject({ exportFormats: ['csv', 'png'], autoExportFormats: [] });
  });
  it('makes the split-format migration idempotent after the v6 rewrite', () => {
    const v5 = JSON.stringify({ version: 5, preferences: {
      exportFormats: ['csv', 'json'], autoExportOnComplete: true,
    } });
    const first = readPreferences(v5);
    const second = readPreferences(JSON.stringify({ version: STORAGE_VERSION, preferences: first.value }));
    expect(first.migrated).toBe(true);
    expect(second.migrated).toBe(false);
    expect(second.value).toEqual(first.value);
  });
  it('falls back safely for corrupt stored data', () => {
    expect(() => loadPreferences('{not json')).not.toThrow();
    expect(loadPreferences('{not json')).toMatchObject({ exportFormats: ['csv', 'png'], autoExportFormats: [] });
    expect(() => loadPreferences(JSON.stringify({ version: STORAGE_VERSION, preferences: null }))).not.toThrow();
  });
  it('builds a friendly versioned job name with an optional local-date prefix', () => {
    const now = new Date(2026, 7, 5, 12, 0, 0);
    expect(jobBaseName({ outputName: ' Tritonia mk2 ', jobVersion: 7, datePrefix: false }, now)).toBe('Tritonia_mk2_v07');
    // YYMMDD, so sorting the stored labels by name sorts them by date too.
    expect(jobBaseName({ outputName: 'Tritonia', jobVersion: 103, datePrefix: true }, now)).toBe('260805_Tritonia_v103');
  });
  it('reads a stored run label back into the name and number that made it', () => {
    expect(parseJobName('260808_horn_v14')).toEqual({ name: 'horn', version: 14 });
    expect(parseJobName('horn_v09')).toEqual({ name: 'horn', version: 9 });
    // The prefix format this app used before YYMMDD still parses.
    expect(parseJobName('2026-08-05_Tritonia_mk2_v103')).toEqual({ name: 'Tritonia_mk2', version: 103 });
    // Not a versioned name: the fallback a run with no label displays under.
    expect(parseJobName('osse_1a2b3c4d')).toBeNull();
    expect(parseJobName(null)).toBeNull();
  });
  it('numbers a reopened config past every run already stored under its name', () => {
    const stored = ['260808_horn_v14', '260808_horn_v09', '260807_horn_v13', '260808_tritonia_v03', null];
    expect(nextJobNaming('260807_horn_v13', stored)).toEqual({ outputName: 'horn', jobVersion: 15 });
    expect(nextJobNaming('260808_tritonia_v03', stored)).toEqual({ outputName: 'tritonia', jobVersion: 4 });
    // A name nothing has been solved under yet starts at 1.
    expect(nextVersionFor('brand_new', stored)).toBe(1);
    // Nothing to inherit from an unversioned label.
    expect(nextJobNaming('osse_1a2b3c4d', stored)).toBeNull();
  });
  it('derives standalone file names and versions from old config filenames', () => {
    const stored = ['260808_horn_v14', 'horn_v09', '260808_other_v30', null];
    expect(nextFileJobNaming('260701_horn_v13.cfg', stored)).toEqual({ outputName: 'horn', jobVersion: 15 });
    expect(nextFileJobNaming('2026-07-01_horn_v20.mwg', stored)).toEqual({ outputName: 'horn', jobVersion: 21 });
    expect(nextFileJobNaming('/archive/My favorite horn.txt', stored)).toEqual({ outputName: 'My_favorite_horn', jobVersion: 1 });
    expect(nextFileJobNaming('horn.cfg', stored)).toEqual({ outputName: 'horn', jobVersion: 15 });
  });
  it('preserves an explicitly disabled date prefix during v4 migration', () => {
    const stored = JSON.stringify({ version: 4, preferences: { outputName: 'tritonia', datePrefix: false } });
    const migrated = loadPreferences(stored);
    expect(migrated.datePrefix).toBe(false);
    expect(migrated.outputName).toBe('tritonia');
  });
  it('uses the enabled default when an old profile never stored datePrefix', () => {
    const stored = JSON.stringify({ version: 4, preferences: { outputName: 'tritonia' } });
    expect(loadPreferences(stored).datePrefix).toBe(true);
  });
  it('runs every migration step from the stored version onwards', () => {
    // A v3 profile used to stop after v3->v4 and never reach v4->v5.
    const stored = JSON.stringify({ version: 3, preferences: { outputName: 'tritonia', datePrefix: false } });
    expect(loadPreferences(stored).datePrefix).toBe(false);
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
    expect(second.value.exportFormats).toEqual(first.value.exportFormats);
    expect(second.value.autoExportFormats).toEqual(first.value.autoExportFormats);
  });
  it('filters by minimum rating and applies each persisted sort', () => {
    const jobs = [job('beta', 2, '2026-01-01T00:00:00Z'), job('alpha', 5, '2026-01-02T00:00:00Z')];
    expect(applyJobPreferences(jobs, 'rating_desc', 3).map(({ id }) => id)).toEqual(['alpha']);
    expect(applyJobPreferences(jobs, 'name_asc', 0).map(({ id }) => id)).toEqual(['alpha', 'beta']);
  });
  it('sorts displayed names case-insensitively and naturally with run-number ties', () => {
    const jobs = [
      { ...job('a', 0, '2026-01-01T00:00:00Z'), run_number: 12, label: 'run10' },
      { ...job('b', 0, '2026-01-01T00:00:00Z'), run_number: 9, label: 'RUN9' },
      { ...job('c', 0, '2026-01-01T00:00:00Z'), run_number: 4, label: 'same' },
      { ...job('d', 0, '2026-01-01T00:00:00Z'), run_number: 2, label: 'Same' },
    ];
    expect(applyJobPreferences(jobs, 'name_asc', 0).map(({ id }) => id)).toEqual(['b', 'a', 'd', 'c']);
  });
  it('uses the same untitled fallback in full and short run identities', () => {
    const untitled = { ...job('1a2b3c4d', 0, '2026-01-01T00:00:00Z'), run_number: 123, label: null };
    expect(runDisplayName(untitled)).toBe('#123 · osse-1a2b3c');
    expect(runDisplayName(untitled, 'short')).toBe('osse-1a2b3c');
  });
});
