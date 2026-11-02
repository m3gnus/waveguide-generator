import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { compareSelection, provisionalResults, resultsCache } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { resetCadReturnStore } from '../stores/cadReturn';
import { resetDocumentStore } from '../stores/document';
import { designForFamily, resetDesignStore, serializeDesign, useDesignStore, type DesignDocument } from '../stores/design';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { ResultsPanel } from './ResultsPanel';

const designOf = (length: number) => { const design = designForFamily('OSSE'); design.L = length; return design; };

function run(id: string, number: number, design: DesignDocument | null, extra: Partial<JobItem> = {}): JobItem {
  return {
    id, run_number: number, label: id, status: 'complete', has_results: true, progress: 1,
    created_at: `2026-08-20T00:00:${String(number).padStart(2, '0')}Z`,
    completed_at: `2026-08-20T00:01:${String(number).padStart(2, '0')}Z`,
    config_summary: { formula_type: 'OSSE' }, solve_options: {},
    script_snapshot: design ? { version: 1, design: serializeDesign(design) } : null,
    design_revision: 1, ...extra,
  } as unknown as JobItem;
}

function publish(jobs: JobItem[]): void {
  (jobsSocket as unknown as { update(patch: Partial<JobsSnapshot>): void }).update({ connection: 'connected', jobs });
}

const payload = (id: string) => ({ frequencies: [1_000], metadata: {}, client_metadata: { job: id } });
const flush = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };

describe('the chart detail view and the run it shows', () => {
  let host: HTMLDivElement;
  let root: Root;
  const held = new Map<string, () => void>();
  const detail = () => document.body.querySelector<HTMLElement>('[role="dialog"]');
  const body = () => detail()?.querySelector('.result-detail-chart')?.textContent ?? '';
  const runLabel = () => detail()?.querySelector('.result-detail-pick.run > span')?.textContent;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore(); resetCadReturnStore(); importedMeshStore.clear(); resetDocumentStore();
    workspaceModeStore.setMode('parametric');
    compareSelection.clear(); provisionalResults.clear(); resultsCache.clear();
    preferencesStore.resetForTests();
    preferencesStore.update({ chartTypes: ['summary'] });
    held.clear();
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const id = /\/api\/results\/([^/?]+)/.exec(String(input))?.[1];
      if (id === undefined) return new Response('{}', { status: 200 });
      const name = decodeURIComponent(id);
      if (name.startsWith('slow')) await new Promise<void>((resolve) => held.set(name, resolve));
      return new Response(JSON.stringify(payload(name)), { status: 200 });
    }));
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    publish([]);
    compareSelection.clear(); resultsCache.clear(); provisionalResults.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  async function open(jobs: JobItem[], primary: string): Promise<void> {
    useDesignStore.setState({ design: designOf(111) });
    publish(jobs);
    act(() => compareSelection.setPrimary(primary));
    await act(async () => { root.render(<ResultsPanel/>); await flush(); });
    await act(async () => { host.querySelector<HTMLButtonElement>('[aria-label="Expand panel 1"]')!.click(); await flush(); });
    expect(detail()).not.toBeNull();
  }

  async function choose(id: string): Promise<void> {
    const select = detail()!.querySelector<HTMLSelectElement>('[aria-label="Run shown"]')!;
    await act(async () => {
      select.value = id;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      await flush();
    });
  }

  it('closes when its run is deleted and nothing replaces it', async () => {
    await open([run('only', 1, designOf(111))], 'only');
    await act(async () => { publish([]); await flush(); });
    expect(detail()).toBeNull();
  });

  it('closes when a live run fails and nothing replaces it', async () => {
    const live = run('live', 1, designOf(111), { status: 'running', has_results: false });
    act(() => { provisionalResults.apply('live', 1, payload('live') as never, true); });
    await open([live], 'live');
    await act(async () => { publish([{ ...live, status: 'error' }]); await flush(); });
    expect(detail()).toBeNull();
  });

  it('moves to the run that replaces a deleted one instead of waiting for it', async () => {
    await open([run('gone', 2, designOf(111)), run('kept', 1, designOf(111))], 'gone');
    await act(async () => { publish([run('kept', 1, designOf(111))]); await flush(); });
    expect(detail()).not.toBeNull();
    expect(runLabel()).toContain('kept');
    expect(body()).not.toContain('Loading this run…');
  });

  it.each([
    ['another design', () => designOf(222)],
    ['an unreadable archive', () => null],
  ])('never shows the outgoing chart under the name of %s', async (_name, design) => {
    await open([run('current', 2, designOf(111)), run('slow', 1, design())], 'current');
    await choose('slow');
    expect(runLabel()).toContain('slow');
    expect(body()).toContain('Loading this run…');
    expect(detail()).not.toBeNull();
    await act(async () => { held.get('slow')!(); await flush(); await flush(); });
    expect(detail()).not.toBeNull();
    expect(body()).not.toContain('Loading this run…');
    expect(host.querySelector('[data-result-primary]')?.getAttribute('data-result-primary')).toBe('slow');
  });
});
