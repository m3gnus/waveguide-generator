import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { compareSelection } from '../api/results';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { resetCadReturnStore } from '../stores/cadReturn';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { workspaceModeStore } from '../stores/workspaceMode';
import { CadLinkCoordinator } from './CadLinkCoordinator';
import { CadProjectHeader, CadProjectHistory, modelStateLabel, projectName } from './CadProjectPanel';

const LINEAGE = 'wgl_project';

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

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => { resolve = settle; });
  return { promise, resolve };
}

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
    resetCadReturnStore(); resetDocumentStore(); resetDesignStore();
    compareSelection.clear();
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('/documents')) return json(documents);
      return json({});
    }));
    useDocumentStore.getState().setCadLink(
      { designId: 'wgd_project', lineageId: LINEAGE, baseEditVersion: 1 }, 'current',
    );
  });
  afterEach(() => {
    act(() => root.unmount());
    compareSelection.clear();
    workspaceModeStore.setMode('parametric');
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

    await render(<CadProjectHeader documentName="Tritonia M"/>);

    expect(host.querySelector('.cad-project-name')?.textContent).toBe('Tritonia M');
    expect(host.textContent).toContain('2 runs in this project');
  });

  it('offers to open a project when none is', async () => {
    resetDocumentStore();
    setJobs([]);

    await render(<CadProjectHeader documentName={null}/>);

    expect(host.textContent).toContain('No CAD project open');
  });

  it('keeps CAD mode when a project is opened from the CAD-only switcher', async () => {
    const nextIdentity = {
      designId: 'wgd_next', lineageId: 'wgl_next', baseEditVersion: 4,
      editVersion: 4, savedAt: '2026-08-22T10:00:00Z', savedDesignHash: 'sha256:next', schema: 1,
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === '/api/cadlink/designs') return json({ items: [{
        designId: nextIdentity.designId,
        lineageId: nextIdentity.lineageId,
        editVersion: 4,
        designHash: 'sha256:next',
        filename: 'next-project.cfg',
        branchedFromDesignId: null,
        branchedFromEditVersion: null,
        exportCount: 1,
        lastExportedAt: '2026-08-22T10:00:00Z',
        createdAt: '2026-08-22T09:00:00Z',
        updatedAt: '2026-08-22T10:00:00Z',
        archiveStem: 'next-project',
      }] });
      if (path === `/api/cadlink/designs/${nextIdentity.designId}`) return json({
        designId: nextIdentity.designId,
        lineageId: nextIdentity.lineageId,
        editVersion: 4,
        filename: 'next-project.cfg',
        updatedAt: '2026-08-22T10:00:00Z',
        text: 'next project snapshot',
      });
      if (path === '/api/design/open') return json({
        dialect: 'ath',
        migrationsApplied: [],
        passthrough: { keysPreserved: [], blocksPreserved: [], keyCount: 0, blockCount: 0 },
        design: useDesignStore.getState().design,
        cadlink: { identity: nextIdentity, classification: 'current', adoptionCandidate: null },
      });
      if (path.endsWith('/returns')) return json({ items: [] });
      if (path.endsWith('/fusion-status')) return json({ state: 'closed' });
      if (path.endsWith('/solve-command')) return json({ command: null });
      return json({});
    }));
    workspaceModeStore.setMode('cad');
    await render(<><CadLinkCoordinator/><CadProjectHeader documentName="Tritonia"/></>);

    await act(async () => {
      host.querySelector<HTMLButtonElement>('.cad-project-switcher > button')!.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => {
      host.querySelector<HTMLButtonElement>('[role="menuitem"]')!.click();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
  });
});

describe('what the history calls things', () => {
  it('names a project by the folder it owns, then by its design file', () => {
    expect(projectName({ archiveStem: 'Tritonia', filename: 'other.cfg' })).toBe('Tritonia');
    expect(projectName({ archiveStem: null, filename: 'Big Horn.cfg' })).toBe('Big Horn');
    expect(projectName({ archiveStem: '   ', filename: 'Big Horn.cfg' })).toBe('Big Horn');
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
