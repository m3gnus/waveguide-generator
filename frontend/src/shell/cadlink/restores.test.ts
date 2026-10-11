// These actions deliberately run with no coordinator or React effects mounted.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { preferencesStore } from '../../prefs/preferences';
import { resetCadReturnStore, useCadReturnStore } from '../../stores/cadReturn';
import { rememberCadProject } from '../../stores/cadProjectMemory';
import { resetDesignStore } from '../../stores/design';
import { resetDocumentStore } from '../../stores/document';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../../stores/solveOptions';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import { restoreRememberedCadProject, observeRememberedCadProject, restoreCadJobModel, refreshChannelDriverBases } from './restores';
import { bundle, deferred, job, json, mesh, record } from './selection.fixtures';

import type { JobItem } from '../../api/jobsSocket';

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
  const restored = cadHistorySetup(job, record);
  expect(restored.driveChannels[0]).toEqual({ id: 'd', source_ids: ['s'], motion: 'axial', exterior_transducer: model });
  expect(restored.driveVoltageV).toBe(5.66);
  expect(restored.channelDrivers).toEqual({});
});


beforeEach(() => {
  localStorage.clear(); resetDesignStore(); resetDocumentStore(); resetCadReturnStore();
  resetSolveOptionsStore(); preferencesStore.resetForTests(); importedMeshStore.clear();
  workspaceModeStore.setMode('parametric');
});
afterEach(() => { importedMeshStore.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const archiveFetcher = (async (input: RequestInfo | URL) => String(input).endsWith('/viewport-mesh')
  ? new Response(mesh) : json(record)) as typeof fetch;

describe('job and remembered-project restores without the coordinator', () => {
  it('restores a job ingest, saved setup, sweep and model through standalone defaults', async () => {
    expect(await restoreCadJobModel(job(), {}, archiveFetcher)).toBe(true);
    expect(useCadReturnStore.getState()).toMatchObject({
      ingestRecord: record, driveVoltageV: 8, maxDriveVoltageV: 20, exteriorOnly: true,
      frequencyStartHz: 100, frequencyEndHz: 1000, frequencyCount: 3,
      driveChannels: [{ id: 'archive-hf', source_ids: ['source-hf'], motion: 'normal' }],
      sourceSizesMm: { 'source-hf': 4 }, needsIngest: false,
    });
    expect(useCadReturnStore.getState().isIngestSettled()).toBe(true);
    expect(useCadReturnStore.getState().selectedBundle?.readable).toBe(false);
    expect(useSolveOptionsStore.getState().frequencyListText).toBe('100\n500\n1000');
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
      String(input).endsWith('/viewport-mesh') ? new Response(mesh) : json(nextRecord)) as typeof fetch)).toBe(true);
    pending.resolve(json(record)); expect(await first).toBe(false);
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe('wgi_next');
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe('wgi_next');
  });

  it('drops a delayed archived mesh after a newer selection', async () => {
    const pending = deferred<Response>(); const requested = deferred<void>(); const status = vi.fn();
    const first = restoreCadJobModel(job(), { reportStatus: status }, (async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/viewport-mesh')) { requested.resolve(); return pending.promise; }
      return json(record);
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
    expect(status.mock.calls.at(-1)?.[0]).toContain('no longer available');
    expect(importedMeshStore.getSnapshot().cad).toBeNull();
  });

  it('reopens the remembered project only while the initiating mode is empty', async () => {
    rememberCadProject('wgl_project'); workspaceModeStore.setMode('cad');
    const fetcher = (async (input: RequestInfo | URL) => json({ items: String(input).endsWith('/designs')
      ? [{ lineageId: 'wgl_project', documentName: 'Speaker' }] : [bundle], cadFolderConfigured: true })) as typeof fetch;
    expect(await restoreRememberedCadProject({}, fetcher)).toBe(true);
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(bundle.bundlePath);
    expect(useCadReturnStore.getState().projectLineageId).toBe('wgl_project');
    const again = vi.fn();
    expect(await restoreRememberedCadProject({}, again)).toBe(false); expect(again).not.toHaveBeenCalled();
  });

  it.each(['pick', 'pick then clear', 'leave CAD mode'] as const)('fences remembered reads after a %s', async (change) => {
    rememberCadProject('wgl_project'); workspaceModeStore.setMode('cad');
    const pending = deferred<Response>(); const status = vi.fn();
    const fetcher = (async (input: RequestInfo | URL) => String(input).endsWith('/designs')
      ? json({ items: [{ lineageId: 'wgl_project', documentName: 'Speaker' }] }) : pending.promise) as typeof fetch;
    const first = restoreRememberedCadProject({ reportStatus: status }, fetcher);
    if (change === 'leave CAD mode') workspaceModeStore.setMode('parametric');
    else {
      useCadReturnStore.getState().selectBundle({ ...bundle, bundlePath: 'manual.wgreturn' });
      if (change === 'pick then clear') useCadReturnStore.getState().selectBundle(null);
    }
    pending.resolve(json({ cadFolderConfigured: true, items: [bundle] }));
    expect(await first).toBe(false);
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).not.toBe(bundle.bundlePath);
    expect(status).not.toHaveBeenCalled();
  });

  it('observes entering empty CAD mode without mounting the coordinator', async () => {
    rememberCadProject('wgl_project');
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => json({
      items: String(input).endsWith('/designs') ? [{ lineageId: 'wgl_project', documentName: 'Speaker' }] : [bundle],
      cadFolderConfigured: true,
    })) as typeof fetch);
    const status = vi.fn(); const stop = observeRememberedCadProject({ reportStatus: status });
    try {
      workspaceModeStore.setMode('cad');
      await vi.waitFor(() => expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(bundle.bundlePath));
      expect(status).toHaveBeenCalledWith('Reopened Speaker.');
    } finally { stop(); }
  });
});

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
