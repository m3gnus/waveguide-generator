import { beforeEach, describe, expect, it } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { applyJobPreferences, CHART_TYPES, EXPORT_FORMATS, loadPreferences, MAP_REFERENCES, preferencesStore, readPreferences, runDisplayName, STORAGE_VERSION } from './preferences';

function job(id: string, rating: number | null, created: string, completed = created): JobItem {
  return { id, run_number: 1, parent_job_id: null, rating, created_at: created, completed_at: completed, label: id, status: 'complete', progress: 1, stage: null, stage_message: null, queued_at: created, started_at: created, config_summary: {}, solve_options: {} as JobItem['solve_options'], has_results: true, has_mesh_artifact: false, error_message: null, cancellation_requested: false, mesh_stats: null, script_snapshot: null, design_revision: 0, polar_grid: {}, exported_files: [], auto_export_completed_at: null, auto_export_formats: {}, raw_results_file: null, mesh_artifact_file: null, log_tail: [] };
}

describe('client preferences', () => {
  beforeEach(() => { localStorage.clear(); preferencesStore.resetForTests(); });
  it('persists the complete format selection and clamps the run sequence', () => {
    expect(EXPORT_FORMATS).toHaveLength(20);
    expect(CHART_TYPES).toHaveLength(18);
    expect(MAP_REFERENCES).toEqual([-3, -6, -9, -12]);
    preferencesStore.update({ exportFormats: [] });
    preferencesStore.toggleFormat('csv');
    preferencesStore.toggleFormat('stl');
    preferencesStore.update({ runSequenceName: ' Tritonia-M ', runSequenceNext: 2_000_000 });
    expect(preferencesStore.getSnapshot().exportFormats).toEqual(['csv', 'stl']);
    expect(preferencesStore.getSnapshot()).toMatchObject({ runSequenceName: 'Tritonia-M', runSequenceNext: 999_999 });
    expect(JSON.parse(localStorage.getItem('waveguide-v2-g3-preferences') ?? '{}').version).toBe(STORAGE_VERSION);
  });
  it('uses useful manual defaults without opting into automatic file writing', () => {
    expect(loadPreferences(null)).toMatchObject({
      exportFormats: ['csv', 'png'],
      autoExportFormats: [],
      autoExportOnComplete: false,
      runNameDatePosition: 'off',
      runNameDateFormat: 'yymmdd',
      runNameNumberPosition: 'suffix',
      runNameNumberFormat: 'natural',
      directivityGuideInterval: 0,
    });
  });
  it('keeps the reverse-null overlay off until it is asked for', () => {
    expect(loadPreferences(null).showReverseNull).toBe(false);
    // Unlike the members overlay, absence means off: it is a diagnostic, so a
    // profile that never mentioned it must not acquire a fourth trace.
    expect(loadPreferences(JSON.stringify({ version: STORAGE_VERSION, preferences: {} })).showReverseNull).toBe(false);
    preferencesStore.update({ showReverseNull: true });
    expect(loadPreferences(localStorage.getItem('waveguide-v2-g3-preferences')).showReverseNull).toBe(true);
  });
  it('reads group delay in milliseconds until cycles is asked for', () => {
    expect(loadPreferences(null).groupDelayUnit).toBe('ms');
    expect(loadPreferences(JSON.stringify({ version: STORAGE_VERSION, preferences: {} })).groupDelayUnit).toBe('ms');
    preferencesStore.update({ groupDelayUnit: 'cycles' });
    expect(loadPreferences(localStorage.getItem('waveguide-v2-g3-preferences')).groupDelayUnit).toBe('cycles');
    // A hand-edited profile cannot invent a third unit for the axis to name.
    expect(loadPreferences(JSON.stringify({ version: STORAGE_VERSION, preferences: { groupDelayUnit: 'seconds' } })).groupDelayUnit).toBe('ms');
  });

  it('persists and bounds the directivity angular guide interval', () => {
    preferencesStore.update({ directivityGuideInterval: 15 });
    expect(loadPreferences(localStorage.getItem('waveguide-v2-g3-preferences')).directivityGuideInterval).toBe(15);
    // 0 is a real setting now -- the graticule off -- not a value to clamp up.
    expect(loadPreferences(JSON.stringify({ version: STORAGE_VERSION, preferences: { directivityGuideInterval: 0 } })).directivityGuideInterval).toBe(0);
    expect(loadPreferences(JSON.stringify({ version: STORAGE_VERSION, preferences: { directivityGuideInterval: -5 } })).directivityGuideInterval).toBe(0);
    expect(loadPreferences(JSON.stringify({ version: STORAGE_VERSION, preferences: { directivityGuideInterval: 999 } })).directivityGuideInterval).toBe(180);
  });
  /**
   * The durable copy is a JSON file the user can open, so every field has to
   * survive being the wrong type. `chartTheme` used to be coerced with
   * `String`, which handed the exporter the literal "[object Object]" as a
   * theme name rather than falling back to the interface.
   */
  it('takes nothing on trust from a hand-edited payload', () => {
    const loaded = loadPreferences(JSON.stringify({ version: STORAGE_VERSION, preferences: {
      chartTheme: { name: 'console' },
      chartTypes: 'frequency_response',
      exportFormats: ['csv', 'not_a_format'],
      smoothing: '1/9',
      mapReference: -5,
      runSequenceNext: -3,
      minRating: 99,
      jobSort: 'oldest',
      cadApplication: 'solidworks',
      directivityGuideInterval: 'wide',
    } }));
    expect(loaded).toMatchObject({
      chartTheme: 'auto',
      exportFormats: ['csv'],
      smoothing: 'none',
      mapReference: -6,
      runSequenceNext: 1,
      minRating: 5,
      jobSort: 'completed_desc',
      cadApplication: 'fusion360',
      directivityGuideInterval: 0,
    });
    // A non-array chart list is unusable, so the shipped six panels stand.
    expect(loaded.chartTypes).toHaveLength(6);
  });
  it('migrates v7 naming preferences to opt-in date decoration', () => {
    const migrated = readPreferences(JSON.stringify({ version: 7, preferences: {
      outputName: 'horn12',
      nameSourceProjection: null,
    } }));
    expect(migrated.migrated).toBe(true);
    expect(migrated.value).toMatchObject({
      runNameDatePosition: 'off',
      runNameDateFormat: 'yymmdd',
      runNameNumberPosition: 'suffix',
      runNameNumberFormat: 'natural',
    });
  });
  it('drops the standalone run name, its projection, and the dead counter', () => {
    const migrated = readPreferences(JSON.stringify({ version: 12, preferences: {
      outputName: 'horn12', nameSourceProjection: { version: 1 }, counter: 7, minRating: 3,
    } }));
    expect(migrated.migrated).toBe(true);
    expect(migrated.value).not.toHaveProperty('outputName');
    expect(migrated.value).not.toHaveProperty('nameSourceProjection');
    expect(migrated.value).not.toHaveProperty('counter');
    // Unrelated settings are carried across untouched.
    expect(migrated.value.minRating).toBe(3);
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
  it('migrates old version/date naming state without keeping the retired fields', () => {
    const stored = JSON.stringify({ version: 4, preferences: {
      outputName: 'tritonia', jobVersion: 12, datePrefix: false,
    } });
    const migrated = loadPreferences(stored);
    expect(migrated).not.toHaveProperty('outputName');
    expect(migrated).not.toHaveProperty('nameSourceProjection');
    expect(migrated).not.toHaveProperty('jobVersion');
    expect(migrated).not.toHaveProperty('datePrefix');
  });
  it('resets only the panel selection when migrating a v1 layout', () => {
    const stored = JSON.stringify({ version: 1, preferences: {
      chartTypes: ['frequency_response', 'directivity_map_h', 'balloon', 'beam_map', 'impedance', 'summary'],
      outputName: 'tritonia', exportFormats: ['csv'], mapReference: -9,
    } });
    const migrated = loadPreferences(stored);
    expect(migrated.chartTypes).not.toContain('balloon');
    expect(migrated.chartTypes).not.toContain('beam_map');
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
    expect(migrated.value).toMatchObject({ exportFormats: ['csv', 'stl'], jobSort: 'name_asc', minRating: 4, mapReference: -12 });
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
  it('keeps active runs visible through rating filters and uses descending run ties', () => {
    const timestamp = '2026-01-01T00:00:00Z';
    const jobs = [
      { ...job('old', 0, timestamp), run_number: 1 },
      { ...job('new', 0, timestamp), run_number: 2 },
      { ...job('active', 0, timestamp), run_number: 3, status: 'running' as const },
    ];
    expect(applyJobPreferences(jobs, 'created_desc', 5).map(({ id }) => id)).toEqual(['active']);
    expect(applyJobPreferences(jobs.slice(0, 2), 'created_desc', 0).map(({ id }) => id)).toEqual(['new', 'old']);
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

describe('angular graticule retirement', () => {
  it('turns off a profile that still holds the old shipped interval', () => {
    const stored = JSON.stringify({ version: 13, preferences: { directivityGuideInterval: 10 } });
    expect(readPreferences(stored).value.directivityGuideInterval).toBe(0);
    expect(readPreferences(stored).migrated).toBe(true);
  });

  it('leaves a deliberately chosen interval alone', () => {
    const stored = JSON.stringify({ version: 13, preferences: { directivityGuideInterval: 15 } });
    expect(readPreferences(stored).value.directivityGuideInterval).toBe(15);
    // Already off stays off, and a profile written at the current version is
    // never migrated at all.
    expect(readPreferences(JSON.stringify({ version: 13, preferences: { directivityGuideInterval: 0 } })).value.directivityGuideInterval).toBe(0);
    expect(readPreferences(JSON.stringify({ version: STORAGE_VERSION, preferences: { directivityGuideInterval: 10 } })).value.directivityGuideInterval).toBe(10);
  });
});
