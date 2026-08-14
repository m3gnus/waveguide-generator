import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnIngestRecord, CadReturnListing, FusionCadStatus } from '../api/cadlink';
import type { OnshapeLink } from '../api/onshape';
import { preferencesStore } from '../prefs/preferences';
import { importedSubmissionBlocker } from '../jobs/importedSubmission';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { importedMeshStore } from '../viewport/importedMeshStore';
import meshFixture from '../viewport/test-fixtures/tagged_sources-small.msh?raw';
import { buildImportedSubmission, CadLinkPanel, fusionWorkflowView, newestReturnArrival, onshapeWorkflowView, showIngestedMeshInViewport } from './CadLinkPanel';
import { CadLinkCoordinator, cadLinkCoordinatorBridge } from './CadLinkCoordinator';

const listing: CadReturnListing = {
  cadFolderConfigured: true,
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
  realizedDimensions: { state: 'link_unavailable', instanceId: null, exportId: null, parameters: [] },
};
const currentFusion: FusionCadStatus = {
  ...closedFusion,
  state: 'current', processRunning: true, running: true, updatedAt: '2026-08-12T15:30:00Z', documentName: 'Tritonia V', documentId: 'fusion:doc-a', fusionFormula: 'osse',
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

function CadLinkTestSurface() {
  return <><CadLinkCoordinator/><CadLinkPanel/></>;
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
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      return json(record);
    }));
  });
  afterEach(() => { act(() => root.unmount()); importedMeshStore.clear(); vi.unstubAllGlobals(); host.remove(); });

  const renderAndSelect = async () => {
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    const bundle = host.querySelector<HTMLButtonElement>('.cad-bundle-list button')!;
    act(() => bundle.click());
    return bundle;
  };

  const clickIngest = async () => {
    const ingest = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === 'Prepare simulation')!;
    await act(async () => { ingest.click(); await Promise.resolve(); await Promise.resolve(); });
    return ingest;
  };

  it('runs listing → ingest → blocking acknowledgement without a duplicate local solve control', async () => {
    await renderAndSelect();
    await clickIngest();
    expect(importedSubmissionBlocker()).toContain('Acknowledge 1 blocking finding');
    const acknowledgement = host.querySelector<HTMLInputElement>('.cad-findings input[type="checkbox"]')!;
    act(() => { acknowledgement.click(); });
    expect(importedSubmissionBlocker()).toBeNull();
    for (const moved of ['Mesh detail', 'Drive channels & drivers', 'Crossover', 'Rebuild mesh']) {
      expect(host.textContent).not.toContain(moved);
    }
    expect(host.textContent).not.toContain('Explicit solve sweep');
    expect([...host.querySelectorAll<HTMLButtonElement>('button')].some((button) => button.textContent === 'Solve CAD import')).toBe(false);
    expect(host.querySelector('.cad-viewport-source-buttons')).toBeNull();
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
      headline: 'WGLink cannot access the selected folder', action: null,
    });
    expect(fusionWorkflowView({ ...currentFusion, cadConnectionIssue: 'folder_mismatch' }).detail).toContain(
      'clear within a few seconds',
    );
    expect(fusionWorkflowView({ ...currentFusion, state: 'stale', wgChangesAvailable: true })).toMatchObject({
      state: 'stale', action: 'update',
    });
  });

  it('maps Onshape link state to one explicit action', () => {
    const credentials = { configured: true, credentialsPath: '/home/x/.config/hornlab/onshape.env', detail: null, insecureKeyFile: false };
    const link: OnshapeLink = {
      designId: 'wgd_1', accountId: 'ACC', documentId: 'DID', workspaceId: 'WID',
      documentName: 'Tritonia', documentUrl: 'https://cad.onshape.com/documents/DID/w/WID',
      isPublic: true, partStudioElementId: 'PART', variableStudioElementId: 'VARS',
      featureStudioElementId: null, nativeFeatureId: null,
      datumFeatureStudioElementId: null, datumFeatureId: null, buildMode: 'import',
      lastSequence: 3, updatedAt: '2026-08-13T09:00:00Z',
    };
    const base = { credentials, link: null, wgChangesAvailable: false, currentFormula: 'osse' } as const;

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
    wgChangesAvailable: false,
    currentFormula: 'osse',
    ...overrides,
  });

  it('offers to create an Onshape document and states the public-plan consequence', async () => {
    const calls = await renderOnshape(onshapeStatus());
    expect(host.querySelector('.cad-connection')?.textContent).toContain('Not in Onshape yet');
    expect(host.querySelector<HTMLButtonElement>('.cad-primary-action')?.textContent)
      .toContain('in Onshape');
    expect(host.textContent).toContain('makes every document world-readable');
    // The Onshape leg needs no workspace folder and no Fusion heartbeat.
    expect(calls.some((path) => path.includes('/fusion-status'))).toBe(false);
    expect(calls.some((path) => path.includes('/returns'))).toBe(false);
  });

  it('does not offer the Fusion workspace-folder return workflow under Onshape', async () => {
    await renderOnshape(onshapeStatus());
    expect(host.textContent).toContain('No linked Onshape Part Studio');
    expect(host.querySelector('.cad-bundle-list')).toBeNull();
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
    const button = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((candidate) => candidate.textContent === 'Bring Onshape geometry into WG')!;
    await act(async () => {
      button.click();
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });

    expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(record.ingest_id);
    expect(useCadReturnStore.getState().selectedBundle?.documentName).toBe('Tritonia');
    expect(host.querySelector('.cad-status-strip')?.textContent).toContain('Returned and ingested Tritonia');
    expect(host.querySelector('.cad-findings')?.textContent).toContain('freshness');
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

  it('reports an Onshape send failure without claiming success', async () => {
    await renderOnshape(
      onshapeStatus(),
      () => json({ detail: 'Onshape rate limit reached (429).' }, 429),
    );
    await act(async () => {
      host.querySelector<HTMLButtonElement>('.cad-primary-action')!.click();
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });
    expect(host.querySelector('.cad-alert[role="alert"]')?.textContent).toContain('rate limit');
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

  it('selects the newest readable return when CAD Link first mounts', async () => {
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(listing.items[0].bundlePath);
    expect(host.textContent).toContain('Prepare this Fusion return for the viewport and solver.');
    expect(host.textContent).not.toContain('Mesh detail');
  });

  it('shows the selected CAD program, settings link, and an Open button when Fusion is closed', async () => {
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    expect(host.querySelector('.cad-workflow-header')?.textContent).toContain('Autodesk Fusion 360');
    expect(host.querySelector<HTMLButtonElement>('.cad-workflow-header button')?.textContent).toContain('Change');
    expect(host.querySelector('.cad-connection')?.textContent).toContain('Fusion 360 is closed');
    expect(host.querySelector<HTMLButtonElement>('.cad-primary-action')?.textContent).toBe('Open waveguide in Fusion 360');
  });

  it('shows an up-to-date active Fusion document and the communicated parameter count', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/fusion-status')
      ? json(currentFusion)
      : json(listing)));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    expect(host.querySelector('.cad-connection')?.textContent).toContain('Fusion 360 is open · Tritonia V');
    expect(host.querySelector('.cad-connection')?.textContent).toContain('13 managed CAD parameters');
    expect(host.querySelector('.cad-primary-action')).toBeNull();
  });

  it('offers an in-place Fusion update when the WG config no longer matches', async () => {
    const stale = { ...currentFusion, state: 'stale' as const, currentFormula: 'R-OSSE', fusionFormula: 'osse', wgChangesAvailable: true };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/fusion-status')
      ? json(stale)
      : json(listing)));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    expect(host.querySelector('.cad-connection')?.textContent).toContain('Fusion has OSSE; WG is now R-OSSE');
    expect(host.querySelector<HTMLButtonElement>('.cad-primary-action')?.textContent).toBe('Send WG changes to Fusion');
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

    expect(host.textContent).toContain('Send WG changes to Fusion');
    expect(host.textContent).toContain('Bring Fusion changes into WG');
    const send = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === 'Send WG changes to Fusion')!;
    await act(async () => { send.click(); await Promise.resolve(); });
    expect(host.textContent).toContain('Both WG and Fusion changed');
    expect(host.textContent).toContain('Continue: send WG changes');
  });

  it('builds acknowledgement wires from the current record, filters skipped sizes, and emits range/list sweep shapes', () => {
    useCadReturnStore.getState().selectBundle(listing.items[0]);
    useCadReturnStore.getState().applyIngest(record, useCadReturnStore.getState().beginIngestIntent());
    useCadReturnStore.getState().acknowledge('finding-a', true);
    useCadReturnStore.setState({
      sourceSizesMm: { 'source-hf': 3.25, optional: 9 },
      skippedSourceIds: ['optional'],
    });
    useCadReturnStore.getState().setSweep({ frequencyStartHz: 250, frequencyEndHz: 12_000, frequencyCount: 31 });

    const range = buildImportedSubmission(useCadReturnStore.getState());
    expect(range.geometry.acknowledged_findings).toEqual([`${record.report_sha256}:finding-a`]);
    expect(range.geometry.mesh.source_size_mm).toEqual({ 'source-hf': 3.25 });
    expect(range.options).toMatchObject({ frequency_range: [250, 12_000], num_frequencies: 31 });
    expect(range.options).not.toHaveProperty('frequencies_hz');

    useSolveOptionsStore.getState().setFrequencyMode('list');
    useSolveOptionsStore.getState().setFrequencyListText('300 700 1500');
    const list = buildImportedSubmission(useCadReturnStore.getState());
    expect(list.options.frequencies_hz).toEqual([300, 700, 1_500]);
    expect(list.options).not.toHaveProperty('frequency_range');
    expect(list.options).not.toHaveProperty('num_frequencies');
  });

  it('refuses to build an imported submission without an ingestion record', () => {
    expect(() => buildImportedSubmission(useCadReturnStore.getState())).toThrow('Ingest a CAD return');
  });

  it('emits the combine wire only when enabled, chained by role band order', () => {
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

    expect(buildImportedSubmission(useCadReturnStore.getState()).geometry).not.toHaveProperty('combine');

    useCadReturnStore.getState().setCombineEnabled(true);
    // Untouched pair input: log-spaced default inside the sweep,
    // round(sqrt(200 * 5000)) = 1000.
    expect(buildImportedSubmission(useCadReturnStore.getState()).geometry.combine)
      .toEqual({ members: ['drive-mf', 'drive-hf'], crossovers_hz: [1_000], level_match: true, align: true });

    useCadReturnStore.getState().setCombineCrossover('drive-mf\u2192drive-hf', 1_200);
    expect(buildImportedSubmission(useCadReturnStore.getState()).geometry.combine)
      .toEqual({ members: ['drive-mf', 'drive-hf'], crossovers_hz: [1_200], level_match: true, align: true });

    useCadReturnStore.getState().setCombineAlign(false);
    expect(buildImportedSubmission(useCadReturnStore.getState()).geometry.combine?.align).toBe(false);

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

  it('size change → re-ingest produces the new report acknowledgement wire', async () => {
    let ingestCount = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/returns')) return json(listing);
      if (String(input).endsWith('/fusion-status')) return json(closedFusion);
      // The advisory display-artifact fetches after each ingest are not ingests.
      if (String(input).endsWith('/viewport-mesh') || String(input).endsWith('/mesh')) {
        return new Response('missing', { status: 404 });
      }
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
    act(() => useCadReturnStore.getState().setSourceSize('source-hf', 2.5));
    await act(async () => { await cadLinkCoordinatorBridge.getSnapshot().ingest(); });
    act(() => host.querySelector<HTMLInputElement>('.cad-findings input[type="checkbox"]')!.click());
    const submission = buildImportedSubmission(useCadReturnStore.getState());
    expect(submission.geometry).toMatchObject({
      mesh: { source_size_mm: { 'source-hf': 2.5 } },
      acknowledged_findings: [`sha256:${'2'.repeat(64)}:finding-a`],
    });
  });

  it('marks a changed refreshed bundle stale and preserves sizing edits', async () => {
    let listingCount = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/returns')) {
        listingCount += 1;
        const body = listingCount === 1 ? listing : { items: [{
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
    act(() => useCadReturnStore.getState().setSourceSize('source-hf', 2.5));
    const refresh = [...host.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Refresh')!;
    await act(async () => { refresh.click(); await Promise.resolve(); await Promise.resolve(); });

    expect(host.textContent).toContain('source inventory or source sizing suggestions changed');
    expect(useCadReturnStore.getState().sourceSizesMm['source-hf']).toBe(2.5);
    expect(importedSubmissionBlocker()).toContain('source inventory or source sizing suggestions changed');
    expect(host.textContent).not.toContain('Rebuild mesh');
  });

  it('renders an unreadable listing row disabled with the server reason', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/fusion-status') ? json(closedFusion) : json({ items: [{
      ...listing.items[0], readable: false, documentName: null, sourceCount: null, instanceCount: null,
      sources: [], reason: 'suggested resolution must be positive',
    }] })));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });
    const row = host.querySelector<HTMLButtonElement>('.cad-bundle-list button')!;
    expect(row.disabled).toBe(true);
    expect(row.textContent).toContain('suggested resolution must be positive');
    expect(row.title).toBe('suggested resolution must be positive');
  });

  it.each([
    ['unlinked', { verdict: 'unlinked', instances: [] }, 'Unlinked return'],
    ['unknown', { verdict: 'per-instance', instances: [{ instance_id: 'instance-a', verdict: 'unknown' }] }, 'Freshness could not be established'],
  ])('renders %s freshness copy', async (_name, freshness, copy) => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      return json({ ...record, freshness });
    }));
    await renderAndSelect();
    await clickIngest();
    expect(host.textContent).toContain(copy);
  });

  it('sends the design on screen to CAD and refreshes the returned bundles', async () => {
    const requested: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      requested.push(path);
      if (path.endsWith('/returns')) return json(listing);
      if (path.endsWith('/fusion-status')) return json(closedFusion);
      if (path === '/api/cad-workspace/path') return json({ selected: true, path: '/cad' });
      return json({
        bundlePath: '/cad/wglink/horn.wglink', bundleId: 'wgb_1', exportId: 'wge_1', sequence: 4,
        designHash: 'sha256:d', geometryHash: 'sha256:g', artifactSha256: 'sha256:a',
        identity: { designId: 'wgd_01K00000000000000000000000', lineageId: 'wgl_01K00000000000000000000000', baseEditVersion: 2 },
      });
    }));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });

    const send = [...host.querySelectorAll<HTMLButtonElement>('.cad-send button')]
      .find((button) => button.textContent === 'Open waveguide in Fusion 360')!;
    expect(send).toBeTruthy();
    await act(async () => { send.click(); await Promise.resolve(); await Promise.resolve(); });

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
      if (path === '/api/cad-workspace/path') return json({ selected: true, path: '/cad' });
      return json({ detail: 'CAD-link bundle name is already used by another design: horn.wglink' }, 409);
    }));
    await act(async () => { root.render(<CadLinkTestSurface/>); await Promise.resolve(); await Promise.resolve(); });

    const send = [...host.querySelectorAll<HTMLButtonElement>('.cad-send button')]
      .find((button) => button.textContent === 'Open waveguide in Fusion 360')!;
    await act(async () => { send.click(); await Promise.resolve(); await Promise.resolve(); });

    expect(host.querySelector('[role="alert"]')?.textContent).toContain('already used by another design');
    expect(host.querySelector('.cad-bundle-list')).toBeTruthy();
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
    expect(host.textContent).not.toContain('Allow recorded area drift');
  });
});
