import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { compareSelection } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { resetCadReturnStore } from '../stores/cadReturn';
import { designForFamily, resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { resetDocumentStore } from '../stores/document';
import { openImported, resetImportedModelStore, unchangedSetup, useImportedModelStore } from '../stores/importedModel';
import { resetSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { deferred, job, json, mesh, record, workspaceFor } from '../shell/cadlink/selection.fixtures';
import { resetSolveAttentionForTests, solveAttention, useJobAttention } from '../shell/solveAttention';
import {
  bindWorkspaceNavigation, publishVisiblePanels, resetWorkspaceNavigationForTests,
  workspaceNavigation, type WorkspacePanel,
} from '../shell/workspaceNavigation';
import { importedMeshStore } from '../viewport/importedMeshStore';
import refusedJob from './cadRefusedJob.fixture.json';
import { selectJob } from './selectJob';
import { showJobModel } from './showJobModel';

const cadRun = (ingestId = record.ingest_id): JobItem => ({ ...job(ingestId), status: 'complete', has_results: true });
const parametricRun = (): JobItem => ({
  ...cadRun(), id: 'parametric-run', config_summary: {},
  script_snapshot: { version: 1, design: serializeDesign({ ...designForFamily('OSSE'), L: 321 }) },
});
const fileRun = (): JobItem => ({ ...cadRun(), config_summary: { geometry_type: 'imported', import_origin: 'mesh-file' } });
const fileWorkspace = () => {
  const workspace = workspaceFor();
  return { ...workspace, model: { model_id: 'mesh-file:fixture', producer: 'mesh-file', display_name: 'speaker.msh' },
    prepared: { ...workspace.prepared, model_id: 'mesh-file:fixture', producer: 'mesh-file', msh_text: mesh,
      source_file: { filename: 'speaker.msh', transform: { units: 'm', forward: '+z', up: '+y' }, source_tags: [1] },
      source_tags: { 'source-hf': 1 }, summary: { node_count: 3, triangle_count: 1, dimensions: [1, 1, 0], groups: [] } },
    original_available: true };
};

describe('history selection keeps the current panel visible', () => {
  let activations: WorkspacePanel[];
  let unbind: () => void;

  beforeEach(() => {
    localStorage.clear();
    resetDesignStore(); resetDocumentStore(); resetCadReturnStore(); resetSolveOptionsStore();
    preferencesStore.resetForTests(); importedMeshStore.clear();
    workspaceModeStore.setImportedInput('cad'); resetImportedModelStore();
    workspaceModeStore.setMode('parametric');
    resetWorkspaceNavigationForTests(); resetSolveAttentionForTests();
    compareSelection.clear();
    activations = [];
    unbind = bindWorkspaceNavigation((panel) => {
      activations.push(panel);
      publishVisiblePanels([panel]);
      return true;
    });
    publishVisiblePanels(['jobs']);
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/workspace')) return json(workspaceFor());
      if (path.endsWith('/viewport-mesh')) return new Response(mesh);
      if (path.startsWith('/api/cadlink/ingest/')) return json(record);
      if (path.includes('/setup')) return json({ setup: null });
      throw new Error(`Unexpected request: ${path}`);
    }));
  });

  afterEach(() => {
    unbind(); compareSelection.clear(); importedMeshStore.clear();
    vi.restoreAllMocks(); vi.unstubAllGlobals();
  });

  it.each(['cad', 'file', 'parametric'] as const)('selects a %s run and leaves Jobs visible', async (input) => {
    const run = input === 'parametric' ? parametricRun() : input === 'file' ? fileRun() : cadRun();
    workspaceModeStore.setMode(input === 'parametric' ? 'cad' : 'parametric');
    if (input === 'file') vi.stubGlobal('fetch', vi.fn(async () => json(fileWorkspace())));

    selectJob(run);

    expect(compareSelection.getSnapshot().primary).toBe(run.id);
    if (input === 'parametric') expect(useDesignStore.getState().design.L).toBe(321);
    else {
      await vi.waitFor(() => expect(useImportedModelStore.getState().phase).toBe('ready'));
      expect(importedMeshStore.getSnapshot()[input]?.ingestId).toBe(record.ingest_id);
      expect(unchangedSetup(input, record.ingest_id)).toEqual({ setup_revision_id: 'wgs_archived' });
    }
    expect(workspaceModeStore.getSnapshot().mode).toBe(input === 'parametric' ? 'parametric' : 'cad');
    expect(activations).toEqual([]);
    expect(workspaceNavigation.isVisible('jobs')).toBe(true);
  });

  it.each(['success', 'failure'] as const)('keeps B after rapid A → B selection and a late A %s', async (outcome) => {
    const pending = deferred<Response>();
    const fetcher = fetch;
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
      String(input) === '/api/runs/job-wgi_first/workspace' ? pending.promise : fetcher(input, init)));
    const error = vi.fn();
    const first = showJobModel(cadRun('wgi_first'), error);
    selectJob(cadRun());
    await vi.waitFor(() => expect(useImportedModelStore.getState().phase).toBe('ready'));
    pending.resolve(outcome === 'success' ? json(workspaceFor('wgi_first')) : json({ detail: 'Gone' }, 404));
    expect(await first).toBe(false);
    expect(error).not.toHaveBeenCalled();
    expect(compareSelection.getSnapshot().primary).toBe(cadRun().id);
    expect(useImportedModelStore.getState().prepared?.ingest_id).toBe(record.ingest_id);
    expect(activations).toEqual([]);
    expect(workspaceNavigation.isVisible('jobs')).toBe(true);
  });

  it('reports a failed load while keeping Jobs visible', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ detail: 'Archived model unavailable' }, 404)));
    const error = vi.fn();
    expect(await showJobModel(cadRun(), error)).toBe(false);
    expect(error).toHaveBeenCalledWith('Archived model unavailable');
    expect(useImportedModelStore.getState().phase).toBe('failed');
    expect(activations).toEqual([]);
    expect(workspaceNavigation.isVisible('jobs')).toBe(true);
  });

  it.each(['geometry', 'results'] as const)('respects navigation to %s while the model loads', async (panel) => {
    const pending = deferred<Response>();
    const fetcher = fetch;
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith('/workspace') ? pending.promise : fetcher(input, init)));
    const loading = showJobModel(cadRun());
    workspaceNavigation.navigate(panel);
    pending.resolve(json(workspaceFor()));
    expect(await loading).toBe(true);
    expect(activations).toEqual([panel]);
    expect(workspaceNavigation.isVisible(panel)).toBe(true);
  });

  it('fences job attention and late results after selecting history', async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const host = document.createElement('div');
    const root = createRoot(host);
    const waiting = refusedJob as unknown as JobItem;
    const processing = { ...waiting, cad_state: { ...waiting.cad_state!, state: 'processing', reason: null } };
    function Attention({ jobs }: { jobs: JobItem[] }) { useJobAttention(jobs); return null; }
    try {
      act(() => root.render(<Attention jobs={[processing]}/>));
      solveAttention.armSolve();
      solveAttention.bindRun('late-result');
      expect(await showJobModel(cadRun())).toBe(true);
      act(() => root.render(<Attention jobs={[waiting]}/>));
      expect(solveAttention.resultsReady('late-result')).toBe('indicated');
      expect(activations).toEqual([]);
      expect(workspaceNavigation.isVisible('jobs')).toBe(true);
    } finally { act(() => root.unmount()); }
  });

  it('keeps the default explicit CAD workspace navigation for other callers', async () => {
    expect(await openImported({ kind: 'run', job: cadRun() })).toBe(true);
    expect(activations).toEqual(['geometry']);
    expect(workspaceNavigation.isVisible('geometry')).toBe(true);
  });
});
