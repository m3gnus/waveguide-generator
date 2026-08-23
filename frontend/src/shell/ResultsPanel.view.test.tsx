import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EChartsOption } from 'echarts';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { compareSelection, provisionalResults, resultsCache, type JobResults } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { latestCombine } from '../results/latestCombine';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDesignStore } from '../stores/design';
import { resetDocumentStore } from '../stores/document';
import { resultViewStore } from '../stores/resultView';
import { workspaceModeStore } from '../stores/workspaceMode';
import { ResultsPanel } from './ResultsPanel';

/** Every option an EChart was handed, so a card can be read by its series
 * rather than through a canvas jsdom does not implement. */
const chartMocks = vi.hoisted(() => ({ drawn: [] as Array<{ label: string; option: unknown }> }));
vi.mock('../results/EChart', async (importOriginal) => ({
  ...await importOriginal<typeof import('../results/EChart')>(),
  EChart: ({ option, label }: { option: unknown; label: string }) => {
    chartMocks.drawn.push({ label, option });
    return <div className="chart-mock" data-label={label}/>;
  },
}));

const recombineMocks = vi.hoisted(() => ({ recombine: vi.fn() }));
vi.mock('../api/results', async (importOriginal) => ({
  ...await importOriginal<typeof import('../api/results')>(),
  recombineJobResults: recombineMocks.recombine,
}));

const SPL_CHART = 'Interactive HornLab sound pressure frequency response';
const POLAR_CHART = 'Interactive HornLab horizontal polar response';
const IMPEDANCE_CHART = 'Interactive HornLab normalized acoustic impedance by frequency';

interface DrawnSeries { name?: string; lineStyle?: { width?: number; opacity?: number } }

/** The series of the last option a named chart was drawn with. */
function series(label: string): DrawnSeries[] {
  const drawn = [...chartMocks.drawn].reverse().find((entry) => entry.label === label);
  const option = drawn?.option as EChartsOption | undefined;
  return Array.isArray(option?.series) ? option.series as DrawnSeries[] : [];
}

function seriesNames(label: string): string[] {
  return series(label).map((entry) => entry.name ?? '');
}

function channel(splDb: number, role?: string, combine?: Record<string, unknown>): JobResults {
  return {
    frequencies: [1_000],
    spl_on_axis: { frequencies: [1_000], spl: [splDb], phase_degrees: [0] },
    directivity: { horizontal: [[[0, 0], [30, -6]]] },
    impedance: { frequencies: [1_000], real: [8], imaginary: [1] },
    metadata: { ...(role ? { role } : {}), ...(combine ? { combine } : {}) },
  };
}

/** A three-way CAD return: two drivers and the LR4 sum of them. */
function threeWay(): JobResults {
  return {
    frequencies: [1_000],
    channel_order: ['drive-mf', 'drive-hf', 'combined'],
    channels: {
      'drive-mf': channel(90, 'MF'),
      'drive-hf': channel(92, 'HF'),
      combined: channel(94, undefined, {
        members: ['drive-mf', 'drive-hf'],
        member_roles: ['MF', 'HF'],
        crossovers_hz: [1_000],
        align: true,
      }),
    },
    metadata: { geometry_type: 'imported' },
  };
}

/** A two-way return with no MF driver, for the substitution rule. */
function highOnly(): JobResults {
  return {
    frequencies: [1_000],
    channel_order: ['drive-hf', 'drive-sub'],
    channels: { 'drive-hf': channel(88, 'HF'), 'drive-sub': channel(80, 'LF') },
    metadata: { geometry_type: 'imported' },
  };
}

function singleChannel(): JobResults {
  return {
    frequencies: [1_000],
    channel_order: ['drive-hf'],
    channels: { 'drive-hf': channel(91, 'HF') },
    metadata: { geometry_type: 'imported' },
  };
}

function job(id: string, runNumber: number): JobItem {
  return {
    id,
    run_number: runNumber,
    parent_job_id: null,
    status: 'complete',
    progress: 1,
    stage: null,
    stage_message: null,
    created_at: `2026-08-21T00:00:${String(runNumber).padStart(2, '0')}Z`,
    queued_at: '2026-08-21T00:00:00Z',
    started_at: null,
    completed_at: '2026-08-21T00:01:00Z',
    config_summary: { geometry_type: 'imported' },
    solve_options: {} as JobItem['solve_options'],
    has_results: true,
    has_mesh_artifact: true,
    field_plane_available: false,
    label: id,
    error_message: null,
    cancellation_requested: false,
    mesh_stats: null,
    script_snapshot: null,
    design_revision: 1,
    polar_grid: {},
    rating: null,
    exported_files: [],
    auto_export_completed_at: null,
    auto_export_formats: {},
    raw_results_file: null,
    mesh_artifact_file: null,
    log_tail: [],
    cad_source: {
      ingest_id: 'wgi_return',
      design_id: 'wgd_return',
      lineage_id: 'wgl_return',
      archive_stem: id,
      manifest_sha256: `sha256:${id}`,
      document_name: `${id} document`,
      return_state_hash: null,
    },
  };
}

function publishJobs(jobs: JobItem[]): void {
  const manager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
  manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}

describe('results dock view switch', () => {
  let host: HTMLDivElement;
  let root: Root;
  let payloads: Record<string, JobResults>;

  const viewButtons = (): HTMLButtonElement[] =>
    [...host.querySelectorAll<HTMLButtonElement>('[role="radiogroup"] [role="radio"]')];
  const chooseView = async (label: string) => {
    await act(async () => {
      viewButtons().find((button) => button.textContent === label)!.click();
      await Promise.resolve();
    });
  };
  const render = async () => {
    await act(async () => { root.render(<ResultsPanel/>); await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });
  };

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore();
    resetCadReturnStore();
    resetDocumentStore();
    resultViewStore.resetForTests();
    workspaceModeStore.setMode('cad');
    useCadReturnStore.setState({
      ingestRecord: { ingest_id: 'wgi_return' } as never,
      driveChannels: [
        { id: 'drive-mf', source_ids: ['source-mf'], motion: 'normal' },
        { id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' },
      ] as never,
    });
    compareSelection.clear();
    provisionalResults.clear();
    resultsCache.clear();
    preferencesStore.resetForTests();
    // Phase off: it doubles every SPL entry and this file is about which
    // channels reach a chart, not about how many traces each draws.
    preferencesStore.update({ chartTypes: ['frequency_response', 'polar_response', 'impedance', 'summary'], splPhase: false });
    chartMocks.drawn.length = 0;
    recombineMocks.recombine.mockReset();
    payloads = { primary: threeWay(), other: highOnly() };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const id = String(input).replace('/api/results/', '');
      return new Response(JSON.stringify(payloads[id] ?? { frequencies: [1_000], metadata: {} }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }));
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    publishJobs([]);
    compareSelection.clear();
    resultsCache.clear();
    provisionalResults.clear();
    resultViewStore.resetForTests();
    workspaceModeStore.setMode('parametric');
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('offers one segmented view per channel, combined first and bands highest first', async () => {
    publishJobs([job('primary', 1)]);
    compareSelection.setPrimary('primary');
    await render();

    expect(host.querySelector('[role="radiogroup"]')?.getAttribute('aria-label')).toBe('Result view');
    expect(viewButtons().map((button) => button.textContent)).toEqual(['Combined', 'HF', 'MF']);
    expect(viewButtons()[1].getAttribute('title')).toBe('drive-hf');
  });

  it('shows no switch for a run with a single channel', async () => {
    payloads.primary = singleChannel();
    publishJobs([job('primary', 1)]);
    compareSelection.setPrimary('primary');
    await render();

    expect(host.querySelector('[role="radiogroup"]')).toBeNull();
  });

  it('opens on the combined sum and draws its members beneath it', async () => {
    publishJobs([job('primary', 1)]);
    compareSelection.setPrimary('primary');
    await render();

    expect(viewButtons().map((button) => button.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false']);
    expect(seriesNames(SPL_CHART)).toEqual(['#1 · primary · Combined', '#1 · primary · MF', '#1 · primary · HF']);
    const [sum, ...members] = series(SPL_CHART);
    expect(sum.lineStyle?.opacity).toBeUndefined();
    expect(members.every((member) => member.lineStyle?.opacity === .45 && member.lineStyle?.width === 1)).toBe(true);
    // The members are the sum's own branches, not other opinions about it.
    expect(seriesNames(POLAR_CHART)).toEqual(['#1 · primary · Combined']);
  });

  it('drops the members from the SPL chart when the preference is off', async () => {
    publishJobs([job('primary', 1)]);
    compareSelection.setPrimary('primary');
    await render();
    await act(async () => { preferencesStore.update({ showMembersUnderCombined: false }); });

    expect(seriesNames(SPL_CHART)).toEqual(['#1 · primary · Combined']);
  });

  it('lists the members on the impedance card the sum has no impedance for', async () => {
    publishJobs([job('primary', 1)]);
    compareSelection.setPrimary('primary');
    await render();

    expect(seriesNames(IMPEDANCE_CHART)).toEqual([
      '#1 · primary · Combined · Re', '#1 · primary · Combined · Im',
      '#1 · primary · MF · Re', '#1 · primary · MF · Im',
      '#1 · primary · HF · Re', '#1 · primary · HF · Im',
    ]);
    expect(host.querySelector('.result-card.result-2 .result-subtitle')?.textContent).toBe('per driver');
  });

  it('draws only the chosen driver on every chart, for every selected run', async () => {
    publishJobs([job('primary', 1), job('other', 2)]);
    compareSelection.setPrimary('primary');
    compareSelection.toggleOverlay('other');
    await render();
    await chooseView('MF');

    // `other` has no MF, so it substitutes its sum -- it has none -- and then
    // its first channel, and says on the label which one that is.
    expect(seriesNames(SPL_CHART)).toEqual(['#1 · primary · MF', '#2 · other · HF']);
    expect(seriesNames(POLAR_CHART)).toEqual(['#1 · primary · MF', '#2 · other · HF']);
  });

  it('keeps the chosen view when the shown run changes', async () => {
    publishJobs([job('primary', 1), job('other', 2)]);
    compareSelection.setPrimary('primary');
    await render();
    await chooseView('MF');
    expect(seriesNames(SPL_CHART)).toEqual(['#1 · primary · MF']);

    await act(async () => { compareSelection.setPrimary('other'); await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });
    expect(resultViewStore.getSnapshot()).toBe('drive-mf');
    expect(seriesNames(SPL_CHART)).toEqual(['#2 · other · HF']);

    await act(async () => { compareSelection.setPrimary('primary'); await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });
    expect(seriesNames(SPL_CHART)).toEqual(['#1 · primary · MF']);
  });

  it('publishes the shown combine to the rail bridge, only in the combined view', async () => {
    publishJobs([job('primary', 1)]);
    compareSelection.setPrimary('primary');
    await render();

    // The strip is gone: crossover settings live in the rail alone, and the
    // dock hands the rail what it needs through the bridge instead.
    expect(host.querySelector('form.result-recombine')).toBeNull();
    const shown = latestCombine.getSnapshot()!;
    expect(shown.jobId).toBe('primary');
    expect(shown.channelId).toBe('combined');
    expect(shown.canApply).toBe(true);
    expect(shown.combine.members).toEqual(['drive-mf', 'drive-hf']);

    // The bridge's callback is the dock's own swap-in.
    const applied = { ...threeWay() };
    (applied.channels!.combined.metadata!.combine as Record<string, unknown>).crossovers_hz = [1_400];
    act(() => shown.onApplied('primary', applied as never));
    expect((latestCombine.getSnapshot()!.combine.crossovers_hz)).toEqual([1_400]);

    await chooseView('HF');
    expect(latestCombine.getSnapshot()).toBeNull();
  });

  it('names the shown channel on the summary card', async () => {
    publishJobs([job('primary', 1)]);
    compareSelection.setPrimary('primary');
    await render();
    await chooseView('MF');

    const rows = [...host.querySelectorAll('.result-summary-row')].map((row) => row.textContent);
    expect(rows).toContain(`ChannelMF (drive-mf) · ${(3).toLocaleString()} channels`);
  });
});
