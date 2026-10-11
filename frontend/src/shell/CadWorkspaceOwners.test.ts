import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cadCoordinationStore, resetCadCoordinationForTests } from '../api/cadCoordination';
import { jobsSocket, type CadOperationListener } from '../api/jobsSocket';
import { cadReceiptsStore, type CadReceipt } from '../stores/cadReceipts';
import { useCadConnectionStore } from '../stores/cadConnection';
import { cadRecoveryNoticesStore } from '../stores/cadRecoveryNotices';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { rememberCadProject } from '../stores/cadProjectMemory';
import { designForFamily, useDesignStore } from '../stores/design';
import { cadWorkspace } from '../stores/cadWorkspace';
import { resetDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { preferencesStore } from '../prefs/preferences';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { mountCadWorkspaceOwners } from './CadWorkspaceOwners';
import { jobsCoordinatorBridge } from './JobsCoordinator';
import { identity, status } from './cadlink/outbound.fixtures';
import { bundle, json, mesh, record } from './cadlink/selection.fixtures';

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
  cadRecoveryNoticesStore.setState({ notices: {}, recoveryError: null });
  cadReceiptsStore.setState({ feedId: null, cursor: 0, since: Date.now() - 600_000, receipts: {}, fronted: [],
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
