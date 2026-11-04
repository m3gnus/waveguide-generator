import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EChartsOption } from 'echarts';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { compareSelection, provisionalResults, resultsCache, type JobResults, type ResultData } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { latestCombine } from '../results/latestCombine';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { addWoofer, commitAssembly, emptyAssembly } from '../stores/assembly';
import { assemblySimulationData } from '../stores/assemblySimulation';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { compiledDesignAssembly } from '../jobs/assemblySolve.fixtures';
import { resolveAssemblySetup } from '../jobs/importedSubmission';
import { resetDocumentStore } from '../stores/document';
import { resultViewStore } from '../stores/resultView';
import { workspaceModeStore } from '../stores/workspaceMode';
import { ResultsPanel } from './ResultsPanel';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { resetImportedModelStore, useImportedModelStore } from '../stores/importedModel';

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

interface DrawnSeries { data?: Array<[number, number]>; name?: string; lineStyle?: { width?: number; opacity?: number } }

/** The series of the last option a named chart was drawn with. */
function series(label: string): DrawnSeries[] {
  const drawn = [...chartMocks.drawn].reverse().find((entry) => entry.label === label);
  const option = drawn?.option as EChartsOption | undefined;
  return Array.isArray(option?.series) ? option.series as DrawnSeries[] : [];
}

function seriesNames(label: string): string[] {
  return series(label).map((entry) => entry.name ?? '');
}

function channel(splDb: number, role?: string, combine?: Record<string, unknown>): ResultData {
  return {
    frequencies: [1_000],
    spl_on_axis: { frequencies: [1_000], spl: [splDb], phase_degrees: [0] },
    directivity: { horizontal: [[[0, 0], [30, -6]]] },
    impedance: { frequencies: [1_000], real: [8], imaginary: [1] },
    metadata: { source_normalization: 'native_rms_velocity', phase_time_convention: 'exp(+ikr)', ...(role ? { role } : {}), ...(combine ? { combine } : {}) },
  };
}

function finalIdentity(kind: 'parametric' | 'multi_channel') {
  const digest = 'a'.repeat(64);
  return {
    result_kind: kind,
    result_contract_version: kind === 'parametric' ? 1 as const : 2 as const,
    client_request_id: null,
    client_metadata: {},
    provenance: {
      schema_version: 1,
      wg_version: 'test',
      dependency_shas: {},
      request_sha256: digest,
      geometry_sha256: digest,
      solve_options_sha256: digest,
      request_identity: 'execution',
      execution_request_sha256: digest,
      execution_geometry_sha256: digest,
      execution_solve_options_sha256: digest,
      effective_request_sha256: digest,
      effective_geometry_sha256: digest,
      effective_solve_options_sha256: digest,
      resolved_engine: 'test',
    },
  };
}

/** A three-way CAD return: two drivers and the LR4 sum of them. */
function threeWay(): JobResults {
  return {
    ...finalIdentity('multi_channel'),
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
    ...finalIdentity('multi_channel'),
    frequencies: [1_000],
    channel_order: ['drive-hf', 'drive-sub'],
    channels: { 'drive-hf': channel(88, 'HF'), 'drive-sub': channel(80, 'LF') },
    metadata: { geometry_type: 'imported' },
  };
}

function singleChannel(): JobResults {
  return {
    ...finalIdentity('multi_channel'),
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
    resetImportedModelStore();
    resultViewStore.resetForTests();
    workspaceModeStore.setMode('cad');
    workspaceModeStore.setImportedInput('cad');
    importedMeshStore.clear();
    useCadReturnStore.setState({
      ingestRecord: { ingest_id: 'wgi_return' } as never,
      driveChannels: [
        { id: 'drive-mf', source_ids: ['source-mf'], motion: 'normal' },
        { id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' },
      ] as never,
    });
    importedMeshStore.setCad({ source: 'cad', ingestId: 'wgi_return' } as never);
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
      return new Response(JSON.stringify(payloads[id] ?? {
        ...finalIdentity('parametric'), frequencies: [1_000], metadata: {},
      }), {
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

  it('opens a retained assembly on Combined with the shared HF/LF view controls', async () => {
    workspaceModeStore.setImportedInput('assembly');
    useImportedModelStore.setState({ input: 'assembly', phase: 'ready',
      prepared: { ingest_id: 'wgi_return', manifest_sha256: 'm', artifact_sha256: 'a' } });
    importedMeshStore.setFile({ ingestId: 'wgi_return' } as never);
    const saved = job('primary', 1);
    saved.config_summary.import_origin = 'design-assembly';
    const result = payloads.primary;
    result.channels!['drive-mf'].metadata!.role = 'LF';
    publishJobs([saved]);
    compareSelection.setPrimary('primary');
    await render();
    expect(viewButtons().map(button => button.textContent)).toEqual(['Combined', 'HF', 'LF']);
    expect(viewButtons()[0].getAttribute('aria-checked')).toBe('true');
    expect(seriesNames(POLAR_CHART)).toEqual(['#1 · primary · Combined']);
    await chooseView('LF');
    expect(seriesNames(POLAR_CHART)).toEqual(['#1 · primary · LF']);
    await chooseView('HF');
    expect(seriesNames(POLAR_CHART)).toEqual(['#1 · primary · HF']);
    await chooseView('Combined');
    expect(seriesNames(POLAR_CHART)).toEqual(['#1 · primary · Combined']);
    expect(latestCombine.getSnapshot()?.canApply).toBe(true);
  });

  it('shows the combined response requested by a newly added first woofer', async () => {
    const added = addWoofer(emptyAssembly(), { anchor: 'below' });
    commitAssembly(added.assembly);
    expect(useDesignStore.getState().design.assembly?.crossover).toEqual({ enabled: true });
    const record = compiledDesignAssembly().prepared;
    const source = { id: `${added.id}/cap`, role: 'LF', radiator_id: added.id, segment_id: 'cap' };
    const built = { ...record, sources: [...record.sources, source], excitations: [...record.excitations,
      { id: added.id, source_ids: [source.id], motion: 'axial' as const, patch_weights: {} }] };
    const design = useDesignStore.getState().design;
    const simulation = { ...assemblySimulationData(design, built), sources: built.sources };
    const request = resolveAssemblySetup(simulation, added.assembly, { engine: 'metal' } as never, design.simulation);
    const combine = request.setup.geometry.combine!;
    expect(combine?.members).toHaveLength(2);
    const [low, high] = combine.members;
    // The build and solver response are fixtures; request setup and result rendering are real.
    payloads.primary = { ...finalIdentity('multi_channel'), frequencies: [1_000],
      channel_order: [high, low, 'combined'], metadata: { geometry_type: 'imported' },
      channels: { [low]: channel(90, 'LF'), [high]: channel(92, 'HF'),
        combined: channel(94, undefined, { ...combine, crossovers_hz: [combine.channels![low].lp!.fc_hz],
          member_roles: ['LF', 'HF'] }) } };
    workspaceModeStore.setImportedInput('assembly');
    useImportedModelStore.setState({ input: 'assembly', phase: 'ready',
      prepared: { ingest_id: 'wgi_return', manifest_sha256: 'm', artifact_sha256: 'a' } });
    importedMeshStore.setFile({ ingestId: 'wgi_return' } as never);
    const run = job('primary', 1);
    run.config_summary.import_origin = 'design-assembly';
    publishJobs([run]);
    compareSelection.setPrimary('primary');
    await render();
    expect(viewButtons().map(button => button.textContent)).toEqual(['Combined', 'HF', 'LF']);
    expect(seriesNames(POLAR_CHART)).toEqual(['#1 · primary · Combined']);
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

  it('aligns an old CAD acceleration overlay to the new CAD RMS channel', async () => {
    const old = payloads.other.channels!['drive-hf'];
    old.metadata = { ...old.metadata, source_normalization: 'unit_normal_acceleration' };
    old.spl_on_axis = { frequencies: [1000], spl: [92 - 20 * Math.log10(2 * Math.PI * 1000)], phase_degrees: [90] };
    publishJobs([job('primary', 1), job('other', 2)]);
    compareSelection.setPrimary('primary');
    compareSelection.toggleOverlay('other');
    await render();
    await chooseView('HF');
    const drawn = series(SPL_CHART);
    expect(drawn.map(trace => trace.name)).toEqual(['#1 · primary · HF', '#2 · other · HF']);
    expect(drawn[1].data![0][1]).toBeCloseTo(drawn[0].data![0][1], 10);
    expect(host.querySelector('.result-card.result-0 [role="note"]')).toBeNull();
  });

  it('withholds unknown stored excitation and shows a not-comparable note', async () => {
    for (const result of Object.values(payloads.other.channels!)) {
      result.metadata = { ...result.metadata, source_normalization: 'future' };
    }
    publishJobs([job('primary', 1), job('other', 2)]);
    compareSelection.setPrimary('primary');
    compareSelection.toggleOverlay('other');
    await render();
    await chooseView('MF');
    expect(seriesNames(SPL_CHART)).toEqual(['#1 · primary · MF']);
    expect(host.querySelector('.result-card.result-0 [role="note"]')?.textContent).toContain('Not comparable');
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

  it('publishes the shown combine to the rail bridge, whichever channel is on screen', async () => {
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
    expect(shown.blockedReason).toBeNull();
    expect(shown.combine.members).toEqual(['drive-mf', 'drive-hf']);

    // The bridge's callback is the dock's own swap-in.
    const applied = { ...threeWay() };
    (applied.channels!.combined.metadata!.combine as Record<string, unknown>).crossovers_hz = [1_400];
    act(() => shown.onApplied('primary', applied as never));
    expect((latestCombine.getSnapshot()!.combine.crossovers_hz)).toEqual([1_400]);

    // Looking at one member's own curve is no reason for the rail's automatic
    // gains and delays to blank out: the run still has a combined channel, and
    // that is what a crossover edit would recombine.
    await chooseView('HF');
    const member = latestCombine.getSnapshot()!;
    expect(member.channelId).toBe('combined');
    expect(member.combine.members).toEqual(['drive-mf', 'drive-hf']);
  });

  it('hides results and recombination until the reopened model is loaded', async () => {
    // A session that has not ingested is exactly what a browser refresh leaves
    // behind: the project's settings are restored, but no ingestion owns them.
    useCadReturnStore.setState({ ingestRecord: null });
    publishJobs([job('primary', 1)]);
    compareSelection.setPrimary('primary');
    await render();

    expect(latestCombine.getSnapshot()).toBeNull();
    expect(host.querySelector('[data-result-primary]')).toBeNull();
    expect(host.textContent).toContain('No CAD model yet');
    expect(host.textContent).toContain("Show that run's model");
  });

  it('names the shown channel on the summary card, with its id only for developers', async () => {
    publishJobs([job('primary', 1)]);
    compareSelection.setPrimary('primary');
    await render();
    await chooseView('MF');

    const rows = () => [...host.querySelectorAll('.result-summary-row')].map((row) => row.textContent);
    expect(rows()).toContain(`ChannelMF · ${(3).toLocaleString()} channels`);
    await act(async () => { preferencesStore.update({ showDeveloperDetails: true }); });
    expect(rows()).toContain(`ChannelMF (drive-mf) · ${(3).toLocaleString()} channels`);
  });
});
