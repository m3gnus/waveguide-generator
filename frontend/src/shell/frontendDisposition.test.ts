import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { preferencesStore } from '../prefs/preferences';
import { useDocumentStore } from '../stores/document';
import { designForFamily, resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import type { JobResults } from '../api/results';
import { splSubtitle } from './ResultsPanel';
import { documentLabel, engineStatusLabel, solveSummary } from './StatusBar';
import { canLoadDesign, meshWarnings, selectJob } from './JobsPanel';

function selectableJob(id: string, label: string, scriptSnapshot: JobItem['script_snapshot']): JobItem {
  return {
    id, run_number: 1, parent_job_id: null,
    label, script_snapshot: scriptSnapshot, status: 'complete', progress: 1,
    stage: null, stage_message: null, created_at: '2026-08-08T00:00:00Z',
    queued_at: '2026-08-08T00:00:00Z', started_at: null,
    completed_at: '2026-08-08T00:00:01Z', config_summary: {}, solve_options: {} as JobItem['solve_options'], has_results: true,
    has_mesh_artifact: false, error_message: null, cancellation_requested: false,
    mesh_stats: null, design_revision: 1, polar_grid: {}, rating: null,
    exported_files: [], auto_export_completed_at: null, auto_export_formats: {},
    raw_results_file: null, mesh_artifact_file: null, log_tail: [],
  };
}

describe('frontend result and status labels', () => {
  beforeEach(() => {
    localStorage.clear();
    resetDesignStore();
    preferencesStore.resetForTests();
  });
  afterEach(() => vi.restoreAllMocks());
  it('labels SPL as absolute at the effective observation distance', () => {
    const result: JobResults = {
      frequencies: [], metadata: { observation: { requested_distance_m: 2, effective_distance_m: 1.75 } },
    };
    expect(splSubtitle(result)).toBe('absolute · 1.75 m');
    expect(splSubtitle({ frequencies: [] })).toBe('absolute · distance unspecified');
  });

  it('derives the solve range and count from the design', () => {
    const design = designForFamily('OSSE');
    design.simulation = { ...design.simulation, f1: 250, f2: 18_000, num_frequencies: 81 };
    expect(solveSummary(design)).toBe('next solve 250 Hz – 18 kHz · 81 f · smoothing none');
    expect(solveSummary(design, { frequencyMode: 'list', frequencyListText: '500, 1000 4000', smoothing: '1/6' }))
      .toBe('next solve 500 Hz – 4 kHz · 3 f · smoothing 1/6');
    expect(solveSummary(design, { frequencyMode: 'list', frequencyListText: 'invalid' }))
      .toBe('invalid frequency list · smoothing none');
  });

  it('labels the selected engine instead of the first available capability', () => {
    const engines = [
      { name: 'metal', available: true, reason: null, version: '1.0', fast_paths: [] },
      { name: 'bempp', available: true, reason: null, version: '2.0', fast_paths: [] },
    ];
    const selection = {
      default: 'auto', resolvedDefault: 'metal', full3dOrder: ['metal', 'bempp'], axisymmetricRunner: 'axisym',
    };
    expect(engineStatusLabel(engines, selection, 'bempp', 'auto')).toBe('BEMPP · 2.0');
    expect(engineStatusLabel(engines, selection, 'auto', 'auto')).toBe('METAL · 1.0');
  });

  it('shows the tracked design filename instead of a path placeholder', () => {
    expect(documentLabel('loaded-design.cfg')).toBe('loaded-design.cfg');
    expect(documentLabel('   ')).toBe('untitled design');
  });

  it('enables Load design only for jobs carrying a design snapshot', () => {
    expect(canLoadDesign({ script_snapshot: null })).toBe(false);
    expect(canLoadDesign({ script_snapshot: { formula: 'OSSE' } })).toBe(true);
  });

  it('surfaces advisory mesh warnings on the completed run card', () => {
    expect(meshWarnings({ mesh_stats: {
      triangle_count: 7_173,
      warnings: ['Large solve mesh: 7,173 triangles; the solve will continue.'],
    } })).toEqual(['Large solve mesh: 7,173 triangles; the solve will continue.']);
    expect(meshWarnings({ mesh_stats: { warnings: 'not an array' } })).toEqual([]);
  });

  it('loads a selected run design without renaming the design', () => {
    useDocumentStore.getState().setDesignName('keep-me');
    const solved = designForFamily('OSSE');
    solved.L = 321;
    const selected = selectableJob(
      'selected',
      '260807_horn_v13',
      { version: 1, design: serializeDesign(solved) },
    );
    vi.spyOn(jobsSocket, 'getSnapshot').mockReturnValue({
      connection: 'connected', epoch: 1, cursor: 1, error: null,
      jobs: [selected, selectableJob('newer', '260808_horn_v14', null)],
    } as JobsSnapshot);

    selectJob(selected);

    expect(useDesignStore.getState().design.L).toBe(321);
    expect(useDocumentStore.getState().designName).toBe('keep-me');
  });

  it('leaves the design name unchanged when a selected snapshot is unreadable', () => {
    useDocumentStore.getState().setDesignName('keep-me');
    const unreadable = selectableJob(
      'unreadable',
      '260808_other_v99',
      { version: 1, design: { formula: 'not-a-family' } },
    );
    const snapshotSpy = vi.spyOn(jobsSocket, 'getSnapshot');

    selectJob(unreadable);

    expect(snapshotSpy).not.toHaveBeenCalled();
    expect(useDocumentStore.getState().designName).toBe('keep-me');
  });
});
