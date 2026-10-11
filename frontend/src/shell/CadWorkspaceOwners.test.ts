import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cadCoordinationStore, resetCadCoordinationForTests } from '../api/cadCoordination';
import { jobsSocket, type CadOperationListener } from '../api/jobsSocket';
import { cadReceiptsStore, type CadReceipt } from '../stores/cadReceipts';
import { useCadConnectionStore } from '../stores/cadConnection';
import { cadRecoveryNoticesStore } from '../stores/cadRecoveryNotices';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { rememberCadProject } from '../stores/cadProjectMemory';
import { designForFamily, useDesignStore } from '../stores/design';
import { resetCadDeliveryHealthStore, useCadDeliveryHealthStore } from '../stores/cadDeliveryHealth';
import { cadWorkspace } from '../stores/cadWorkspace';
import { resetDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { preferencesStore } from '../prefs/preferences';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { mountCadWorkspaceOwners } from './CadWorkspaceOwners';
import { jobsCoordinatorBridge } from './JobsCoordinator';
import { identity, status, exported } from './cadlink/outbound.fixtures';
import { bundle, deferred, json, mesh, record } from './cadlink/selection.fixtures';

let stop: (() => void) | undefined;
let returns: typeof bundle[];
let receipts: CadReceipt[];
let channels: CadOperationListener[];
let fetcher: ReturnType<typeof vi.fn<typeof fetch>>;
const flush = () => vi.advanceTimersByTimeAsync(0);
const calls = (path: string) => fetcher.mock.calls.filter(([input]) => String(input) === path);
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime('2026-10-11T12:00:00Z');
  localStorage.clear(); sessionStorage.clear(); preferencesStore.resetForTests();
  resetCadCoordinationForTests();
  resetDesignStore(); resetDocumentStore(); resetCadReturnStore(); resetSolveOptionsStore();
  importedMeshStore.clear(); useCadConnectionStore.invalidate(); workspaceModeStore.setMode('parametric');
  useDocumentStore.getState().setCadLink(identity, 'current');
  resetCadDeliveryHealthStore();
  cadRecoveryNoticesStore.setState({ notices: {}, recoveryError: null });
  cadReceiptsStore.setState({ feedId: null, cursor: 0, since: Date.now() - 600_000, receipts: {}, fronted: [], frontedBundles: [],
    feedback: {}, newestSequence: 0, newestSentAt: 0, intakeRefusals: [], displayRetries: {}, recoveryError: null });
  returns = []; receipts = []; channels = [];
  vi.spyOn(jobsSocket, 'subscribeCadOperations').mockImplementation((listener) => { channels.push(listener); return () => undefined; });
  vi.spyOn(jobsSocket, 'refresh').mockResolvedValue(undefined);
  fetcher = vi.fn(async (input, init) => {
    const path = String(input);
    if (path === '/api/cadlink/returns') return json({ items: returns, cadFolderConfigured: true, coordination: 'on' });
    if (path === '/api/cadlink/fusion-status') return json(status());
    if (path.includes('/receipts?')) return json({ feed_id: 'cutover', receipts, cursor: receipts.at(-1)?.cursor ?? 0, has_more: false });
    if (path === '/api/cadlink/recovery-notices') return json({ notices: [] });
    if (path === '/api/cadlink/request-fusion-return') return json({ requestId: 'pull-1', documentName: 'Speaker' });
    if (path === '/api/cadlink/return-requests/pull-1') return json({ requestId: 'pull-1', state: 'processing', reason: null, message: null });
    if (path === '/api/cadlink/ingest') return json(record);
    if (path.endsWith('/viewport-mesh') || path.endsWith('/mesh')) return new Response(mesh);
    throw new Error(`Unexpected request ${path} ${init?.method ?? 'GET'}`);
  });
  vi.stubGlobal('fetch', fetcher);
});
afterEach(async () => {
  stop?.(); stop = undefined;
  await flush(); importedMeshStore.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();
});

it('gives a correlated pull one selection and ingest owner despite simultaneous observation listings (rows 5, 25)', async () => {
  stop = mountCadWorkspaceOwners(); await flush();
  const first = cadWorkspace.getSnapshot().pullFromFusion();
  expect(cadWorkspace.getSnapshot().pullFromFusion()).toBe(first);
  await flush();
  returns = [{ ...bundle, requestId: 'pull-1', modifiedAt: new Date().toISOString() }];
  await cadWorkspace.getSnapshot().refresh({ background: true, autoOpenNew: true });
  await first; await flush();
  expect(calls('/api/cadlink/request-fusion-return')).toHaveLength(1);
  expect(calls('/api/cadlink/ingest')).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(5_000);
  expect(calls('/api/cadlink/ingest')).toHaveLength(1);
  expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(record.ingest_id);
  expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe(record.ingest_id);
  expect(fetcher.mock.calls.every(([input]) => !String(input).includes('/api/cadlink/live'))).toBe(true);
});

it('Bring in and solve joins the arrival preparation and submits once through the job owner (rows 18, 25)', async () => {
  const solve = vi.fn(async () => 'submitted' as const);
  vi.spyOn(jobsCoordinatorBridge, 'getSnapshot').mockReturnValue({ ...jobsCoordinatorBridge.getSnapshot(), solveCurrentCadImport: solve });
  stop = mountCadWorkspaceOwners(); await flush();
  const action = cadWorkspace.getSnapshot().pullAndSolve(); await flush();
  returns = [{ ...bundle, requestId: 'pull-1' }];
  await cadWorkspace.getSnapshot().refresh({ background: true, autoOpenNew: true });
  expect(await action).toBe('solving');
  expect(calls('/api/cadlink/ingest')).toHaveLength(1);
  expect(solve).toHaveBeenCalledOnce();
});

it('mounts the receipt feed and display without a solve or operations-list reader (rows 1, 7, 14)', async () => {
  stop = mountCadWorkspaceOwners(); await flush();
  returns = [{ ...bundle, modifiedAt: new Date().toISOString() }];
  receipts = [{ receipt_id: 'receipt-1', send_id: 'send-1', send_sequence: 1, project_id: null, design_ids: [], ingest_id: null,
    outcome: 'accepted', reason: null, message: null, created_at: new Date().toISOString(), sent_at: new Date().toISOString(),
    cursor: 1, bundle_path: bundle.bundlePath, manifest_sha256: record.manifest_sha256, document_name: 'Speaker', retained_copy: true, refusal: null }];
  channels.forEach((channel) => channel.resync()); await flush();
  expect(calls('/api/cadlink/ingest')).toHaveLength(1);
  expect(calls('/api/cadlink/operations')).toHaveLength(0);
  expect(calls('/api/jobs/cad-solve')).toHaveLength(0);
  expect(cadWorkspace.getSnapshot().status).toContain('Received Speaker');
  channels.forEach((channel) => channel.resync()); await flush();
  expect(calls('/api/cadlink/ingest')).toHaveLength(1);
});


it('unmount fences a receipt listing that ignores cancellation before it can select or ingest (row 5)', async () => {
  stop = mountCadWorkspaceOwners(); await flush();
  const base = fetcher.getMockImplementation()!;
  let release!: (response: Response) => void;
  const hung = new Promise<Response>((resolve) => { release = resolve; });
  fetcher.mockImplementation((input, init) => String(input).endsWith('/returns') ? hung : base(input, init));
  receipts = [{ receipt_id: 'delayed', send_id: 'delayed', send_sequence: 1, project_id: null, design_ids: [], ingest_id: null,
    outcome: 'accepted', reason: null, message: null, created_at: new Date().toISOString(), sent_at: new Date().toISOString(),
    cursor: 1, bundle_path: bundle.bundlePath, manifest_sha256: record.manifest_sha256, document_name: 'Speaker', retained_copy: true, refusal: null }];
  channels.forEach((channel) => channel.resync()); await flush();
  stop(); stop = undefined;
  release(json({ items: [bundle], cadFolderConfigured: true })); await flush();
  expect(useCadReturnStore.getState().selectedBundle).toBeNull();
  expect(calls('/api/cadlink/ingest')).toHaveLength(0);
});


it('learns a missing coordination gate as off from the listing and stays idle without a gate request (row 10)', async () => {
  const base = fetcher.getMockImplementation()!;
  fetcher.mockImplementation((input, init) => String(input).endsWith('/returns')
    ? Promise.resolve(json({ items: [], cadFolderConfigured: true })) : base(input, init));
  stop = mountCadWorkspaceOwners(); await flush();
  expect(cadCoordinationStore.getSnapshot()).toBe('off');
  const statusReads = calls('/api/cadlink/fusion-status').length;
  const returnReads = calls('/api/cadlink/returns').length;
  await vi.advanceTimersByTimeAsync(600_000);
  expect(calls('/api/cadlink/fusion-status')).toHaveLength(statusReads);
  expect(calls('/api/cadlink/returns')).toHaveLength(returnReads);
  expect(fetcher.mock.calls.every(([input]) => !String(input).includes('solve-command'))).toBe(true);
});


it('a refused foreign receipt never reopens or prepares the remembered project (rows 22, 23)', async () => {
  stop = mountCadWorkspaceOwners(); await flush();
  rememberCadProject('remembered');
  returns = [{ ...bundle, designIds: ['another-design'] }];
  receipts = [{ receipt_id: 'foreign', send_id: 'foreign', send_sequence: 1, project_id: null, design_ids: [], ingest_id: null,
    outcome: 'accepted', reason: null, message: null, created_at: new Date().toISOString(), sent_at: new Date().toISOString(),
    cursor: 1, bundle_path: bundle.bundlePath, manifest_sha256: record.manifest_sha256, document_name: 'Speaker', retained_copy: true, refusal: null }];
  channels.forEach((channel) => channel.resync()); await flush();
  expect(cadWorkspace.getSnapshot().error).toContain('belongs to another CAD-linked project');
  expect(calls('/api/cadlink/designs')).toHaveLength(0);
  expect(calls('/api/cadlink/ingest')).toHaveLength(0);
  expect(useCadReturnStore.getState().selectedBundle).toBeNull();
});

it('opening an ordinary design returns to Parametric mode and invalidates held CAD state (row 5)', async () => {
  stop = mountCadWorkspaceOwners(); await flush();
  cadWorkspace.getSnapshot().selectBundle(bundle); await flush();
  expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
  useDesignStore.getState().replaceDesign(designForFamily('ICW')); await flush();
  expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
  expect(useCadReturnStore.getState().needsIngest).toBe(true);
});

it('legacy folder discovery selects and prepares a new arrival once without a receipt (rows 5, 10)', async () => {
  const base = fetcher.getMockImplementation()!;
  fetcher.mockImplementation((input, init) => String(input).endsWith('/fusion-status')
    ? Promise.resolve(json(status({ addinInboxTransfer: false }))) : base(input, init));
  stop = mountCadWorkspaceOwners(); await flush();
  returns = [{ ...bundle, modifiedAt: new Date().toISOString() }];
  await vi.advanceTimersByTimeAsync(2_500);
  expect(calls('/api/cadlink/ingest')).toHaveLength(1);
  expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(record.ingest_id);
  await vi.advanceTimersByTimeAsync(2_500);
  expect(calls('/api/cadlink/ingest')).toHaveLength(1);
});

const sendReceipt = (): CadReceipt => ({ receipt_id: 'receipt-1', send_id: 'send-1', send_sequence: 1,
  project_id: null, design_ids: [], ingest_id: null, outcome: 'accepted', reason: null, message: null,
  created_at: new Date().toISOString(), sent_at: new Date().toISOString(), cursor: 1,
  bundle_path: bundle.bundlePath, manifest_sha256: record.manifest_sha256, document_name: 'Speaker',
  retained_copy: true, refusal: null });

it.each(['pending reload', 'invalidated status'])('one Send has one ingest owner with %s (rows 1, 5)', async (scenario) => {
  if (scenario === 'pending reload') {
    returns = [{ ...bundle, modifiedAt: new Date().toISOString() }]; receipts = [sendReceipt()];
    cadReceiptsStore.setState({ feedId: 'cutover', cursor: 1, receipts: { 'receipt-1': receipts[0] } });
  }
  stop = mountCadWorkspaceOwners(); await flush();
  if (scenario === 'invalidated status') {
    useCadConnectionStore.invalidate();
    returns = [{ ...bundle, modifiedAt: new Date().toISOString() }]; receipts = [sendReceipt()];
    await cadWorkspace.getSnapshot().refresh({ background: true, autoOpenNew: true }); await flush();
  }
  const count = 1;
  expect(calls('/api/cadlink/ingest')).toHaveLength(count);
  expect(cadWorkspace.getSnapshot().status).toBe('Received Speaker from Fusion 360.');
  expect(Object.values(cadReceiptsStore.getState().feedback).every((item) => item.outcome === 'accepted')).toBe(true);
  // A later discovery cannot steal ownership from a fronted Send.
  await cadWorkspace.getSnapshot().refresh({ background: true, autoOpenNew: true }); await flush();
  expect(calls('/api/cadlink/ingest')).toHaveLength(count);
  expect(cadWorkspace.getSnapshot().status).not.toMatch(/superseded|Discarded/);
});

it.each(['mount', 'listing', 'ingest', 'mesh'])('Onshape gates Fusion receipts at %s (rows 1, 5)', async (phase) => {
  const selected = { ...bundle, bundlePath: 'onshape/speaker', bundleOrigin: 'onshape' as const };
  const setOnshape = () => { preferencesStore.update({ cadApplication: 'onshape' }); };
  useCadReturnStore.getState().selectBundle(selected);
  // Enable the optional adapter for this isolation test.
  preferencesStore.setOnshapeAvailable(true);
  if (phase === 'mount') setOnshape();
  stop = mountCadWorkspaceOwners(); await flush();
  const base = fetcher.getMockImplementation()!;
  const hung = deferred<Response>();
  const target = phase === 'listing' ? '/returns' : phase === 'ingest' ? '/ingest' : '/mesh';
  fetcher.mockImplementation((input, init) => String(input).endsWith(target) || (phase === 'mesh' && String(input).endsWith('/viewport-mesh'))
    ? hung.promise : base(input, init));
  returns = [{ ...bundle, modifiedAt: new Date().toISOString() }]; receipts = [sendReceipt()];
  channels.forEach((channel) => channel.resync()); await flush();
  if (phase !== 'mount') setOnshape();
  hung.resolve(phase === 'listing' ? json({ items: returns, cadFolderConfigured: true }) : phase === 'ingest' ? json(record) : new Response(mesh));
  await flush();
  expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(phase === 'mount' || phase === 'listing' ? selected.bundlePath : bundle.bundlePath);
  if (phase === 'ingest') expect(useCadReturnStore.getState().ingestRecord).toBeNull();
  expect(importedMeshStore.getSnapshot().cad).toBeNull();
  expect(cadReceiptsStore.getState().receipts['receipt-1']).toBeDefined();
  expect(cadReceiptsStore.getState().feedback).toEqual({});
  if (phase === 'mount' || phase === 'listing') expect(calls('/api/cadlink/ingest')).toHaveLength(0);
});

it('outbound sends keep status reads moving with coordination off until settlement (row 10)', async () => {
  const base = fetcher.getMockImplementation()!;
  fetcher.mockImplementation((input, init) => String(input) === '/api/cadlink/returns'
    ? Promise.resolve(json({ items: [], cadFolderConfigured: true, coordination: 'off' }))
    : String(input) === '/api/cad-workspace/path' ? Promise.resolve(json({ selected: true, path: 'exchange' }))
      : String(input).includes('/export/wglink') ? Promise.resolve(json({ ...exported, cadHandoff: 'published', cadHandoffRequestId: 'insert-1' }))
      : base(input, init));
  stop = mountCadWorkspaceOwners(); await flush();
  await cadWorkspace.getSnapshot().sendWgToFusion(); await flush();
  const sent = calls('/api/cadlink/fusion-status').length;
  await vi.advanceTimersByTimeAsync(30_000);
  expect(calls('/api/cadlink/fusion-status').length - sent).toBe(12);
  channels.forEach((channel) => channel.operation({ operationId: 'insert-1', kind: 'insert_link', state: 'accepted' } as never));
  const settled = calls('/api/cadlink/fusion-status').length;
  await vi.advanceTimersByTimeAsync(30_000);
  expect(calls('/api/cadlink/fusion-status')).toHaveLength(settled);
});

it('pending outbound operations restored by the existing operation reader resume status reads (row 10)', async () => {
  const base = fetcher.getMockImplementation()!;
  fetcher.mockImplementation((input, init) => String(input) === '/api/cadlink/returns'
    ? Promise.resolve(json({ items: [], cadFolderConfigured: true, coordination: 'off' })) : base(input, init));
  useCadDeliveryHealthStore.setState({ outboundOperations: { restored: { operationId: 'restored', kind: 'update_link', state: 'processing' } as never } });
  stop = mountCadWorkspaceOwners(); await flush();
  const before = calls('/api/cadlink/fusion-status').length;
  await vi.advanceTimersByTimeAsync(5_000);
  expect(calls('/api/cadlink/fusion-status').length - before).toBe(2);
});

it('a pull settling while reconnect waits for receipts clears recovery warnings through retries and Refresh (row 25)', async () => {
  stop = mountCadWorkspaceOwners(); await flush();
  const pull = cadWorkspace.getSnapshot().pullFromFusion(); await flush();
  const base = fetcher.getMockImplementation()!;
  const feed = deferred<Response>();
  fetcher.mockImplementation((input, init) => String(input).includes('/receipts?') ? feed.promise : base(input, init));
  channels.forEach((channel) => channel.resync()); await flush();
  returns = [{ ...bundle, requestId: 'pull-1' }];
  await vi.advanceTimersByTimeAsync(2_500); await pull;
  fetcher.mockImplementation(base);
  feed.resolve(json({ feed_id: 'cutover', receipts: [], cursor: 0, has_more: false })); await flush();
  expect(useCadConnectionStore.getState().recoveryError).toBeNull();
  await vi.advanceTimersByTimeAsync(5_000);
  await cadWorkspace.getSnapshot().refresh(); await flush();
  expect(useCadConnectionStore.getState().recoveryError).toBeNull();
});

it.each(['Onshape', 'hidden'])('clears initial model loading when mounted in %s (row 10)', async (mode) => {
  if (mode === 'Onshape') { preferencesStore.setOnshapeAvailable(true); preferencesStore.update({ cadApplication: 'onshape' }); }
  else vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  stop = mountCadWorkspaceOwners(); await flush();
  expect(cadWorkspace.getSnapshot().loading).toBe(false);
});

it('pull-and-solve reports preparation and submission phases and clears stale status on error (rows 18, 25)', async () => {
  const ingest = deferred<Response>(); const submit = deferred<'submitted'>();
  const base = fetcher.getMockImplementation()!;
  fetcher.mockImplementation((input, init) => String(input) === '/api/cadlink/ingest' ? ingest.promise : base(input, init));
  vi.spyOn(jobsCoordinatorBridge, 'getSnapshot').mockReturnValue({ ...jobsCoordinatorBridge.getSnapshot(), solveCurrentCadImport: () => submit.promise });
  stop = mountCadWorkspaceOwners(); await flush();
  const solve = cadWorkspace.getSnapshot().pullAndSolve(); await flush();
  returns = [{ ...bundle, requestId: 'pull-1' }];
  await cadWorkspace.getSnapshot().refresh(); await flush();
  expect(cadWorkspace.getSnapshot().status).toContain('Preparing the simulation');
  ingest.resolve(json(record)); await flush();
  expect(cadWorkspace.getSnapshot().status).toContain('Submitting the solve');
  submit.reject(new Error('Submission failed')); expect(await solve).toBe('blocked');
  expect(cadWorkspace.getSnapshot().status).toBeNull();
  expect(cadWorkspace.getSnapshot().error).toBe('Submission failed');
});

it('a return landing after the pull timeout is available to discovery (rows 5, 25)', async () => {
  const base = fetcher.getMockImplementation()!;
  fetcher.mockImplementation((input, init) => String(input).endsWith('/fusion-status')
    ? Promise.resolve(json(status({ addinInboxTransfer: false }))) : base(input, init));
  stop = mountCadWorkspaceOwners(); await flush();
  const pull = cadWorkspace.getSnapshot().pullFromFusion().catch((error: Error) => error); await flush();
  await vi.advanceTimersByTimeAsync(60_000); expect(await pull).toBeInstanceOf(Error);
  returns = [{ ...bundle, requestId: 'pull-1', modifiedAt: new Date().toISOString() }];
  await vi.advanceTimersByTimeAsync(2_500);
  expect(calls('/api/cadlink/ingest')).toHaveLength(1);
  expect(useCadReturnStore.getState().selectedBundle?.requestId).toBe('pull-1');
});

it('coalesces mount discovery/status and the initial socket hello (row 10)', async () => {
  stop = mountCadWorkspaceOwners();
  channels.forEach((channel) => channel.resync()); await flush();
  expect(calls('/api/cadlink/fusion-status')).toHaveLength(1);
  expect(calls('/api/cadlink/returns')).toHaveLength(1);
  expect(fetcher.mock.calls.filter(([input]) => String(input).includes('/receipts?'))).toHaveLength(1);
});

it.each(['listing change', 'Send'])('coordination on: %s resets quiet observation in the mounted owners (row 10)', async (activity) => {
  const base = fetcher.getMockImplementation()!;
  fetcher.mockImplementation((input, init) => String(input).endsWith('/fusion-status')
    ? Promise.resolve(json(status({ state: 'closed', processRunning: false, running: false, link: null })))
    : String(input) === '/api/cad-workspace/path' ? Promise.resolve(json({ selected: true, path: 'exchange' }))
      : String(input) === '/api/export/wglink' ? Promise.resolve(json(exported)) : base(input, init));
  stop = mountCadWorkspaceOwners(); await flush(); await vi.advanceTimersByTimeAsync(60_000);
  if (activity === 'listing change') {
    returns = [{ ...bundle, modifiedAt: new Date(Date.now() - 120_000).toISOString() }];
    await vi.advanceTimersByTimeAsync(30_000);
  } else await cadWorkspace.getSnapshot().sendWgToFusion();
  await flush(); const before = calls('/api/cadlink/fusion-status').length;
  expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
  await vi.advanceTimersByTimeAsync(2_500);
  expect(calls('/api/cadlink/fusion-status').length - before).toBe(1);
});

it('lists models for manual use when the receipt feed fails and never auto-ingests an unclaimed Send (rows 1, 5)', async () => {
  const base = fetcher.getMockImplementation()!;
  returns = [{ ...bundle, modifiedAt: new Date().toISOString() }];
  fetcher.mockImplementation((input, init) => String(input).includes('/receipts?') ? Promise.reject(new Error('offline')) : base(input, init));
  stop = mountCadWorkspaceOwners(); await flush();
  expect(cadWorkspace.getSnapshot().bundles).toHaveLength(1);
  expect(cadWorkspace.getSnapshot().loading).toBe(false);
  expect(calls('/api/cadlink/ingest')).toHaveLength(0);
  receipts = [sendReceipt()]; fetcher.mockImplementation(base);
  channels.forEach((channel) => channel.resync()); await flush();
  expect(calls('/api/cadlink/ingest')).toHaveLength(1);
  expect(cadWorkspace.getSnapshot().status).toBe('Received Speaker from Fusion 360.');
});

it('coalesces a delayed initial hello with mount status and discovery reads still in flight (row 10)', async () => {
  const base = fetcher.getMockImplementation()!;
  const statusRead = deferred<Response>(); const returnRead = deferred<Response>();
  fetcher.mockImplementation((input, init) => String(input).endsWith('/fusion-status') ? statusRead.promise
    : String(input).endsWith('/returns') ? returnRead.promise : base(input, init));
  stop = mountCadWorkspaceOwners(); await flush();
  channels.forEach((channel) => channel.resync()); await flush();
  expect(calls('/api/cadlink/fusion-status')).toHaveLength(1);
  expect(calls('/api/cadlink/returns')).toHaveLength(1);
  statusRead.resolve(json(status())); returnRead.resolve(json({ items: [], cadFolderConfigured: true })); await flush();
  expect(useCadConnectionStore.getState().fusionStatus?.state).toBe('stale');
});
