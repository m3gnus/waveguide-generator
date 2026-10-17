import { importedSubmissionBlocker, buildImportedSubmission } from '../../jobs/importedSubmission';
import { buildCadProjectSetup } from '../cadSetupPublisher';
import { cadSolveBlockerNow } from '../JobsCoordinator';
// These actions deliberately run with no coordinator or React effects mounted.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { preferencesStore } from '../../prefs/preferences';
import { resetCadReturnStore, useCadReturnStore } from '../../stores/cadReturn';
import { readLastModel, rememberLastModel } from '../../stores/lastModel';
import { DurableSettings } from '../../stores/durableSettings';
import { resetDesignStore } from '../../stores/design';
import { resetDocumentStore } from '../../stores/document';
import { resetSolveOptionsStore, useImportedSolveOptionsStore, useSolveOptionsStore } from '../../stores/solveOptions';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import { observeLastModel, refreshChannelDriverBases } from './restores';
import { archivedRequest, bundle, deferred, job, json, mesh, record, workspaceFor } from './selection.fixtures';
import { openImported, resetImportedModelStore, unchangedSetup, useImportedModelStore, type ImportedDisplay } from '../../stores/importedModel';

import type { JobItem } from '../../api/jobsSocket';
import { createCadArrivalActions, createCadReturnArrivalReader } from './arrivals';
import { adoptSolvedCadModel } from './solvedModel';

/** Reopen a CAD run as history does: `openImported` of the run, shown by the CAD rail. */
const restoreCadJobModel = (run: JobItem, display: ImportedDisplay = {}, fetcher: typeof fetch = fetch) =>
  openImported({ kind: 'run', job: run, display }, fetcher).catch(() => false);
const isWorkspace = (input: RequestInfo | URL) => String(input).endsWith('/workspace');

it('does not recall an unnumbered CAD preparation as an archived run', async () => {
  const enterCadWorkspace = vi.fn();
  const reportStatus = vi.fn();
  const reportViewportNotice = vi.fn();
  const showIngestedMesh = vi.fn();
  const job = {
    run_number: null,
    config_summary: { geometry_type: 'imported' },
  } as unknown as JobItem;

  expect(await restoreCadJobModel(job, {
    enterCadWorkspace, reportStatus, reportViewportNotice, showIngestedMesh,
  })).toBe(false);
  expect(enterCadWorkspace).not.toHaveBeenCalled();
  expect(reportStatus).not.toHaveBeenCalled();
});

it('restores the coupled BEAT model without legacy driver conversion', async () => {
  const { cadHistorySetup } = await import('./restores');
  const model = { version: 1, motion_axis: [0, 1, 0], re_ohm: 6, le_h: .0005,
    bl_n_per_a: 7, mmd_kg: .02, cms_m_per_n: .001, rms_n_s_per_m: 1.5 };
  const job = { cad_setup: { drive_channels: [{ id: 'd', source_ids: ['s'], motion: 'axial', exterior_transducer: model }],
    drive_voltage_v: 5.66 }, solve_options: { frequencies_hz: [100, 500] } } as unknown as JobItem;
  const record = { sources: [{ id: 's', default_drive_channel_id: 'd' }], skipped_source_ids: [],
    mesh_sizes: { rigid_size_mm: 8, transition_mm: 20, source_size_mm: { s: 3 } } } as never;
  const restored = cadHistorySetup({ geometry: job.cad_setup, options: job.solve_options } as never, record);
  expect(restored.driveChannels[0]).toEqual({ id: 'd', source_ids: ['s'], motion: 'axial', exterior_transducer: model });
  expect(restored.driveVoltageV).toBe(5.66);
  expect(restored.channelDrivers).toEqual({});
});


beforeEach(() => {
  localStorage.clear(); resetDesignStore(); resetDocumentStore(); resetCadReturnStore();
  resetSolveOptionsStore(); preferencesStore.resetForTests(); importedMeshStore.clear();
  workspaceModeStore.setImportedInput('cad'); resetImportedModelStore();
  workspaceModeStore.setMode('parametric');
});
afterEach(() => { importedMeshStore.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const archiveFetcher = (async (input: RequestInfo | URL) => String(input).endsWith('/viewport-mesh')
  ? new Response(mesh) : isWorkspace(input) ? json(workspaceFor()) : json(record)) as typeof fetch;

describe('job and last-model restores without the coordinator', () => {
  it('restores a job ingest, saved setup, sweep and model through standalone defaults', async () => {
    workspaceModeStore.setImportedInput('file');
    expect(await restoreCadJobModel(job(), {}, archiveFetcher)).toBe(true);
    expect(workspaceModeStore.getSnapshot().importedInput).toBe('cad');
    expect(useCadReturnStore.getState()).toMatchObject({
      ingestRecord: record, driveVoltageV: 8, maxDriveVoltageV: 20, exteriorOnly: true,
      frequencyStartHz: 100, frequencyEndHz: 1000, frequencyCount: 3,
      driveChannels: [{ id: 'archive-hf', source_ids: ['source-hf'], motion: 'normal' }],
      sourceSizesMm: { 'source-hf': 4 }, needsIngest: false,
    });
    expect(useCadReturnStore.getState().isIngestSettled()).toBe(false);
    expect(useCadReturnStore.getState().selectedBundle?.readable).toBe(false);
    expect(useImportedSolveOptionsStore.getState().frequencyListText).toBe('100\n500\n1000');
    expect(useSolveOptionsStore.getState().frequencyListText).toBe('');
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe(record.ingest_id);
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
  });

  it.each(['success', 'error'] as const)('never lets a delayed archived ingest %s overtake a newer pick', async (outcome) => {
    const pending = deferred<Response>(); const status = vi.fn();
    const first = restoreCadJobModel(job(), { reportStatus: status }, (() => pending.promise) as typeof fetch);
    useCadReturnStore.getState().selectBundle(bundle);
    useCadReturnStore.getState().setDriveVoltage(5);
    const state = useCadReturnStore.getState(); const calls = status.mock.calls.length;
    pending.resolve(outcome === 'success' ? json(record) : json({ detail: 'missing archived ingest' }, 404));
    expect(await first).toBe(false);
    expect(useCadReturnStore.getState()).toBe(state);
    expect(status).toHaveBeenCalledTimes(calls);
    expect(importedMeshStore.getSnapshot().cad).toBeNull();
  });

  it('keeps the newest overlapping archived run', async () => {
    const pending = deferred<Response>();
    const first = restoreCadJobModel(job(), {}, (() => pending.promise) as typeof fetch);
    const nextRecord = { ...record, ingest_id: 'wgi_next' };
    expect(await restoreCadJobModel(job('wgi_next'), {}, (async (input: RequestInfo | URL) =>
      String(input).endsWith('/viewport-mesh') ? new Response(mesh) : isWorkspace(input)
        ? json(workspaceFor('wgi_next')) : json(nextRecord)) as typeof fetch)).toBe(true);
    pending.resolve(json(record)); expect(await first).toBe(false);
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe('wgi_next');
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe('wgi_next');
  });

  it('drops a delayed archived mesh after a newer selection', async () => {
    const pending = deferred<Response>(); const requested = deferred<void>(); const status = vi.fn();
    const first = restoreCadJobModel(job(), { reportStatus: status }, (async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/viewport-mesh')) { requested.resolve(); return pending.promise; }
      return isWorkspace(input) ? json(workspaceFor()) : json(record);
    }) as typeof fetch);
    await requested.promise;
    useCadReturnStore.getState().selectBundle(bundle);
    const state = useCadReturnStore.getState(); const calls = status.mock.calls.length;
    pending.resolve(new Response(mesh));
    expect(await first).toBe(false);
    expect(useCadReturnStore.getState()).toBe(state); expect(status).toHaveBeenCalledTimes(calls);
    expect(importedMeshStore.getSnapshot().cad).toBeNull();
  });

  it('reports missing archived artifacts without showing a different model', async () => {
    const status = vi.fn();
    expect(await restoreCadJobModel(job(), { reportStatus: status },
      (async () => json({ detail: 'missing' }, 404)) as typeof fetch)).toBe(false);
    expect(status.mock.calls.at(-1)?.[0]).toContain('missing');
    expect(importedMeshStore.getSnapshot().cad).toBeNull();
  });

  const cadModelFetcher = (modelId = 'wgl_project') => vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.startsWith(`/api/models/${modelId}/setup`)) return json({ setup: workspaceFor().setup?.content, revision_id: 'wgs_saved' });
    if (path.startsWith(`/api/models/${modelId}`)) {
      return json({ model: { model_id: modelId, producer: 'cad', display_name: 'Speaker' }, latest_prepared: workspaceFor(record.ingest_id, undefined, { modelId }).prepared,
        original_available: false });
    }
    if (path === `/api/cadlink/ingest/${record.ingest_id}`) return json(record);
    if (path.endsWith('/viewport-mesh')) return new Response(mesh);
    return json({ detail: 'unexpected' }, 404);
  }) as unknown as ReturnType<typeof vi.fn<typeof fetch>>;

  it('reopens the last CAD model at start in Imported mode, through openImported', async () => {
    rememberLastModel({ model_id: 'wgl_project', input: 'cad' }); workspaceModeStore.setMode('cad');
    const fetcher = cadModelFetcher(); const status = vi.fn();
    const stop = observeLastModel({ reportStatus: status, fetcher });
    try {
      await vi.waitFor(() => expect(useImportedModelStore.getState()).toMatchObject({ phase: 'ready', input: 'cad', modelId: 'wgl_project' }));
      expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(record.ingest_id);
      expect(status).toHaveBeenCalledWith('Reopened the last model.');
    } finally { stop(); }
  });

  it('opens nothing and keeps Parametric when that was the persisted mode', async () => {
    rememberLastModel({ model_id: 'wgl_project', input: 'cad' });
    const fetcher = cadModelFetcher();
    const stop = observeLastModel({ fetcher });
    await Promise.resolve(); await Promise.resolve();
    stop();
    expect(fetcher).not.toHaveBeenCalled();
    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
    expect(useImportedModelStore.getState().phase).toBe('empty');
    expect(readLastModel('cad')).toEqual({ model_id: 'wgl_project', input: 'cad' });
  });

  const modelReads = (fetcher: ReturnType<typeof cadModelFetcher>) =>
    fetcher.mock.calls.filter(([input]) => String(input) === '/api/models/wgl_project').length;

  it('reopens the model when the person switches into Imported mode mid-session, never changing the mode itself', async () => {
    rememberLastModel({ model_id: 'wgl_project', input: 'cad' });
    const fetcher = cadModelFetcher();
    const stop = observeLastModel({ fetcher });
    try {
      await Promise.resolve();
      expect(fetcher).not.toHaveBeenCalled();
      expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
      workspaceModeStore.setMode('cad');
      await vi.waitFor(() => expect(useImportedModelStore.getState()).toMatchObject({ phase: 'ready', modelId: 'wgl_project' }));
      // At most once per pointer in a session, even when the view is emptied and entered again.
      const reads = modelReads(fetcher);
      workspaceModeStore.setMode('parametric'); resetImportedModelStore(); workspaceModeStore.setMode('cad');
      await Promise.resolve(); await Promise.resolve();
      expect(modelReads(fetcher)).toBe(reads);
    } finally { stop(); }
  });

  it('does nothing on entry when a model is already open', async () => {
    rememberLastModel({ model_id: 'wgl_project', input: 'cad' });
    const fetcher = cadModelFetcher();
    const stop = observeLastModel({ fetcher });
    try {
      await openImported({ kind: 'cad', record: { ...record, model_id: 'wgl_other' } as typeof record });
      workspaceModeStore.setMode('cad');
      await Promise.resolve(); await Promise.resolve();
      expect(fetcher).not.toHaveBeenCalled();
    } finally { stop(); }
  });

  it.each([['a refused foreign return', { refusedForeignReturn: { current: true } }], ['a pending ?job= link', { jobLinkPending: () => true }]] as const)(
    'stands down for %s', async (_name, guard) => {
      rememberLastModel({ model_id: 'wgl_project', input: 'cad' }); workspaceModeStore.setMode('cad');
      const fetcher = cadModelFetcher();
      const stop = observeLastModel({ fetcher, ...guard });
      await Promise.resolve(); await Promise.resolve();
      stop();
      expect(fetcher).not.toHaveBeenCalled();
      expect(readLastModel('cad')).not.toBeNull();
    });

  it('shows the newest return of a CAD model that was never solved, on defaults', async () => {
    rememberLastModel({ model_id: 'wgl_project', input: 'cad' }); workspaceModeStore.setMode('cad');
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes('/setup')) return json({ setup: null });
      if (path.startsWith('/api/models/')) return json({ model: { model_id: 'wgl_project', producer: 'cad', display_name: 'Speaker' },
        latest_prepared: workspaceFor().prepared, original_available: false });
      return json({ items: path.endsWith('/designs') ? [{ lineageId: 'wgl_project', documentName: 'Speaker' }] : [bundle], cadFolderConfigured: true });
    }) as unknown as typeof fetch;
    const status = vi.fn(); const select = vi.fn();
    const stop = observeLastModel({ fetcher, reportStatus: status, selectBundle: select });
    try {
      // The owners' selection does the ingest and shows refusals: it is what must be called, not a fresh one.
      await vi.waitFor(() => expect(select).toHaveBeenCalledWith(bundle, 'wgl_project'));
      expect(status).toHaveBeenCalledWith('Reopened Speaker.');
      expect(readLastModel('cad')).not.toBeNull();
    } finally { stop(); }
  });

  it('says why when a never-solved CAD project has no return to show', async () => {
    rememberLastModel({ model_id: 'wgl_project', input: 'cad' }); workspaceModeStore.setMode('cad');
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes('/setup')) return json({ setup: null });
      if (path.startsWith('/api/models/')) return json({ model: { model_id: 'wgl_project', producer: 'cad', display_name: 'Speaker' },
        latest_prepared: workspaceFor().prepared, original_available: false });
      return json({ items: path.endsWith('/designs') ? [{ lineageId: 'wgl_project', documentName: 'Speaker' }] : [], cadFolderConfigured: true });
    }) as unknown as typeof fetch;
    const status = vi.fn(); const select = vi.fn(); const stop = observeLastModel({ fetcher, reportStatus: status, selectBundle: select });
    try { await vi.waitFor(() => expect(status).toHaveBeenCalledWith(expect.stringContaining('no return to show')));
      expect(select).not.toHaveBeenCalled(); } finally { stop(); }
  });

  const settingsEnvelope = (pointers: Record<string, string>) =>
    json({ schemaVersion: 1, namespaces: { lastModel: JSON.stringify(pointers) } });

  it('reattempts when the settings server delivers the pointer after start-up (a real durable-settings notification)', async () => {
    workspaceModeStore.setMode('cad');
    const fetcher = cadModelFetcher();
    const settings = new DurableSettings({ storage: localStorage, fetcher: vi.fn(async () => settingsEnvelope({ cad: 'wgl_project' })) as unknown as typeof fetch });
    const stop = observeLastModel({ fetcher, settings });
    try {
      await Promise.resolve();
      expect(fetcher).not.toHaveBeenCalled();
      await settings.hydrate();
      await vi.waitFor(() => expect(useImportedModelStore.getState()).toMatchObject({ phase: 'ready', modelId: 'wgl_project' }));
      // One open is two reads: the existence check, then the open itself.
      expect(modelReads(fetcher)).toBe(2);
    } finally { stop(); }
  });

  it('migrates locally when the settings wait times out, and the late server answer does not open it twice', async () => {
    workspaceModeStore.setMode('cad');
    localStorage.setItem('wg2.cad.project.v1', 'wgl_project');
    const fetcher = cadModelFetcher();
    const late = deferred<Response>();
    const settings = new DurableSettings({ storage: localStorage, fetcher: vi.fn(() => late.promise) as unknown as typeof fetch });
    const stop = observeLastModel({ fetcher, settings });
    try {
      await vi.waitFor(() => expect(useImportedModelStore.getState().phase).toBe('ready'));
      expect(localStorage.getItem('wg2.cad.project.v1')).toBeNull();
      // As if the migrated pointer's upload had been acknowledged: the server's answer may then replace it.
      localStorage.removeItem('wg2.last.model.v1.local-newer');
      await settings.hydrate({ timeoutMs: 5 });
      late.resolve(settingsEnvelope({ cad: 'wgl_project', file: 'mesh-file:sha256:other' }));
      await vi.waitFor(() => expect(readLastModel('file')).toEqual({ model_id: 'mesh-file:sha256:other', input: 'file' }));
      await Promise.resolve(); await Promise.resolve();
      expect(modelReads(fetcher)).toBe(2);
    } finally { stop(); }
  });

  it('never forces Imported mode when the pointer is hydrated late', async () => {
    const fetcher = cadModelFetcher();
    const stop = observeLastModel({ fetcher });
    rememberLastModel({ model_id: 'wgl_project', input: 'cad' });
    stop();
    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
    expect(fetcher).not.toHaveBeenCalled();
  });
});

it('keeps a recalled run ineligible for solved-model adoption', async () => {
  expect(await restoreCadJobModel(job(), {}, archiveFetcher)).toBe(true);
  const fetcher = vi.fn(async () => json({ ...record, ingest_id: 'wgi_prepared' }));
  expect(await adoptSolvedCadModel(job('wgi_prepared'), {
    manifestSha256: record.manifest_sha256, sourceIngestId: record.ingest_id,
  }, { showIngestedMesh: vi.fn() }, fetcher)).toBe('declined');
  expect(fetcher).not.toHaveBeenCalled();
  expect(useCadReturnStore.getState().ingestRecord).toEqual(record);
});

it.each(['manual selection', 'listing arrival'] as const)(
  're-reads drivers for the same %s while auto-ingest is in flight', async (caller) => {
    const preset = { id: 'driver-fixture', label: 'Fixture', source: 'database' as const,
      kind: 'cd' as const, z_ohm: 8, xo_min_hz: null, base: { re_ohm: 5 } };
    const state = useCadReturnStore.getState();
    state.selectBundle(bundle, 'wgl_project');
    state.setChannelDriverPreset('drive-hf', preset);
    const pending = deferred<Response>();
    const ingestPending = deferred<Response>();
    const status = vi.fn();
    let refresh!: Promise<string[]>;
    let ingest!: Promise<typeof record>;
    const reread = () => {
      refresh = refreshChannelDriverBases((() => pending.promise) as typeof fetch);
      return refresh.then((drivers) => {
        if (drivers.length) status(`Re-read ${drivers.length} drivers from the library.`);
      });
    };
    const autoIngestSelected = () => { ingest = actions.ingestSelected(); };
    const actions = createCadArrivalActions({
      rereadDrivers: () => { void reread(); }, autoIngestSelected, showIngestedMesh: async () => undefined,
    }, (() => ingestPending.promise) as typeof fetch);
    if (caller === 'manual selection') {
      actions.selectBundleAs(bundle, 'wgl_project');
    } else {
      await createCadReturnArrivalReader({
        refreshChannelDriverBases: reread, autoIngestSelected,
      }, (async () => json({ cadFolderConfigured: true,
        items: [{ ...bundle, modifiedAt: new Date().toISOString() }] })) as typeof fetch)
        .refresh({ autoOpenNew: true });
    }
    pending.resolve(json({ spec: { re_ohm: 7 }, xo_min_hz: 1200 }));
    expect(await refresh).toEqual(['drive-hf']);
    expect(useCadReturnStore.getState().channelDrivers['drive-hf'].preset?.base.re_ohm).toBe(7);
    expect(status).toHaveBeenCalledWith('Re-read 1 drivers from the library.');
    ingestPending.resolve(json(record)); await ingest;
  },
);

it('an archive restore leaves the project profile intact', async () => {
  const state = useCadReturnStore.getState(); state.selectBundle(bundle, 'wgl_project');
  state.setDriveVoltage(5);
  const profile = localStorage.getItem('waveguide-v2-g3-cad-solve-profiles');
  expect(profile).not.toBeNull();
  expect(await restoreCadJobModel(job(), {}, archiveFetcher)).toBe(true);
  expect(useCadReturnStore.getState().driveVoltageV).toBe(8);
  expect(localStorage.getItem('waveguide-v2-g3-cad-solve-profiles')).toBe(profile);
  resetCadReturnStore(); useCadReturnStore.getState().selectBundle(bundle, 'wgl_project');
  expect(useCadReturnStore.getState().driveVoltageV).toBe(5);
});

it('brings a parked CAD run back with its own engine, not the project profile over it', async () => {
  const state = useCadReturnStore.getState(); state.selectBundle(bundle, 'wgl_project');
  workspaceModeStore.setMode('cad');
  // The project's profile remembers another engine.
  useImportedSolveOptionsStore.getState().setEngine('bempp');
  expect(await restoreCadJobModel(job(), {}, archiveFetcher)).toBe(true);
  expect(useImportedSolveOptionsStore.getState().engine).toBe('beat-cpu');
  // The same return becomes readable on screen again, so its profile could apply.
  useCadReturnStore.setState({ selectedBundle: bundle });
  workspaceModeStore.setImportedInput('file');
  workspaceModeStore.setImportedInput('cad');
  expect(useImportedSolveOptionsStore.getState()).toMatchObject({ engine: 'beat-cpu', accuracy: 'accurate' });
  expect(unchangedSetup('cad', record.ingest_id)).toEqual({ setup_revision_id: 'wgs_archived' });
});

it('ends the opened setup when a new selection replaces the options', async () => {
  workspaceModeStore.setMode('cad');
  expect(await restoreCadJobModel(job(), {}, archiveFetcher)).toBe(true);
  expect(unchangedSetup('cad', record.ingest_id)).not.toBeNull();
  useCadReturnStore.getState().selectBundle(bundle, 'wgl_project');
  expect(useImportedModelStore.getState()).toMatchObject({ setup: null, restoredRevisionId: null });
});

it('does not refresh drivers into a newer project with the same preset', async () => {
  const preset = { id: 'driver-fixture', label: 'Fixture', source: 'database' as const,
    kind: 'cd' as const, z_ohm: 8, xo_min_hz: null, base: { re_ohm: 5 } };
  useCadReturnStore.getState().selectBundle(bundle, 'wgl_first');
  useCadReturnStore.getState().setChannelDriverPreset('drive-hf', preset);
  const pending = deferred<Response>();
  const refresh = refreshChannelDriverBases((() => pending.promise) as typeof fetch);
  useCadReturnStore.getState().selectBundle(bundle, 'wgl_second');
  useCadReturnStore.getState().setChannelDriverPreset('drive-hf', preset);
  const state = useCadReturnStore.getState();
  pending.resolve(json({ spec: { re_ohm: 7 }, xo_min_hz: 1200 }));
  expect(await refresh).toEqual([]);
  expect(useCadReturnStore.getState()).toBe(state);
});


describe('restoring a CAD run and the parametric options', () => {
  it('leaves the persisted parametric store byte-identical', async () => {
    useSolveOptionsStore.getState().setFrequencySpacing('linear');
    useSolveOptionsStore.getState().updatePolar({ distance: 4.5 });
    const before = JSON.stringify(useSolveOptionsStore.getState().options());
    const stateBefore = JSON.stringify(useSolveOptionsStore.getState());
    const storedBefore = JSON.stringify(useSolveOptionsStore.persist.getOptions().partialize?.(useSolveOptionsStore.getState()));
    expect(await restoreCadJobModel(job(), {}, archiveFetcher)).toBe(true);
    expect(JSON.stringify(useSolveOptionsStore.getState().options())).toBe(before);
    expect(JSON.stringify(useSolveOptionsStore.getState())).toBe(stateBefore);
    expect(JSON.stringify(useSolveOptionsStore.persist.getOptions().partialize?.(useSolveOptionsStore.getState()))).toBe(storedBefore);
    expect(useImportedSolveOptionsStore.getState().frequencyMode).toBe('list');
  });
});

describe('shared historical restoration transaction', () => {
  it.each(['request', 'record', 'viewport'] as const)('blocks a ready A while B is pending at %s and after failure', async stage => {
    expect(await restoreCadJobModel(job(), {}, archiveFetcher)).toBe(true);
    expect(buildImportedSubmission().geometry.ingest_id).toBe(record.ingest_id);
    const pending = deferred<Response>();
    const reached = deferred<void>();
    const status = vi.fn();
    const restoring = restoreCadJobModel(job('wgi_missing'), { reportStatus: status }, (async input => {
      const path = String(input);
      if ((stage === 'request' && path.endsWith('/workspace'))
        || (stage === 'viewport' && path.endsWith('/viewport-mesh'))
        || (stage === 'record' && !path.endsWith('/workspace'))) {
        reached.resolve(); return pending.promise;
      }
      return path.endsWith('/workspace') ? json(workspaceFor('wgi_missing'))
        : json({ ...record, ingest_id: 'wgi_missing' });
    }) as typeof fetch);
    await reached.promise;
    expect(cadSolveBlockerNow()).toContain('loading');
    expect(importedSubmissionBlocker()).toContain('loading');
    expect(() => buildImportedSubmission()).toThrow('loading');
    pending.resolve(json({ detail: 'retained target missing' }, 404));
    expect(await restoring).toBe(false);
    expect(cadSolveBlockerNow()).not.toBeNull();
    expect(() => buildImportedSubmission()).toThrow();
    expect(useCadReturnStore.getState().ingestRecord).toBeNull();
    expect(status.mock.calls.at(-1)?.[0]).toContain('Cannot show');
  });

  it('opens a run\'s setup without reading current project drivers, and solves its revision until edited', async () => {
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_project');
    useCadReturnStore.getState().setChannelDriverPreset('drive-hf', {
      id: 'today', label: 'Today', source: 'database', kind: 'cd', z_ohm: 8, xo_min_hz: null, base: { re_ohm: 99 },
    });
    useImportedSolveOptionsStore.setState({ engine: 'bempp', accuracy: 'fast', adaptiveFrequencySampling: false });
    const frozen = archivedRequest();
    frozen.geometry.drive_channels![0].driver = { re_ohm: 6, sd_cm2: 50, bl_t_m: 7, mms_g: 10, cms_m_per_n: .001 };
    const request = { ...frozen, options: { ...frozen.options, adaptive_frequency_sampling: true,
      ground_plane: { enabled: true, axis: 'y', height_m: 1.2 } } };
    const fetcher = vi.fn<typeof fetch>(async input => isWorkspace(input)
      ? json(workspaceFor(record.ingest_id, request as never)) : archiveFetcher(input));
    expect(await restoreCadJobModel(job(), {}, fetcher)).toBe(true);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/runs/job-${record.ingest_id}/workspace`);
    expect(fetcher.mock.calls.some(([input]) => String(input).includes('/drivers/'))).toBe(false);
    expect(fetcher.mock.calls.some(([input]) => String(input).endsWith('/requests'))).toBe(false);
    expect(useImportedSolveOptionsStore.getState()).toMatchObject({ engine: 'beat-cpu', accuracy: 'accurate',
      adaptiveFrequencySampling: true, groundPlane: { enabled: true, height_m: 1.2 } });
    expect(useCadReturnStore.getState().channelDrivers['archive-hf'].fields.re_ohm).toBe(6);
    // The project is the model the server named.
    expect(useCadReturnStore.getState().projectLineageId).toBe('wgl_project');
    expect(useImportedModelStore.getState()).toMatchObject({ input: 'cad', modelId: 'wgl_project', phase: 'ready' });
    // An unedited Solve names the run's setup revision.
    expect(unchangedSetup('cad', record.ingest_id)).toEqual({ setup_revision_id: 'wgs_archived' });
    // The rail shows the same setup, so an edit starts from it.
    const setup = buildCadProjectSetup(undefined, undefined, undefined, 'manual');
    expect(setup?.setup.geometry).toMatchObject({ drive_channels: [{ driver: { re_ohm: 6 } }], drive_voltage_v: 8 });
    expect(setup?.setup.options).toMatchObject({ engine: 'beat-cpu', accuracy: 'accurate', adaptive_frequency_sampling: true,
      frequencies_hz: [100, 500, 1000] });
    // Background global hydration is not an explicit edit to the opened setup.
    useImportedSolveOptionsStore.setState({ engine: 'bempp' });
    workspaceModeStore.setMode('parametric');
    resetDesignStore();
    workspaceModeStore.setMode('cad');
    expect(unchangedSetup('cad', record.ingest_id)).toEqual({ setup_revision_id: 'wgs_archived' });
    // A deliberate edit ends it and becomes the setup a Solve sends.
    useImportedSolveOptionsStore.getState().setEngine('beat-cpu');
    expect(unchangedSetup('cad', record.ingest_id)).toBeNull();
    useCadReturnStore.getState().setDriveVoltage(4);
    expect(buildCadProjectSetup(undefined, undefined, undefined, 'manual')?.setup.geometry.drive_voltage_v).toBe(4);
  });

  it('files an unfiled run under no guessed project', async () => {
    useCadReturnStore.getState().selectBundle(bundle, 'wgl_open_project');
    const fetcher = vi.fn<typeof fetch>(async input => isWorkspace(input)
      ? json({ ...workspaceFor(record.ingest_id, archivedRequest(), { modelId: 'unfiled' }), model: null })
      : archiveFetcher(input));
    expect(await restoreCadJobModel(job(), {}, fetcher)).toBe(true);
    expect(useCadReturnStore.getState().projectLineageId).toBeNull();
    expect(useImportedModelStore.getState().modelId).toBe('unfiled');
  });

  it('rejects mismatched CAD retained hashes before publishing its setup', async () => {
    expect(await restoreCadJobModel(job(), {}, archiveFetcher)).toBe(true);
    const status = vi.fn();
    expect(await restoreCadJobModel(job('wgi_bad'), { reportStatus: status }, (async input => isWorkspace(input)
      ? json(workspaceFor('wgi_bad')) : json({ ...record, ingest_id: 'wgi_bad', artifact_sha256: 'wrong' })) as typeof fetch)).toBe(false);
    expect(status.mock.calls.at(-1)?.[0]).toContain('does not match');
    expect(() => buildImportedSubmission()).toThrow();
  });
});

it('does not hide an edit made while the retained viewport mesh is still loading', async () => {
  const pending = deferred<Response>(); const reached = deferred<void>();
  const restoring = restoreCadJobModel(job(), {}, (async input => {
    if (String(input).endsWith('/viewport-mesh')) { reached.resolve(); return pending.promise; }
    return archiveFetcher(input);
  }) as typeof fetch);
  await reached.promise;
  useCadReturnStore.getState().setDriveVoltage(3);
  pending.resolve(new Response(mesh));
  expect(await restoring).toBe(false);
  expect(useCadReturnStore.getState().driveVoltageV).toBe(3);
  expect(importedSubmissionBlocker()).toContain('Settings changed');
  expect(() => buildImportedSubmission()).toThrow('Select the run again');
});
