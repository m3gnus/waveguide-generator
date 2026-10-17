import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnBundle, CadReturnIngestRecord, CadReturnIngestRequest } from '../../api/cadlink';
import { createCadReceiptsStore, type CadReceipt } from '../../stores/cadReceipts';
import { resetCadReturnStore, useCadReturnStore } from '../../stores/cadReturn';
import { resetDocumentStore, useDocumentStore } from '../../stores/document';
import { resetSolveOptionsStore } from '../../stores/solveOptions';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import { resetDesignStore, useDesignStore } from '../../stores/design';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { restoreCadJobModel } from './restores';
import { deferred, job, record as archivedRecord } from './selection.fixtures';
import { createReceiptDisplay, type ReceiptDisplay } from './receiptDisplay';

const NOW = Date.parse('2026-10-11T12:00:00Z');
const bundle: CadReturnBundle = {
  name: 'speaker.wgreturn', bundlePath: 'wgreturn/speaker.wgreturn', modifiedAt: new Date(NOW).toISOString(),
  readable: true, documentName: 'Speaker', requestId: null, sourceCount: 1, instanceCount: 1, designIds: [],
  sources: [{ id: 'hf', role: 'HF', required: true, suggestedResolutionMm: 3, defaultDriveChannelId: 'drive-hf' }],
};
function receipt(cursor = 1, overrides: Partial<CadReceipt> = {}): CadReceipt {
  return { receipt_id: `send:${cursor}`, send_id: `send-${cursor}`, send_sequence: cursor, project_id: 'project-a', design_ids: [], ingest_id: null,
    outcome: 'accepted', reason: null, message: null, created_at: new Date(NOW).toISOString(), sent_at: new Date(NOW).toISOString(),
    cursor, bundle_path: bundle.bundlePath, manifest_sha256: 'sha256:m', document_name: 'Speaker', retained_copy: true, refusal: null, ...overrides };
}
function ingestion(request: CadReturnIngestRequest): CadReturnIngestRecord {
  return { ingest_id: 'wgi_one', created_at: '', return_id: '', manifest_sha256: 'sha256:m', artifact_sha256: 'sha256:a', report_sha256: 'sha256:r',
    acoustic_domain: 'free-space', scope: { status: 'complete', degraded_skip_count: 0 }, sources: [],
    mesh_sizes: { rigid_size_mm: request.mesh.rigidSizeMm, transition_mm: request.mesh.transitionMm, source_size_mm: request.mesh.sourceSizeMm },
    skipped_source_ids: [], freshness: { verdict: 'per-instance', instances: [] }, findings: [],
    symmetry: {}, healing: {}, sizing_estimate: {}, polar_grid_derivation: {}, tag_map: {},
    project: { lineage_id: 'project-a', design_id: null, document_native_id: null, document_name: 'Speaker', archive_stem: null } };
}
function serving(items = [bundle], configured = true, hash = 'sha256:m') {
  return vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    if (String(url) === '/api/cadlink/returns') return new Response(JSON.stringify({ items, cadFolderConfigured: configured }));
    if (String(url) === '/api/cadlink/ingest') {
      const request = JSON.parse(String(init?.body)) as CadReturnIngestRequest;
      return new Response(JSON.stringify({ ...ingestion(request), manifest_sha256: hash }));
    }
    throw new Error(`Unexpected request: ${String(url)}`);
  });
}
function renderer(): ReceiptDisplay {
  return {
    enterCadWorkspace: vi.fn(() => { workspaceModeStore.setMode('cad'); }),
    showIngestedMesh: vi.fn(async (record, name, _notice, _fetcher, generation) => {
      importedMeshStore.setCad({ source: 'cad', ingestId: record.ingest_id, name } as never, generation, true);
    }),
  };
}

it('does not front an older Send after a newer Fusion solve claimed the viewport', async () => {
  const store = createCadReceiptsStore(null, () => NOW);
  const display = renderer();
  display.automaticSelectionAt = () => NOW + 1;
  await expect(createReceiptDisplay(display, store, serving())(receipt())).resolves.toBe(true);
  expect(display.showIngestedMesh).not.toHaveBeenCalled();
  expect(store.getState().feedback['send:1'].outcome).toBe('superseded');
});

it('cancels an in-flight Send when a newer Fusion solve claims its viewport', async () => {
  const store = createCadReceiptsStore(null, () => NOW);
  const display = renderer(); let solveAt = 0;
  display.automaticSelectionAt = () => solveAt;
  const response = deferred<Response>();
  const action = createReceiptDisplay(display, store, vi.fn(() => response.promise))(receipt());
  solveAt = NOW + 1;
  response.resolve(new Response(JSON.stringify({ items: [bundle], cadFolderConfigured: true })));
  await expect(action).resolves.toBe(true);
  expect(display.showIngestedMesh).not.toHaveBeenCalled();
  expect(store.getState().feedback['send:1'].outcome).toBe('superseded');
});

function editedParametricState() {
  useDesignStore.getState().updateValue('length', 180);
  useDesignStore.getState().updateValue('length', 190);
  useDesignStore.getState().undo();
  const before = parametricState();
  expect(before.past.length).toBeGreaterThan(0);
  expect(before.future.length).toBeGreaterThan(0);
  return before;
}
function parametricState() {
  const history = useDesignStore.temporal.getState();
  return { design: useDesignStore.getState().design, revision: useDesignStore.getState().designRevision,
    past: history.pastStates, future: history.futureStates };
}

beforeEach(() => {
    sessionStorage.clear(); localStorage.clear(); resetSolveOptionsStore(); resetDocumentStore(); resetCadReturnStore(); resetDesignStore();
    importedMeshStore.clear(); workspaceModeStore.setMode('parametric');
});

describe('receipt-keyed Send display', () => {
  it('ingests and fronts a receipt with no job once across reload; a new Send fronts the same model again', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const display = renderer();
    const fetcher = serving();
    const action = createReceiptDisplay(display, store, fetcher);
    await expect(action(receipt())).resolves.toBe(true);
    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe('wgi_one');
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe('wgi_one');
    expect(importedMeshStore.getSnapshot().showing).toBe('cad');
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(display.showIngestedMesh).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(['/api/cadlink/returns', '/api/cadlink/ingest']);
    const reloaded = createCadReceiptsStore(sessionStorage, () => NOW);
    const next = createReceiptDisplay(display, reloaded, fetcher);
    await next(receipt());
    expect(display.showIngestedMesh).toHaveBeenCalledTimes(1);
    await next(receipt(2));
    expect(display.showIngestedMesh).toHaveBeenCalledTimes(2);
    expect(reloaded.getState().fronted).toEqual(['send:1', 'send:2']);
  });

  it('coalesces concurrent copies of one receipt', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const display = renderer();
    const fetcher = serving();
    const action = createReceiptDisplay(display, store, fetcher);
    await Promise.all([action(receipt()), action(receipt())]);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(display.showIngestedMesh).toHaveBeenCalledOnce();
  });

  it('recovers and reports a refusal after reload with no job, operation store or coordinator', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const refused = receipt(1, { outcome: 'refused', reason: 'snapshot_invalid', message: 'invalid geometry' });
    store.getState().merge({ feed_id: 'feed-a', receipts: [refused], cursor: 1, has_more: false });
    const reloaded = createCadReceiptsStore(sessionStorage, () => NOW);
    const fetcher = serving();
    const display = renderer();
    await createReceiptDisplay(display, reloaded, fetcher)(reloaded.getState().receipts['send:1']);
    expect(reloaded.getState().feedback['send:1']).toEqual({ outcome: 'refused', message: expect.stringContaining('invalid geometry') });
    expect(fetcher).not.toHaveBeenCalled();
    expect(display.showIngestedMesh).not.toHaveBeenCalled();
    expect(createCadReceiptsStore(sessionStorage, () => NOW).getState().feedback['send:1'].message).toContain('invalid geometry');
  });

  it.each([
    ['missing_source', [], true, 'no longer in the WGLink folder'],
    ['unreadable', [{ ...bundle, readable: false, reason: 'bad manifest' }], true, 'unreadable: bad manifest'],
    ['retained_copy', [], false, 'no WGLink folder is selected'],
  ] as const)('reports %s distinctly and never opens the retained copy', async (outcome, items, configured, message) => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const display = renderer();
    const fetcher = serving([...items], configured);
    await createReceiptDisplay(display, store, fetcher)(receipt());
    expect(store.getState().feedback['send:1']).toEqual({ outcome, message: expect.stringContaining(message) });
    expect(display.showIngestedMesh).not.toHaveBeenCalled();
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(['/api/cadlink/returns']);
    expect(store.getState().fronted).toEqual(['send:1']);
  });

  it('a failed folder read stays pending with feedback and can succeed after reload', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    store.getState().merge({ feed_id: 'feed-a', receipts: [receipt()], cursor: 1, has_more: false });
    const display = renderer();
    const failed = vi.fn().mockRejectedValue(new Error('folder offline'));
    await expect(createReceiptDisplay(display, store, failed)(receipt())).resolves.toBe(false);
    expect(store.getState().feedback['send:1'].message).toContain('folder offline');
    expect(store.getState().fronted).toEqual([]);
    const reloaded = createCadReceiptsStore(sessionStorage, () => NOW);
    await createReceiptDisplay(display, reloaded, serving())(receipt());
    expect(reloaded.getState().receipts).toEqual({});
    expect(display.showIngestedMesh).toHaveBeenCalledOnce();
  });

  it('carries mesh, driver, channel and solve settings for a compatible project arrival through cadReturn', async () => {
    useCadReturnStore.getState().selectArrivedBundle(bundle, 'project-a');
    const state = useCadReturnStore.getState();
    state.setRigidSize(17); state.setSourceSize('hf', 6); state.setDriveVoltage(4);
    state.setSweep({ frequencyStartHz: 80, frequencyEndHz: 9000, frequencyCount: 42 });
    state.setChannelDriverField('drive-hf', 're_ohm', 5);
    const before = useCadReturnStore.getState();
    const fetcher = serving([{ ...bundle, bundlePath: 'wgreturn/new.wgreturn' }]);
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    await createReceiptDisplay(renderer(), store, fetcher)(receipt(1, { bundle_path: 'wgreturn/new.wgreturn' }));
    const held = useCadReturnStore.getState();
    expect(held.rigidSizeMm).toBe(17); expect(held.sourceSizesMm.hf).toBe(6); expect(held.driveVoltageV).toBe(4);
    expect(held.frequencyCount).toBe(42); expect(held.channelDrivers).toEqual(before.channelDrivers);
    expect(held.driveChannels).toEqual(before.driveChannels);
    const request = JSON.parse(String(fetcher.mock.calls[1][1]?.body));
    expect(request.mesh.rigidSizeMm).toBe(17); expect(request.mesh.sourceSizeMm.hf).toBe(6);
  });

  it('resets settings when the source inventory changes through cadReturn', async () => {
    useCadReturnStore.getState().selectArrivedBundle(bundle, 'project-a');
    useCadReturnStore.getState().setDriveVoltage(4);
    useCadReturnStore.getState().setRigidSize(17);
    useCadReturnStore.getState().setChannelDriverField('drive-hf', 're_ohm', 5);
    const changed = { ...bundle, sources: [{ ...bundle.sources[0], id: 'new-hf' }] };
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    await createReceiptDisplay(renderer(), store, serving([changed]))(receipt());
    expect(useCadReturnStore.getState().driveVoltageV).not.toBe(4);
    expect(useCadReturnStore.getState().rigidSizeMm).toBe(3);
    expect(useCadReturnStore.getState().channelDrivers).toEqual({});
  });

  it('a recovered receipt for another project leaves the parametric document, undo history and CAD selection intact', async () => {
    useDocumentStore.setState({ identity: { designId: 'design-a', lineageId: 'project-a' } as never });
    const document = useDocumentStore.getState();
    const parametric = editedParametricState();
    useCadReturnStore.getState().selectBundle(bundle, 'project-a');
    const selected = useCadReturnStore.getState().selectedBundle;
    const foreign = { ...bundle, designIds: ['design-b'] };
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    await createReceiptDisplay(renderer(), store, serving([foreign]))(receipt(1, { project_id: 'project-b', design_ids: ['design-b'] }));
    expect(useDocumentStore.getState()).toBe(document);
    expect(useCadReturnStore.getState().selectedBundle).toBe(selected);
    expect(parametricState()).toEqual(parametric);
    expect(store.getState().feedback['send:1'].message).toContain('another CAD-linked project');
  });

  it('displaying a matching Send leaves the parametric design and undo history intact', async () => {
    const document = useDocumentStore.getState();
    const parametric = editedParametricState();
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    await createReceiptDisplay(renderer(), store, serving())(receipt());
    expect(useDocumentStore.getState()).toBe(document);
    expect(parametricState()).toEqual(parametric);
  });

  it('does not replace a manual choice made after the Send', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const display = { ...renderer(), manualSelectionAt: () => NOW + 1 };
    await createReceiptDisplay(display, store, serving())(receipt());
    expect(useCadReturnStore.getState().selectedBundle).toBeNull();
    expect(display.showIngestedMesh).not.toHaveBeenCalled();
    expect(store.getState().feedback['send:1'].outcome).toBe('superseded');
    await createReceiptDisplay(display, store, serving())(receipt(2, { sent_at: new Date(NOW + 2).toISOString() }));
    expect(display.showIngestedMesh).toHaveBeenCalledOnce();
    expect(store.getState().feedback['send:2'].outcome).toBe('accepted');
  });

  it.each(['manual selection', 'newer Send'] as const)('fences a late listing behind a %s', async (change) => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const display = renderer();
    let resolve!: (value: Response) => void;
    const fetcher = vi.fn().mockImplementationOnce(() => new Promise<Response>((done) => { resolve = done; }))
      .mockImplementation(serving());
    const action = createReceiptDisplay(display, store, fetcher);
    const old = action(receipt(1));
    const manual = { ...bundle, bundlePath: 'wgreturn/manual.wgreturn' };
    if (change === 'manual selection') useCadReturnStore.getState().selectBundle(manual);
    else await action(receipt(2));
    resolve(new Response(JSON.stringify({ items: [bundle], cadFolderConfigured: true })));
    await old;
    expect(store.getState().feedback['send:1'].outcome).toBe('superseded');
    if (change === 'manual selection') {
      expect(display.showIngestedMesh).not.toHaveBeenCalled();
      expect(useCadReturnStore.getState().selectedBundle).toBe(manual);
    } else {
      expect(display.showIngestedMesh).toHaveBeenCalledOnce();
      expect(store.getState().feedback['send:2'].outcome).toBe('accepted');
    }
  });

  it('does not let an earlier Send that settles late replace a newer Send, also across reload', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const display = renderer();
    const newer = receipt(1, { send_sequence: 20 });
    store.getState().merge({ feed_id: 'feed-a', receipts: [newer], cursor: 1, has_more: false });
    await createReceiptDisplay(display, store, serving())(newer);
    const reloaded = createCadReceiptsStore(sessionStorage, () => NOW);
    await createReceiptDisplay(display, reloaded, serving())(receipt(2, { send_sequence: 10 }));
    expect(display.showIngestedMesh).toHaveBeenCalledOnce();
    expect(reloaded.getState().feedback['send:2'].outcome).toBe('superseded');
  });

  it('fences a late ingest behind a newer selection and viewport choice', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const display = renderer();
    let resolve!: (value: Response) => void;
    const request = serving();
    const fetcher = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => String(url) === '/api/cadlink/ingest'
      ? new Promise<Response>((done) => { resolve = done; }) : request(url, init));
    const pending = createReceiptDisplay(display, store, fetcher)(receipt());
    await vi.waitFor(() => expect(resolve).toBeDefined());
    useCadReturnStore.getState().selectBundle({ ...bundle, bundlePath: 'wgreturn/manual.wgreturn' });
    importedMeshStore.beginIntent();
    resolve(await serving()('/api/cadlink/ingest', { body: JSON.stringify({ mesh: { rigidSizeMm: 3, transitionMm: 3, sourceSizeMm: {} } }) }));
    await pending;
    expect(display.showIngestedMesh).not.toHaveBeenCalled();
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe('wgreturn/manual.wgreturn');
    expect(useCadReturnStore.getState().ingestRecord).toBeNull();
  });

  it('does not display a folder entry that changed since the accepted Send', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const display = renderer();
    await createReceiptDisplay(display, store, serving([bundle], true, 'sha256:changed'))(receipt());
    expect(display.showIngestedMesh).not.toHaveBeenCalled();
    expect(useCadReturnStore.getState().ingestRecord).toBeNull();
    expect(store.getState().feedback['send:1'].outcome).toBe('unreadable');
  });
});

describe('receipt display review regressions', () => {
  beforeEach(() => {
    sessionStorage.clear(); localStorage.clear(); resetDocumentStore(); resetCadReturnStore();
    resetDesignStore(); importedMeshStore.clear(); workspaceModeStore.setMode('parametric');
  });

  it('reports a deterministic ingest refusal once and still opens a later Send across reload', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const items = [receipt(1), receipt(2)];
    store.getState().merge({ feed_id: 'feed-a', receipts: items, cursor: 2, has_more: false });
    let ingests = 0;
    const good = serving();
    const fetcher = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (String(url) === '/api/cadlink/ingest' && ++ingests === 1)
        return new Response(JSON.stringify({ detail: 'invalid geometry' }), { status: 422 });
      return good(url, init);
    });
    const display = renderer();
    const action = createReceiptDisplay(display, store, fetcher);
    await expect(action(items[0])).resolves.toBe(true);
    expect(store.getState().feedback['send:1'].outcome).toBe('refused');
    expect(store.getState().feedback['send:1'].message).toContain('invalid geometry');
    await expect(action(items[1])).resolves.toBe(true);
    const reload = createCadReceiptsStore(sessionStorage, () => NOW);
    await createReceiptDisplay(display, reload, fetcher)(items[0]);
    expect(ingests).toBe(2);
    expect(display.showIngestedMesh).toHaveBeenCalledOnce();
  });

  it.each([503, 429])('keeps a transient ingest HTTP %s pending', async (status) => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const good = serving();
    const fetcher = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => String(url) === '/api/cadlink/ingest'
      ? new Response(JSON.stringify({ detail: 'temporarily unavailable' }), { status }) : good(url, init));
    await expect(createReceiptDisplay(renderer(), store, fetcher)(receipt())).resolves.toBe(false);
    expect(store.getState().fronted).toEqual([]);
  });

  it('fronts an unexpected display exception instead of replaying ingestion forever', async () => {
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    const display = { ...renderer(), showIngestedMesh: vi.fn(async () => { throw new Error('invalid mesh'); }) };
    const fetcher = serving();
    const action = createReceiptDisplay(display, store, fetcher);
    await expect(action(receipt())).resolves.toBe(true);
    await action(receipt());
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(store.getState().feedback['send:1'].message).toContain('invalid mesh');
  });

  it('accepts a Fusion-native document with different lineage while a WG design is open', async () => {
    useDocumentStore.setState({ identity: { designId: 'design-a', lineageId: 'project-a' } as never });
    const parametric = editedParametricState();
    const display = renderer();
    const store = createCadReceiptsStore(sessionStorage, () => NOW);
    await createReceiptDisplay(display, store, serving([{ ...bundle, designIds: [] }]))(receipt(1, { project_id: 'native-project' }));
    expect(display.showIngestedMesh).toHaveBeenCalledOnce();
    expect(store.getState().feedback['send:1'].outcome).toBe('accepted');
    expect(parametricState()).toEqual(parametric);
  });
});

it.each(['listing revision', 'mesh edit'] as const)('displays a delayed receipt through a background %s (row 5)', async (change) => {
  useCadReturnStore.getState().selectBundle(bundle);
  let release!: (response: Response) => void;
  const listing = new Promise<Response>((resolve) => { release = resolve; });
  const rest = serving();
  const fetcher: typeof fetch = (input, init) => String(input).endsWith('/returns') ? listing : rest(input, init);
  const store = createCadReceiptsStore(null, () => NOW);
  const item = receipt(); store.getState().merge({ feed_id: 'background', receipts: [item], cursor: 1, has_more: false });
  const display = renderer(); const pending = createReceiptDisplay(display, store, fetcher)(item);
  if (change === 'listing revision') useCadReturnStore.getState().refreshSelectedBundle({ ...bundle, modifiedAt: new Date(NOW + 1_000).toISOString() });
  else useCadReturnStore.getState().setRigidSize(9);
  release(new Response(JSON.stringify({ items: [bundle], cadFolderConfigured: true })));
  await pending;
  expect(display.showIngestedMesh).toHaveBeenCalledOnce();
  expect(store.getState().feedback[item.receipt_id].outcome).toBe('accepted');
});


it('a delayed receipt yields to an archived run as soon as its restore begins (rows 5, 24)', async () => {
  resetDocumentStore(); resetDesignStore(); resetCadReturnStore(); importedMeshStore.clear();
  const folder = deferred<Response>(); const archive = deferred<Response>();
  const store = createCadReceiptsStore(null, () => NOW);
  const display = renderer();
  const sending = createReceiptDisplay(display, store, () => folder.promise)(receipt());
  const restoring = restoreCadJobModel(job(), renderer(), () => archive.promise);
  folder.resolve(new Response(JSON.stringify({ items: [bundle], cadFolderConfigured: true })));
  await sending;
  expect(display.showIngestedMesh).not.toHaveBeenCalled();
  expect(store.getState().feedback['send:1'].outcome).toBe('superseded');
  archive.resolve(new Response(JSON.stringify(archivedRecord))); expect(await restoring).toBe(true);
  expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(archivedRecord.ingest_id);
});


it('fronts each receipt once when session storage is blocked (row 1)', async () => {
  const blocked: Storage = { length: 0, clear: () => undefined, key: () => null, removeItem: () => undefined,
    getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } };
  const store = createCadReceiptsStore(blocked, () => NOW);
  const display = renderer();
  const action = createReceiptDisplay(display, store, serving());
  await action(receipt()); await action(receipt());
  expect(display.showIngestedMesh).toHaveBeenCalledOnce();
  await action(receipt(2)); await action(receipt(2));
  expect(display.showIngestedMesh).toHaveBeenCalledTimes(2);
});


it('keeps the first Send with a tied timestamp when acceptance sequence is unavailable, also after reload (row 5)', async () => {
  const store = createCadReceiptsStore(sessionStorage, () => NOW);
  const display = renderer();
  await createReceiptDisplay(display, store, serving())(receipt(1, { send_sequence: null }));
  const reloaded = createCadReceiptsStore(sessionStorage, () => NOW);
  await createReceiptDisplay(display, reloaded, serving())(receipt(2, { send_sequence: null }));
  expect(display.showIngestedMesh).toHaveBeenCalledOnce();
  expect(reloaded.getState().feedback['send:2'].outcome).toBe('superseded');
});

it('retries its own receipt with no acceptance sequence after a transient listing failure (row 7)', async () => {
  const store = createCadReceiptsStore(null, () => NOW);
  const display = renderer();
  const good = serving();
  const fetcher = vi.fn<typeof fetch>().mockRejectedValueOnce(new Error('offline')).mockImplementation(good);
  const action = createReceiptDisplay(display, store, fetcher);
  expect(await action(receipt(1, { send_sequence: null }))).toBe(false);
  expect(await action(receipt(1, { send_sequence: null }))).toBe(true);
  expect(display.showIngestedMesh).toHaveBeenCalledOnce();
});

it('does not let a delayed CAD listing replace a newer file selection', async () => {
  const store = createCadReceiptsStore(null, () => NOW);
  const display = renderer();
  let resolve!: (response: Response) => void;
  const wait = new Promise<Response>(done => { resolve = done; });
  const fetcher = vi.fn<typeof fetch>(async () => wait);
  workspaceModeStore.setImportedInput('cad');
  const opening = createReceiptDisplay(display, store, fetcher)(receipt());
  workspaceModeStore.setImportedInput('file');
  resolve(new Response(JSON.stringify({ items: [bundle], cadFolderConfigured: true })));
  await opening;
  expect(display.showIngestedMesh).not.toHaveBeenCalled();
  expect(store.getState().feedback['send:1'].outcome).toBe('superseded');
  workspaceModeStore.setImportedInput('cad');
});
