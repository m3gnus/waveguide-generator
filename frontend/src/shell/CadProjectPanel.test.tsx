import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { compareSelection } from '../api/results';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetDesignStore } from '../stores/design';
import { resetWorkspaceFolderStore } from '../stores/workspaceFolder';
import { cadProjectReference } from '../api/cadProjects';
import { CadProjectHeader, CadProjectHistory, modelStateLabel, newestReturnForProject, projectName } from './CadProjectPanel';
import { cadLinkCoordinatorBridge } from './CadLinkCoordinator';
import type { CadReturnBundle } from '../api/cadlink';

const LINEAGE = 'wgl_project';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => { resolve = settle; });
  return { promise, resolve };
}

function run(overrides: Partial<JobItem> & { id: string; returnStateHash?: string | null }): JobItem {
  return {
    run_number: 1,
    label: overrides.label ?? `run ${overrides.id}`,
    status: 'complete',
    has_results: true,
    rating: null,
    created_at: '2026-08-21T09:00:00Z',
    completed_at: '2026-08-21T09:05:00Z',
    config_summary: { geometry_type: 'imported' },
    cad_source: {
      ingest_id: `wgi_${overrides.id}`,
      lineage_id: LINEAGE,
      return_state_hash: overrides.returnStateHash ?? 'sha256:aaa',
    },
    ...overrides,
  } as unknown as JobItem;
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

const projects = {
  items: [
    {
      designId: 'wgd_project',
      lineageId: LINEAGE,
      filename: 'hans-rosse.cfg',
      documentName: 'Tritonia M',
      archiveStem: 'Tritonia',
      exportCount: 1,
      createdAt: '2026-08-20T08:00:00Z',
      updatedAt: '2026-08-21T08:00:00Z',
    },
  ],
};

const documents = {
  archiveStem: 'Tritonia',
  folder: '/runs/Tritonia',
  items: [
    { returnStateHash: 'sha256:bbb', documentName: 'Tritonia', ingestId: null, returnId: null, capturedAt: '2026-08-21T08:00:00Z', filename: 'sha256_bbb.f3d', bytes: 10 },
    { returnStateHash: 'sha256:aaa', documentName: 'Tritonia', ingestId: null, returnId: null, capturedAt: '2026-08-20T08:00:00Z', filename: 'sha256_aaa.f3d', bytes: 10 },
  ],
};

describe('CAD project history', () => {
  let host: HTMLDivElement;
  let root: Root;

  const setJobs = (jobs: JobItem[]) => {
    vi.spyOn(jobsSocket, 'getSnapshot').mockReturnValue({
      ...jobsSocket.getSnapshot(), jobs,
    });
  };

  const render = (node: React.ReactNode) => {
    act(() => root.render(node));
    // The document listing resolves on a microtask after mount.
    return act(async () => { await Promise.resolve(); });
  };

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetCadReturnStore(); resetDocumentStore(); resetDesignStore(); resetWorkspaceFolderStore();
    compareSelection.clear();
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('/documents')) return json(documents);
      if (String(input).includes('/api/cadlink/designs')) return json(projects);
      return json({});
    }));
    useDocumentStore.getState().setCadLink(
      { designId: 'wgd_project', lineageId: LINEAGE, baseEditVersion: 1 }, 'current',
    );
  });
  afterEach(() => {
    act(() => root.unmount());
    compareSelection.clear();
    vi.restoreAllMocks(); vi.unstubAllGlobals(); host.remove();
  });

  it('lists this project’s runs, newest first, and nobody else’s', async () => {
    setJobs([
      run({ id: 'mine-new' }),
      { ...run({ id: 'other' }), cad_source: { lineage_id: 'wgl_other', return_state_hash: 'sha256:aaa' } } as JobItem,
      run({ id: 'mine-old' }),
    ]);

    await render(<CadProjectHistory/>);

    const names = [...host.querySelectorAll('.cad-run-open b')].map((node) => node.textContent);
    // Named exactly as the jobs rail names them: one run has one name.
    expect(names).toEqual(['#1 \u00b7 run mine-new', '#1 \u00b7 run mine-old']);
  });

  it('breaks the list where the CAD model changed and offers that model', async () => {
    setJobs([
      run({ id: 'b1', returnStateHash: 'sha256:bbb' }),
      run({ id: 'a1', returnStateHash: 'sha256:aaa' }),
    ]);

    await render(<CadProjectHistory/>);

    const downloads = [...host.querySelectorAll<HTMLAnchorElement>('.cad-model-download')];
    expect(downloads).toHaveLength(2);
    expect(downloads[0].getAttribute('href')).toBe(
      `/api/cadlink/projects/${LINEAGE}/documents/sha256%3Abbb`,
    );
    expect(downloads[1].getAttribute('href')).toBe(
      `/api/cadlink/projects/${LINEAGE}/documents/sha256%3Aaaa`,
    );
  });

  it('says why a model has no Fusion file rather than offering a broken link', async () => {
    setJobs([run({ id: 'x', returnStateHash: 'sha256:missing' })]);

    await render(<CadProjectHistory/>);

    expect(host.querySelector('.cad-model-download')).toBeNull();
    const missing = host.querySelector('.cad-model-missing');
    expect(missing?.textContent).toBe('no Fusion file');
    expect(missing?.getAttribute('title')).toContain('Capture was off');
  });

  it('compares a run against the one being shown', async () => {
    setJobs([run({ id: 'a' }), run({ id: 'b' })]);
    await render(<CadProjectHistory/>);

    const compare = host.querySelectorAll<HTMLButtonElement>('.cad-run-compare');
    act(() => compare[1].click());

    expect(compareSelection.getSnapshot().overlays).toEqual(['b']);
  });

  /**
   * The same removal as the jobs rail, on the same confirmation and the same
   * endpoint. The list is a projection of the jobs snapshot, so nothing here
   * has to remove the row: `deleteJob` drops it from that snapshot.
   */
  it('removes a run on the same confirmed deletion as the jobs rail', async () => {
    const deleteJob = vi.spyOn(jobsSocket, 'deleteJob').mockResolvedValue(undefined);
    setJobs([run({ id: 'a' }), run({ id: 'b', status: 'running', has_results: false, completed_at: null })]);
    await render(<CadProjectHistory/>);

    // A run still in flight is stopped from the jobs rail, not removed here.
    const remove = host.querySelectorAll<HTMLButtonElement>('.cad-run-remove');
    expect(remove).toHaveLength(1);
    expect(remove[0].getAttribute('aria-label')).toBe('Remove #1 · run a');

    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    act(() => remove[0].click());
    expect(confirm).toHaveBeenCalledWith('Remove “#1 · run a” and its saved results?');
    expect(deleteJob).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    act(() => remove[0].click());
    expect(deleteJob).toHaveBeenCalledWith('a');
  });

  it('reports a removal the server refused instead of losing it', async () => {
    vi.spyOn(jobsSocket, 'deleteJob').mockRejectedValue(new Error('Run is still exporting.'));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    setJobs([run({ id: 'a' })]);
    await render(<CadProjectHistory/>);

    await act(async () => {
      host.querySelector<HTMLButtonElement>('.cad-run-remove')!.click();
      await Promise.resolve();
    });

    expect(host.querySelector('[role="alert"]')?.textContent).toContain('Run is still exporting.');
  });

  it('shows a run when its row is chosen', async () => {
    setJobs([run({ id: 'a' })]);
    await render(<CadProjectHistory/>);

    act(() => host.querySelector<HTMLButtonElement>('.cad-run-open')!.click());

    expect(compareSelection.getSnapshot().primary).toBe('a');
  });

  it('keeps the run list when the archived models cannot be read', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ detail: 'No run archive folder is available.' }), { status: 409 },
    )));
    setJobs([run({ id: 'a' })]);

    await render(<CadProjectHistory/>);

    expect(host.querySelectorAll('.cad-run-row')).toHaveLength(1);
    expect(host.textContent).toContain('No run archive folder is available.');
  });

  it('invites a first run rather than showing an empty list', async () => {
    setJobs([]);

    await render(<CadProjectHistory/>);

    expect(host.querySelector('.cad-run-list')).toBeNull();
    expect(host.textContent).toContain('No runs yet');
  });

  it('renders nothing at all when no project is open', async () => {
    resetDocumentStore();
    setJobs([run({ id: 'a' })]);

    await render(<CadProjectHistory/>);

    expect(host.textContent).toBe('');
  });

  it('names the project by the Fusion document and counts its runs', async () => {
    setJobs([run({ id: 'a' }), run({ id: 'b' })]);

    await render(<CadProjectHeader/>);

    expect(host.querySelector('.cad-project-name')?.textContent).toBe('Tritonia M');
    expect(host.textContent).toContain('2 runs in this project');
  });

  it('names the project the ingested return belongs to, not the design file', async () => {
    // The regression this closes: the heading was filled from whatever document
    // Fusion had open while the rest of the panel pointed at the open `.cfg`,
    // so one project showed two names.
    useCadReturnStore.setState({
      ingestRecord: {
        project: {
          lineage_id: 'wgl_cad_first',
          design_id: null,
          document_native_id: 'urn:adsk:lineage:1',
          document_name: '260627 - PartyMEH v10',
          archive_stem: '260627 - PartyMEH v10',
        },
      } as never,
    });
    setJobs([]);

    await render(<CadProjectHeader/>);

    expect(host.querySelector('.cad-project-name')?.textContent).toBe('260627 - PartyMEH v10');
  });

  it('shows the folder projects are archived in, and can change it', async () => {
    let stored = '/exports';
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === '/api/workspace/path') return json({ selected: true, path: stored });
      if (path === '/api/workspace/select' && init?.method === 'POST') {
        stored = '/picked/projects';
        return json({ selected: true, path: stored });
      }
      if (path === '/api/workspace/open' && init?.method === 'POST') return json({ status: 'opened', path: stored });
      if (path.includes('/documents')) return json(documents);
      if (path.includes('/api/cadlink/designs')) return json(projects);
      return json({});
    }));
    setJobs([run({ id: 'a' })]);

    await render(<CadProjectHeader/>);
    // The folder plumbing lives behind the project card's overflow menu now.
    act(() => host.querySelector<HTMLButtonElement>('.cad-overflow-trigger')!.click());
    const menu = host.querySelector<HTMLElement>('.cad-overflow-menu')!;
    expect(menu.textContent).toContain('/exports');

    const open = [...menu.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('Open workspace folder'))!;
    await act(async () => { open.click(); await Promise.resolve(); });
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/workspace/open', { method: 'POST' });

    // Chosen through the server, not the browser: the same v1 mechanism the
    // output and WGLink folders use, so it works away from Chromium.
    act(() => host.querySelector<HTMLButtonElement>('.cad-overflow-trigger')!.click());
    const change = [...host.querySelectorAll<HTMLButtonElement>('.cad-overflow-menu button')]
      .find((button) => button.textContent?.includes('Change workspace folder'))!;
    await act(async () => { change.click(); await Promise.resolve(); });
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/workspace/select', { method: 'POST' });
    act(() => host.querySelector<HTMLButtonElement>('.cad-overflow-trigger')!.click());
    expect(host.querySelector<HTMLElement>('.cad-overflow-menu')!.textContent).toContain('/picked/projects');
  });

  it('offers to open a project when none is', async () => {
    resetDocumentStore();
    setJobs([]);

    await render(<CadProjectHeader/>);

    expect(host.textContent).toContain('No CAD project open');
  });
  it('clears the previous project documents while the new lineage loads and after failure', async () => {
    const nextLineage = 'wgl_next_project';
    setJobs([
      run({ id: 'old', returnStateHash: 'sha256:aaa' }),
      {
        ...run({ id: 'next', returnStateHash: 'sha256:aaa' }),
        cad_source: {
          ingest_id: 'wgi_next', lineage_id: nextLineage, return_state_hash: 'sha256:aaa',
        },
      } as JobItem,
    ]);
    await render(<CadProjectHistory/>);
    expect(host.querySelector('.cad-model-download')).not.toBeNull();

    const failedRefresh = deferred<Response>();
    vi.stubGlobal('fetch', vi.fn(() => failedRefresh.promise));
    await act(async () => {
      useDocumentStore.getState().setCadLink(
        { designId: 'wgd_next', lineageId: nextLineage, baseEditVersion: 1 }, 'current',
      );
      await Promise.resolve();
    });

    expect(host.querySelector('.cad-model-download')).toBeNull();
    await act(async () => {
      failedRefresh.resolve(new Response(
        JSON.stringify({ detail: 'archive unavailable' }), { status: 409 },
      ));
      await failedRefresh.promise;
      await Promise.resolve();
    });
    expect(host.querySelector('.cad-model-download')).toBeNull();
    expect(host.textContent).toContain('archive unavailable');
  });

  it('ignores a document response for a lineage that is no longer current', async () => {
    const nextLineage = 'wgl_next_project';
    const oldListing = deferred<Response>();
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes(`/${LINEAGE}/documents`)) return oldListing.promise;
      if (path.includes(`/${nextLineage}/documents`)) {
        return Promise.resolve(json({ archiveStem: 'Next', folder: '/runs/Next', items: [] }));
      }
      return Promise.resolve(json({}));
    }));
    setJobs([{
      ...run({ id: 'next', returnStateHash: 'sha256:aaa' }),
      cad_source: {
        ingest_id: 'wgi_next', lineage_id: nextLineage, return_state_hash: 'sha256:aaa',
      },
    } as JobItem]);
    await render(<CadProjectHistory/>);

    await act(async () => {
      useDocumentStore.getState().setCadLink(
        { designId: 'wgd_next', lineageId: nextLineage, baseEditVersion: 1 }, 'current',
      );
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(host.querySelector('.cad-model-download')).toBeNull();

    await act(async () => {
      oldListing.resolve(json(documents));
      await oldListing.promise;
      await Promise.resolve();
    });
    expect(host.querySelector('.cad-model-download')).toBeNull();
  });

  it('reopens a CAD-only project from the newest return its document sent', async () => {
    // After a reload nothing on the client remembers a CAD-authored project:
    // it has no design snapshot to open, and the switcher used to grey it out
    // with advice to send from Fusion again although its returns were there.
    const bundle = (name: string, modifiedAt: string): CadReturnBundle => ({
      name, bundlePath: `wgreturn/${name}`, modifiedAt, readable: true,
      documentName: '260627 - PartyMEH v10', requestId: null, sourceCount: 3, instanceCount: 1, designIds: [], sources: [],
    });
    const older = bundle('260627 - PartyMEH v10-4.wgreturn', '2026-08-23T15:09:02Z');
    const newest = bundle('260627 - PartyMEH v10-5.wgreturn', '2026-08-23T19:14:58Z');
    const selectBundle = vi.fn();
    vi.spyOn(cadLinkCoordinatorBridge, 'getSnapshot').mockReturnValue({
      ...cadLinkCoordinatorBridge.getSnapshot(), bundles: [], selectBundle,
    });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('/api/cadlink/returns')) return json({ items: [older, newest], cadFolderConfigured: true });
      if (String(input).includes('/documents')) return json(documents);
      if (String(input).includes('/api/cadlink/designs')) {
        return json({ items: [{
          designId: null, lineageId: 'wgl_cad_first', filename: null, documentName: '260627 - PartyMEH v10',
          archiveStem: '260627 - PartyMEH v10', exportCount: 0, createdAt: '2026-08-23T14:34:20Z', updatedAt: '2026-08-23T19:15:10Z',
        }] });
      }
      return json({});
    }));
    setJobs([]);
    await render(<CadProjectHeader/>);

    const toggle = host.querySelector<HTMLButtonElement>('button[aria-haspopup="menu"]')!;
    await act(async () => { toggle.click(); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });
    const item = host.querySelector<HTMLButtonElement>('.cad-project-menu [role="menuitem"]')!;
    expect(item.textContent).toContain('PartyME');
    expect(item.disabled).toBe(false);
    await act(async () => { item.click(); await Promise.resolve(); await Promise.resolve(); });

    expect(selectBundle).toHaveBeenCalledWith(newest);
    expect(newestReturnForProject([older, newest], { documentName: '260627 - PartyMEH v10', archiveStem: null })).toBe(newest);
    expect(newestReturnForProject([older, newest], { documentName: 'Other', archiveStem: null })).toBeNull();
  });
});

describe('what the history calls things', () => {
  it('names a project by its CAD document, then the folder, then its design file', () => {
    // The document name leads: in CAD mode the Fusion document owns the name,
    // and the archive stem is frozen against renames so it goes stale as a label.
    expect(projectName({ documentName: 'PartyMEH v10', archiveStem: 'PartyMEH v9', filename: 'other.cfg' })).toBe('PartyMEH v10');
    expect(projectName({ documentName: null, archiveStem: 'Tritonia', filename: 'other.cfg' })).toBe('Tritonia');
    expect(projectName({ documentName: null, archiveStem: null, filename: 'Big Horn.cfg' })).toBe('Big Horn');
    expect(projectName({ documentName: '  ', archiveStem: '   ', filename: 'Big Horn.cfg' })).toBe('Big Horn');
    expect(projectName({ documentName: null, archiveStem: null, filename: null })).toBe('Untitled project');
  });

  it('gives same-named registry heads a stable visible reference', () => {
    expect(cadProjectReference({ designId: 'wgd_01K000000000000000ABCDEF' }))
      .toBe('ID …ABCDEF');
  });

  it('dates a model rather than numbering it', () => {
    // An ordinal would silently change meaning as old runs are pruned.
    const label = modelStateLabel({
      returnStateHash: 'sha256:a',
      document: { returnStateHash: 'sha256:a', documentName: null, ingestId: null, returnId: null, capturedAt: new Date().toISOString(), filename: 'a.f3d', bytes: 1 },
      runs: [],
    });
    expect(label).toBe('Model from just now');
    expect(modelStateLabel({ returnStateHash: 'sha256:a', document: null, runs: [] })).toBe('Earlier model');
    expect(modelStateLabel({ returnStateHash: null, document: null, runs: [] })).toBe('Model state not recorded');
  });
});
