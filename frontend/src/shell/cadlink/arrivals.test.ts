import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { preferencesStore } from '../../prefs/preferences';
import { resetCadReturnStore, useCadReturnStore } from '../../stores/cadReturn';
import { designForFamily, resetDesignStore, useDesignStore } from '../../stores/design';
import { resetDocumentStore, useDocumentStore } from '../../stores/document';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import { createCadArrivalActions, createCadReturnArrivalReader, SupersededError } from './arrivals';
import { bundle, deferred, json, mesh, record } from './selection.fixtures';

const newer = { ...bundle, name: 'new.wgreturn', bundlePath: 'wgreturn/new.wgreturn' };

beforeEach(() => {
  localStorage.clear(); resetDesignStore(); resetDocumentStore(); resetCadReturnStore();
  importedMeshStore.clear(); workspaceModeStore.setMode('parametric');
  preferencesStore.resetForTests();
});
afterEach(() => { importedMeshStore.clear(); vi.restoreAllMocks(); });

describe('selection actions without the coordinator', () => {
  it.each(['success', 'error'] as const)('fences an older ingest %s even when fetch ignores abort', async (outcome) => {
    const old = deferred<Response>();
    const signals: AbortSignal[] = [];
    let reads = 0;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith('/ingest')) {
        signals.push(init!.signal as AbortSignal);
        return ++reads === 1 ? old.promise : json({ ...record, ingest_id: 'wgi_new' });
      }
      return new Response(mesh);
    });
    const status = vi.fn(); const error = vi.fn(); const busy = vi.fn();
    const actions = createCadArrivalActions({ setStatus: status, setError: error, setIngesting: busy }, fetcher);
    actions.selectBundleAs(bundle);
    const first = actions.ingestSelected().catch((reason) => reason);
    actions.selectBundleAs(newer);
    const second = await actions.ingestSelected();
    const state = useCadReturnStore.getState();
    const feedback = [status.mock.calls.length, error.mock.calls.length, busy.mock.calls.length];
    expect(signals[0].aborted).toBe(true);
    old.resolve(outcome === 'success' ? json(record) : json({ detail: "source 'source-hf' area drift" }, 409));
    expect(await first).toBeInstanceOf(SupersededError);
    expect(second.ingest_id).toBe('wgi_new');
    expect(useCadReturnStore.getState()).toBe(state);
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe('wgi_new');
    expect([status.mock.calls.length, error.mock.calls.length, busy.mock.calls.length]).toEqual(feedback);
  });

  it('drops an ingest after document replacement with no restore effect mounted', async () => {
    const pending = deferred<Response>();
    const actions = createCadArrivalActions({}, (() => pending.promise) as typeof fetch);
    actions.selectBundleAs(bundle);
    const result = actions.ingestSelected().catch((error) => error);
    useDesignStore.getState().replaceDocument(designForFamily('ICW'));
    pending.resolve(json(record));
    expect(await result).toBeInstanceOf(SupersededError);
    expect(useCadReturnStore.getState().ingestRecord).toBeNull();
    expect(importedMeshStore.getSnapshot().cad).toBeNull();
  });

  it.each(['success', 'error'] as const)('ignores an older listing %s after a newer listing', async (outcome) => {
    const old = deferred<Response>(); let reads = 0;
    const errors = vi.fn(); const status = vi.fn(); const items = vi.fn();
    const reader = createCadReturnArrivalReader({ setError: errors, setStatus: status, setBundles: items },
      (async () => ++reads === 1 ? old.promise : json({ cadFolderConfigured: true, items: [newer] })) as typeof fetch);
    const first = reader.refresh(); await reader.refresh();
    const selected = useCadReturnStore.getState().selectedBundle;
    const feedback = [errors.mock.calls.length, status.mock.calls.length, items.mock.calls.length];
    old.resolve(outcome === 'success' ? json({ cadFolderConfigured: true, items: [bundle] }) : json({ detail: 'old error' }, 500));
    await first;
    expect(useCadReturnStore.getState().selectedBundle).toBe(selected);
    expect(selected?.bundlePath).toBe(newer.bundlePath);
    expect([errors.mock.calls.length, status.mock.calls.length, items.mock.calls.length]).toEqual(feedback);
  });

  it('does not adopt a listing started before a manual pick', async () => {
    const pending = deferred<Response>();
    const items = vi.fn();
    const reader = createCadReturnArrivalReader({ setBundles: items }, (() => pending.promise) as typeof fetch);
    const first = reader.refresh({ autoOpenNew: true });
    createCadArrivalActions().selectBundleAs(newer);
    pending.resolve(json({ cadFolderConfigured: true, items: [{ ...bundle, modifiedAt: new Date().toISOString() }] }));
    await first;
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(newer.bundlePath);
    expect(items).not.toHaveBeenCalled();
  });

  it('keeps settings for a compatible arrival and resets a changed inventory', async () => {
    let listed = bundle;
    const reader = createCadReturnArrivalReader({}, (async () => json({ cadFolderConfigured: true, items: [listed] })) as typeof fetch);
    await reader.refresh({ autoOpenNew: true });
    useCadReturnStore.getState().setDriveVoltage(8);
    useCadReturnStore.getState().setRigidSize(11);
    listed = { ...bundle, modifiedAt: '2026-08-12T00:00:00Z' };
    await reader.refresh({ autoOpenNew: true });
    expect(useCadReturnStore.getState()).toMatchObject({ driveVoltageV: 8, rigidSizeMm: 11 });
    listed = { ...listed, modifiedAt: '2026-08-13T00:00:00Z', sources: [{ ...bundle.sources[0], role: 'LF' }] };
    await reader.refresh({ autoOpenNew: true });
    expect(useCadReturnStore.getState().driveVoltageV).toBe(2.83);
    expect(useCadReturnStore.getState().rigidSizeMm).toBe(4);
  });

  it('refuses a foreign arrival without replacing design or undo history', async () => {
    useDocumentStore.getState().setCadLink({ designId: 'wgd_open', lineageId: 'wgl_open', baseEditVersion: 1 }, 'current');
    useDesignStore.getState().updateField('R', 321);
    const design = useDesignStore.getState(); const identity = useDocumentStore.getState().identity;
    const foreign = { ...bundle, designIds: ['wgd_foreign'], modifiedAt: new Date().toISOString() };
    const fetcher = (async () => json({ cadFolderConfigured: true, items: [foreign] })) as typeof fetch;
    const error = vi.fn();
    await createCadReturnArrivalReader({ setError: error }, fetcher).refresh({ autoOpenNew: true });
    expect(error).toHaveBeenCalled();
    expect(useCadReturnStore.getState().selectedBundle).toBeNull();
    expect(useDesignStore.getState()).toBe(design);
    expect(useDocumentStore.getState().identity).toBe(identity);
  });

  it('returns and rebuilds an Onshape bundle using its own origin with no Fusion listing', async () => {
    preferencesStore.setOnshapeAvailable(true); preferencesStore.update({ cadApplication: 'onshape' });
    useDocumentStore.getState().setCadLink({ designId: 'wgd_open', lineageId: 'wgl_open', baseEditVersion: 1 }, 'current');
    const requests: Array<{ path: string; body: Record<string, unknown> }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input); const body = JSON.parse(String(init?.body ?? '{}'));
      requests.push({ path, body });
      if (path.endsWith('/onshape/return')) return json({ bundle: { ...bundle, bundlePath: 'onshape.wgreturn' }, ingest: record });
      if (path.endsWith('/ingest')) return json({ ...record, ingest_id: 'wgi_rebuilt' });
      return new Response(mesh);
    });
    const actions = createCadArrivalActions({}, fetcher);
    await actions.returnFromOnshape();
    expect(useCadReturnStore.getState().selectedBundle?.bundleOrigin).toBe('onshape');
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    await createCadReturnArrivalReader({}, fetcher).refresh();
    await actions.ingestSelected();
    expect(requests.find((item) => item.path.endsWith('/ingest'))?.body.bundleOrigin).toBe('onshape');
    expect(requests.some((item) => item.path.endsWith('/returns'))).toBe(false);
  });

  it('preserves the direct Onshape action while its build capability is unavailable', async () => {
    preferencesStore.setOnshapeAvailable(false);
    useDocumentStore.getState().setCadLink({ designId: 'wgd_open', lineageId: 'wgl_open', baseEditVersion: 1 }, 'current');
    const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/onshape/return')
      ? json({ bundle, ingest: record }) : new Response(mesh));
    await createCadArrivalActions({}, fetcher).returnFromOnshape();
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(record.ingest_id);
    expect(fetcher.mock.calls.some(([input]) => String(input).endsWith('/onshape/return'))).toBe(true);
  });

  it.each(['success', 'error'] as const)('fences an Onshape %s after another selection', async (outcome) => {
    preferencesStore.setOnshapeAvailable(true);
    useDocumentStore.getState().setCadLink({ designId: 'wgd_open', lineageId: 'wgl_open', baseEditVersion: 1 }, 'current');
    const pending = deferred<Response>(); const error = vi.fn();
    const actions = createCadArrivalActions({ setError: error }, (() => pending.promise) as typeof fetch);
    const first = actions.returnFromOnshape().catch((reason) => reason);
    actions.selectBundleAs(newer);
    const before = error.mock.calls.length;
    pending.resolve(outcome === 'success' ? json({ bundle, ingest: record }) : json({ detail: 'late error' }, 500));
    await first;
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(newer.bundlePath);
    expect(useCadReturnStore.getState().ingestRecord).toBeNull();
    expect(error).toHaveBeenCalledTimes(before);
  });
});


it.each(['listing revision', 'mesh edit'] as const)(
  'accepts a listing through a background %s', async (change) => {
    const state = useCadReturnStore.getState(); state.selectBundle(bundle);
    const pending = deferred<Response>(); const items = vi.fn();
    const reader = createCadReturnArrivalReader({
      setBundles: items, seenReturnRevisions: { current: new Map([[bundle.bundlePath, bundle.modifiedAt]]) },
    }, (() => pending.promise) as typeof fetch);
    const first = reader.refresh({ background: true });
    if (change === 'listing revision') state.refreshSelectedBundle({ ...bundle, modifiedAt: '2026-08-14T00:00:00Z' });
    else state.setRigidSize(9);
    pending.resolve(json({ cadFolderConfigured: true, items: [bundle] })); await first;
    expect(items).toHaveBeenCalledWith([bundle]);
  },
);

it('fences a manual pick without aborting its previous in-flight ingest', async () => {
  const pending = deferred<Response>(); const busy = vi.fn(); let signal: AbortSignal | undefined;
  const actions = createCadArrivalActions({ setIngesting: busy }, (async (_input, init) => {
    signal = init?.signal as AbortSignal; return pending.promise;
  }) as typeof fetch);
  actions.selectBundleAs(bundle);
  const first = actions.ingestSelected().catch((error) => error);
  const before = busy.mock.calls.length;
  actions.selectBundleAs(newer);
  expect(signal?.aborted).toBe(false);
  expect(busy).toHaveBeenCalledTimes(before);
  pending.resolve(json(record));
  expect(await first).toBeInstanceOf(SupersededError);
  expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(newer.bundlePath);
  expect(useCadReturnStore.getState().ingestRecord).toBeNull();
});

it.each(['design identity', 'instance'] as const)('fences an Onshape return after a changed %s without React', async (change) => {
  useDocumentStore.getState().setCadLink({ designId: 'wgd_first', lineageId: 'wgl_first', baseEditVersion: 1 }, 'current');
  let instanceId = 'instance-first';
  const pending = deferred<Response>();
  const actions = createCadArrivalActions({ onshapeInstanceId: () => instanceId }, (() => pending.promise) as typeof fetch);
  const first = actions.returnFromOnshape();
  if (change === 'instance') instanceId = 'instance-second';
  else useDocumentStore.getState().setCadLink({ designId: 'wgd_second', lineageId: 'wgl_second', baseEditVersion: 1 }, 'current');
  pending.resolve(json({ bundle, ingest: record })); await first;
  expect(useCadReturnStore.getState().selectedBundle).toBeNull();
  expect(useCadReturnStore.getState().ingestRecord).toBeNull();
  expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
});

it('retains prepared native geometry when the Fusion listing has no matching return (row 5)', async () => {
  const native = { ...bundle, bundleOrigin: 'native' as const };
  useCadReturnStore.getState().selectBundle(native);
  const generation = useCadReturnStore.getState().beginIngestIntent();
  useCadReturnStore.getState().applyIngest(record, generation);
  const before = useCadReturnStore.getState().ingestRecord;
  const reader = createCadReturnArrivalReader({}, async () => json({ cadFolderConfigured: true, items: [] }));
  await reader.refresh({ background: true, autoOpenNew: true });
  expect(useCadReturnStore.getState().selectedBundle).toBe(native);
  expect(useCadReturnStore.getState().ingestRecord).toBe(before);
  expect(useCadReturnStore.getState().needsIngest).toBe(false);
});


it('rebuilds a workspace return explicitly against WGLink rather than the Onshape area (row 26)', async () => {
  const requests: Array<Record<string, unknown>> = [];
  const fetcher: typeof fetch = async (input, init) => {
    if (String(input).endsWith('/ingest')) {
      requests.push(JSON.parse(String(init?.body)));
      return json(record);
    }
    return new Response(mesh);
  };
  const actions = createCadArrivalActions({}, fetcher);
  actions.selectBundleAs(bundle);
  await actions.ingestSelected();
  expect(requests).toHaveLength(1);
  expect(requests[0].bundleOrigin).toBe('wglink');
});

it.each(['matching', 'no match'])('holds project-open feedback until the deferred initial choice finds %s', async (result) => {
  useDocumentStore.getState().setCadLink({ designId: 'wgd_open', lineageId: 'wgl_open', baseEditVersion: 1 }, 'current');
  const matching = { ...bundle, designIds: ['wgd_open'] };
  let pending = true;
  const projectOpenPending = { current: true }; const initialChoiceOwed = { current: true };
  const status = vi.fn(); const ingest = vi.fn();
  const reader = createCadReturnArrivalReader({ projectOpenPending, initialChoiceOwed, setStatus: status,
    autoIngestSelected: ingest, awaitingReceiptDisplay: () => pending },
  async () => json({ cadFolderConfigured: true, items: result === 'matching' ? [matching] : [] }));
  await reader.refresh(); await reader.refresh({ background: true });
  expect(useCadReturnStore.getState().selectedBundle).toBeNull();
  expect(initialChoiceOwed.current).toBe(true);
  expect(projectOpenPending.current).toBe(true);
  expect(status).not.toHaveBeenCalled();
  pending = false;
  await reader.refresh({ background: true });
  expect(projectOpenPending.current).toBe(false);
  if (result === 'matching') {
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(matching.bundlePath);
    expect(initialChoiceOwed.current).toBe(false);
    expect(status).toHaveBeenCalledWith(expect.stringContaining('Selected the latest matching return'));
  } else {
    expect(useCadReturnStore.getState().selectedBundle).toBeNull();
    expect(status).toHaveBeenCalledWith(expect.stringContaining('No matching CAD return'));
  }
  expect(ingest).not.toHaveBeenCalled();
});
