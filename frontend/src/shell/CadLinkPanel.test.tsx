import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnIngestRecord, CadReturnListing, FusionCadStatus } from '../api/cadlink';
import { resetCadCoordinationForTests } from '../api/cadCoordination';
import type { CadOperationSummary } from '../api/cadOperations';
import { cadJobFixture, publishCadJobs } from '../jobs/cadSolve.fixtures';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { applyOpenedDesign } from '../design/openCadProject';
import type { OnshapeLink } from '../api/onshape';
import { preferencesStore } from '../prefs/preferences';
import { importedSubmissionBlocker } from '../jobs/importedSubmission';
import { expandLegacy, toWire, withDelayMode, withPair } from '../results/crossoverSpec';
import { cadRecoveryNoticesStore } from '../stores/cadRecoveryNotices';
import { useCadConnectionStore } from '../stores/cadConnection';
import { cadReceiptsStore } from '../stores/cadReceipts';
import { combineSpecEffective, resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { resetSolveOptionsStore, useImportedSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import meshFixture from '../viewport/test-fixtures/tagged_sources-small.msh?raw';
import { buildImportedSubmission, CadLinkPanel, declaredDomainPhrase, fusionWorkflowView, newestReturnArrival, onshapeWorkflowView, showIngestedMeshInViewport } from './CadLinkPanel';
import { cadWorkspace } from '../stores/cadWorkspace';
import { CadWorkspaceOwners } from './CadWorkspaceOwners';
import { JobsCoordinator } from './JobsCoordinator';
import { workspaceNavigation } from './workspaceNavigation';

const listing: CadReturnListing = {
  cadFolderConfigured: true,
  coordination: 'on',
  items: [{
    name: 'speaker.wgreturn', bundlePath: 'wgreturn/speaker.wgreturn', modifiedAt: '2026-08-11T00:00:00Z', readable: true,
    documentName: 'Speaker', requestId: null, sourceCount: 1, instanceCount: 1,
    sources: [{ id: 'source-hf', role: 'HF', required: true, suggestedResolutionMm: 4, defaultDriveChannelId: 'drive-hf' }],
  }],
};
const record: CadReturnIngestRecord = {
  ingest_id: 'wgi_01J5A8QK3M9T2XVBH0RD7NWE6C', created_at: '', return_id: '', manifest_sha256: `sha256:${'1'.repeat(64)}`, artifact_sha256: `sha256:${'2'.repeat(64)}`, report_sha256: `sha256:${'3'.repeat(64)}`,
  acoustic_domain: 'free-space', scope: { status: 'clean', degraded_skip_count: 0 },
  sources: [{ id: 'source-hf', role: 'HF', required: true, instance_id: null, default_drive_channel_id: 'drive-hf', suggested_resolution_mm: 4 }],
  mesh_sizes: { rigid_size_mm: 4, transition_mm: 4, source_size_mm: { 'source-hf': 4 } }, skipped_source_ids: [],
  freshness: { verdict: 'per-instance', instances: [{ instance_id: 'instance-a', verdict: 'design_changed' }] },
  findings: [{ id: 'finding-a', kind: 'freshness', blocking: true, verdict: 'design_changed' }],
  symmetry: { planes: { x0: { accepted: true }, y0: { accepted: false } }, cut_planes: ['x0'] }, healing: { performed: false, mode: 'none' },
  sizing_estimate: { triangles: 1200 },
  polar_grid_derivation: {
    axes: {
      horizontal: { plane: 'x0', symmetry_accepted: true, minimum_deg: 0, maximum_deg: 180, may_widen_not_narrow: true },
      vertical: { plane: 'y0', symmetry_accepted: false, minimum_deg: -180, maximum_deg: 180, may_widen_not_narrow: true },
      diagonal: { plane: 'x0+y0', symmetry_accepted: false, minimum_deg: -180, maximum_deg: 180, may_widen_not_narrow: true },
    },
    cut_planes: ['x0'],
  },
  tag_map: {},
};
const closedFusion: FusionCadStatus = {
  cadApplication: 'fusion360', state: 'closed', processRunning: false, running: false, updatedAt: null,
  cadFolderConfigured: true, cadFolderPath: '/cad',
  documentName: null, documentId: null, currentFormula: 'OSSE', fusionFormula: null, link: null,
  wgChangesAvailable: false, fusionChangesAvailable: false,
  documentChanged: false, documentChangeDetectable: false, staleDetectionExplanation: null,
  realizedDimensions: { state: 'link_unavailable', instanceId: null, exportId: null, parameters: [] },
};
const currentFusion: FusionCadStatus = {
  ...closedFusion,
  state: 'current', processRunning: true, running: true, updatedAt: new Date().toISOString(), documentName: 'Tritonia V', documentId: 'fusion:doc-a', fusionFormula: 'osse',
  link: {
    instanceId: 'instance-a', bundlePath: '/cad/wglink/horn.wglink', designId: 'wgd_a', lineageId: 'wgl_a', editVersion: '2',
    designHash: 'sha256:current', designName: 'Tritonia-V', formula: 'osse', configPresent: true, parameterCount: 13,
    parameterDriftCount: 0, localBodyState: 'unmodified',
    bodyFingerprintHash: 'sha256:body',
    documentSignatureHash: 'sha256:return-state', documentBodyCount: 3, sourceStateHash: 'sha256:sources',
    exportId: 'wge_2', exportSequence: '2',
  },
  realizedDimensions: { state: 'current', instanceId: 'instance-a', exportId: 'wge_2', parameters: [] },
};

/** The panel reads solver capabilities to report whether an imported model can
 * be solved here at all, so it needs the same query client the app provides. */
const capabilityClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function CadLinkTestSurface() {
  return <QueryClientProvider client={capabilityClient}><CadWorkspaceOwners/><CadLinkPanel/></QueryClientProvider>;
}

function FullCadLinkTestSurface() {
  return <QueryClientProvider client={capabilityClient}>
    <JobsCoordinator><CadWorkspaceOwners/><CadLinkPanel/></JobsCoordinator>
  </QueryClientProvider>;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

describe('CadLinkPanel', () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetCadReturnStore(); resetSolveOptionsStore(); resetDocumentStore(); resetDesignStore(); preferencesStore.resetForTests();
    capabilityClient.clear();
    publishCadJobs([]); sessionStorage.clear();
    reviewDetail = undefined; missingReview = false;
    useCadConnectionStore.setRecoveryError(null); cadReceiptsStore.setState({ recoveryError: null });
    fixtureOperations = {}; reportedRecovery = null; cadRecoveryNoticesStore.setState({ notices: {}, recoveryError: null });

    resetCadCoordinationForTests('on');
    workspaceModeStore.setMode('parametric');
    vi.spyOn(jobsSocket, 'start').mockImplementation(() => undefined);
    vi.spyOn(jobsSocket, 'stop').mockImplementation(() => undefined);
    vi.spyOn(jobsSocket, 'refresh').mockResolvedValue(undefined);
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/api/capabilities')) return json({ engines: [], femRegions: true });
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: Object.values(fixtureOperations).filter((item) => item.state === 'recovery_required' && ['insert_link', 'update_link'].includes(item.kind)).map(noticeFor) });
      return json(record);
    }));
  });
  afterEach(() => { act(() => root.unmount()); importedMeshStore.clear();  workspaceModeStore.setMode('parametric'); vi.restoreAllMocks(); vi.clearAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); host.remove(); });

  it.each([true, false, undefined])('shows the manual Update upgrade notice for capability %s', async (immutableBundles) => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/api/capabilities')) return json({ engines: [], femRegions: true });
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json({ ...currentFusion, immutableBundles });
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      return json(record);
    }));
    await act(async () => { root.render(<CadLinkTestSurface/>); });
    await vi.waitFor(() => expect(cadWorkspace.getSnapshot().fusionStatus?.running).toBe(true));
    const message = 'Update the Fusion add-in to let manual Update follow newer exports.';
    expect(host.textContent?.includes(message)).toBe(immutableBundles !== true);
    if (immutableBundles !== true) {
      expect(host.textContent).toContain('Choose Install add-in here, then restart Fusion. Automatic Send still works.');
      const install = Array.from(host.querySelectorAll('button')).find((button) => button.textContent === 'Install add-in');
      expect(install).toBeDefined();
      expect(host.textContent?.split(message)).toHaveLength(2);
    }
  });

  it('shows the missing WGLink path with direct repair and the shared chooser', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/fusion-status')) return json({ ...closedFusion, cadFolderConfigured: false, cadFolderMissingPath: '/chosen/cadlink' });
      if (path === '/api/folders/path') return json({ path: '/chosen', runs: '/chosen/runs', cadlink: '/chosen/cadlink', cadlinkExists: false, overrides: {} });
      if (path.endsWith('/returns')) return json({ items: [], cadFolderConfigured: false });
      return json(record);
    }));
    await act(async () => root.render(<CadLinkTestSurface/>));
    await vi.waitFor(() => expect(host.textContent).toContain('WGLink folder not found: /chosen/cadlink'));
    expect([...host.querySelectorAll('button')].some(button => button.textContent === 'Create it')).toBe(true);
    expect([...host.querySelectorAll('button')].some(button => button.textContent === 'Choose WG folder…')).toBe(true);
  });

  it('offers install and repair before any signal or selected folder', async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/install-addin')) return json({ verdict: 'pending', detail: 'Close Fusion to finish installing the Waveguide Generator add-in in Fusion.' });
      if (path.endsWith('/fusion-status')) return json({ ...closedFusion, cadFolderConfigured: false, addinRefresh: { verdict: 'not-in-use', detail: 'No signal' } });
      if (path.endsWith('/returns')) return json({ items: [], cadFolderConfigured: false, coordination: 'off' });
      return json(record);
    });
    vi.stubGlobal('fetch', fetcher);
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    const install = Array.from(host.querySelectorAll('button')).find((button) => button.textContent === 'Install add-in')!;
    expect(install).toBeDefined();
    expect(install.disabled).toBe(false);
    await act(async () => { install.click(); await Promise.resolve(); await Promise.resolve(); });
    expect(fetcher).toHaveBeenCalledWith('/api/cadlink/install-addin', { method: 'POST' });
    expect(host.textContent).toContain('Close Fusion to finish installing the Waveguide Generator add-in in Fusion.');
    expect(install.disabled).toBe(false);
  });

  it('replaces a deferred install answer with the status once the add-in is installed', async () => {
    let refresh = { verdict: 'pending', detail: 'Close Fusion to finish installing the Waveguide Generator add-in in Fusion.' };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/install-addin')) return json(refresh);
      if (path.endsWith('/fusion-status')) return json({ ...closedFusion, addinRefresh: refresh });
      if (path.endsWith('/returns')) return json(listing);
      return json(record);
    }));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    const install = Array.from(host.querySelectorAll('button')).find((button) => button.textContent === 'Install add-in')!;
    await act(async () => { install.click(); await Promise.resolve(); await Promise.resolve(); });
    const notice = () => host.querySelector('.cad-link-actions [role="status"]')?.textContent;
    expect(notice()).toBe('Close Fusion to finish installing the Waveguide Generator add-in in Fusion.');

    // Fusion closes and the start-up pass installs it with no further click.
    refresh = { verdict: 'installed', detail: 'The Waveguide Generator add-in in Fusion 0.3.3 was installed.' };
    await act(async () => {
      window.dispatchEvent(new Event('focus'));
      for (let turn = 0; turn < 8; turn += 1) await Promise.resolve();
    });
    expect(notice()).toBe('The Waveguide Generator add-in in Fusion 0.3.3 was installed.');
  });

  const openHistory = () => {
    const disclosure = host.querySelector<HTMLButtonElement>('.cad-history > .section-heading button')!;
    if (disclosure.getAttribute('aria-expanded') === 'false') act(() => disclosure.click());
    return disclosure;
  };

  const renderAndSelect = async (withOwner = false) => {
    await act(async () => { root.render(withOwner ? <FullCadLinkTestSurface/> : <CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    openHistory();
    const bundle = host.querySelector<HTMLButtonElement>('.cad-bundle-list button')!;
    act(() => bundle.click());
    return bundle;
  };

  const clickIngest = async () => {
    // Selecting a readable row now starts preparation. This helper retains its
    // old name so the workflow tests below stay compact, but only waits for the
    // automatic ingest and its viewport follow-up to settle.
    await act(async () => {
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });
  };

  it('shows that model settings are loading before preparation', async () => {
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    act(() => useCadReturnStore.setState({ setupLoading: true }));
    expect(host.textContent).toContain('Loading model settings…');
    act(() => useCadReturnStore.setState({ setupLoading: false }));
    expect(host.textContent).not.toContain('Loading model settings…');
  });

  it('explains which settings Fusion uses while the rail contains an incomplete draft', async () => {
    await renderAndSelect(); await clickIngest();
    act(() => useCadReturnStore.getState().setChannelDriverField('drive-hf', 'sd_cm2', 42));
    expect(host.textContent).toContain('Fusion solves use the last complete setup.');
    act(() => useCadReturnStore.getState().setChannelDriverField('drive-hf', 'sd_cm2', null));
    expect(host.textContent).not.toContain('Fusion solves use the last complete setup.');
  });

  it('lists a blocking finding for understanding without gating the solve', async () => {
    await renderAndSelect();
    await clickIngest();
    // Blocking findings inform; they never gate. The wire still records them.
    expect(importedSubmissionBlocker()).toBeNull();
    expect(host.querySelector('.cad-blocking-suffix')?.textContent).toContain('blocking');
    expect(host.querySelector('.cad-checks input[type="checkbox"]')).toBeNull();
    expect(host.querySelector('.cad-check-findings')?.textContent).toContain('freshness');
    // A record that needs attention arrives with its checklist open.
    expect(host.querySelector('.cad-checks .section-head')?.getAttribute('aria-expanded')).toBe('true');
    expect(host.querySelector('.cad-checks .cad-state-chip')?.textContent).toContain('need attention');
    for (const moved of ['Mesh detail', 'Crossover', 'Rebuild mesh']) {
      expect(host.textContent).not.toContain(moved);
    }
    // The Drivers section moved too, but the guide above names it in prose, so
    // this asks the DOM for its controls rather than for the word.
    expect(host.querySelector('.cad-channel')).toBeNull();
    expect(host.querySelector('[aria-label^="Drive channel for"]')).toBeNull();
    expect(host.textContent).not.toContain('Explicit solve sweep');
    expect([...host.querySelectorAll<HTMLButtonElement>('button')].some((button) => button.textContent === 'Solve CAD import')).toBe(false);
    expect(host.querySelector('.cad-viewport-source-buttons')).toBeNull();
  });

  const cadOperation = (overrides: Partial<CadOperationSummary> = {}): CadOperationSummary => ({
    operationId: 'op-1', kind: 'prepare_and_solve', state: 'needs_user_input', stage: 'ready',
    reason: 'ready_to_solve', message: 'Prepared, and waiting for you to start the solve.', jobId: null,
    attemptGeneration: 1, setupRevisionId: 'wgs_1', preparationId: 'wgp_1',
    snapshot: { manifestSha256: record.manifest_sha256, documentName: 'Speaker', projectLineageId: 'wgl_speaker' },
    legacy: false,
    createdAt: '2026-09-14T10:00:00Z', updatedAt: '2026-09-14T10:00:05Z',
    ...overrides,
  });

  let fixtureOperations: Record<string, CadOperationSummary> = {};
  let reportedRecovery: string | null = null;
  const noticeFor = (item: CadOperationSummary) => ({ id: item.operationId, kind: item.kind as 'insert_link' | 'update_link', document_id: null, document_name: item.snapshot?.documentName ?? null, created_at: item.createdAt ?? '', sequence: 1, active_document: item.operationId === reportedRecovery });
  let reviewDetail: Record<string, unknown> | undefined;
  let missingReview = false;
  function applyCadRequest(summary: CadOperationSummary): void {
    fixtureOperations[summary.operationId] = summary;
    if (summary.kind !== 'prepare_and_solve') {
      if (summary.state === 'recovery_required' && ['insert_link', 'update_link'].includes(summary.kind)) {
        cadRecoveryNoticesStore.setState((state) => ({ notices: { ...state.notices, [summary.operationId]: noticeFor(summary) } }));
      }
      return;
    }
    const item = cadJobFixture(summary);

    const preparation = reviewDetail?.preparation as { preparationId: string; blockingFindingIds: string[]; reportSha256: string } | undefined;
    if (preparation && item.cad_state) {
      item.cad_state.preparation = missingReview ? null : {
        preparation_id: preparation.preparationId, ingest_id: record.ingest_id, blocking_finding_ids: preparation.blockingFindingIds,
        report_sha256: preparation.reportSha256,
      };
    }
    const previous = jobsSocket.getSnapshot().jobs.filter((job) => job.cad_state?.operation_id !== summary.operationId && job.id !== item.id);
    publishCadJobs([...previous, item]);
  }

  const buttonTexts = (card: HTMLElement) => [...card.querySelectorAll<HTMLButtonElement>('button')].map((button) => button.textContent);
  const buttonLabels = (card: HTMLElement) => [...card.querySelectorAll<HTMLButtonElement>('button')].map((button) => button.getAttribute('aria-label'));
  const operationCard = (operationId: string) => host.querySelector<HTMLElement>(`[data-operation-id="${operationId}"]`)!;
  const buttonIn = (card: HTMLElement, text: string) => [...card.querySelectorAll<HTMLButtonElement>('button')]
    .find((button) => button.textContent === text);

  /** Routes the operation actions through the real coordinator and records
   * what they send. Prepare answers with the row as it was before the claim,
   * as the route does; `failPrepare` names operations whose prepare fails, and
   * the first `detailFailures` reads of `detail` fail. */
  const recordOperationRequests = (options: {
    detail?: Record<string, unknown>;
    detailFailures?: number;
    failPrepare?: string[];
    reconcileAccepted?: string[];
    setupEngine?: string;
    /** Answer an operation's detail from the operations this page holds, as the route does. */
    detailFromStore?: boolean;
  } = {}) => {
    const posted: Array<{ path: string; body: unknown }> = [];
    let detailFailures = options.detailFailures ?? 0;
    reviewDetail = options.detail; missingReview = detailFailures > 0;
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === '/api/cadlink/recovery-notices') {
        for (const id of options.reconcileAccepted ?? []) delete fixtureOperations[id];
        posted.push({ path, body: null });
        return json({ notices: Object.values(fixtureOperations).filter((item) => item.state === 'recovery_required' && ['insert_link', 'update_link'].includes(item.kind)).map(noticeFor) });
      }
      if (path.startsWith('/api/cadlink/recovery-notices/') && path.endsWith('/dismiss')) {
        const id = decodeURIComponent(path.split('/')[4]); delete fixtureOperations[id];
        posted.push({ path, body: null });
        return json({ id, dismissed: true, repaired: false });
      }
      if (/^\/api\/models\/[^/]+\/solve$/.test(path) && init?.method === 'POST') {
        posted.push({ path, body: JSON.parse(String(init?.body)) });
        return json({ job_id: 'job-model' });
      }
      if (/^\/api\/models\/[^/]+\/setup$/.test(path) && init?.method === 'PUT') {
        const body = { lineageId: decodeURIComponent(path.split('/')[3]), ...JSON.parse(String(init?.body)) };
        posted.push({ path, body });
        return json({ revision_id: 'wgs_9', content_sha256: 'sha256:c', inventory_sha256: 'sha256:i' });
      }
      if (path === '/api/cadlink/setup-revisions' && init?.method === 'POST') {
        posted.push({ path, body: JSON.parse(String(init.body)) });
        return json({ revisionId: 'wgs_manual', contentSha256: 'sha256:m', createdAt: '2026-09-14T10:00:00Z' });
      }
      if (path.startsWith('/api/cadlink/setup-revisions/')) {
        const revisionId = decodeURIComponent(path.split('/').at(-1)!);
        return json({
          revisionId, contentSha256: 'sha256:setup', createdAt: '2026-09-14T10:00:00Z',
          setup: { schema_version: 1, geometry: {}, options: { engine: options.setupEngine ?? 'auto' } },
        });
      }
      if (path.startsWith('/api/jobs/')) {
        posted.push({ path, body: init?.body ? JSON.parse(String(init.body)) : null });
        if (path.endsWith('/solve-again')) return json({ job_id: 'child' });
        return json({});
      }
      if (path.startsWith('/api/cadlink/operations/')) {
        const operationId = decodeURIComponent(path.split('/')[4]);
        if (!init?.method) {
          if (detailFailures > 0) {
            detailFailures -= 1;
            return json({ detail: 'The CAD operations store is busy.' }, 503);
          }
          const known = fixtureOperations[operationId];
          if (options.detailFromStore && known) return json({ ...known, approvals: [], preparation: null });
          return options.detail ? json(options.detail) : json({}, 404);
        }
        posted.push({ path, body: init.body ? JSON.parse(String(init.body)) : null });
        const held = fixtureOperations[operationId] ?? cadOperation({ operationId });
        if (path.endsWith('/cancel')) return json({ ...held, state: 'cancelled', updatedAt: '2026-09-14T11:00:00Z' });
        if (path.endsWith('/reconcile') && options.reconcileAccepted?.includes(operationId)) {
          return json({ operation: { ...held, state: 'accepted', updatedAt: '2026-09-14T11:00:00Z' } });
        }
        if (options.failPrepare?.includes(operationId)) return json({ detail: 'The jobs system is not answering.' }, 503);
        return json({ operation: held });
      }
      return base(input, init);
    }));
    return posted;
  };

  it('shows a solve the update restart holds as resuming by itself, with only Dismiss to press', async () => {
    await renderAndSelect();
    await clickIngest();
    act(() => {
      applyCadRequest(cadOperation({
        operationId: 'op-restart', reason: 'update_restart_pending',
        message: 'Waveguide Generator is about to restart to install 0.3.4, so it is not starting new solves. Submit this again after the restart.',
      }));
    });

    const card = host.querySelector<HTMLElement>('.cad-operation[data-operation-id="op-restart"]')!;
    expect(card.querySelector('[role="status"]')).toBeNull();
    expect(host.querySelector('.cad-solve-run')?.textContent).toContain('held for the update restart');
    expect(card.textContent).toContain('again by itself');
    // Solve now would only be refused until the restart; nothing is needed from the user.
    expect(buttonTexts(card)).toEqual(['Dismiss']);
  });

  it('does not offer generic Dismiss for outgoing file-backed operations', async () => {
    await renderAndSelect();
    await clickIngest();
    act(() => {
      const apply = applyCadRequest;
      for (const kind of ['request_return', 'insert_link', 'update_link']) {
        apply(cadOperation({ operationId: `op-${kind}`, kind, state: 'received', stage: 'received' }));
      }
    });
    expect(host.querySelectorAll('.cad-operation')).toHaveLength(0);
    expect([...host.querySelectorAll<HTMLButtonElement>('button')].some((button) => button.textContent === 'Dismiss')).toBe(false);
  });

  /** The active Fusion document reports the interrupted update `operationId`,
   * which is what makes its recovery card loud. */
  const reportRecovery = async (operationId: string) => {
    reportedRecovery = operationId;
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => (
      String(input).endsWith('/fusion-status')
        ? json({
          ...currentFusion,
          recoveryRequired: {
            operationId, kind: 'update', instanceId: 'instance-a', exportId: 'wge_2', phase: 'applied',
          },
        })
        : base(input, init)
    )));
    await act(async () => {
      window.dispatchEvent(new Event('focus'));
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });
  };

  it('shows only durable interrupted mutations the active document reports, with their recovery actions', async () => {
    await renderAndSelect();
    await clickIngest();
    await reportRecovery('op-update');
    act(() => {
      const apply = applyCadRequest;
      apply(cadOperation({ operationId: 'op-return', kind: 'request_return', state: 'recovery_required' }));
      apply(cadOperation({ operationId: 'op-insert', kind: 'insert_link', state: 'processing' }));
      apply(cadOperation({ operationId: 'op-update', kind: 'update_link', state: 'recovery_required' }));
    });
    const card = operationCard('op-update');
    expect(host.querySelectorAll('.cad-operation')).toHaveLength(1);
    expect(card.textContent).toContain('Update interrupted — recovery required');
    expect(card.textContent).toContain('Fusion has no transaction covering these edits');
    // The journal and the operation id are WG's bookkeeping, not the user's.
    expect(card.textContent).not.toContain('Journal phase');
    expect(card.textContent).not.toContain('op-update');
    expect(buttonTexts(card)).toEqual(['Dismiss', 'Check Fusion again']);
  });

  it('settles a recovery card from Fusion evidence without offering the update again', async () => {
    await renderAndSelect();
    await clickIngest();
    await reportRecovery('op-update');
    const posted = recordOperationRequests({ reconcileAccepted: ['op-update'] });
    act(() => applyCadRequest(cadOperation({
      operationId: 'op-update', kind: 'update_link', state: 'recovery_required',
    })));

    await act(async () => {
      buttonIn(operationCard('op-update'), 'Check Fusion again')!.click();
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });

    expect(posted).toContainEqual({ path: '/api/cadlink/operations/op-update/reconcile', body: null });
    expect(posted).toContainEqual({ path: '/api/cadlink/recovery-notices', body: null });
    expect(operationCard('op-update')).toBeNull();
    expect(cadRecoveryNoticesStore.getState().notices['op-update']).toBeUndefined();
    expect(cadWorkspace.getSnapshot().status).toContain('Checked Fusion recovery notices');
    expect(fusionWorkflowView({
      ...currentFusion,
      recoveryRequired: {
        operationId: 'op-update', kind: 'update', instanceId: 'instance-a', exportId: 'wge_2', phase: 'verified',
      },
    }).action).toBeNull();
  });

  it('does not claim Fusion reported an interruption when reconciliation finds no current evidence', async () => {
    await renderAndSelect();
    await clickIngest();
    await reportRecovery('op-update');
    const posted = recordOperationRequests();
    act(() => applyCadRequest(cadOperation({
      operationId: 'op-update', kind: 'update_link', state: 'recovery_required',
    })));

    await act(async () => {
      buttonIn(operationCard('op-update'), 'Check Fusion again')!.click();
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });

    expect(posted).toContainEqual({ path: '/api/cadlink/operations/op-update/reconcile', body: null });
    expect(posted).toContainEqual({ path: '/api/cadlink/recovery-notices', body: null });
    expect(operationCard('op-update')).not.toBeNull();
    expect(fixtureOperations['op-update'].state).toBe('recovery_required');
    expect(cadWorkspace.getSnapshot().status).toBe(
      'Checked Fusion recovery notices. Use Undo in Fusion or repair the link if its warning remains.',
    );
    expect(cadWorkspace.getSnapshot().status).not.toContain('Fusion still reports');
  });

  it('dismisses the durable card without claiming that Fusion was repaired', async () => {
    await renderAndSelect();
    await clickIngest();
    const posted = recordOperationRequests();
    act(() => applyCadRequest(cadOperation({
      operationId: 'op-update', kind: 'update_link', state: 'recovery_required',
    })));

    await act(async () => {
      buttonIn(operationCard('op-update'), 'Dismiss')!.click();
      await Promise.resolve(); await Promise.resolve();
    });

    expect(posted).toContainEqual({ path: '/api/cadlink/recovery-notices/op-update/dismiss', body: null });
    expect(operationCard('op-update')).toBeNull();
    expect(cadRecoveryNoticesStore.getState().notices['op-update']).toBeUndefined();
    expect(cadWorkspace.getSnapshot().status).toContain('Fusion still needs Undo or repair');
  });

  it('shows each pending CAD operation with its state, reason, identity and the action it needs', async () => {
    await renderAndSelect();
    await clickIngest();
    const posted = recordOperationRequests({ setupEngine: 'metal' });
    const engineBefore = useImportedSolveOptionsStore.getState().engine;
    const jobManager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
    const previousJobs = jobManager.snapshot;
    jobManager.snapshot = {
      ...previousJobs,
      jobs: [{ id: 'job-1', solve_options: { engine: 'beat-cpu' } } as JobItem],
    };
    act(() => jobManager.listeners.forEach((listener) => listener()));
    act(() => {
      const apply = applyCadRequest;
      apply(cadOperation({ operationId: 'op-ready' }));
      apply(cadOperation({
        operationId: 'op-engine', reason: 'engine_unavailable', preparationId: null, createdAt: '2026-09-14T10:00:01Z',
        snapshot: { manifestSha256: record.manifest_sha256, documentName: null, projectLineageId: null },
        message: 'The selected engine, metal, cannot solve this model on this machine. Engines that can: bempp, beat-cpu.',
      }));
      apply(cadOperation({
        operationId: 'op-setup', reason: 'setup_required', stage: 'received', setupRevisionId: null, preparationId: null,
        createdAt: '2026-09-14T10:00:02Z', message: 'Choose the solve settings for this model in WG, then press Solve now.',
      }));
      // Finished: its run is in the Jobs rail, not here.
      apply(cadOperation({ operationId: 'op-done', state: 'accepted', stage: 'submitted', reason: null, jobId: 'job-1', createdAt: '2026-09-14T09:00:00Z' }));
    });

    const cards = [...host.querySelectorAll<HTMLElement>('.cad-operation')];
    expect(cards.map((card) => card.dataset.operationId)).toEqual(['op-setup', 'op-engine', 'op-ready']);
    const ready = cards.find((card) => card.dataset.operationId === 'op-ready')!;
    const engine = cards.find((card) => card.dataset.operationId === 'op-engine')!;
    const setup = cards.find((card) => card.dataset.operationId === 'op-setup')!;
    // The shared run line is the sole status, outside the action card.
    expect(ready.getAttribute('role')).toBeNull();
    expect(ready.querySelector('[role="status"]')).toBeNull();
    expect(host.querySelector('.cad-solve-run')?.textContent).toContain('Waiting for you');
    expect(ready.querySelector('[role="status"] button')).toBeNull();
    expect(ready.textContent).toContain('Fusion asked for a solve');
    expect(ready.textContent).toContain('Prepared, and waiting for you to start the solve.');
    // Its bound inputs are bookkeeping: in the model card's Details, not on the card.
    for (const id of ['op-ready', 'wgs_1', 'wgp_1']) expect(ready.textContent).not.toContain(id);
    const details = host.querySelector<HTMLDetailsElement>('.cad-model-details')!;
    expect(details.open).toBe(false);
    const inputs = [...details.querySelectorAll<HTMLElement>('.cad-solve-inputs')]
      .find((item) => item.textContent?.includes('op-ready'))!;
    for (const id of ['op-ready', 'wgs_1', 'wgp_1']) expect(inputs.textContent).toContain(id);
    await vi.waitFor(() => expect(inputs.textContent).toContain('Enginemetal'));
    expect(inputs.textContent).not.toContain('beat-cpu');
    // Solving it is the Solve card's one Solve (M1b); the request's own card
    // offers only what Solve never does. Each action names what it acts on.
    expect(buttonTexts(ready)).toEqual(['Dismiss']);
    expect(buttonLabels(ready)).toEqual(['Dismiss: Speaker']);
    expect(ready.closest('.cad-solve-card')).not.toBeNull();
    // Older requests retain their reported refusal in Details. Only current
    // model status owns readiness and remedies, with no copy on each request.
    const engineInputs = [...details.querySelectorAll('.cad-solve-inputs')]
      .find((item) => item.textContent?.includes('op-engine'))!;
    expect(engineInputs.textContent).toContain('Engines that can: bempp, beat-cpu.');
    expect(engine.querySelector('.solve-readiness')).toBeNull();
    expect(buttonTexts(engine)).toEqual(['Dismiss']);
    expect(buttonLabels(engine)[0]).toBe('Dismiss: operation op-engine');
    // The model is on screen, so the card's own guidance replaces the backend's
    // message; only the collapsed Solve inputs record keeps it, as reported.
    expect(setup.querySelector(':scope > div > span:not([role])')?.textContent).toContain('This model is on screen');
    expect([...setup.querySelectorAll(':scope > div > span')].map((span) => span.textContent).join(' '))
      .not.toContain('Choose the solve settings for this model in WG');
    expect([...details.querySelectorAll('.cad-solve-inputs-reported')].map((item) => item.textContent).join(' '))
      .toContain('Choose the solve settings for this model in WG');
    expect(buttonTexts(setup)).toEqual(['Dismiss']);
    // One Solve for all of them, on the Solve card.
    expect([...host.querySelectorAll('button')].filter((button) => /^Solve/.test(button.textContent ?? ''))
      .map((button) => button.getAttribute('data-action'))).toEqual(['solve']);

    await act(async () => { engine.querySelector<HTMLButtonElement>('button')!.click(); });
    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted).toEqual([
      { path: '/api/jobs/job%3Aop-engine/dismiss', body: {} },
    ]);
    jobManager.snapshot = previousJobs;
    expect(useImportedSolveOptionsStore.getState().engine).toBe(engineBefore);
  });

  it('recovers missing job preparation details and approves its blocking findings on that preparation only', async () => {
    await renderAndSelect(true);
    await clickIngest();
    const posted = recordOperationRequests({
      detailFailures: 1,
      detail: {
        ...cadOperation({ operationId: 'op-review', reason: 'findings_need_review', preparationId: 'wgp_7' }),
        approvals: [],
        preparation: {
          preparationId: 'wgp_7', ingestId: record.ingest_id, snapshotSha256: 'sha256:s', setupRevisionId: 'wgs_1',
          reportSha256: record.report_sha256, blockingFindingIds: ['finding-a'], attemptGeneration: 1,
        },
      },
    });
    act(() => {
      applyCadRequest(cadOperation({
        operationId: 'op-review', reason: 'findings_need_review', preparationId: 'wgp_7',
        message: 'Review the blocking findings, then approve them to solve.',
      }));
    });
    const card = operationCard('op-review');
    await vi.waitFor(() => expect(card.textContent).toContain('Could not read the findings'));
    expect(buttonIn(card, 'Approve and solve')).toBeUndefined();
    await act(async () => {
      const retry = buttonIn(card, 'Retry')!;
      missingReview = false;
      applyCadRequest(cadOperation({ operationId: 'op-review', reason: 'findings_need_review', preparationId: 'wgp_7' }));
      retry.click();
    });
    await vi.waitFor(() => expect(card.querySelector('.cad-operation-findings')?.textContent).toBe('freshness'));
    expect(card.textContent).not.toContain('Could not read the findings');
    // In words, never by id.
    expect(card.textContent).not.toContain('finding-a');
    await act(async () => { buttonIn(card, 'Approve and solve')!.click(); });
    await vi.waitFor(() => expect(posted).toHaveLength(2));
    expect(posted[0]).toEqual({
      path: '/api/jobs/job%3Aop-review/approvals',
      body: { preparation_id: 'wgp_7', finding_ids: ['finding-a'] },
    });
  });

  it.each([false, true])('explains an absent owner and releases a busy Approve press (owner=%s)', async (withOwner) => {
    workspaceModeStore.setMode('cad');
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/api/capabilities')) return json({
        engines: [{ name: 'metal', available: true, reason: null, version: null, fast_paths: [], formulations: ['full-3d'] }],
      });
      if (path === '/api/solve/imported-plan') return json({
        ingest_id: record.ingest_id, requested: 'auto', engine: 'metal', code: null, reason: 'AUTO', domain: 'full',
        engines: [{ name: 'metal', label: 'Metal', solves: true }],
      });
      if (path.endsWith('/mesh') || path.endsWith('/viewport-mesh')) return new Response(meshFixture, { status: 200 });
      return base(input, init);
    }));
    await renderAndSelect(withOwner);
    await clickIngest();
    const posted = recordOperationRequests({
      detail: {
        ...cadOperation({ operationId: 'op-busy', reason: 'findings_need_review', preparationId: 'wgp_7' }),
        approvals: [],
        preparation: {
          preparationId: 'wgp_7', ingestId: record.ingest_id, snapshotSha256: 'sha256:s', setupRevisionId: 'wgs_1',
          reportSha256: record.report_sha256, blockingFindingIds: ['finding-a'], attemptGeneration: 1,
        },
      },
    });
    act(() => applyCadRequest(cadOperation({ operationId: 'op-busy', reason: 'findings_need_review', preparationId: 'wgp_7' })));
    const card = operationCard('op-busy');
    await vi.waitFor(() => expect(buttonIn(card, 'Approve and solve')).toBeDefined());
    const approve = () => buttonIn(card, 'Approve and solve')!;
    if (!withOwner) {
      expect(approve().disabled).toBe(true);
      expect(card.textContent).toContain('Solve coordinator is unavailable.');
      await act(async () => approve().click());
      expect(posted).toEqual([]);
      return;
    }
    let finish!: () => void;
    const pending = new Promise<void>((resolve) => { finish = resolve; });
    const backend = vi.mocked(fetch).getMockImplementation()!;
    const submitting = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (/^\/api\/models\/[^/]+\/solve$/.test(String(input))) await pending;
      return backend(input, init);
    });
    vi.stubGlobal('fetch', submitting);
    const solve = () => host.querySelector<HTMLButtonElement>('.cad-solve-card button[data-action="solve"]')!;
    await vi.waitFor(() => expect(solve().disabled).toBe(false));
    await act(async () => solve().click());
    await vi.waitFor(() => expect(submitting.mock.calls.some(([path]) => String(path).endsWith('/solve'))).toBe(true));
    await act(async () => approve().click());
    await vi.waitFor(() => expect(host.textContent).toContain('Another solve is being submitted. Try again in a moment.'));
    expect(approve().disabled).toBe(false);
    expect(posted).toEqual([]);
    await act(async () => { finish(); });
    await vi.waitFor(() => expect(posted).toHaveLength(1));
    await act(async () => approve().click());
    await vi.waitFor(() => expect(posted).toHaveLength(3));
    expect(posted.slice(1).map(({ path }) => path)).toEqual(['/api/jobs/job%3Aop-busy/approvals', '/api/jobs/job%3Aop-busy/solve-again']);
    expect(approve().disabled).toBe(true); // Accepted presses stay held until the job advances.
  });

  /** A continuation -- an approval, a frame confirmation, a retry -- keeps the
   * setup revision the operation already holds, which the backend reuses. It
   * used to rebuild the settings from the controls: a different revision (no
   * run label), so a new preparation, the approval lost, and no job. Only an
   * action that chooses settings sends them: here, a new engine after
   * engine_unavailable. */
  it('continues a solve with the settings it holds, and sends new ones only when the card asks for them', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      if (path.endsWith('/viewport-mesh')) return new Response(meshFixture, { status: 200 });
      return json({ ...record, project: { lineage_id: 'wgl_speaker' } });
    }));
    await renderAndSelect(true);
    await clickIngest();
    const manual = 'manual-solve:op-wg';
    const posted = recordOperationRequests({
      detail: {
        ...cadOperation({ operationId: manual, reason: 'findings_need_review', preparationId: 'wgp_7' }),
        approvals: [],
        preparation: {
          preparationId: 'wgp_7', ingestId: record.ingest_id, snapshotSha256: 'sha256:s', setupRevisionId: 'wgs_manual',
          reportSha256: record.report_sha256, blockingFindingIds: ['finding-a'], attemptGeneration: 1,
        },
      },
    });
    act(() => {
      applyCadRequest(cadOperation({
        operationId: manual, reason: 'findings_need_review', preparationId: 'wgp_7', setupRevisionId: 'wgs_manual',
      }));
    });
    const card = operationCard(manual);
    await vi.waitFor(() => expect(buttonIn(card, 'Approve and solve')).toBeDefined());
    await act(async () => { buttonIn(card, 'Approve and solve')!.click(); });
    await vi.waitFor(() => expect(posted).toHaveLength(2));
    expect(posted[0]).toEqual({
      path: `/api/jobs/${encodeURIComponent(`job:${manual}`)}/approvals`,
      body: { preparation_id: 'wgp_7', finding_ids: ['finding-a'] },
    });
    // Its progress line is about the user's own solve, not one Fusion sent.
    await vi.waitFor(() => expect(host.querySelector('.cad-status-strip')?.textContent).toBeTruthy());
    expect(host.querySelector('.cad-status-strip')?.textContent).not.toContain('Fusion sent');

    // After engine_unavailable the card asks for another engine, then Solve:
    // the Solve card's one Solve, which sends the settings on screen
    // (M1bSolveCard.test.tsx). No second Solve here.
    act(() => {
      applyCadRequest(cadOperation({
        operationId: 'op-fusion', reason: 'engine_unavailable', createdAt: '2026-09-14T10:00:03Z',
        message: 'Choose an available engine, then press Solve.',
      }));
    });
    expect(host.querySelector('.cad-solve-card .solve-readiness')?.textContent).toContain('Choose an available engine, then press Solve.');
    expect(operationCard('op-fusion').querySelector('.solve-readiness')).toBeNull();
    expect(buttonIn(operationCard('op-fusion'), 'Solve now')).toBeUndefined();
    expect(posted).toHaveLength(2);
  });

  it('holds Dismiss until its operation moves on, offers it again when the request fails, and leaves a received one to the backend', async () => {
    await renderAndSelect();
    await clickIngest();
    const posted = recordOperationRequests();
    act(() => {
      const apply = applyCadRequest;
      apply(cadOperation({ operationId: 'op-ready' }));
      apply(cadOperation({
        operationId: 'op-new', state: 'received', stage: 'received', reason: null, message: null,
        createdAt: '2026-09-14T10:00:02Z',
      }));
    });
    // The backend's own loop prepares an operation nobody has touched: no
    // guidance to act on, and nothing to solve from its card.
    expect(buttonTexts(operationCard('op-new'))).toEqual(['Stop']);
    expect(operationCard('op-new').textContent).not.toContain('Press Solve');
    const dismiss = () => buttonIn(operationCard('op-ready'), 'Dismiss')!;
    // A dismissal that fails is offered again.
    vi.mocked(fetch).mockImplementationOnce(async () => json({ detail: 'The CAD operations store is busy.' }, 503));
    await act(async () => { dismiss().click(); });
    await vi.waitFor(() => expect(dismiss().disabled).toBe(false));
    await act(async () => { dismiss().click(); });
    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toEqual({ path: '/api/jobs/job%3Aop-ready/dismiss', body: {} });
  });

  /** The design on screen as it was opened from its own project. */
  function openedProject(designId = 'wgd_current', lineageId = 'wgl_current') {
    return {
      dialect: 'ath', migrationsApplied: [],
      passthrough: { keysPreserved: [], blocksPreserved: [], keyCount: 0, blockCount: 0 },
      design: { ...useDesignStore.getState().design, R: 150 },
      cadlink: { identity: { designId, lineageId, baseEditVersion: 2 }, classification: 'current' },
    } as unknown as Parameters<typeof applyOpenedDesign>[0];
  }

  it('opens the project a waiting operation belongs to, asking before unsaved work is discarded', async () => {
    act(() => { applyOpenedDesign(openedProject(), 'current.cfg'); });
    useDesignStore.getState().updateField('R', 321);
    const opened: string[] = [];
    const prepared: unknown[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === '/api/cadlink/operations/op-other/prepare') {
        prepared.push(JSON.parse(String(init?.body)));
        return json({ operation: fixtureOperations['op-other'] });
      }
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      if (path.startsWith('/api/jobs')) return json({ items: [] });
      if (path === '/api/cadlink/designs') return json({ items: [{
        designId: 'wgd_other', lineageId: 'wgl_other', filename: 'Tritonia.cfg', documentName: 'Tritonia',
        archiveStem: 'Tritonia', exportCount: 1, editVersion: 2,
        createdAt: '2026-09-04T00:00:00Z', updatedAt: '2026-09-04T00:00:00Z',
      }] });
      if (path === '/api/cadlink/designs/wgd_other') {
        opened.push(path);
        return json({ designId: 'wgd_other', lineageId: 'wgl_other', editVersion: 2, filename: 'Tritonia.cfg', updatedAt: '2026-09-04T00:00:00Z', text: 'R = 160' });
      }
      if (path === '/api/design/open') {
        opened.push(path);
        return json(openedProject('wgd_other', 'wgl_other'));
      }
      return json({}, 404);
    }));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    act(() => {
      applyCadRequest(cadOperation({
        operationId: 'op-other', reason: 'setup_required', stage: 'received', setupRevisionId: null, preparationId: null,
        message: 'Choose the solve settings for this model in WG, then press Solve now.',
        snapshot: { manifestSha256: `sha256:${'b'.repeat(64)}`, documentName: 'Tritonia', projectLineageId: 'wgl_other' },
      }));
    });
    // Not the model on screen: one quiet line under Earlier requests, which
    // can still open its project.
    const card = operationCard('op-other');
    expect(card.closest('.cad-earlier-requests')).not.toBeNull();
    expect(buttonTexts(card)).toEqual(['Open Tritonia', 'Dismiss']);

    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    act(() => {
      // A fast double click asks once.
      const open = buttonIn(card, 'Open Tritonia')!;
      open.click();
      open.click();
    });
    await vi.waitFor(() => expect(confirm).toHaveBeenCalled());
    await act(async () => { for (let i = 0; i < 12; i += 1) await Promise.resolve(); });
    expect(confirm).toHaveBeenCalledOnce();
    // Declined: the design on screen stays, edits and all.
    expect(opened).toEqual([]);
    expect(useDesignStore.getState().design.R).toBe(321);
    expect(useDocumentStore.getState().identity?.designId).toBe('wgd_current');

    confirm.mockReturnValue(true);
    await act(async () => { buttonIn(operationCard('op-other'), 'Open Tritonia')!.click(); });
    await vi.waitFor(() => expect(useDocumentStore.getState().identity?.designId).toBe('wgd_other'));
    expect(opened).toEqual(['/api/cadlink/designs/wgd_other', '/api/design/open']);
    // The coordinator says what the open found; the card does not talk over it.
    await vi.waitFor(() => expect(host.querySelector('.cad-status-strip')?.textContent).toContain('Project design loaded'));
    expect(host.querySelector('.cad-status-strip')?.textContent).not.toContain('Opened');
    // Its return is still not on screen: it stays a quiet line, and nothing is
    // prepared from here.
    expect(buttonIn(operationCard('op-other'), 'Solve now')).toBeUndefined();
    expect(prepared).toEqual([]);
  });

  it('solves the waiting version on screen with its settings, and keeps the other one a quiet line', async () => {
    // The ingestion files the model on screen under the project both versions belong to.
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      return json({ ...record, project: { lineage_id: 'wgl_other' } });
    }));
    await renderAndSelect();
    await clickIngest();
    const posted = recordOperationRequests();
    const waiting = {
      reason: 'setup_required', stage: 'received', setupRevisionId: null, preparationId: null,
      message: 'Choose the solve settings for this model in WG, then press Solve now.',
    };
    act(() => {
      const apply = applyCadRequest;
      apply(cadOperation({
        ...waiting, operationId: 'op-v1',
        snapshot: { manifestSha256: `sha256:${'d'.repeat(64)}`, documentName: 'Tritonia v1', projectLineageId: 'wgl_other' },
      }));
      apply(cadOperation({
        ...waiting, operationId: 'op-v2', createdAt: '2026-09-14T10:00:01Z',
        snapshot: { manifestSha256: record.manifest_sha256, documentName: 'Tritonia v2', projectLineageId: 'wgl_other' },
      }));
    });
    // v2 is the model on screen: its request is on the Solve card, whose one
    // Solve continues it with the settings on screen (M1bSolveCard.test.tsx).
    expect(operationCard('op-v2').closest('.cad-solve-card')).not.toBeNull();
    expect(buttonTexts(operationCard('op-v2'))).toEqual(['Dismiss']);
    expect(posted).toEqual([]);
    // v1's return is not on screen: one quiet line under Earlier requests.
    expect(operationCard('op-v1').closest('.cad-earlier-requests')).not.toBeNull();
    expect(buttonTexts(operationCard('op-v1'))).toEqual(['Open Tritonia v1', 'Dismiss']);
  });

  it('lists a request whose model has no project yet as a quiet line to dismiss', async () => {
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    act(() => {
      applyCadRequest(cadOperation({
        operationId: 'op-first', reason: 'setup_required', stage: 'received', setupRevisionId: null, preparationId: null,
        message: 'Choose the solve settings for this model in WG, then press Solve now.',
        snapshot: { manifestSha256: `sha256:${'c'.repeat(64)}`, documentName: 'Tritonia v2', projectLineageId: null },
      }));
    });
    const card = operationCard('op-first');
    expect(card.closest('.cad-earlier-requests')).not.toBeNull();
    expect(card.textContent).toContain('Tritonia v2');
    expect(buttonTexts(card)).toEqual(['Dismiss']);
  });

  it('solves a stored snapshot with Fusion closed: one Solve of the model, which the server prepares', async () => {
    workspaceModeStore.setMode('cad');
    const withProject = { ...record, project: { lineage_id: 'wgl_speaker' } };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      if (path.endsWith('/api/capabilities')) return json({
        engines: [{ name: 'metal', available: true, reason: null, version: null, fast_paths: [], formulations: ['full-3d'] }],
      });
      if (path === '/api/solve/imported-plan') return json({
        ingest_id: record.ingest_id, requested: 'auto', engine: 'metal', code: null, reason: 'AUTO', domain: 'full',
        engines: [{ name: 'metal', label: 'Metal', solves: true }],
      });
      if (path.endsWith('/mesh') || path.endsWith('/viewport-mesh')) return new Response(meshFixture, { status: 200 });
      return json(withProject);
    }));
    await act(async () => { root.render(<FullCadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    openHistory();
    act(() => host.querySelector<HTMLButtonElement>('.cad-bundle-list button')!.click());
    await clickIngest();
    await vi.waitFor(() => expect(cadWorkspace.getSnapshot().fusionStatus?.running).toBe(false));
    const posted = recordOperationRequests({ detailFromStore: true });
    act(() => {
      applyCadRequest(cadOperation({
        operationId: 'op-stored', reason: 'setup_required', stage: 'received', setupRevisionId: null, preparationId: null,
        message: 'Choose the solve settings for this model in WG, then press Solve now.',
      }));
    });
    const solve = () => host.querySelector<HTMLButtonElement>('.cad-solve-card button[data-action="solve"]')!;
    await vi.waitFor(() => expect(solve().disabled).toBe(false));
    await act(async () => { solve().click(); });
    await vi.waitFor(() => expect(posted.filter((item) => item.path.endsWith('/solve'))).toHaveLength(1));
    // One press with the settings on screen; the server records them for the
    // model and continues the waiting request.
    const pressed = posted.find((item) => item.path.endsWith('/solve'))!;
    expect(pressed.path).toBe('/api/models/wgl_speaker/solve');
    expect(pressed.body).toMatchObject({ prepared_id: record.ingest_id, setup: { schema_version: 1 } });
    expect(posted.some((item) => item.path.endsWith('/solve-again') || item.path === '/api/cadlink/operations')).toBe(false);
  });

  it('leaves Fusion solve commands to the backend: nothing on screen reads or solves one', async () => {
    vi.useFakeTimers();
    const requests: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      requests.push(path);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      if (path.endsWith('/solve-command')) return json({ command: {
        commandId: 'cmd-fusion-1', returnId: 'wgr-fusion-1', bundlePath: listing.items[0].bundlePath,
        manifestSha256: `sha256:${'4'.repeat(64)}`, requestedAt: '2026-08-20T12:00:00Z',
      }, outcome: null });
      if (path.endsWith('/api/capabilities')) return json({
        engines: [{ name: 'metal', available: true, reason: null, version: null, fast_paths: [] }],
      });
      return json({}, 404);
    }));
    await act(async () => {
      root.render(<FullCadLinkTestSurface/>);
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(2_600); });

    expect(requests.filter((path) => path.includes('/solve-command'))).toEqual([]);
    expect(host.querySelector('.cad-operation')).toBeNull();
  });

  it('names the snapshot and the preparation of the model on screen', async () => {
    await renderAndSelect();
    await clickIngest();
    const provenance = host.querySelector('.cad-model-provenance')!;
    expect(provenance.textContent).toContain(`Snapshot ${'1'.repeat(12)}`);
    expect(provenance.textContent).toContain(`Preparation ${record.ingest_id}`);
  });

  it('labels a prepared native assembly as experimental but not a CAD return', async () => {
    await renderAndSelect();
    await clickIngest();
    expect(host.querySelector('.cad-model-identity .experimental-badge')).toBeNull();
    await act(async () => { useCadReturnStore.setState({ selectedBundle: { ...useCadReturnStore.getState().selectedBundle!, bundleOrigin: 'native' } }); });
    const badge = host.querySelector('.cad-model-identity .experimental-badge');
    expect(badge?.textContent).toBe('Experimental');
    expect(badge?.getAttribute('title')).toContain('not yet acoustically qualified');
  });

  it('lists retained FEM regions with group kinds and mesh counts', async () => {
    await renderAndSelect();
    await clickIngest();
    expect(host.querySelector('.cad-fem-regions')).toBeNull();
    await act(async () => useCadReturnStore.setState({ ingestRecord: {
      ...record,
      fem_regions: [{
        region_id: 'rear-chamber', name: 'Rear chamber', object_id: 'air', file: 'fem-regions/air.step',
        mesh_size_mm: 8, medium: { sound_speed_m_per_s: 343, density_kg_per_m3: 1.21, bulk_loss_factor: 0 },
        mesh_cache_key: 'key', mesh_lookup_key: 'lookup', mesh_content_sha256: 'sha256:mesh', mesh_size_bytes: 500,
        surface_groups: [
          { group_id: 'cone', name: 'Cone', kind: 'moving', mesh_size_mm: 4, relative_level_db: -6 },
          { group_id: 'port', name: 'Port', kind: 'interface', mesh_size_mm: 4 },
          { group_id: 'end', name: 'End', kind: 'plane-wave-termination', mesh_size_mm: 8 },
          { group_id: 'walls', name: 'Walls', kind: 'rigid', mesh_size_mm: 8,
            lining: { model: 'miki', thickness_mm: 30, flow_resistivity_pa_s_per_m2: 5000 } },
        ],
        stats: { tetrahedra: 1234, nodes: 456, surface_groups: {
          cone: { kind: 'moving', triangle_count: 210, area_mm2: 1200, face_count: 1 },
          port: { kind: 'interface', triangle_count: 100, area_mm2: 600, face_count: 1 },
          end: { kind: 'plane-wave-termination', triangle_count: 120, area_mm2: 800, face_count: 1 },
          walls: { kind: 'rigid', triangle_count: 130, area_mm2: 1000, face_count: 1 },
          _walls: { kind: 'rigid', triangle_count: 400, area_mm2: 3400, face_count: 4 },
        } },
      }],
    } }));
    const regions = host.querySelector('.cad-fem-regions')!;
    expect(regions.querySelector('.experimental-badge')).toBeNull();
    expect(regions.textContent).not.toContain('Experimental');
    expect(regions.textContent).toContain('Rear chamber');
    expect(regions.textContent).toContain('1,234 tetrahedra · 456 nodes · 8 mm region size');
    for (const kind of ['moving', 'interface', 'plane wave termination', 'rigid']) {
      expect(regions.textContent).toContain(kind);
    }
    expect(regions.textContent).toContain('moving · 210 triangles · 1,200 mm²');
    expect(regions.textContent).toContain('interface · 100 triangles · 600 mm²');
    expect(regions.textContent).toContain('plane wave termination · 120 triangles · 800 mm²');
    expect(regions.textContent).toContain('rigid · 130 triangles · 1,000 mm²');
    expect(regions.textContent).toContain('-6 dB relative motion');
    expect(regions.textContent).toContain('Miki lining: 30 mm · 5000 Pa·s/m²');
    expect(regions.textContent).toContain('Unclaimed walls');
    expect(regions.textContent).toContain('rigid · 400 triangles · 3,400 mm²');
    await act(async () => capabilityClient.setQueryData(['capabilities'], { engines: [], femRegions: false }));
    await vi.waitFor(() => expect(host.querySelector('.cad-fem-regions')).toBeNull());
    await act(async () => useCadReturnStore.setState({ ingestRecord: { ...record, fem_regions: [] } }));
    expect(host.querySelector('.cad-fem-regions')).toBeNull();
  });

  it('tries the full-domain viewport artifact before silently falling back on 404', async () => {
    const requests: string[] = [];
    const notices: string[] = [];
    const fetcher = (async (input: RequestInfo | URL) => {
      const path = String(input);
      requests.push(path);
      if (path.endsWith('/viewport-mesh')) return new Response('missing', { status: 404 });
      return new Response(meshFixture, { status: 200 });
    }) as typeof fetch;

    await showIngestedMeshInViewport(record, 'Speaker', (notice) => notices.push(notice), fetcher);

    expect(requests).toEqual([
      `/api/cadlink/ingest/${record.ingest_id}/viewport-mesh`,
      `/api/cadlink/ingest/${record.ingest_id}/mesh`,
    ]);
    expect(notices).toEqual([]);
    expect(importedMeshStore.getSnapshot().cad?.artifactToken).toBe(`${record.ingest_id}:solver`);
  });

  it('reports viewport artifact corruption before falling back to the solver mesh', async () => {
    const events: string[] = [];
    const fetcher = (async (input: RequestInfo | URL) => {
      const path = String(input);
      events.push(`fetch:${path}`);
      if (path.endsWith('/viewport-mesh')) return new Response('corrupt', { status: 409 });
      return new Response(meshFixture, { status: 200 });
    }) as typeof fetch;

    await showIngestedMeshInViewport(
      record,
      'Speaker',
      (notice) => events.push(`notice:${notice}`),
      fetcher,
    );

    expect(events[0]).toContain('/viewport-mesh');
    expect(events[1]).toContain('failed verification');
    expect(events[2]).toContain(`/${record.ingest_id}/mesh`);
  });

  it('shows the solve mesh at once when the display tessellation is still building, then swaps it in', async () => {
    const requests: string[] = [];
    let displayReady = false;
    let viewportReady: (ingestId: string) => void = () => undefined;
    vi.spyOn(jobsSocket, 'subscribeCadViewportReady').mockImplementation((listener) => {
      viewportReady = listener;
      return () => undefined;
    });
    const fetcher = (async (input: RequestInfo | URL) => {
      const path = String(input);
      requests.push(path);
      if (!path.endsWith('/viewport-mesh')) return new Response(meshFixture, { status: 200 });
      return displayReady
        ? new Response(meshFixture, { status: 200 })
        : new Response('', { status: 202 });
    }) as typeof fetch;

    workspaceModeStore.setMode('cad');
    await showIngestedMeshInViewport(
      { ...record, viewport_mesh: { available: false, pending: true, lookup_key: 'a'.repeat(64) } },
      'Speaker',
      undefined,
      fetcher,
    );

    // Visible immediately, on the artifact that already exists.
    expect(importedMeshStore.getSnapshot().cad?.artifactToken).toBe(`${record.ingest_id}:solver`);
    expect(requests).toEqual([
      `/api/cadlink/ingest/${record.ingest_id}/viewport-mesh`,
      `/api/cadlink/ingest/${record.ingest_id}/mesh`,
      `/api/cadlink/ingest/${record.ingest_id}/viewport-mesh`,
    ]);

    displayReady = true;
    viewportReady(record.ingest_id);
    await vi.waitFor(() => {
      expect(importedMeshStore.getSnapshot().cad?.artifactToken).toBe(`${record.ingest_id}:viewport`);
    });
    // The upgrade replaced the scene in place rather than opening a second view.
    expect(importedMeshStore.getSnapshot().showing).toBe('cad');
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe(record.ingest_id);
  });

  it('observes viewport completion that lands before its websocket subscription', async () => {
    const requests: string[] = [];
    let displayReady = false;
    vi.spyOn(jobsSocket, 'subscribeCadViewportReady').mockImplementation(() => {
      // Completion landed after the first 202 but before the listener existed.
      displayReady = true;
      return () => undefined;
    });
    const fetcher = (async (input: RequestInfo | URL) => {
      const path = String(input);
      requests.push(path);
      if (!path.endsWith('/viewport-mesh')) return new Response(meshFixture, { status: 200 });
      return displayReady
        ? new Response(meshFixture, { status: 200 })
        : new Response('', { status: 202 });
    }) as typeof fetch;

    workspaceModeStore.setMode('cad');
    await showIngestedMeshInViewport(
      { ...record, viewport_mesh: { available: false, pending: true, lookup_key: 'a'.repeat(64) } },
      'Speaker',
      undefined,
      fetcher,
    );

    await vi.waitFor(() => {
      expect(importedMeshStore.getSnapshot().cad?.artifactToken).toBe(`${record.ingest_id}:viewport`);
    });
    expect(requests.filter((path) => path.endsWith('/viewport-mesh'))).toHaveLength(2);
  });

  it('shares one artifact request between the two triggers that both want the CAD scene', async () => {
    const requests: string[] = [];
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const fetcher = (async (input: RequestInfo | URL) => {
      requests.push(String(input));
      await gate;
      return new Response(meshFixture, { status: 200 });
    }) as typeof fetch;

    workspaceModeStore.setMode('cad');
    // The coordinator and the viewport effect both fire on the same ingestion.
    const first = showIngestedMeshInViewport(record, 'Speaker', undefined, fetcher);
    const second = showIngestedMeshInViewport(record, 'Speaker', undefined, fetcher);
    release();
    await Promise.all([first, second]);

    expect(requests).toEqual([`/api/cadlink/ingest/${record.ingest_id}/viewport-mesh`]);
  });

  it('does not publish a viewport scene superseded while the response body is read', async () => {
    let resolveText!: (text: string) => void;
    const text = new Promise<string>((resolve) => { resolveText = resolve; });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue({
      ok: true,
      status: 200,
      text: () => text,
    } as Response);
    const generation = importedMeshStore.beginIntent();
    const pending = showIngestedMeshInViewport(record, 'Old speaker', undefined, fetcher, generation);
    await Promise.resolve();
    importedMeshStore.beginIntent();
    resolveText(meshFixture);
    await pending;

    expect(importedMeshStore.getSnapshot().cad).toBeNull();
  });

  it('maps Fusion presence and config freshness to one explicit action', () => {
    expect(fusionWorkflowView({ ...closedFusion, cadFolderConfigured: false })).toMatchObject({
      state: 'not-configured', action: null,
    });
    expect(fusionWorkflowView(closedFusion)).toMatchObject({
      headline: 'Fusion 360 is closed', action: 'open',
    });
    expect(fusionWorkflowView(currentFusion)).toMatchObject({
      state: 'current', action: null,
    });
    expect(fusionWorkflowView({ ...closedFusion, state: 'addin_offline', processRunning: true })).toMatchObject({
      state: 'addin-offline', action: null,
    });
    expect(fusionWorkflowView({ ...currentFusion, cadConnectionIssue: 'folder_unreadable' })).toMatchObject({
      headline: 'The Waveguide Generator add-in in Fusion cannot access the selected folder', action: null,
    });
    expect(fusionWorkflowView({ ...currentFusion, cadConnectionIssue: 'folder_mismatch' }).detail).toContain(
      'clear within a few seconds',
    );
    expect(fusionWorkflowView({ ...currentFusion, state: 'stale', wgChangesAvailable: true })).toMatchObject({
      state: 'stale', action: 'update',
    });
    expect(fusionWorkflowView({
      ...currentFusion,
      state: 'instance_selection_required',
      link: null,
      matchingLinks: [currentFusion.link!, { ...currentFusion.link!, instanceId: 'instance-b' }],
    })).toMatchObject({
      state: 'instance-selection', action: null,
    });
    const staleDetectionExplanation = 'Stale detection is limited for this returned bundle.';
    expect(fusionWorkflowView({
      ...currentFusion,
      state: 'stale',
      wgChangesAvailable: true,
      staleDetectionExplanation,
    }).detail).toContain(staleDetectionExplanation);
  });

  it('never reads an earlier observation as a measurement of the model now', () => {
    // WGLink's heartbeat inspects no geometry: its measured half is whatever a
    // previous measurement cached, and the revision tokens are what say which
    // revision that was (fusion-addins/WGLink/README.md). Three states, not two.
    const measured = { ...currentFusion, observationFreshness: 'current' as const };
    expect(fusionWorkflowView(measured)).toMatchObject({ state: 'current' });

    // Moved on since the measurement: nothing here is evidence of anything.
    const moved = fusionWorkflowView({
      ...measured, state: 'stale' as const, observationFreshness: 'stale' as const,
    });
    expect(moved.state).toBe('refresh-needed');
    expect(moved.detail).not.toContain('already been returned to WG');
    expect(moved.detail).toContain('measured');
    expect(moved.action).toBeNull();

    // Never measured: a restart, or a document nothing has walked yet.
    const unmeasured = fusionWorkflowView({
      ...measured,
      state: 'stale' as const,
      observationFreshness: 'none' as const,
      link: { ...currentFusion.link!, localBodyState: 'unknown', documentSignatureHash: null },
    });
    expect(unmeasured.state).toBe('unmeasured');
    expect(unmeasured.detail).not.toContain('already been returned to WG');

    // A WG-side change is read from stored identity, not from a measurement, so
    // it still stands -- and Send stays offered.
    expect(fusionWorkflowView({
      ...measured,
      state: 'stale' as const,
      observationFreshness: 'stale' as const,
      wgChangesAvailable: true,
    })).toMatchObject({ state: 'refresh-needed', action: 'update' });

    // Positive evidence of a Fusion change survives. Not because a difference
    // cannot stop being one -- an undo back to the returned state leaves an
    // observation reporting a difference the document no longer has -- but
    // because over-reporting is the conservative direction here.
    expect(fusionWorkflowView({
      ...measured,
      state: 'stale' as const,
      observationFreshness: 'stale' as const,
      fusionChangesAvailable: true,
      documentChanged: true,
    })).toMatchObject({ state: 'stale' });

    // An add-in that publishes neither token is unchanged by all of this.
    expect(fusionWorkflowView({ ...currentFusion, observationFreshness: 'unknown' as const }))
      .toEqual(fusionWorkflowView(currentFusion));
  });

  it('keeps the WG-side copy when the Fusion observation is also out of date', () => {
    // Both halves can be out of date at once, and they are known in different
    // ways: the WG side is read from stored identity, the Fusion side is
    // explicitly not known. The certain fact leads; the uncertainty qualifies
    // it; the state keeps carrying the uncertainty so nothing claims a
    // measurement. Before this, the freshness branch pre-empted the WG copy
    // entirely (A5 review finding D5).
    const both = {
      ...currentFusion,
      state: 'stale' as const,
      observationFreshness: 'stale' as const,
      wgChangesAvailable: true,
      fusionFormula: 'osse',
      currentFormula: 'R-OSSE',
      link: { ...currentFusion.link!, configPresent: false, parameterDriftCount: 2 },
    };
    const view = fusionWorkflowView(both);
    // The honesty state and the offered action are A5's, and unchanged.
    expect(view.state).toBe('refresh-needed');
    expect(view.action).toBe('update');
    expect(view.headline).toContain('WG design changed');
    // Everything the later branch would have said is still said.
    expect(view.detail).toContain('Fusion has OSSE; WG is now R-OSSE.');
    expect(view.detail).toContain('predates full WG config synchronization');
    expect(view.detail).toContain('2 managed Fusion parameters have local edits');
    // And the freshness caveat is still there, with its remedy.
    expect(view.detail).toContain('moved on since the Waveguide Generator add-in in Fusion measured it');
    expect(view.detail).toContain('the Waveguide Generator add-in in Fusion measures the model as it exports it');

    // Never measured at all, with the same WG-side change.
    const unmeasured = fusionWorkflowView({ ...both, observationFreshness: 'none' as const });
    expect(unmeasured.state).toBe('unmeasured');
    expect(unmeasured.headline).toContain('WG design changed');
    expect(unmeasured.detail).toContain('Fusion has OSSE; WG is now R-OSSE.');
    expect(unmeasured.detail).toContain('has not measured this document');

    // The other ordering is untouched: with no WG-side change the freshness
    // headline is still the one that leads, and no WG copy is invented.
    const fusionOnly = fusionWorkflowView({
      ...both, wgChangesAvailable: false,
    });
    expect(fusionOnly.state).toBe('refresh-needed');
    expect(fusionOnly.headline).toContain('Fusion geometry may have changed');
    expect(fusionOnly.headline).not.toContain('WG design changed');
    expect(fusionOnly.detail).not.toContain('WG is now R-OSSE');
    expect(fusionOnly.action).toBeNull();
  });

  it('refuses an add-in older than WG with the remedy startup left', () => {
    // Nothing the older add-in reports about the document is acted on, and the
    // prompt names what WG already did: installed its own, or could not.
    const outdated = { ...currentFusion, state: 'addin_outdated' as const };
    expect(fusionWorkflowView({
      ...outdated, addinRefresh: { verdict: 'updated', detail: 'updated the Waveguide Generator add-in in Fusion; restart Fusion' },
    })).toMatchObject({
      state: 'addin-outdated', headline: 'The Waveguide Generator add-in in Fusion is out of date', action: null,
    });
    expect(fusionWorkflowView({
      ...outdated, addinRefresh: { verdict: 'failed', detail: 'the bundled package for the Waveguide Generator add-in in Fusion is missing' },
    }).detail).toContain('the bundled package for the Waveguide Generator add-in in Fusion is missing');
    expect(fusionWorkflowView({
      ...outdated, addinRefresh: { verdict: 'external', detail: 'managed by another Waveguide Generator' },
    }).detail).toContain('another Waveguide Generator installation');
    // No report yet is not a claim that anything was installed.
    const unknown = fusionWorkflowView({ ...outdated, addinRefresh: null }).detail;
    expect(unknown).toContain('still checking');
    expect(unknown).not.toContain('installed');
  });

  it('states each WGLink activation outcome honestly', () => {
    const outdated = { ...currentFusion, state: 'addin_outdated' as const };
    const detail = (addinRefresh: FusionCadStatus['addinRefresh']) =>
      fusionWorkflowView({ ...outdated, addinRefresh }).detail;

    // Pending: WG never replaces the add-in while Fusion is open.
    const pending = detail({ verdict: 'pending', detail: 'Activation of the Waveguide Generator add-in in Fusion is pending until Fusion closes' });
    expect(pending).toContain('Activation of the Waveguide Generator add-in in Fusion is pending until Fusion closes');
    expect(pending).toContain('Close Fusion to finish updating the add-in');
    expect(pending).not.toMatch(/Restart Fusion/);

    // Activated: installed on disk for Fusion's next start, never "running".
    for (const verdict of ['installed', 'updated', 'replaced']) {
      const activated = detail({ verdict, detail: '' });
      expect(activated).toContain('Fusion loads it the next time it starts');
      expect(activated).not.toContain('Restart Fusion 360 to load it');
    }

    // Superseded: pending work from another build is named, and never installed.
    const superseded = 'a pending activation of the Waveguide Generator add-in in Fusion was discarded: it was staged by build 0.3.4 (bbbb), and this is 0.3.3 (aaaa)';
    expect(detail({ verdict: 'current', detail: 'the Waveguide Generator add-in in Fusion is at the pinned aaaa', superseded }))
      .toContain('A pending activation of the Waveguide Generator add-in in Fusion was discarded: it was staged by build 0.3.4');
    expect(detail({ verdict: 'superseded', detail: 'the installed Waveguide Generator is now 0.3.4' }))
      .toContain('Restart Waveguide Generator');

    // Failed, with the reason.
    expect(detail({ verdict: 'failed', detail: 'could not update the Waveguide Generator add-in in Fusion: disk full' }))
      .toContain('could not update the Waveguide Generator add-in in Fusion: disk full');

    // Waiting for this start to be confirmed.
    expect(detail({ verdict: 'awaiting-startup', detail: 'update transaction t1 is open' }))
      .toContain('only once this start is confirmed');

    // Fusion's own registry is reported, never edited.
    const registration = { state: 'duplicate', detail: 'Fusion has the Waveguide Generator add-in in Fusion registered 2 times.' };
    expect(detail({ verdict: 'updated', detail: '', registration }))
      .toContain('Fusion has the Waveguide Generator add-in in Fusion registered 2 times.');
  });

  it('carries a pending or failed WGLink activation into every other state', () => {
    const closed = { ...currentFusion, state: 'closed' as const, running: false, processRunning: false };
    expect(fusionWorkflowView({ ...closed, addinRefresh: { verdict: 'updated', detail: '' } }).detail)
      .toContain('Fusion loads it when it next starts');
    expect(fusionWorkflowView({ ...currentFusion, addinRefresh: { verdict: 'pending', detail: '' } }).detail)
      .toContain('Activation of the Waveguide Generator add-in in Fusion is pending until Fusion closes');
    expect(fusionWorkflowView({ ...currentFusion, addinRefresh: { verdict: 'failed', detail: 'lock held' } }).detail)
      .toContain('WG could not update the Waveguide Generator add-in in Fusion (lock held)');
    // Nothing to say is nothing added.
    expect(fusionWorkflowView({ ...currentFusion, addinRefresh: { verdict: 'current', detail: '' } }).detail)
      .toBe(fusionWorkflowView(currentFusion).detail);
    // A registry problem is named where WGLink is not working.
    const manual = { state: 'manual', detail: 'Tick Run on Startup for WGLink under Utilities → Scripts and Add-Ins.' };
    expect(fusionWorkflowView({ ...closed, addinRefresh: { verdict: 'current', detail: '', registration: manual } }).detail)
      .toContain('Tick Run on Startup for WGLink under Utilities → Scripts and Add-Ins.');
  });

  it('names a pending activation of the Waveguide Generator add-in in Fusion even with no CAD folder chosen', () => {
    // WG updates WGLink whether or not a CAD folder is set, so a user without
    // one must still learn that Fusion has to close to finish it (the updater
    // review §3.8).
    const unconfigured = { ...closedFusion, cadFolderConfigured: false };
    const view = fusionWorkflowView({ ...unconfigured, addinRefresh: { verdict: 'pending', detail: '' } });
    expect(view).toMatchObject({ state: 'not-configured', action: null });
    expect(view.detail).toContain('close Fusion to finish updating the add-in');
    expect(fusionWorkflowView(unconfigured).detail).not.toContain('Activation of the Waveguide Generator add-in in Fusion');
  });

  it('says an interrupted update needs recovery before anything else about the link', () => {
    expect(fusionWorkflowView({
      ...currentFusion,
      recoveryRequired: { operationId: 'req-9', kind: 'update', instanceId: 'instance-a', exportId: 'wge_4', phase: 'applied' },
    })).toMatchObject({
      state: 'recovery-required',
      headline: expect.stringContaining('Update interrupted — recovery required'),
      detail: expect.stringContaining('Fusion has no transaction covering these edits'),
      action: null,
    });
  });

  it('maps Onshape link state to one explicit action', () => {
    const credentials = { configured: true, credentialsPath: '/home/x/.config/hornlab/onshape.env', detail: null, insecureKeyFile: false };
    const link: OnshapeLink = {
      instanceId: 'wgo_demo',
      designId: 'wgd_1', accountId: 'ACC', documentId: 'DID', workspaceId: 'WID',
      documentName: 'Tritonia', documentUrl: 'https://cad.onshape.com/documents/DID/w/WID',
      isPublic: true, partStudioElementId: 'PART', variableStudioElementId: 'VARS',
      featureStudioElementId: null, nativeFeatureId: null,
      datumFeatureStudioElementId: null, datumFeatureId: null, buildMode: 'import',
      lastSequence: 3, updatedAt: '2026-08-13T09:00:00Z',
    };
    const base = {
      credentials, link: null, matchingLinks: [], selectedInstanceId: null,
      wgChangesAvailable: false, currentFormula: 'osse',
    } as const;

    expect(onshapeWorkflowView(null)).toMatchObject({ state: 'checking', action: null });
    expect(onshapeWorkflowView({ ...base, state: 'not_configured' })).toMatchObject({
      state: 'not-configured', action: null,
    });
    // The path to the key file is what makes the message actionable.
    expect(onshapeWorkflowView({ ...base, state: 'not_configured' }).detail)
      .toContain('/home/x/.config/hornlab/onshape.env');
    expect(onshapeWorkflowView({ ...base, state: 'not_linked' })).toMatchObject({
      state: 'not-linked', action: 'open',
    });
    expect(onshapeWorkflowView({
      ...base,
      state: 'instance_selection_required',
      matchingLinks: [link, { ...link, instanceId: 'wgo_other' }],
    })).toMatchObject({ state: 'instance-selection', action: null });
    expect(onshapeWorkflowView({ ...base, state: 'current', link })).toMatchObject({
      state: 'current', headline: 'Onshape · Tritonia', action: null,
    });
    expect(onshapeWorkflowView({ ...base, state: 'stale', link, wgChangesAvailable: true })).toMatchObject({
      state: 'stale', action: 'update',
    });
    // The reason an update is safe is the whole point of the blob re-upload.
    expect(onshapeWorkflowView({ ...base, state: 'stale', link, wgChangesAvailable: true }).detail)
      .toContain('in place');
  });

  /** Render the panel with Onshape selected, serving canned Onshape replies.
   * `send` decides what POST /send answers, so one helper covers the happy
   * path, the consent path, and a failure. */
  const renderOnshape = async (
    status: Record<string, unknown>,
    send: () => Response | Promise<Response> = () => json({}),
    connection: Record<string, unknown> = {
      configured: true, reachable: true, credentialsPath: '/x/onshape.env', detail: null,
      insecureKeyFile: false, account: { id: 'ACC', name: 'Owner' },
      plan: { name: 'Onshape Free public only', group: 'Free', publicOnly: true },
    },
  ) => {
    preferencesStore.update({ cadApplication: 'onshape' });
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      calls.push(path);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      if (path.startsWith('/api/cadlink/onshape/connection')) return json(connection);
      if (path.endsWith('/onshape/status')) return json(status);
      if (path.endsWith('/onshape/send')) return send();
      return json({}, 404);
    }));
    await act(async () => {
      root.render(<CadLinkTestSurface/>);
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });
    return calls;
  };

  const onshapeStatus = (overrides: Record<string, unknown> = {}) => ({
    state: 'not_linked',
    credentials: { configured: true, credentialsPath: '/x/onshape.env', detail: null, insecureKeyFile: false },
    link: null,
    matchingLinks: [],
    selectedInstanceId: null,
    wgChangesAvailable: false,
    currentFormula: 'osse',
    ...overrides,
  });

  it('shows no Onshape UI and never calls its routes when the server does not offer Onshape', async () => {
    preferencesStore.setOnshapeAvailable(false);
    const calls = await renderOnshape(onshapeStatus());
    expect(host.textContent).not.toContain('Onshape');
    expect(calls.filter((path) => path.includes('/onshape'))).toEqual([]);
  });

  it('offers to create an Onshape document and states the public-plan consequence', async () => {
    const calls = await renderOnshape(onshapeStatus());
    expect(host.querySelector('.cad-connection')?.textContent).toContain('Not in Onshape yet');
    expect(host.querySelector<HTMLButtonElement>('.cad-primary-action')?.textContent)
      .toContain('in Onshape');
    expect(host.textContent).toContain('makes every document world-readable');
    expect([...host.querySelectorAll('.cad-alert-notice[role="status"]')]
      .some((notice) => notice.textContent?.includes('makes every document world-readable'))).toBe(true);
    // The Onshape leg needs no workspace folder and no Fusion heartbeat.
    expect(calls.some((path) => path.includes('/fusion-status'))).toBe(false);
    expect(calls.some((path) => path.includes('/returns'))).toBe(false);
  });

  it('does not offer the Fusion workspace-folder return workflow under Onshape', async () => {
    await renderOnshape(onshapeStatus());
    expect(host.textContent).toContain('No CAD model yet');
    expect(host.textContent).toContain('Send this design to Onshape');
    expect(host.querySelector('.cad-bundle-list')).toBeNull();
  });

  it('shows exact Onshape link choices and offers no action while ambiguous', async () => {
    const link = (instanceId: string, documentName: string) => ({
      instanceId,
      designId: 'wgd_a', accountId: 'ACC', documentId: `DID-${instanceId}`, workspaceId: 'WID',
      documentName, documentUrl: null, isPublic: false, partStudioElementId: 'PART',
      variableStudioElementId: null, featureStudioElementId: null, nativeFeatureId: null,
      datumFeatureStudioElementId: null, datumFeatureId: null, buildMode: 'import',
      lastSequence: 1, updatedAt: '2026-08-20T12:00:00Z',
    });
    await renderOnshape(onshapeStatus({
      state: 'instance_selection_required',
      matchingLinks: [link('wgo_a', 'Cabinet A'), link('wgo_b', 'Cabinet B')],
    }));

    expect(host.querySelector('.cad-connection')?.textContent).toContain('Choose an Onshape link');
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Linked Onshape instance"]')?.options)
      .toHaveLength(3);
    expect(host.querySelector('.cad-primary-action')).toBeNull();
    expect(host.textContent).not.toContain('Bring Onshape geometry into WG');
  });

  it('returns a linked Onshape Part Studio, selects its ingest, and shows findings', async () => {
    useDocumentStore.setState({
      identity: { designId: 'wgd_a', lineageId: 'wgl_a', baseEditVersion: 2 },
    });
    const link = {
      designId: 'wgd_a', accountId: 'ACC', documentId: 'DID', workspaceId: 'WID',
      documentName: 'Tritonia', documentUrl: 'https://cad.onshape.com/documents/DID/w/WID',
      isPublic: true, partStudioElementId: 'PART', variableStudioElementId: 'VARS',
      featureStudioElementId: null, nativeFeatureId: null,
      datumFeatureStudioElementId: null, datumFeatureId: null, buildMode: 'import',
      lastSequence: 2, updatedAt: '2026-08-13T09:00:00Z',
    };
    preferencesStore.update({ cadApplication: 'onshape' });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.startsWith('/api/cadlink/onshape/connection')) return json({
        configured: true, reachable: true, credentialsPath: '/x/onshape.env', detail: null,
        insecureKeyFile: false, account: { id: 'ACC', name: 'Owner' },
        plan: { name: 'Professional', group: 'Professional', publicOnly: false },
      });
      if (path.endsWith('/onshape/status')) return json(onshapeStatus({ state: 'current', link }));
      if (path.endsWith('/onshape/return')) return json({
        translationId: 'TID',
        bundle: {
          name: 'wgr_demo.wgreturn', bundlePath: '/data/cadlink/onshape/wgreturn/wgr_demo.wgreturn',
          documentName: 'Tritonia', sourceCount: 1, instanceCount: 1,
        },
        ingest: { ...record, created_at: '2026-08-13T10:00:00Z' },
      });
      if (path.endsWith('/viewport-mesh') || path.endsWith('/mesh')) {
        return new Response(meshFixture, { status: 200 });
      }
      return json({}, 404);
    }));
    await act(async () => {
      root.render(<CadLinkTestSurface/>);
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });
    // The in-sync link folds quiet; its bring action lives in the fold body.
    const button = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((candidate) => candidate.textContent === 'Bring geometry into WG')!;
    await act(async () => {
      button.click();
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });

    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(record.ingest_id);
    expect(useCadReturnStore.getState().selectedBundle?.documentName).toBe('Tritonia');
    expect(host.querySelector('.cad-status-strip')?.textContent).toContain('Returned and ingested Tritonia');
    expect(host.querySelector('.cad-check-findings')?.textContent).toContain('freshness');
  });

  it('asks for confirmation before creating a world-readable document, then sends', async () => {
    let allowPublic: unknown = null;
    const mintedIdentity = { designId: 'wgd_a', lineageId: 'wgl_a', baseEditVersion: 1 };
    const statusIdentities: unknown[] = [];
    preferencesStore.update({ cadApplication: 'onshape' });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.startsWith('/api/cadlink/onshape/connection')) {
        return json({
          configured: true, reachable: true, credentialsPath: '/x/onshape.env', detail: null,
          insecureKeyFile: false, account: { id: 'ACC', name: 'Owner' },
          plan: { name: 'Onshape Free public only', group: 'Free', publicOnly: true },
        });
      }
      if (path.endsWith('/onshape/status')) {
        statusIdentities.push(JSON.parse(String(init?.body)).identity);
        return json(onshapeStatus());
      }
      if (path.endsWith('/onshape/send')) {
        allowPublic = JSON.parse(String(init?.body)).allowPublic;
        if (allowPublic !== true) {
          return json({ detail: 'This Onshape account is on the Free plan, which can only create public documents.' }, 428);
        }
        return json({
          bundlePath: '/data/x.wglink', bundleId: 'wgb_1', exportId: 'wge_1', sequence: 1,
          designHash: 'sha256:a', geometryHash: 'sha256:b', artifactSha256: 'sha256:c',
          identity: mintedIdentity,
          onshape: {
            documentId: 'DID', workspaceId: 'WID', documentName: 'Tritonia',
            documentUrl: 'https://cad.onshape.com/documents/DID/w/WID',
            createdDocument: true, isPublic: true, variablesPushed: 6, partNames: ['Tritonia'], accountId: 'ACC',
          },
        });
      }
      return json({}, 404);
    }));
    await act(async () => {
      root.render(<CadLinkTestSurface/>);
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });

    await act(async () => {
      host.querySelector<HTMLButtonElement>('.cad-primary-action')!.click();
      await Promise.resolve(); await Promise.resolve();
    });
    expect(allowPublic).toBe(false);
    const confirm = host.querySelector<HTMLElement>('.cad-direction-alert')!;
    expect(confirm.textContent).toContain('This document will be public');
    // Nothing is created until the user actually confirms.
    expect(host.querySelector('.cad-status-strip')).toBeNull();

    const proceed = [...confirm.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent!.startsWith('Continue'))!;
    await act(async () => {
      proceed.click();
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });
    expect(allowPublic).toBe(true);
    expect(host.querySelector('.cad-status-strip')?.textContent)
      .toContain('Created Tritonia in Onshape · 6 parameters · public document');
    expect(statusIdentities.at(-1)).toEqual(mintedIdentity);
  });

  /** The registry is committed before the upload, and a Free account's first
   * send is refused only at the upload: the copy the design was opened from
   * may already be overwritten, so it no longer counts as kept. */
  it('forgets the opened copy when an Onshape send is refused after the registry may have been written', async () => {
    const { applyOpenedDesign } = await import('../design/openCadProject');
    act(() => {
      applyOpenedDesign({
        dialect: 'ath', migrationsApplied: [],
        passthrough: { keysPreserved: [], blocksPreserved: [], keyCount: 0, blockCount: 0 },
        design: { ...useDesignStore.getState().design, R: 150 },
        cadlink: { identity: { designId: 'wgd_a', lineageId: 'wgl_a', baseEditVersion: 1 }, classification: 'current' },
      } as never, 'tritonia.cfg');
    });
    expect(useDocumentStore.getState().openedContentKey).not.toBeNull();
    act(() => useDesignStore.getState().updateField('R', 321));
    await renderOnshape(onshapeStatus(), () => json({ detail: 'This Onshape account can only create public documents.' }, 428));

    await act(async () => {
      host.querySelector<HTMLButtonElement>('.cad-primary-action')!.click();
      await new Promise((settle) => setTimeout(settle, 0));
    });

    expect(host.textContent).toContain('This document will be public');
    expect(useDocumentStore.getState().openedContentKey).toBeNull();
  });

  it('offers to update a linked document and links out to it', async () => {
    await renderOnshape(onshapeStatus({
      state: 'stale',
      wgChangesAvailable: true,
      link: {
        designId: 'wgd_a', accountId: 'ACC', documentId: 'DID', workspaceId: 'WID',
        documentName: 'Tritonia', documentUrl: 'https://cad.onshape.com/documents/DID/w/WID',
        isPublic: true, partStudioElementId: 'PART', variableStudioElementId: 'VARS',
        featureStudioElementId: null, nativeFeatureId: null,
        datumFeatureStudioElementId: null, datumFeatureId: null, buildMode: 'import',
        lastSequence: 2, updatedAt: '2026-08-13T09:00:00Z',
      },
    }));
    expect(host.querySelector('.cad-connection')?.textContent).toContain('WG design changed · Tritonia');
    expect(host.querySelector<HTMLButtonElement>('.cad-primary-action')?.textContent)
      .toBe('Send WG changes to Onshape');
    const link = host.querySelector<HTMLAnchorElement>('.cad-onshape-open')!;
    expect(link.href).toBe('https://cad.onshape.com/documents/DID/w/WID');
    expect(link.rel).toContain('noopener');
  });

  it('unlinks the linked Onshape document and leaves the document itself alone', async () => {
    const link = {
      instanceId: 'inst-a', designId: 'wgd_a', accountId: 'ACC', documentId: 'DID', workspaceId: 'WID',
      documentName: 'Tritonia', documentUrl: 'https://cad.onshape.com/documents/DID/w/WID',
      isPublic: false, partStudioElementId: 'PART', variableStudioElementId: 'VARS',
      featureStudioElementId: null, nativeFeatureId: null,
      datumFeatureStudioElementId: null, datumFeatureId: null, buildMode: 'import',
      lastSequence: 2, updatedAt: '2026-08-13T09:00:00Z',
    };
    useDocumentStore.getState().setCadLink({ designId: 'wgd_a', lineageId: 'wgl_a', baseEditVersion: 1 }, 'current');
    let linked = true;
    const unlinked: unknown[] = [];
    preferencesStore.update({ cadApplication: 'onshape' });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.startsWith('/api/cadlink/onshape/connection')) {
        return json({
          configured: true, reachable: true, credentialsPath: '/x/onshape.env', detail: null,
          insecureKeyFile: false, account: null, plan: null,
        });
      }
      if (path.endsWith('/onshape/status')) {
        return json(onshapeStatus(linked
          ? { state: 'stale', wgChangesAvailable: true, link, matchingLinks: [link], selectedInstanceId: 'inst-a' }
          : {}));
      }
      if (path.endsWith('/onshape/unlink')) {
        unlinked.push(JSON.parse(String(init?.body)));
        linked = false;
        return json({ unlinked: true });
      }
      return json({}, 404);
    }));
    await act(async () => {
      root.render(<CadLinkTestSurface/>);
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });

    const unlink = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === 'Unlink')!;
    expect(unlink.title).toContain('left as it is');
    // Two steps, like creating a public document: the first only asks.
    const question = () => [...host.querySelectorAll<HTMLElement>('.cad-direction-alert[role="alert"]')]
      .find((alert) => alert.textContent?.includes('Unlink Tritonia?'));
    await act(async () => { unlink.click(); });
    expect(question()).toBeDefined();
    expect(unlinked).toEqual([]);
    await act(async () => { buttonIn(question()!, 'Cancel')!.click(); });
    expect(question()).toBeUndefined();
    await act(async () => { unlink.click(); });
    await act(async () => { buttonIn(question()!, 'Unlink Tritonia')!.click(); });
    await vi.waitFor(() => expect(unlinked).toEqual([{ designId: 'wgd_a', instanceId: 'inst-a' }]));
    expect(question()).toBeUndefined();
    await vi.waitFor(() => expect(host.querySelector('.cad-status-strip')?.textContent).toContain('Unlinked Tritonia'));
    await vi.waitFor(() => {
      expect([...host.querySelectorAll('button')].some((button) => button.textContent === 'Unlink')).toBe(false);
    });
  });

  it('offers no action and explains where the key goes when Onshape is not connected', async () => {
    await renderOnshape(
      onshapeStatus({
        state: 'not_configured',
        credentials: { configured: false, credentialsPath: '/x/onshape.env', detail: 'No key pair', insecureKeyFile: false },
      }),
      () => json({}),
      { configured: false, reachable: false, credentialsPath: '/x/onshape.env', detail: 'No key pair', insecureKeyFile: false, account: null, plan: null },
    );
    expect(host.querySelector('.cad-connection')?.textContent).toContain('Onshape is not connected');
    expect(host.querySelector('.cad-connection')?.textContent).toContain('/x/onshape.env');
    expect(host.querySelector('.cad-primary-action')).toBeNull();
  });

  it('routes an insecure Onshape key file through the error treatment', async () => {
    await renderOnshape(
      onshapeStatus(),
      () => json({}),
      {
        configured: true, reachable: true, credentialsPath: '/x/onshape.env', detail: null,
        insecureKeyFile: true, account: { id: 'ACC', name: 'Owner' },
        plan: { name: 'Professional', group: 'Professional', publicOnly: false },
      },
    );
    expect(host.querySelector('.cad-alert-error[role="alert"]')?.textContent).toContain('chmod 600');
    expect(host.querySelector('.cad-alert-notice[role="alert"]')).toBeNull();
  });

  it('reports an Onshape send failure without claiming success', async () => {
    await renderOnshape(
      onshapeStatus(),
      () => json({ detail: 'Onshape rate limit reached (429).' }, 429),
    );
    await act(async () => {
      host.querySelector<HTMLButtonElement>('.cad-primary-action')!.click();
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });
    expect(host.querySelector('.cad-alert-error[role="alert"]')?.textContent).toContain('rate limit');
    expect(host.querySelector('.cad-status-strip')).toBeNull();
  });

  it('does not attach an Onshape identity to a design changed during upload', async () => {
    let resolveSend!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => { resolveSend = resolve; });
    await renderOnshape(onshapeStatus(), () => pending);
    act(() => host.querySelector<HTMLButtonElement>('.cad-primary-action')!.click());
    act(() => useDesignStore.getState().updateField('R', 155));

    await act(async () => {
      resolveSend(json({
        bundlePath: '/data/x.wglink', bundleId: 'wgb_1', exportId: 'wge_1', sequence: 1,
        designHash: 'sha256:a', geometryHash: 'sha256:b', artifactSha256: 'sha256:c',
        identity: { designId: 'wgd_stale', lineageId: 'wgl_stale', baseEditVersion: 1 },
        onshape: {
          documentId: 'DID', workspaceId: 'WID', documentName: 'Old design',
          documentUrl: 'https://cad.onshape.com/documents/DID/w/WID', createdDocument: true,
          isPublic: false, variablesPushed: 6, partNames: ['Old design'], accountId: 'ACC',
        },
      }));
      await pending;
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });

    expect(useDocumentStore.getState().identity).toBeNull();
    expect(host.querySelector('.cad-status-strip')?.textContent).toContain('WG design changed while it was uploading');
  });

  it('recognizes a newly written or replaced Fusion return for automatic opening', () => {
    const previous = new Map([[listing.items[0].bundlePath, listing.items[0].modifiedAt]]);
    const replaced = { ...listing.items[0], modifiedAt: '2026-08-12T15:31:00Z' };
    expect(newestReturnArrival([replaced], previous, Date.parse('2026-08-12T15:31:05Z'))).toBe(replaced);
    expect(newestReturnArrival(listing.items, previous, Date.parse('2026-08-12T15:31:05Z'))).toBeNull();
    expect(newestReturnArrival([replaced], null, Date.parse('2026-08-12T15:31:05Z'))).toBe(replaced);
  });

  it('shows which WGLink the connected add-in reports, and nothing when it reports none', async () => {
    const withFusion = (status: FusionCadStatus) => {
      vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input);
        if (path.endsWith('/returns')) return json(listing);
        if (path.endsWith('/fusion-status')) return json(status);
        return json(record);
      }));
    };

    withFusion({ ...currentFusion, adapterVersion: '0.1.1' });
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    // Informational only, so it is a hover on the card, not a line in it.
    const summary = () => host.querySelector('.cad-link-quiet > summary')!;
    expect(summary().getAttribute('title')).toContain('The Waveguide Generator add-in in Fusion 0.1.1');
    expect(host.querySelector('.cad-link-card')!.textContent).not.toContain('The Waveguide Generator add-in in Fusion');
    // It must not turn into a connection problem: the workflow state is untouched.
    expect(host.querySelector('.cad-connection-dot-current')).not.toBeNull();

    await act(async () => { root.unmount(); });
    root = createRoot(host);
    withFusion({ ...currentFusion, adapterVersion: null });
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    expect(summary().getAttribute('title')).not.toContain('The Waveguide Generator add-in in Fusion');
  });

  it('selects the newest readable return when CAD Link first mounts', async () => {
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(listing.items[0].bundlePath);
    expect(host.querySelector('.cad-model-name')?.textContent).toBe('Speaker');
    expect([...host.querySelectorAll<HTMLButtonElement>('button')]
      .some((button) => button.textContent === 'Prepare simulation')).toBe(true);
    expect(host.querySelector('.cad-history .section-head')?.getAttribute('aria-expanded')).toBe('false');
    expect(host.textContent).not.toContain('Mesh detail');
  });

  it('points directly to the Simulation tab from the Solve card', async () => {
    const activate = vi.spyOn(workspaceNavigation, 'activate');
    // Nothing gates this model's solve.
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      return json({ ...record, findings: [] });
    }));
    await renderAndSelect();
    await clickIngest();

    // The settings line on the Solve card opens them where they live.
    const open = host.querySelector<HTMLButtonElement>('.cad-solve-card button[data-action="open-settings"]')!;
    expect(open.textContent).toBe('Settings…');
    // The hover tooltip names the inputs that moved to the Simulation tab.
    expect(open.title).toContain('Drivers, crossover, sweep, directivity');
    act(() => open.click());
    expect(activate).toHaveBeenCalledWith('simulation');
  });

  it('shows the Solve card, never a "prepared" claim, while a finding still stands before the solve', async () => {
    await renderAndSelect();
    await clickIngest();
    // The record's blocking finding still needs approving: the Solve card is
    // what is there, and it claims nothing about readiness.
    expect(host.querySelector('.cad-model-card .cad-solve-card')).not.toBeNull();
    expect(host.textContent).not.toContain('Prepared for simulation');
  });

  it('rolls the selected return summary into its preparing state', async () => {
    let resolveIngest!: (response: Response) => void;
    const pendingIngest = new Promise<Response>((resolve) => { resolveIngest = resolve; });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/returns')) return json(listing);
      if (String(input).endsWith('/fusion-status')) return json(closedFusion);
      if (String(input).endsWith('/ingest')) return pendingIngest;
      return json(record);
    }));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });

    act(() => host.querySelector<HTMLButtonElement>('.cad-model-card .cad-primary-action')!.click());
    expect(host.querySelector('.cad-model-card .cad-primary-action')?.textContent).toContain('Preparing…');
    expect(host.querySelector<HTMLButtonElement>('.cad-model-card .cad-primary-action')?.disabled).toBe(true);
    await act(async () => {
      resolveIngest(json(record));
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });
  });

  it('renders a relative-time summary and a collapsed, selectable history with correct plurality', async () => {
    const now = Date.parse('2026-08-20T12:00:00Z');
    vi.spyOn(Date, 'now').mockReturnValue(now);
    const historyListing: CadReturnListing = {
      cadFolderConfigured: true,
      items: [
        {
          ...listing.items[0],
          name: 'unlinked.wgreturn',
          bundlePath: 'wgreturn/unlinked.wgreturn',
          modifiedAt: '2026-08-20T11:58:00Z',
          documentName: null,
          instanceCount: 0,
        },
        {
          ...listing.items[0],
          name: 'speaker-two.wgreturn',
          bundlePath: 'wgreturn/speaker-two.wgreturn',
          modifiedAt: '2026-08-20T11:00:00Z',
          documentName: 'Speaker two',
          sourceCount: 2,
          instanceCount: 2,
        },
      ],
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/fusion-status')
      ? json(closedFusion)
      : json(historyListing)));

    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    const identity = host.querySelector('.cad-model-identity')!;
    expect(identity.querySelector('.cad-model-name')?.textContent).toMatch(/^Return · \d{2}:\d{2}$/);
    expect(identity.querySelector('time')?.textContent).toBe('2 min ago');
    expect(identity.querySelector('time')?.title).toBeTruthy();
    const disclosure = host.querySelector<HTMLButtonElement>('.cad-history .section-head')!;
    expect(disclosure.textContent).toContain('Model versions (2)');
    expect(disclosure.getAttribute('aria-expanded')).toBe('false');
    expect(host.querySelector('.cad-bundle-list')).toBeNull();

    openHistory();
    const options = [...host.querySelectorAll<HTMLButtonElement>('[role="option"]')];
    expect(options).toHaveLength(2);
    expect(options[0].getAttribute('aria-selected')).toBe('true');
    expect(options[1].getAttribute('aria-selected')).toBe('false');
    expect(options[0].textContent).toContain('1 source');
    expect(options[0].textContent).not.toContain('linked instance');
    expect(options[1].textContent).toContain('2 sources · 2 linked instances');

    act(() => options[1].click());
    expect(options[0].getAttribute('aria-selected')).toBe('false');
    expect(options[1].getAttribute('aria-selected')).toBe('true');
  });

  it('keeps another project out of the active return history', async () => {
    useDocumentStore.getState().setCadLink({
      designId: 'wgd_current', lineageId: 'wgl_current', baseEditVersion: 1,
    }, 'current');
    const historyListing: CadReturnListing = {
      cadFolderConfigured: true,
      items: [
        { ...listing.items[0], designIds: ['wgd_current'], documentName: 'Current project' },
        {
          ...listing.items[0], designIds: ['wgd_other'], documentName: 'Other project',
          bundlePath: 'wgreturn/other.wgreturn', modifiedAt: '2026-08-12T00:00:00Z',
        },
      ],
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/fusion-status')
      ? json(closedFusion)
      : json(historyListing)));

    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });

    openHistory();
    expect(host.querySelector('.cad-history')?.textContent)
      .toContain('1 return from other CAD-linked projects is not listed');
    const history = host.querySelector('.cad-bundle-list')!;
    expect(history.textContent).toContain('Current project');
    expect(history.textContent).not.toContain('Other project');
  });

  it('collapses all clean record details by default and preserves a semantic heading hierarchy', async () => {
    const cleanRecord: CadReturnIngestRecord = {
      ...record,
      freshness: { verdict: 'per-instance', instances: [{ instance_id: 'instance-a', verdict: 'current' }] },
      findings: [],
      symmetry: { planes: { x0: { accepted: true }, y0: { accepted: true } }, cut_planes: ['x0', 'y0'] },
      polar_grid_derivation: {
        axes: {
          horizontal: { symmetry_accepted: true },
          vertical: { symmetry_accepted: true },
        },
        cut_planes: ['x0', 'y0'],
      },
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/returns')) return json(listing);
      if (String(input).endsWith('/fusion-status')) return json(closedFusion);
      return json(cleanRecord);
    }));
    await renderAndSelect();
    await clickIngest();

    // Six always-open drawers became one collapsed checklist line.
    const checks = host.querySelector<HTMLElement>('.cad-checks')!;
    expect(checks.querySelector('.section-head')?.getAttribute('aria-expanded')).toBe('false');
    expect(checks.querySelector('.cad-state-chip')?.textContent).toBe('all passed');
    expect(checks.querySelector('.section-head')?.textContent).toContain('Checks (6)');
    expect(host.querySelector('h2')?.textContent).toBe('CAD Link');
    expect(host.querySelector('h3')).toBeTruthy();
    expect(checks.querySelector('h4')).toBeTruthy();
  });

  it('auto-expands degraded scope and combines degradation with pending findings in the summary', async () => {
    const degradedRecord: CadReturnIngestRecord = {
      ...record,
      scope: {
        status: 'degraded',
        degraded_skip_count: 2,
        skipped: [
          { object_id: 'body-a', name: 'Body A', severity: 'warning', reason: 'unsupported' },
          { object_id: 'body-b', name: 'Body B', severity: 'warning', reason: 'suppressed' },
        ],
      },
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/returns')) return json(listing);
      if (String(input).endsWith('/fusion-status')) return json(closedFusion);
      return json(degradedRecord);
    }));
    await renderAndSelect();
    await clickIngest();

    const checks = host.querySelector<HTMLElement>('.cad-checks')!;
    expect(checks.querySelector('.section-head')?.getAttribute('aria-expanded')).toBe('true');
    expect(checks.querySelector('.cad-state-chip')?.textContent).toContain('need attention');
    const scope = [...checks.querySelectorAll<HTMLDetailsElement>('details.cad-check')]
      .find((row) => row.textContent?.includes('Scope'))!;
    expect(scope.open).toBe(true);
    expect(scope.textContent).toContain('2 objects skipped — solve is degraded');
    expect(scope.textContent).toContain('Skipped · Body A');
    expect(scope.textContent).toContain('Skipped · Body B');
  });

  it('shows symmetry residuals in millimetres to two significant figures, and a rejected candidate as information', async () => {
    const stepUnitRecord = {
      ...record,
      symmetry: {
        cut_planes: [],
        planes: {
          x0: {
            accepted: false,
            max_residual_step_units: 0.125,
            worst_off_model_distance_step_units: 0.25,
          },
        },
      },
    } as CadReturnIngestRecord;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      if (path.endsWith('/ingest')) return json(stepUnitRecord);
      return json({}, 404);
    }));

    await renderAndSelect();
    await clickIngest();

    // WG imports STEP through OpenCASCADE in millimetres (the mesh it builds
    // from the same geometry is read as points_mm), so the verifier's STEP
    // units are millimetres: shown as such, to two significant figures.
    expect(host.textContent).toContain('max residual 0.13 mm');
    expect(host.textContent).toContain('worst off-model 0.25 mm');
    expect(host.textContent).not.toContain('STEP units');
    const symmetry = [...host.querySelectorAll<HTMLDetailsElement>('details.cad-check')]
      .find((row) => row.textContent?.includes('Symmetry'))!;
    // A rejected candidate is information, not a failing check.
    expect(symmetry.textContent).toContain('Full model');
    expect(symmetry.className).toContain('cad-check-ok');
    expect(symmetry.textContent).not.toContain('Resolved independently from Parametric mode');
    // The hover explanation carries the safe-domain rule.
    expect(symmetry.querySelector('summary')?.title).toContain('keeps the larger safe domain');
  });

  it('routes neutral notices separately from errors', async () => {
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    act(() => useCadReturnStore.setState({ ingestStaleReason: 'The return changed after preparation.' }));
    const staleNotice = [...host.querySelectorAll<HTMLElement>('.cad-alert-notice[role="status"]')]
      .find((notice) => notice.textContent?.includes('return changed'));
    expect(staleNotice).toBeTruthy();
    expect(staleNotice?.classList.contains('cad-alert-error')).toBe(false);
    expect(host.querySelector('.cad-solver-unavailable.cad-alert-notice[role="status"]')).toBeTruthy();
  });

  it('does not claim no engine can solve a CAD model when BEAT · Metal is offered', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      if (path.endsWith('/api/capabilities')) return json({
        engines: [
          { name: 'metal', available: false, reason: 'No Metal device', version: null, fast_paths: [], geometry_sources: ['parametric'] },
          { name: 'beat-metal', available: true, reason: null, version: null, fast_paths: [], geometry_sources: ['parametric'] },
        ],
      });
      return json(record);
    }));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    await vi.waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith('/api/capabilities'))).toBe(true));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 50)); });
    expect(host.querySelector('.cad-solver-unavailable')).toBeNull();
    expect(host.textContent).not.toContain('No engine here');
  });

  it('says no engine can solve a CAD model when none that could is available', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      if (path.endsWith('/api/capabilities')) return json({
        engines: [
          { name: 'metal', available: false, reason: 'No Metal device', version: null, fast_paths: [], geometry_sources: ['parametric'] },
          { name: 'beat-metal', available: false, reason: 'No Metal device', version: null, fast_paths: [], geometry_sources: ['parametric'] },
        ],
      });
      return json(record);
    }));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    await vi.waitFor(() => expect(host.querySelector('.cad-solver-unavailable')).toBeTruthy());
    expect(host.textContent).toContain('No engine here can solve this CAD model');
  });

  it('shows the connection state with its settings link and the symmetric outbound action', async () => {
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    const card = host.querySelector('.cad-link-card')!;
    expect(card.textContent).toContain('Fusion 360 is closed');
    expect([...card.querySelectorAll<HTMLButtonElement>('button')]
      .some((button) => button.textContent === 'Settings')).toBe(true);
    // A closed Fusion offers its resolving action right on the card.
    expect(card.querySelector('.cad-primary-action')?.textContent).toBe('Open in Fusion 360');
    expect(host.textContent).not.toContain('WG → CAD');
  });

  it('shows an up-to-date active Fusion document and the communicated parameter count', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/fusion-status')
      ? json(currentFusion)
      : json(listing)));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    const quiet = host.querySelector<HTMLDetailsElement>('.cad-link-quiet')!;
    expect(quiet.open).toBe(false);
    expect(quiet.querySelector('summary')?.textContent).toContain('Fusion 360 · in sync');
    expect(quiet.querySelector('summary')?.textContent).toContain('Tritonia V');
    // The full detail is one hover — or one click — away.
    expect(quiet.querySelector('summary')?.title).toContain('13 managed CAD parameters');
    expect(quiet.textContent).toContain('13 managed CAD parameters');
    expect(host.querySelector('.cad-link-card .cad-primary-action')).toBeNull();
  });

  it('explains a stale link in the connection card and offers the update in place', async () => {
    const stale = { ...currentFusion, state: 'stale' as const, currentFormula: 'R-OSSE', fusionFormula: 'osse', wgChangesAvailable: true };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/fusion-status')
      ? json(stale)
      : json(listing)));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    expect(host.querySelector('.cad-connection')?.textContent).toContain('Fusion has OSSE; WG is now R-OSSE');
    expect(host.querySelector('.cad-primary-action')?.textContent).toBe('Send WG changes to Fusion');
  });

  it('explains local Fusion parameter edits instead of claiming synchronization', async () => {
    const stale = { ...currentFusion, state: 'stale' as const, wgChangesAvailable: true, link: { ...currentFusion.link!, parameterDriftCount: 2 } };
    expect(fusionWorkflowView(stale).detail).toContain('2 managed Fusion parameters have local edits');
  });

  it('keeps Fusion body changes separate from WG parameter changes', () => {
    const fusionOnly = {
      ...currentFusion,
      state: 'stale' as const,
      fusionChangesAvailable: true,
      link: { ...currentFusion.link!, localBodyState: 'modified' as const },
    };
    expect(fusionWorkflowView(fusionOnly)).toMatchObject({
      headline: 'Fusion geometry has changed · Tritonia V', action: null,
    });
    const both = { ...fusionOnly, wgChangesAvailable: true };
    expect(fusionWorkflowView(both)).toMatchObject({
      headline: 'WG and Fusion both changed · Tritonia V', action: 'update',
    });
  });

  it('shows both directions and confirms before replacing a changed linked waveguide', async () => {
    useDocumentStore.setState({
      identity: { designId: 'wgd_a', lineageId: 'wgl_a', baseEditVersion: 2 },
    });
    const both = {
      ...currentFusion,
      state: 'stale' as const,
      wgChangesAvailable: true,
      fusionChangesAvailable: true,
      link: { ...currentFusion.link!, localBodyState: 'modified' as const },
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/fusion-status')) return json(both);
      if (path.endsWith('/returns')) return json(listing);
      return json(record);
    }));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });

    // Both directions live on the one card; sending still parks on the
    // coordinator's conflict dialog because Fusion changed too.
    expect(host.textContent).toContain('Bring Fusion changes in');
    const send = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === 'Send WG changes to Fusion')!;
    await act(async () => { send.click(); await Promise.resolve(); });
    expect(host.textContent).toContain('Both WG and Fusion changed');
    expect(host.textContent).toContain('Continue: send WG changes');
  });

  it('keeps both Fusion pull controls busy and suppresses repeated requests until arrival', async () => {
    useDocumentStore.setState({
      identity: { designId: 'wgd_a', lineageId: 'wgl_a', baseEditVersion: 2 },
    });
    const fusion = { ...currentFusion, state: 'stale' as const, fusionChangesAvailable: true };
    let resolveRequest!: (response: Response) => void;
    const request = new Promise<Response>((resolve) => { resolveRequest = resolve; });
    let requestCount = 0;
    let returns = { items: listing.items };
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/fusion-status')) return Promise.resolve(json(fusion));
      if (path.endsWith('/returns')) return Promise.resolve(json(returns));
      if (path.endsWith('/request-fusion-return')) {
        requestCount += 1;
        return request;
      }
      return Promise.resolve(json(record));
    }));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    const pullButtons = () => [...host.querySelectorAll<HTMLButtonElement>('.cad-link-card .cad-confirm-actions button')];

    await act(async () => {
      pullButtons()[0].click();
      cadWorkspace.getSnapshot().pullAndSolve();
      cadWorkspace.getSnapshot().pullFromFusion();
      await Promise.resolve();
    });
    expect(requestCount).toBe(1);
    expect(pullButtons()).toHaveLength(2);
    expect(pullButtons().every((button) => button.disabled)).toBe(true);
    expect(pullButtons().map((button) => button.textContent)).toEqual(['Waiting for Fusion…', 'Waiting for Fusion…']);

    await act(async () => {
      resolveRequest(json({ status: 'requested', requestId: 'req_busy', documentName: 'Tritonia V' }));
      await Promise.resolve(); await Promise.resolve();
    });
    expect(pullButtons().every((button) => button.disabled)).toBe(true);
    returns = { items: [{ ...listing.items[0], requestId: 'req_busy' }] };
    await act(async () => {
      await cadWorkspace.getSnapshot().refresh({ background: true, autoOpenNew: true });
      await Promise.resolve();
    });
    expect(cadWorkspace.getSnapshot().pullingFromFusion).toBe(false);
    expect(requestCount).toBe(1);
  });

  it('puts a Fusion refusal on the primary path and its report behind a disclosure', async () => {
    useDocumentStore.setState({
      identity: { designId: 'wgd_a', lineageId: 'wgl_a', baseEditVersion: 2 },
    });
    const refusal = "WGLink instance '393aaad4-9e78-462d-ad6c-126d411fbefd' "
      + 'has no resolvable wrapper occurrence; placement was not defaulted to identity.';
    const fusion = { ...currentFusion, state: 'stale' as const, fusionChangesAvailable: true };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/fusion-status')) return json(fusion);
      if (path.endsWith('/returns')) return json({ cadFolderConfigured: true, items: [] });
      if (path.endsWith('/return-requests/req_refused')) return json({ requestId: 'req_refused', state: fixtureOperations.req_refused?.state ?? 'processing', reason: null, message: fixtureOperations.req_refused?.message ?? null });
      if (path.endsWith('/request-fusion-return')) {
        return json({ status: 'requested', requestId: 'req_refused', documentName: 'Tritonia V' });
      }
      return json(record);
    }));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => {
      cadWorkspace.getSnapshot().pullFromFusion().catch(() => undefined);
      await Promise.resolve(); await Promise.resolve();
    });
    act(() => {
      applyCadRequest({
        operationId: 'req_refused', kind: 'request_return', state: 'rejected', stage: 'executing',
        reason: 'adapter_refused', message: refusal, jobId: null, attemptGeneration: 1,
        setupRevisionId: null, preparationId: null, snapshot: null, legacy: false,
        createdAt: '2026-09-20T10:00:00Z', updatedAt: '2026-09-20T10:00:01Z',
      } as CadOperationSummary);
    });
    await act(async () => {
      await cadWorkspace.getSnapshot().refresh({ background: true, autoOpenNew: true });
      await Promise.resolve();
    });

    const alert = host.querySelector<HTMLElement>('.cad-alert-error[role="alert"]')!;
    expect(alert).not.toBeNull();
    const disclosure = alert.querySelector<HTMLDetailsElement>('details.cad-alert-diagnostics')!;
    expect(disclosure).not.toBeNull();
    const summary = disclosure.querySelector<HTMLElement>('summary')!;

    // Primary path: what happened and what to do, with no opaque identity.
    const primary = alert.textContent!.replace(disclosure.textContent!, '');
    expect(primary).toContain('refused');
    expect(primary).not.toContain('393aaad4');
    expect(primary).not.toContain('defaulted to identity');

    // The disclosure is shut to begin with, and its evidence is not read out
    // as part of the alert until it is opened.
    expect(disclosure.open).toBe(false);

    // Accessible: a real summary element, with a name, reachable by keyboard
    // and operable from it.
    expect(summary.tagName).toBe('SUMMARY');
    expect(summary.textContent).toBe('Diagnostics');
    summary.focus();
    expect(document.activeElement).toBe(summary);
    act(() => { summary.click(); });
    expect(disclosure.open).toBe(true);
    expect(disclosure.textContent).toContain(refusal);
    expect(disclosure.textContent).toContain('393aaad4-9e78-462d-ad6c-126d411fbefd');

    // A later, unrelated error takes the alert over. The report belonged to
    // the refusal, so it goes with it rather than standing under a headline it
    // does not explain.
    await act(async () => { cadWorkspace.getSnapshot().reportError('The jobs system is not answering.'); });
    const replaced = host.querySelector<HTMLElement>('.cad-alert-error[role="alert"]')!;
    expect(replaced.textContent).toContain('The jobs system is not answering.');
    expect(replaced.querySelector('details.cad-alert-diagnostics')).toBeNull();
    expect(replaced.textContent).not.toContain('393aaad4');
  });

  it('records blocking findings on the wire, filters skipped sizes, and emits range/list sweep shapes', () => {
    useCadReturnStore.getState().selectBundle(listing.items[0]);
    useCadReturnStore.getState().applyIngest(record, useCadReturnStore.getState().beginIngestIntent());
    useCadReturnStore.setState({
      meshSizes: { ...useCadReturnStore.getState().meshSizes, source_size_mm: { 'source-hf': 3.25, optional: 9 } },
      skippedSourceIds: ['optional'],
    });
    useCadReturnStore.getState().setSweep({ frequencyStartHz: 250, frequencyEndHz: 12_000, frequencyCount: 31 });

    const range = buildImportedSubmission(useCadReturnStore.getState());
    expect(range.geometry.acknowledged_findings).toEqual([`${record.report_sha256}:finding-a`]);
    expect(range.geometry.mesh?.source_size_mm).toEqual({ 'source-hf': 3.25 });
    expect(range.options).toMatchObject({ frequency_range: [250, 12_000], num_frequencies: 31 });
    expect(range.options).not.toHaveProperty('frequencies_hz');

    useImportedSolveOptionsStore.getState().setFrequencyMode('list');
    useImportedSolveOptionsStore.getState().setFrequencyListText('300 700 1500');
    const list = buildImportedSubmission(useCadReturnStore.getState());
    expect(list.options.frequencies_hz).toEqual([300, 700, 1_500]);
    expect(list.options).not.toHaveProperty('frequency_range');
    expect(list.options).not.toHaveProperty('num_frequencies');
  });

  it('refuses to build an imported submission without an ingestion record', () => {
    expect(() => buildImportedSubmission(useCadReturnStore.getState())).toThrow('Ingest a CAD return');
  });

  it('emits the combine wire unless switched off, chained by role band order', () => {
    useCadReturnStore.getState().selectBundle(listing.items[0]);
    useCadReturnStore.getState().applyIngest(record, useCadReturnStore.getState().beginIngestIntent());
    useCadReturnStore.setState({
      selectedBundle: {
        ...listing.items[0],
        sources: [
          { id: 'source-hf', role: 'HF', required: true, suggestedResolutionMm: 4, defaultDriveChannelId: 'drive-hf' },
          { id: 'source-mf', role: 'MF', required: false, suggestedResolutionMm: 8, defaultDriveChannelId: 'drive-mf' },
        ],
      },
      // Listed HF-first on purpose: the chain must still run MF -> HF.
      driveChannels: [
        { id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' },
        { id: 'drive-mf', source_ids: ['source-mf'], motion: 'normal' },
      ],
    });
    useCadReturnStore.getState().setSweep({ frequencyStartHz: 200, frequencyEndHz: 5_000, frequencyCount: 24 });

    // Two drive channels combine without being asked to; the MF -> HF role
    // default is 1000 Hz and the 200 Hz - 5 kHz sweep carries it.
    expect(buildImportedSubmission(useCadReturnStore.getState()).geometry.combine)
      .toEqual(toWire(expandLegacy(['drive-mf', 'drive-hf'], [1_000])));

    useCadReturnStore.getState().setCombineEnabled(false);
    expect(buildImportedSubmission(useCadReturnStore.getState()).geometry).not.toHaveProperty('combine');

    useCadReturnStore.getState().setCombineEnabled(true);

    useCadReturnStore.getState().setCombineSpec(withPair(combineSpecEffective(useCadReturnStore.getState())!, 'drive-mf\u2192drive-hf', { hz: 1_200 }));
    expect(buildImportedSubmission(useCadReturnStore.getState()).geometry.combine)
      .toEqual(toWire(expandLegacy(['drive-mf', 'drive-hf'], [1_200])));

    useCadReturnStore.getState().setCombineSpec(withDelayMode(combineSpecEffective(useCadReturnStore.getState())!, 'manual'));
    expect(buildImportedSubmission(useCadReturnStore.getState()).geometry.combine?.channels?.['drive-hf'].delay)
      .toEqual({ mode: 'manual', ms: 0 });

    // A single remaining channel drops the wire even while enabled.
    useCadReturnStore.setState({ driveChannels: [{ id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' }] });
    expect(buildImportedSubmission(useCadReturnStore.getState()).geometry).not.toHaveProperty('combine');
  });


  it('widens the polar request to the derivation instead of submitting a narrowing grid', () => {
    useCadReturnStore.getState().selectBundle(listing.items[0]);
    useCadReturnStore.getState().applyIngest(record, useCadReturnStore.getState().beginIngestIntent());
    const submission = buildImportedSubmission(useCadReturnStore.getState());
    const polar = submission.options.polar_config as {
      angle_range: [number, number, number];
      enabled_axes: string[];
    };
    // The record pins vertical and diagonal (rejected mirror planes): the
    // default 0..180/37 grid must widen to a full circle at the same 5° step,
    // with every pinned axis enabled.
    expect(polar.angle_range[0]).toBe(-180);
    expect(polar.angle_range[1]).toBe(180);
    expect(polar.angle_range[2]).toBe(73);
    expect(polar.enabled_axes).toEqual(expect.arrayContaining(['vertical', 'diagonal']));
  });

  it('size change → re-ingest carries the new report on the finding wire', async () => {
    let ingestCount = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/returns')) return json(listing);
      if (String(input).endsWith('/fusion-status')) return json(closedFusion);
      // The advisory display-artifact fetches after each ingest are not ingests.
      if (String(input).endsWith('/viewport-mesh') || String(input).endsWith('/mesh')) {
        return new Response('missing', { status: 404 });
      }
      // Only the ingest route advances the report; the background polls (the
      // CAD solve command among them) must not be counted as one.
      if (!String(input).endsWith('/ingest')) return json({ command: null });
      ingestCount += 1;
      const next = {
        ...record,
        ingest_id: `${record.ingest_id.slice(0, -1)}${ingestCount}`,
        report_sha256: `sha256:${String(ingestCount).repeat(64)}`,
        mesh_sizes: {
          ...record.mesh_sizes,
          source_size_mm: { 'source-hf': ingestCount === 1 ? 4 : 2.5 },
        },
      };
      return new Response(JSON.stringify(next), { status: 200 });
    }));
    await renderAndSelect();
    await clickIngest();
    act(() => useCadReturnStore.setState(s => ({ meshSizes: { ...s.meshSizes, source_size_mm: { ...s.meshSizes.source_size_mm, ['source-hf']: 2.5 } } })));
    await act(async () => { await cadWorkspace.getSnapshot().ingest(); });
    const submission = buildImportedSubmission(useCadReturnStore.getState());
    expect(submission.geometry).toMatchObject({
      mesh: { source_size_mm: { 'source-hf': 2.5 } },
      acknowledged_findings: [`sha256:${'2'.repeat(64)}:finding-a`],
    });
  });

  it('marks a changed refreshed bundle stale and preserves sizing edits', async () => {
    let listingChanged = false;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/returns')) {
        const body = !listingChanged ? listing : { cadFolderConfigured: true, items: [{
          ...listing.items[0], modifiedAt: '2026-08-11T01:00:00Z',
          sources: [{ ...listing.items[0].sources[0], suggestedResolutionMm: 2.75 }],
        }] };
        return json(body);
      }
      if (String(input).endsWith('/fusion-status')) return json(closedFusion);
      return json(record);
    }));
    await renderAndSelect();
    await clickIngest();
    act(() => useCadReturnStore.setState(s => ({ meshSizes: { ...s.meshSizes, source_size_mm: { ...s.meshSizes.source_size_mm, ['source-hf']: 2.5 } } })));
    listingChanged = true;
    const refresh = host.querySelector<HTMLButtonElement>('button[aria-label="Refresh CAD returns"]')!;
    await act(async () => { refresh.click(); await Promise.resolve(); await Promise.resolve(); });

    expect(host.textContent).toContain('source inventory or source sizing suggestions changed');
    expect(useCadReturnStore.getState().meshSizes.source_size_mm['source-hf']).toBe(2.5);
    expect(importedSubmissionBlocker()).toContain('source inventory or source sizing suggestions changed');
    expect(host.textContent).not.toContain('Rebuild mesh');
  });

  it('renders an unreadable listing row disabled with the server reason', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/fusion-status') ? json(closedFusion) : json({ cadFolderConfigured: true, items: [{
      ...listing.items[0], readable: false, documentName: null, sourceCount: null, instanceCount: null,
      sources: [], reason: 'suggested resolution must be positive',
    }] })));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    openHistory();
    const row = host.querySelector<HTMLButtonElement>('.cad-bundle-list button')!;
    expect(row.disabled).toBe(true);
    expect(row.textContent).toContain('suggested resolution must be positive');
    expect(row.title).toBe('suggested resolution must be positive');
  });

  it('renders unknown per-instance freshness copy', async () => {
    const freshness = { verdict: 'per-instance', instances: [{ instance_id: 'instance-a', verdict: 'unknown' }] };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      return json({ ...record, freshness });
    }));
    await renderAndSelect();
    await clickIngest();
    expect(host.textContent).toContain('Freshness could not be established');
  });

  it('renders unlinked CAD as a neutral, solver-frame mode that does not gate', async () => {
    const unlinked = {
      ...record,
      freshness: { verdict: 'unlinked' as const, instances: [], finding_id: 'unlinked-mode' },
      findings: [{
        id: 'unlinked-mode', kind: 'freshness', blocking: false, verdict: 'unlinked',
      }],
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json({
        ...listing,
        items: [{ ...listing.items[0], instanceCount: 0 }],
      });
      if (path.endsWith('/fusion-status')) return json(currentFusion);
      return json(unlinked);
    }));

    await renderAndSelect();
    await clickIngest();

    // A model authored in Fusion is not a warning: a neutral "from Fusion".
    const chip = host.querySelector('.cad-model-identity .cad-state-chip')!;
    expect(chip.textContent).toBe('from Fusion');
    expect(chip.className).not.toContain('warn');
    // Its hover no longer claims it is solved along +Z as-is: WG works the
    // frame out and Solve confirms it.
    expect(chip.getAttribute('title')).toContain('WG works out which way the model radiates');
    expect(host.textContent).not.toContain('radiation along +Z');
    // There is no WG design to be fresh against, so there is no Freshness row.
    expect([...host.querySelectorAll('.cad-check b')].map((name) => name.textContent)).not.toContain('Freshness');
    expect(host.querySelector('.cad-verdict.warn')).toBeNull();
    expect(host.querySelector('.cad-findings input[type="checkbox"]')).toBeNull();
    expect(importedSubmissionBlocker()).toBeNull();
    expect([...host.querySelectorAll('button')].some((button) => button.textContent === 'Refresh geometry from Fusion')).toBe(false);
  });

  it('shows Fusion as one quiet connected line while a Fusion-authored model is on screen, with nothing that inserts the WG design', async () => {
    const unlinked = {
      ...record,
      freshness: { verdict: 'unlinked' as const, instances: [], finding_id: 'unlinked-mode' },
      findings: [],
    };
    const notLinked: FusionCadStatus = { ...currentFusion, state: 'not_linked', link: null, documentName: 'PartyMEH v10', adapterVersion: '0.1.1' };
    const surface = async (ingested: CadReturnIngestRecord) => {
      vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input);
        if (path.endsWith('/returns')) return json(listing);
        if (path.endsWith('/fusion-status')) return json(notLinked);
        return json(ingested);
      }));
      await renderAndSelect();
      await clickIngest();
      return host.querySelector<HTMLElement>('.cad-link-card')!;
    };
    const buttons = (card: HTMLElement) => [...card.querySelectorAll('button')].map((button) => button.textContent);

    const card = await surface(unlinked as CadReturnIngestRecord);
    expect(card.querySelector('.cad-link-quiet > summary b')?.textContent).toBe('Fusion 360 · connected');
    expect(card.querySelector('.cad-link-quiet > summary .cad-link-meta')?.textContent).toBe('PartyMEH v10');
    expect(card.textContent).not.toContain('not linked in the active Fusion document');
    expect(buttons(card)).not.toContain('Open in Fusion 360');
    expect(buttons(card)).not.toContain('Send to Fusion');
    // The add-in version is a hover, not a line.
    expect(card.textContent).not.toContain('The Waveguide Generator add-in in Fusion');
    expect(card.querySelector('.cad-link-quiet > summary')?.getAttribute('title')).toContain('The Waveguide Generator add-in in Fusion 0.1.1');

    // Positive control: with a WG-linked model on screen, not_linked is the
    // parametric design's state, and opening it in Fusion is the action.
    act(() => root.unmount());
    root = createRoot(host);
    resetCadReturnStore();
    const linkedCard = await surface(record);
    expect(linkedCard.querySelector('.cad-primary-action')?.textContent).toBe('Open in Fusion 360');
  });

  it('mentions background coordination only when it is off', async () => {
    const { CadCoordinationNote } = await import('./CadLinkPanel');
    const { resetCadCoordinationForTests } = await import('../api/cadCoordination');
    resetCadCoordinationForTests('on');
    await act(async () => { root.render(<CadCoordinationNote/>); });
    expect(host.textContent).toBe('');
    resetCadCoordinationForTests('off');
    await act(async () => { root.render(<CadCoordinationNote/>); });
    expect(host.textContent).toContain('Background coordination: off');
    expect(host.textContent).not.toContain('WG2_CAD_COORDINATION');
    resetCadCoordinationForTests();
  });

  it('flags only mistakes in the checks: a declared cut that fails, never a candidate plane, hidden skips of construction, or double counts', async () => {
    const reviewed = {
      ...record,
      freshness: { verdict: 'unlinked' as const, instances: [], finding_id: 'unlinked-mode' },
      scope: {
        status: 'degraded', degraded_skip_count: 1,
        skipped: [
          { object_id: 'body-11', name: 'Body11', kind: 'hidden_body', severity: 'degraded', reason: 'hidden bodies are excluded by policy' },
          { name: 'construction entities', kind: 'construction', severity: 'info', reason: 'construction entities have no STEP representation' },
        ],
      },
      findings: [
        { id: 'finding-scope', kind: 'scope-degradation', blocking: true, reason: 'hidden bodies are excluded by policy' },
        { id: 'unlinked-mode', kind: 'freshness', blocking: false, verdict: 'unlinked' },
      ],
      symmetry: {
        mode: 'auto-cut', cut_planes: ['x0'], declared_cut_planes: [], domain_planes: ['x0'],
        planes: {
          x0: { accepted: true, max_residual_step_units: 0.003615718258137861, worst_off_model_distance_step_units: null },
          y0: { accepted: false, max_residual_step_units: 0.005859440511656944, worst_off_model_distance_step_units: 463.55555545555603 },
        },
      },
      sizing_estimate: {
        n_triangles: 2825, ram_gb: 0.128, solve_seconds_total: 12, freq_count: 1, is_lower_bound: true,
        measured: { n_triangles: 10084, ram_gb: 1.627, solve_seconds_total: 30, freq_count: 1, feasibility: 'ok' },
      },
    } as unknown as CadReturnIngestRecord;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      return json(reviewed);
    }));
    await renderAndSelect();
    await clickIngest();

    const checks = host.querySelector<HTMLElement>('.cad-checks')!;
    const row = (name: string) => [...checks.querySelectorAll<HTMLElement>('.cad-check')]
      .find((item) => item.querySelector('b')?.textContent === name);
    // The hidden body and its blocking finding are one problem, not two.
    expect(checks.querySelector('.cad-state-chip')?.textContent).toBe('1 need attention');
    expect(row('Symmetry')!.className).toContain('cad-check-ok');
    expect(row('Symmetry')!.textContent).toContain('Half model · mirrored at x = 0');
    expect(row('Symmetry')!.textContent).toContain('max residual 0.0036 mm');
    expect(row('Symmetry')!.textContent).toContain('worst off-model 460 mm');
    expect(row('Symmetry')!.textContent).not.toContain('—');
    expect(row('Freshness')).toBeUndefined();
    expect(row('Scope')!.textContent).toContain('Body11');
    expect(row('Scope')!.textContent).not.toContain('construction entities');
    // The measured mesh, not the lower-bound estimate; no time without a sweep.
    expect(row('Mesh')!.querySelector('.cad-check-verdict')?.textContent).toBe('10.1 k triangles · ~1.6 GB');
    // Only the finding that blocks is listed, and its hover says what it does.
    const findings = checks.querySelector('.cad-check-findings')!;
    expect(findings.textContent).toContain('scope degradation');
    expect(findings.textContent).not.toContain('freshness');
    expect(findings.querySelector('.cad-blocking-suffix')?.getAttribute('title')).not.toContain('Solving is not blocked');
    expect(findings.querySelector('.cad-blocking-suffix')?.getAttribute('title')).toContain('approve');
  });

  it('keeps a finding that limits confidence in view, in words, and drops only the unlinked line of a Fusion-first model', async () => {
    const legacy = {
      ...record,
      freshness: { verdict: 'unlinked' as const, instances: [], finding_id: 'unlinked-mode' },
      findings: [
        {
          id: 'finding-stale-7c1e', kind: 'stale-detection-unavailable', blocking: false,
          reason: 'stale detection unavailable: this returned bundle predates wgreturn 1.1 and carries no document signature',
        },
        { id: 'unlinked-mode', kind: 'freshness', blocking: false, verdict: 'unlinked' },
      ],
    } as unknown as CadReturnIngestRecord;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      return json(legacy);
    }));
    await renderAndSelect();
    await clickIngest();

    const checks = host.querySelector<HTMLElement>('.cad-checks')!;
    // It limits confidence in the result, so the checks open on it, quietly.
    expect(checks.querySelector('.cad-state-chip')?.textContent).toBe('passed · 1 note');
    expect(checks.className).not.toContain('degraded');
    const notes = checks.querySelector<HTMLElement>('.cad-check-notes');
    expect(notes).not.toBeNull();
    // WG cannot tell whether this model is stale: said in words, never by id.
    expect(notes!.textContent).toContain('WG cannot tell whether this model is out of date');
    expect(notes!.textContent).toContain('carries no document signature');
    expect(checks.textContent).not.toContain('finding-stale-7c1e');
    // Not a blocker: nothing to approve, and it is not counted as one.
    expect(checks.querySelector('.cad-check-findings')).toBeNull();
    expect(notes!.querySelector('.cad-blocking-suffix')).toBeNull();
    // The unlinked freshness line of a Fusion-first model is the one dropped.
    expect(notes!.textContent).not.toContain('unlinked');
    expect(notes!.querySelectorAll('.cad-check')).toHaveLength(1);
  });

  it('shows the FEM termination warning with a readable title and its remedy', async () => {
    const reason = 'A plane-wave termination is supported only in an interior-only solve. Choose exterior-only.';
    const reviewed = {
      ...record,
      findings: [{ id: 'fem-termination', kind: 'fem_termination_with_exterior', blocking: false, reason }],
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      return json(reviewed);
    }));
    await renderAndSelect();
    await clickIngest();

    const notes = host.querySelector<HTMLElement>('.cad-check-notes')!;
    expect(notes.querySelector('b')?.textContent).toBe('FEM termination requires an interior-only solve');
    expect(notes.textContent).toContain(reason);
    expect(notes.textContent).not.toContain('fem_termination_with_exterior');
  });

  it('keeps a note that only records what was asked for one click away', async () => {
    // Positive control for the test above: a declared reduced model is not a
    // limit on confidence, so its note does not open the checks.
    const declared = {
      ...record,
      freshness: { verdict: 'unlinked' as const, instances: [], finding_id: 'unlinked-mode' },
      findings: [{
        id: 'finding-declared-1', kind: 'declared-reduced-domain', blocking: false,
        detail: 'the return declares it was already cut on x0',
      }],
    } as unknown as CadReturnIngestRecord;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      return json(declared);
    }));
    await renderAndSelect();
    await clickIngest();

    const checks = host.querySelector<HTMLElement>('.cad-checks')!;
    expect(checks.querySelector('.cad-state-chip')?.textContent).toBe('passed · 1 note');
    expect(checks.querySelector('.cad-check-notes')).toBeNull();
    await act(async () => { checks.querySelector<HTMLButtonElement>('.section-head')!.click(); });
    expect(checks.querySelector('.cad-check-notes')?.textContent).toContain('the return declares it was already cut on x0');
  });

  it('warns about symmetry when a cut plane the CAD author declared does not mirror', async () => {
    // Positive control for the check above: a declared cut is a promise.
    const declared = {
      ...record,
      symmetry: {
        cut_planes: [], declared_cut_planes: ['y0'], domain_planes: ['y0'],
        planes: { y0: { accepted: false, source: 'declared-by-cad-author' } },
      },
    } as unknown as CadReturnIngestRecord;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      return json(declared);
    }));
    await renderAndSelect();
    await clickIngest();
    const symmetry = [...host.querySelectorAll<HTMLElement>('.cad-checks .cad-check')]
      .find((item) => item.querySelector('b')?.textContent === 'Symmetry')!;
    expect(symmetry.className).toContain('cad-check-warn');
    expect(symmetry.textContent).toContain('declared cut y = 0 does not mirror');
  });

  it('sends the design on screen to CAD and refreshes the returned bundles', async () => {
    const requested: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      requested.push(path);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      if (path === '/api/cad-workspace/path') return json({ selected: true, path: '/cad' });
      return json({
        bundlePath: '/cad/wglink/horn.wglink', bundleId: 'wgb_1', exportId: 'wge_1', sequence: 4,
        designHash: 'sha256:d', geometryHash: 'sha256:g', artifactSha256: 'sha256:a',
        identity: { designId: 'wgd_01K00000000000000000000000', lineageId: 'wgl_01K00000000000000000000000', baseEditVersion: 2 },
      });
    }));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });

    // The outbound entry points (menu, rail) all route through this bridge.
    await act(async () => { await cadWorkspace.getSnapshot().sendWgToFusion(); await Promise.resolve(); });

    expect(requested).toContain('/api/export/wglink');
    expect(host.textContent).toContain('Opening in Fusion 360 · sequence 4');
    // The bundle just written is what CAD picks up, so the listing is re-read.
    expect(requested.filter((path) => path.endsWith('/returns')).length).toBeGreaterThan(1);
    expect(useDocumentStore.getState().identity?.baseEditVersion).toBe(2);
  });

  it('reports a refused send without clearing the panel', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path.includes('/receipts?')) return json({ feed_id: 'panel', receipts: [], cursor: 0, has_more: false });
      if (path.endsWith('/recovery-notices')) return json({ notices: [] });
      if (path === '/api/cad-workspace/path') return json({ selected: true, path: '/cad' });
      return json({ detail: 'CAD-link bundle name is already used by another design: horn.wglink' }, 409);
    }));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });

    await act(async () => {
      await cadWorkspace.getSnapshot().sendWgToFusion().catch(() => undefined);
      await Promise.resolve();
    });

    expect(host.querySelector('[role="alert"]')?.textContent).toContain('already used by another design');
    expect(host.querySelector('.cad-history')).toBeTruthy();
  });

  it('discovers area-drift overrides from structured refusal data', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/returns')) return json(listing);
      if (String(input).endsWith('/fusion-status')) return json(closedFusion);
      return json({
        detail: { message: 'Role resolution refused.', area_drift_sources: ['source-hf'] },
      }, 422);
    }));
    await renderAndSelect();
    await clickIngest();
    expect(useCadReturnStore.getState().areaDriftSourceIds).toContain('source-hf');
    expect(host.querySelector('.cad-model-card')?.textContent).toContain('Preparation failed');
    expect(host.querySelector('.cad-alert-error[role="alert"]')?.textContent).toContain('Role resolution refused');
    expect([...host.querySelectorAll<HTMLButtonElement>('button')]
      .some((button) => button.textContent === 'Prepare simulation')).toBe(true);
    expect(host.textContent).not.toContain('Allow recorded area drift');
  });
});

describe('declaredDomainPhrase', () => {
  it('names the domain a CAD author declared, and says nothing for a full model', () => {
    expect(declaredDomainPhrase(undefined)).toBe('');
    expect(declaredDomainPhrase([])).toBe('');
    expect(declaredDomainPhrase(['y0'])).toBe('half model, cut on y = 0');
    expect(declaredDomainPhrase(['x0', 'y0'])).toBe('quarter model, cut on x = 0 and y = 0');
    // A plane this build cannot mirror is not a domain it may claim to know.
    expect(declaredDomainPhrase(['z0'])).toBe('');
  });
});
