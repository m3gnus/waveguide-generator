import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { bundle, record, json, deferred, mesh, workspaceFor } from '../shell/cadlink/selection.fixtures';
import '../shell/cadlink/restores';
import { importedSubmissionBlocker } from '../jobs/importedSubmission';
import { buildCadProjectSetup, startCadSetupPublisher } from '../shell/cadSetupPublisher';
import { buildImportedSubmission } from '../jobs/importedSubmission';
import { resetCadReturnStore, sourceInventorySignature, useCadReturnStore } from './cadReturn';
import { resetSolveOptionsStore, useImportedSolveOptionsStore, useSolveOptionsStore } from './solveOptions';
import { resetCadPreparationStore } from './cadPreparation';
import { resetImportedModelStore, openImported, reopenLastModel, unchangedSetup } from './importedModel';
import { createCadArrivalActions } from '../shell/cadlink/arrivals';
import { driverValues, channelDriverWire } from './cadReturn';
import { resetDocumentStore, useDocumentStore } from './document';
import { DurableSettings, durableSettings } from './durableSettings';
import { CAD_DRAFTS_NAMESPACE, cadDraftHash, mergeCadDrafts } from './cadDraftJournal';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { workspaceModeStore } from './workspaceMode';
import { cadDraftKey, retainCadDraft, waitForCadProjectSettings, observeCadDraftHydration } from './cadProjectSettings';

function modelDrafts(owner: string): string | null {
  const entries = Object.entries(mergeCadDrafts(durableSettings.get(CAD_DRAFTS_NAMESPACE)))
    .filter(([key, entry]) => key.startsWith(`${cadDraftHash(owner)}_`) && entry.draft)
    .map(([key, entry]) => [key, { ...entry.draft, baseRevisionId: entry.baseRevisionId, updatedAt: entry.updatedAt }]);
  return entries.length ? JSON.stringify(Object.fromEntries(entries)) : null;
}
const stops: Array<() => void> = [];
function publisher(options: Parameters<typeof startCadSetupPublisher>[0]) {
  const stop = startCadSetupPublisher(options); stops.push(stop); return stop;
}
const model = 'wgl_project';
const inventory = bundle.sources.map(({ id, role, required }) => ({ id, role, required }));
const key = cadDraftKey(model, inventory);
const saved = { ...workspaceFor().setup.content,
  preparation: { symmetry_mode: 'full', area_drift_overrides: ['source-hf'] },
  options: { ...workspaceFor().setup.content.options, frequency_spacing: 'linear', adaptive_frequency_sampling: true },
};
function server(setup: unknown = saved) {
  return vi.fn<typeof fetch>(async input => {
    const path = String(input);
    if (path.includes('/setup')) return json({ setup, revision_id: setup ? 'wgs_saved' : null, cad_claimed: true });
    if (path === `/api/models/${model}`) return json({ model: workspaceFor().model,
      latest_prepared: workspaceFor().prepared, original_available: false });
    if (path.endsWith('/viewport-mesh')) return new Response(mesh);
    return json(record);
  });
}
beforeEach(() => {
  localStorage.clear(); resetDocumentStore(); resetCadReturnStore(); resetSolveOptionsStore(); resetCadPreparationStore();
  workspaceModeStore.setImportedInput('cad'); workspaceModeStore.setMode('cad'); resetImportedModelStore();
});
afterEach(() => { stops.splice(0).forEach(stop => stop()); vi.useRealTimers(); vi.unstubAllGlobals(); });

it('restores identical model settings on reopen, selection and arrival, independent of app defaults', async () => {
  const fetcher = server(); vi.stubGlobal('fetch', fetcher);
  useSolveOptionsStore.getState().updatePolar({ distance: 19 });
  expect(await openImported({ kind: 'model', modelId: model }, fetcher)).toBe(true);
  const reopened = buildCadProjectSetup();
  expect(reopened?.setup.preparation).toEqual(saved.preparation);
  expect(reopened?.setup.geometry.mesh).toEqual(saved.geometry.mesh);
  for (const arrive of [false, true]) {
    resetCadReturnStore(); resetSolveOptionsStore(); resetCadPreparationStore();
    useSolveOptionsStore.getState().updatePolar({ distance: 11 });
    if (arrive) useCadReturnStore.getState().selectArrivedBundle(bundle, model);
    else useCadReturnStore.getState().selectBundle(bundle, model);
    expect(await waitForCadProjectSettings()).toBe(true);
    expect(buildCadProjectSetup()).toEqual(reopened);
  }
  const read = fetcher.mock.calls.find(([input]) => String(input).includes('?inventory='))!;
  expect(JSON.parse(decodeURIComponent(String(read[0]).split('inventory=')[1]))).toEqual(inventory);
  expect(useImportedSolveOptionsStore.getState()).toMatchObject({ frequencySpacing: 'linear', adaptiveFrequencySampling: true });
});

it('publishes and solves only the edited CAD option while retaining the rest of the archived setup', async () => {
  const fetcher = server();
  expect(await openImported({ kind: 'model', modelId: model }, fetcher)).toBe(true);
  expect(buildCadProjectSetup()?.setup).toEqual(saved);
  useImportedSolveOptionsStore.getState().updatePolar({ distance: 7 });
  const expected = structuredClone(saved);
  expected.options.polar_config.distance = 7;
  expect(buildCadProjectSetup()?.setup).toEqual(expected);
  const submitted = buildImportedSubmission();
  expect(submitted.geometry.drive_voltage_v).toBe(saved.geometry.drive_voltage_v);
  expect(submitted.geometry.max_drive_voltage_v).toBe(saved.geometry.max_drive_voltage_v);
  expect(submitted.options).toEqual(expected.options);
  // Preparing another mesh of this model keeps the effective edited setup.
  const next = { ...record, model_id: model, ingest_id: 'wgi_prepared_again' };
  await openImported({ kind: 'cad', record: next });
  useCadReturnStore.setState({ ingestRecord: next });
  expect(buildCadProjectSetup()?.setup).toEqual(expected);
});

it('persists an incomplete driver immediately, restores it over the last valid setup, and clears only on successful publish', async () => {
  const fetcher = server(); vi.stubGlobal('fetch', fetcher);
  useCadReturnStore.getState().selectBundle(bundle, model); await waitForCadProjectSettings();
  const stop = publisher({ fetcher, debounceMs: 1 });
  useCadReturnStore.getState().setChannelDriverField('archive-hf', 'sd_cm2', 42);
  expect(buildCadProjectSetup()).toBeNull();
  expect(modelDrafts(model)).toContain('42');
  stop(); resetCadReturnStore(); resetSolveOptionsStore();
  useCadReturnStore.getState().selectBundle(bundle, model); await waitForCadProjectSettings();
  expect(useCadReturnStore.getState().channelDrivers['archive-hf'].fields).toEqual({ sd_cm2: 42 });
  expect(buildCadProjectSetup()).toBeNull();
  const again = publisher({ fetcher, debounceMs: 1 });
  useCadReturnStore.getState().setChannelDriverField('archive-hf', 'sd_cm2', null);
  await vi.waitFor(() => expect(modelDrafts(model)).toBeNull());
  again();
});

it('reopens a durable draft without allowing the old valid revision to bypass incomplete fields', async () => {
  const fetcher = server(); vi.stubGlobal('fetch', fetcher);
  useCadReturnStore.getState().selectBundle(bundle, model); await waitForCadProjectSettings();
  useCadReturnStore.getState().setChannelDriverField('archive-hf', 're_ohm', 6);
  retainCadDraft(); resetCadReturnStore();
  expect(await openImported({ kind: 'model', modelId: model }, fetcher)).toBe(true);
  expect(unchangedSetup('cad', record.ingest_id)).toBeNull();
  expect(useCadReturnStore.getState().channelDrivers['archive-hf'].fields).toEqual({ re_ohm: 6 });
});

it('keeps an incomplete successor when an older valid publish acknowledges', async () => {
  const fetcher = server(); vi.stubGlobal('fetch', fetcher);
  useCadReturnStore.getState().selectBundle(bundle, model); await waitForCadProjectSettings();
  const ack = deferred<Response>();
  const writer = vi.fn<typeof fetch>(async (input) => String(input).endsWith('/setup') ? ack.promise : json({}));
  const stop = publisher({ fetcher: writer, debounceMs: 1 });
  useCadReturnStore.getState().setDriveVoltage(7);
  await vi.waitFor(() => expect(writer.mock.calls.some(([input]) => String(input).endsWith('/setup'))).toBe(true));
  useCadReturnStore.getState().setChannelDriverField('archive-hf', 'sd_cm2', 51);
  ack.resolve(json({})); await Promise.resolve(); await Promise.resolve();
  expect(modelDrafts(model)).toContain('51'); stop();
});

function profile(version = 4) {
  const state = useCadReturnStore.getState();
  state.selectBundle(bundle, model);
  useCadReturnStore.setState({ driveVoltageV: 9, channelDrivers: {
    'drive-hf': { preset: null, fields: { sd_cm2: 42, bl_t_m: 7, re_ohm: 6, mms_g: 20, cms_m_per_n: .001 } },
  } });
  const snapshot = JSON.parse(retainCadDraft()!.raw).settings;
  durableSettings.set(CAD_DRAFTS_NAMESPACE, '{}');
  if (version < 3) Object.assign(snapshot, { combineCrossoversHz: {}, combineAlign: null, combineLevelMatch: null });
  if (version === 1) snapshot.combineEnabled = false;
  return { version, profiles: [{ key: JSON.stringify([`cad:${model}`, sourceInventorySignature(bundle)]),
    owner: `cad:${model}`, designId: 'wgd_project', lineageId: model, inventory, settings: snapshot,
    solveSelection: { engine: 'beat-cpu', accuracy: 'accurate' } }] };
}
it.each([1, 2, 3, 4])('migrates a v%i profile once into the publisher draft when no server setup exists', async version => {
  const fetcher = server(null); vi.stubGlobal('fetch', fetcher);
  const legacy = profile(version);
  durableSettings.set('cadSolveProfiles', JSON.stringify(legacy)); resetCadReturnStore();
  useCadReturnStore.getState().selectBundle(bundle, model); await waitForCadProjectSettings();
  expect(useCadReturnStore.getState().driveVoltageV).toBe(9);
  expect(useImportedSolveOptionsStore.getState()).toMatchObject({ engine: 'beat-cpu', accuracy: 'accurate' });
  await vi.waitFor(() => expect(durableSettings.get('cadSolveProfiles')).toBeNull());
  expect(JSON.parse(modelDrafts(model)!)[key].legacy).toBe(true);
});

it('lets an existing server setup win over a v4 profile and drops the legacy key', async () => {
  const fetcher = server(); vi.stubGlobal('fetch', fetcher);
  durableSettings.set('cadSolveProfiles', JSON.stringify(profile())); resetCadReturnStore();
  useCadReturnStore.getState().selectBundle(bundle, model); await waitForCadProjectSettings();
  expect(useCadReturnStore.getState().driveVoltageV).toBe(8);
  await vi.waitFor(() => expect(durableSettings.get('cadSolveProfiles')).toBeNull());
  expect(modelDrafts(model)).toBeNull();
});

it('publishes a migrated v4 profile through the publisher and deletes its draft', async () => {
  const fetcher = server(null); vi.stubGlobal('fetch', fetcher);
  durableSettings.set('cadSolveProfiles', JSON.stringify(profile())); resetCadReturnStore();
  const stop = publisher({ fetcher, debounceMs: 1 });
  useCadReturnStore.getState().selectBundle(bundle, model); await waitForCadProjectSettings();
  await vi.waitFor(() => expect(modelDrafts(model)).toBeNull());
  const put = fetcher.mock.calls.find(([input, init]) => String(input).endsWith('/setup') && init?.method === 'PUT')!;
  expect(JSON.parse(String(put[1]!.body)).setup.geometry.drive_voltage_v).toBe(9);
  stop();
});

it('never applies another inventory or model draft', async () => {
  const fetcher = server(null); vi.stubGlobal('fetch', fetcher);
  useCadReturnStore.getState().selectBundle(bundle, model); await waitForCadProjectSettings();
  useCadReturnStore.getState().setDriveVoltage(9); retainCadDraft();
  for (const [next, id] of [[{ ...bundle, sources: [{ ...bundle.sources[0], role: 'LF' }] }, model], [bundle, 'wgl_other']] as Array<[typeof bundle, string]>) {
    useCadReturnStore.getState().selectBundle(next, id); await waitForCadProjectSettings();
    expect(useCadReturnStore.getState().driveVoltageV).toBe(2.83);
  }
  expect(modelDrafts(model)).toContain('9');
});

it('fences delayed reads by selection and edits', async () => {
  const first = deferred<Response>(); const fetcher = server(); vi.stubGlobal('fetch', fetcher);
  useCadReturnStore.getState().selectBundle(bundle, model, false, () => first.promise);
  useCadReturnStore.getState().selectBundle(bundle, 'wgl_other'); await waitForCadProjectSettings();
  useCadReturnStore.getState().setDriveVoltage(6);
  first.resolve(json({ setup: saved })); await Promise.resolve(); await Promise.resolve();
  expect(useCadReturnStore.getState()).toMatchObject({ projectLineageId: 'wgl_other', driveVoltageV: 6 });
});

it('restores parked CAD options without changing a file’s options', async () => {
  const fetcher = server(); vi.stubGlobal('fetch', fetcher);
  workspaceModeStore.setImportedInput('file');
  useImportedSolveOptionsStore.getState().updatePolar({ distance: 13 });
  useCadReturnStore.getState().selectBundle(bundle, model); await waitForCadProjectSettings();
  expect(useImportedSolveOptionsStore.getState().polar.distance).toBe(13);
  workspaceModeStore.setImportedInput('cad');
  expect(useImportedSolveOptionsStore.getState().polar.distance).toBe(saved.options.polar_config.distance);
});

it('holds edits during a setup read and applies them over the fetched baseline before publishing', async () => {
  const pending = deferred<Response>();
  const setup = { ...saved, geometry: { ...saved.geometry, mesh: { ...record.mesh_sizes, rigid_size_mm: 12 },
    drive_channels: [{ id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal',
      driver: { sd_cm2: 42, bl_t_m: 7, re_ohm: 7, mms_g: 20, cms_m_per_n: .001 } }] } };
  const fetcher = server(setup); vi.stubGlobal('fetch', fetcher);
  useCadReturnStore.getState().selectBundle(bundle, model, false, () => pending.promise);
  const stop = publisher({ fetcher, debounceMs: 1 });
  expect(importedSubmissionBlocker()).toContain('settings to finish loading');
  useCadReturnStore.getState().setDriveVoltage(7);
  expect(fetcher.mock.calls.some(([, init]) => init?.method === 'PUT' && String(init.body).includes('inventory'))).toBe(false);
  pending.resolve(json({ setup })); await waitForCadProjectSettings();
  expect(useCadReturnStore.getState()).toMatchObject({ driveVoltageV: 7, rigidSizeMm: 12, setupLoading: false });
  expect(useImportedSolveOptionsStore.getState().polar.distance).toBe(saved.options.polar_config.distance);
  await vi.waitFor(() => expect(modelDrafts(model)).toBeNull());
  const put = fetcher.mock.calls.find(([input, init]) => String(input).endsWith('/setup') && init?.method === 'PUT')!;
  expect(JSON.parse(String(put[1]!.body)).setup.geometry).toMatchObject({ drive_voltage_v: 7, mesh: { rigid_size_mm: 12 },
    drive_channels: [{ driver: { re_ohm: 7 } }] });
  stop();
});

it('keeps the saved effective driver values when selection refreshes its library reference', async () => {
  const setup = { ...saved, geometry: { ...saved.geometry,
    drive_channels: [{ id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal',
      driver: { label: 'Fixture', sd_cm2: 42, bl_t_m: 7, re_ohm: 7, mms_g: 20, cms_m_per_n: .001 } }] },
    driver_references: { 'drive-hf': { driver_id: 'fixture', source: 'database' } } };
  const base = server(setup);
  const fetcher = vi.fn<typeof fetch>(async (input, init) => String(input).includes('/api/drivers/')
    ? json({ id: 'fixture', spec: { re_ohm: 6, sd_cm2: 40, bl_t_m: 6, mms_g: 18, cms_m_per_n: .002, le_mh: .5, xmax_mm: 4 } }) : base(input, init));
  vi.stubGlobal('fetch', fetcher);
  const { refreshChannelDriverBases } = await import('../shell/cadlink/restores');
  const actions = createCadArrivalActions({ rereadDrivers: () => { void refreshChannelDriverBases(fetcher); } }, fetcher);
  actions.selectBundleAs(bundle, model); await waitForCadProjectSettings();
  await vi.waitFor(() => expect(useCadReturnStore.getState().channelDrivers['drive-hf'].preset?.base.re_ohm).toBe(6));
  expect(driverValues(useCadReturnStore.getState().channelDrivers['drive-hf']).re_ohm).toBe(7);
  expect(channelDriverWire(useCadReturnStore.getState().channelDrivers['drive-hf'])).toEqual(setup.geometry.drive_channels[0].driver);
  expect(buildCadProjectSetup()?.setup.geometry.drive_channels).toMatchObject([{ driver: { re_ohm: 7 } }]);
  useCadReturnStore.getState().clearChannelDriverOverrides('drive-hf');
  expect(driverValues(useCadReturnStore.getState().channelDrivers['drive-hf']).le_mh).toBe(.5);
});

it('adopts a draft that hydrates after the model has opened', async () => {
  const fetcher = server(); vi.stubGlobal('fetch', fetcher);
  useCadReturnStore.getState().selectBundle(bundle, model); await waitForCadProjectSettings();
  useCadReturnStore.getState().setChannelDriverField('archive-hf', 'sd_cm2', 55); retainCadDraft();
  const draft = durableSettings.get(CAD_DRAFTS_NAMESPACE)!;
  localStorage.clear(); resetCadReturnStore();
  const remote = deferred<Response>();
  const settings = new DurableSettings({ storage: localStorage, fetcher: () => remote.promise });
  const stop = observeCadDraftHydration(settings);
  const hydrate = settings.hydrate({ timeoutMs: 1 }); await hydrate;
  useCadReturnStore.getState().selectBundle(bundle, model); await waitForCadProjectSettings();
  expect(buildCadProjectSetup()).not.toBeNull();
  remote.resolve(json({ namespaces: { [CAD_DRAFTS_NAMESPACE]: draft } }));
  await vi.waitFor(() => expect(useCadReturnStore.getState().channelDrivers['archive-hf']?.fields.sd_cm2).toBe(55));
  expect(buildCadProjectSetup()).toBeNull(); stop();
});

it('uses the server’s resolved CAD lineage while an unrelated design is open', async () => {
  const fetcher = server(); vi.stubGlobal('fetch', fetcher);
  useDocumentStore.getState().setCadLink({ designId: 'wgd_unrelated', lineageId: 'wgl_unrelated', baseEditVersion: 1 }, 'current');
  useCadReturnStore.getState().selectArrivedBundle({ ...bundle, projectLineageId: model });
  await waitForCadProjectSettings();
  expect(useCadReturnStore.getState().projectLineageId).toBe(model);
  expect(fetcher.mock.calls.some(([input]) => String(input).includes('wgl_unrelated/setup'))).toBe(false);
  expect(useCadReturnStore.getState().driveVoltageV).toBe(8);
});

it('keeps a newer selection’s held edits when the previous setup read fails', async () => {
  const first = deferred<Response>(); const second = deferred<Response>();
  useCadReturnStore.getState().selectBundle(bundle, 'wgl_first', false, () => first.promise);
  useCadReturnStore.getState().selectBundle(bundle, model, false, () => second.promise);
  useCadReturnStore.getState().setDriveVoltage(6);
  first.reject(new Error('old offline read')); await Promise.resolve(); await Promise.resolve();
  second.resolve(json({ setup: saved })); await waitForCadProjectSettings();
  expect(useCadReturnStore.getState()).toMatchObject({ driveVoltageV: 6, setupLoading: false });
});

it('finishes a CAD read while file options are edited on the shared rail', async () => {
  const pending = deferred<Response>();
  useCadReturnStore.getState().selectBundle(bundle, model, false, () => pending.promise);
  workspaceModeStore.setImportedInput('file');
  useImportedSolveOptionsStore.getState().updatePolar({ distance: 17 });
  pending.resolve(json({ setup: saved })); await waitForCadProjectSettings();
  expect(useCadReturnStore.getState().setupLoading).toBe(false);
  expect(useImportedSolveOptionsStore.getState().polar.distance).toBe(17);
  workspaceModeStore.setImportedInput('cad');
  expect(useImportedSolveOptionsStore.getState().polar.distance).toBe(saved.options.polar_config.distance);
});

it('does not replay a held CAD control into a file selected while the read was pending', async () => {
  const pending = deferred<Response>();
  useCadReturnStore.getState().selectBundle(bundle, model, false, () => pending.promise);
  useImportedSolveOptionsStore.getState().setEngine('bempp');
  workspaceModeStore.setImportedInput('file');
  const fileEngine = useImportedSolveOptionsStore.getState().engine;
  pending.resolve(json({ setup: saved })); await waitForCadProjectSettings();
  expect(useImportedSolveOptionsStore.getState().engine).toBe(fileEngine);
  workspaceModeStore.setImportedInput('cad');
  expect(useImportedSolveOptionsStore.getState().engine).toBe(saved.options.engine);
});

it('reopens an unsolved model’s incomplete draft without requiring its original return folder', async () => {
  const fetcher = server(null); vi.stubGlobal('fetch', fetcher);
  useCadReturnStore.getState().selectBundle(bundle, model); await waitForCadProjectSettings();
  useCadReturnStore.getState().setChannelDriverField('drive-hf', 'sd_cm2', 66); retainCadDraft();
  const { rememberLastModel } = await import('./lastModel');
  rememberLastModel({ model_id: model, input: 'cad' });
  resetCadReturnStore(); resetImportedModelStore();
  expect(await reopenLastModel(fetcher)).toBe(true);
  expect(useCadReturnStore.getState().channelDrivers['drive-hf'].fields.sd_cm2).toBe(66);
  expect(buildCadProjectSetup()).toBeNull();
  expect(unchangedSetup('cad', record.ingest_id)).toBeNull();
});

it.each(['read failure', 'missing ingest', 'file supersedes'])('releases a pending return setup read when a model open ends with %s', async outcome => {
  const old = deferred<Response>();
  useCadReturnStore.getState().selectBundle(bundle, model, false, () => old.promise);
  await vi.waitFor(() => expect(useCadReturnStore.getState().setupLoading).toBe(true));
  const newer = deferred<Response>();
  const base = server();
  const fetcher = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input) === `/api/models/${model}`) {
      if (outcome === 'read failure') throw new Error('read failed');
      if (outcome === 'file supersedes') return newer.promise;
    }
    if (outcome === 'missing ingest' && String(input).includes('/ingest/')) return new Response('{}', { status: 404 });
    return base(input, init);
  });
  const opened = openImported({ kind: 'model', modelId: model }, fetcher);
  if (outcome === 'file supersedes') {
    importedMeshStore.beginIntent();
    workspaceModeStore.setImportedInput('file');
    await openImported({ kind: 'file', prepared: null }, fetcher);
    newer.resolve(json({ model: workspaceFor().model, latest_prepared: workspaceFor().prepared }));
    expect(await opened).toBe(false);
  } else await expect(opened).rejects.toThrow();
  expect(useCadReturnStore.getState().setupLoading).toBe(false);
  old.resolve(json({ setup: saved, revision_id: 'old' }));
  await Promise.resolve(); await Promise.resolve();
  workspaceModeStore.setImportedInput('cad');
  useCadReturnStore.getState().setDriveVoltage(6);
  expect(useCadReturnStore.getState().driveVoltageV).toBe(6);
  expect(useCadReturnStore.getState().setupLoading).toBe(false);
});

it('discards a failed publication draft when a newer server revision exists, without publishing it', async () => {
  const initial = server(); vi.stubGlobal('fetch', initial);
  useCadReturnStore.getState().selectBundle(bundle, model); await waitForCadProjectSettings();
  const failed = vi.fn<typeof fetch>(async () => { throw new Error('offline'); });
  const stop = publisher({ fetcher: failed, debounceMs: 1, retryDelaysMs: [] });
  useCadReturnStore.getState().setDriveVoltage(7);
  await vi.waitFor(() => expect(failed).toHaveBeenCalled()); stop();
  expect(JSON.parse(modelDrafts(model)!)[key].baseRevisionId).toBe('wgs_saved');
  resetCadReturnStore();
  const newer = vi.fn<typeof fetch>(async (input, init) => String(input).includes('/setup') && init?.method !== 'PUT'
    ? json({ setup: saved, revision_id: 'wgs_newer' }) : initial(input, init));
  vi.stubGlobal('fetch', newer);
  const again = publisher({ fetcher: newer, debounceMs: 1 });
  useCadReturnStore.getState().selectBundle(bundle, model); await waitForCadProjectSettings();
  await new Promise(resolve => setTimeout(resolve, 10));
  expect(useCadReturnStore.getState().driveVoltageV).toBe(8);
  expect(modelDrafts(model)).toBeNull();
  expect(newer.mock.calls.filter(([input, init]) => String(input).includes('/setup') && init?.method === 'PUT')).toHaveLength(0);
  again();
});

it('drops a design-owned profile whose recorded lineage was never claimed by CAD', async () => {
  const legacy = profile(3);
  legacy.profiles[0].lineageId = 'wgl_unrelated_design';
  durableSettings.set('cadSolveProfiles', JSON.stringify(legacy)); resetCadReturnStore();
  const fetcher = vi.fn<typeof fetch>(async input => json({ setup: null, cad_claimed: !String(input).includes('wgl_unrelated_design') }));
  vi.stubGlobal('fetch', fetcher);
  useCadReturnStore.getState().selectBundle(bundle, 'wgl_unrelated_design'); await waitForCadProjectSettings();
  expect(useCadReturnStore.getState().driveVoltageV).toBe(2.83);
  expect(modelDrafts('wgl_unrelated_design')).toBeNull();
});

it('skips malformed legacy entries and retains the source until migrated drafts upload', async () => {
  const legacy = profile();
  durableSettings.set('cadSolveProfiles', JSON.stringify({ ...legacy, profiles: [null, { bad: true }, ...legacy.profiles] }));
  resetCadReturnStore();
  const upload = deferred<Response>();
  const base = server(null);
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => String(input).startsWith(`/api/settings/${CAD_DRAFTS_NAMESPACE}`) ? upload.promise : base(input, init)));
  useCadReturnStore.getState().selectBundle(bundle, model); await waitForCadProjectSettings();
  expect(useCadReturnStore.getState().driveVoltageV).toBe(9);
  expect(durableSettings.get('cadSolveProfiles')).not.toBeNull();
  upload.resolve(json({}));
  await vi.waitFor(() => expect(durableSettings.get('cadSolveProfiles')).toBeNull());
});

it('journals and restores edits before a new CAD document is claimed, then transfers them to its model', async () => {
  vi.stubGlobal('fetch', server(null));
  useCadReturnStore.getState().selectBundle(bundle, null); await waitForCadProjectSettings();
  useCadReturnStore.getState().setDriveVoltage(6); retainCadDraft();
  resetCadReturnStore();
  useCadReturnStore.getState().selectBundle(bundle, null); await waitForCadProjectSettings();
  expect(useCadReturnStore.getState().driveVoltageV).toBe(6);
  const before = Object.keys(mergeCadDrafts(durableSettings.get(CAD_DRAFTS_NAMESPACE)));
  useCadReturnStore.getState().applyIngest({ ...record, project: { ...record.project!, lineage_id: model } }, useCadReturnStore.getState().beginIngestIntent());
  await waitForCadProjectSettings();
  expect(useCadReturnStore.getState().driveVoltageV).toBe(6);
  expect(modelDrafts(model)).toContain('6');
  expect(before.every(key => mergeCadDrafts(durableSettings.get(CAD_DRAFTS_NAMESPACE))[key]?.draft === null)).toBe(true);
});

it('bounds the journal to the most recent 20 drafts', async () => {
  vi.stubGlobal('fetch', server(null));
  for (let i = 0; i < 25; i += 1) {
    vi.spyOn(Date, 'now').mockReturnValue(i + 1);
    useCadReturnStore.getState().selectBundle(bundle, `wgl_${i}`); await waitForCadProjectSettings();
    retainCadDraft();
  }
  expect(Object.values(mergeCadDrafts(durableSettings.get(CAD_DRAFTS_NAMESPACE))).filter(entry => entry.draft)).toHaveLength(20);
  expect(modelDrafts('wgl_0')).toBeNull();
  expect(modelDrafts('wgl_24')).not.toBeNull();
  vi.restoreAllMocks();
});

it('advances an incomplete draft through multiple queued successful predecessors', async () => {
  vi.stubGlobal('fetch', server());
  useCadReturnStore.getState().selectBundle(bundle, model); await waitForCadProjectSettings();
  const a = deferred<Response>(); const b = deferred<Response>(); let count = 0;
  const writer = vi.fn<typeof fetch>(async input => String(input).endsWith('/setup') ? (++count === 1 ? a.promise : b.promise) : json({}));
  const stop = publisher({ fetcher: writer, debounceMs: 1 });
  useCadReturnStore.getState().setDriveVoltage(5);
  await vi.waitFor(() => expect(count).toBe(1));
  useCadReturnStore.getState().setDriveVoltage(6);
  await new Promise(resolve => setTimeout(resolve, 10));
  useCadReturnStore.getState().setChannelDriverField('archive-hf', 'sd_cm2', 51);
  a.resolve(json({ revision_id: 'wgs_a' }));
  await vi.waitFor(() => expect(count).toBe(2));
  b.resolve(json({ revision_id: 'wgs_b' }));
  await vi.waitFor(() => expect(JSON.parse(modelDrafts(model)!)[key].baseRevisionId).toBe('wgs_b'));
  stop(); resetCadReturnStore();
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => json({ setup: saved, revision_id: 'wgs_b' })));
  useCadReturnStore.getState().selectBundle(bundle, model); await waitForCadProjectSettings();
  expect(useCadReturnStore.getState().channelDrivers['archive-hf'].fields.sd_cm2).toBe(51);
});

it('journals and restores a long unclaimed bundle path using a fixed length key', async () => {
  vi.stubGlobal('fetch', server(null));
  const longBundle = { ...bundle, bundlePath: `/returns/${'very-long-path/'.repeat(700)}speaker.wgreturn` };
  useCadReturnStore.getState().selectBundle(longBundle, null); await waitForCadProjectSettings();
  useCadReturnStore.getState().setDriveVoltage(6);
  const retained = retainCadDraft()!;
  expect(retained.key).toHaveLength(33);
  expect(durableSettings.get(CAD_DRAFTS_NAMESPACE)).not.toContain(longBundle.bundlePath);
  resetCadReturnStore();
  useCadReturnStore.getState().selectBundle(longBundle, null); await waitForCadProjectSettings();
  expect(useCadReturnStore.getState().driveVoltageV).toBe(6);
});
