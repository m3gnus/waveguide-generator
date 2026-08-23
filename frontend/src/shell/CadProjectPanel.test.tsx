import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { compareSelection } from '../api/results';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { resetCadReturnStore } from '../stores/cadReturn';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetDesignStore } from '../stores/design';
import { resetWorkspaceFolderStore } from '../stores/workspaceFolder';
import { cadProjectReference } from '../api/cadProjects';
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

    await render(<CadProjectHeader documentName="Tritonia M"/>);

    expect(host.querySelector('.cad-project-name')?.textContent).toBe('Tritonia M');
    expect(host.textContent).toContain('2 runs in this project');
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
      return json({});
    }));
    setJobs([run({ id: 'a' })]);

    await render(<CadProjectHeader documentName="Tritonia M"/>);
    const strip = host.querySelector<HTMLElement>('.cad-projects-folder')!;
    expect(strip.textContent).toContain('/exports');

    const buttons = [...strip.querySelectorAll<HTMLButtonElement>('button')];
    await act(async () => { buttons[0].click(); await Promise.resolve(); });
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/workspace/open', { method: 'POST' });

    // Chosen through the server, not the browser: the same v1 mechanism the
    // output and WGLink folders use, so it works away from Chromium.
    await act(async () => { buttons[1].click(); await Promise.resolve(); });
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/workspace/select', { method: 'POST' });
    expect(strip.textContent).toContain('/picked/projects');
  });

  it('offers to open a project when none is', async () => {
    resetDocumentStore();
    setJobs([]);

    await render(<CadProjectHeader documentName={null}/>);

    expect(host.textContent).toContain('No CAD project open');
  });
});

describe('what the history calls things', () => {
  it('names a project by the folder it owns, then by its design file', () => {
    expect(projectName({ archiveStem: 'Tritonia', filename: 'other.cfg' })).toBe('Tritonia');
    expect(projectName({ archiveStem: null, filename: 'Big Horn.cfg' })).toBe('Big Horn');
    expect(projectName({ archiveStem: '   ', filename: 'Big Horn.cfg' })).toBe('Big Horn');
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
