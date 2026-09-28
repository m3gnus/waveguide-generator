import { readFileSync } from 'node:fs';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EChartsOption } from 'echarts';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { compareSelection, provisionalResults, resultsCache } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { UNQUALIFIED_MESSAGE } from '../results/powerQualification';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDesignStore, serializeDesign, useDesignStore } from '../stores/design';
import { resetDocumentStore } from '../stores/document';
import { resultViewStore } from '../stores/resultView';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { ResultsPanel } from './ResultsPanel';

const chartMocks = vi.hoisted(() => ({ drawn: [] as Array<{ label: string; option: unknown }> }));
vi.mock('../results/EChart', async (importOriginal) => ({
  ...await importOriginal<typeof import('../results/EChart')>(),
  EChart: ({ option, label }: { option: unknown; label: string }) => {
    chartMocks.drawn.push({ label, option });
    return <div className="chart-mock" data-label={label}/>;
  },
}));

const SPL_CHART = 'Interactive HornLab sound pressure frequency response';

interface MarkArea { data: Array<[{ name?: string; xAxis: number }, { xAxis: number }]> }

/** The hatched bands of the last SPL option drawn, as [from, to] pairs. */
function splBands(): Array<[number, number]> {
  const drawn = [...chartMocks.drawn].reverse().find((entry) => entry.label === SPL_CHART);
  const series = (drawn?.option as EChartsOption | undefined)?.series;
  const first = Array.isArray(series) ? series[0] as { markArea?: MarkArea } : undefined;
  return first?.markArea?.data.map(([from, to]) => [from.xAxis, to.xAxis]) ?? [];
}

function identity(kind: 'parametric' | 'multi_channel') {
  const digest = 'a'.repeat(64);
  return {
    result_kind: kind,
    result_contract_version: kind === 'parametric' ? 1 : 2,
    client_request_id: null,
    client_metadata: {},
    provenance: {
      schema_version: 1, wg_version: 'test', dependency_shas: {},
      request_sha256: digest, geometry_sha256: digest, solve_options_sha256: digest,
      request_identity: 'execution',
      execution_request_sha256: digest, execution_geometry_sha256: digest, execution_solve_options_sha256: digest,
      effective_request_sha256: digest, effective_geometry_sha256: digest, effective_solve_options_sha256: digest,
      resolved_engine: 'metal',
    },
  };
}

/** The archived Speaker2 v4 run exactly as the server serves it on open. */
function speaker2(): Record<string, unknown> {
  const served = JSON.parse(new TextDecoder().decode(readFileSync('../server/tests/data/power-qualification-speaker2-v4.read-time.json'))) as Record<string, unknown>;
  return { ...served, ...identity('multi_channel') };
}

function job(id: string, runNumber: number, imported: boolean): JobItem {
  return {
    id, run_number: runNumber, parent_job_id: null, status: 'complete', progress: 1,
    stage: null, stage_message: null,
    created_at: `2026-09-23T00:00:${String(runNumber).padStart(2, '0')}Z`,
    queued_at: '2026-09-23T00:00:00Z', started_at: null, completed_at: '2026-09-23T00:01:00Z',
    config_summary: imported ? { geometry_type: 'imported' } : {},
    solve_options: {} as JobItem['solve_options'],
    has_results: true, has_mesh_artifact: imported, field_plane_available: false,
    label: id, error_message: null, cancellation_requested: false, mesh_stats: null,
    script_snapshot: imported ? null : { version: 1, design: serializeDesign(useDesignStore.getState().design) },
    design_revision: 1, polar_grid: {}, rating: null, exported_files: [],
    auto_export_completed_at: null, auto_export_formats: {}, raw_results_file: null,
    mesh_artifact_file: null, log_tail: [],
    cad_source: imported ? {
      ingest_id: 'wgi_speaker2', design_id: 'wgd_speaker2', lineage_id: 'wgl_speaker2',
      archive_stem: id, manifest_sha256: `sha256:${id}`, document_name: 'Speaker2 v4', return_state_hash: null,
    } : null,
  };
}

function publishJobs(jobs: JobItem[]): void {
  const manager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
  manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}

describe('results power qualification', () => {
  let host: HTMLDivElement;
  let root: Root;
  let payload: Record<string, unknown>;

  const chip = () => host.querySelector<HTMLButtonElement>('.result-power-check');
  const badges = () => [...host.querySelectorAll<HTMLElement>('.result-unqualified')];
  const render = async () => {
    await act(async () => { root.render(<ResultsPanel/>); await Promise.resolve(); });
    await act(async () => { await Promise.resolve(); });
  };
  const chooseView = async (label: string) => {
    await act(async () => {
      [...host.querySelectorAll<HTMLButtonElement>('[role="radiogroup"] [role="radio"]')]
        .find((button) => button.textContent === label)!.click();
      await Promise.resolve();
    });
  };
  const openChip = async () => { await act(async () => { chip()!.click(); }); };

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetDesignStore();
    resetCadReturnStore();
    resetDocumentStore();
    resultViewStore.resetForTests();
    importedMeshStore.clear();
    compareSelection.clear();
    provisionalResults.clear();
    resultsCache.clear();
    preferencesStore.resetForTests();
    preferencesStore.update({ chartTypes: ['frequency_response', 'polar_response'], splPhase: false });
    chartMocks.drawn.length = 0;
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(payload), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })));
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

  describe('CAD mode, archived Speaker2 v4', () => {
    beforeEach(() => {
      workspaceModeStore.setMode('cad');
      useCadReturnStore.setState({
        ingestRecord: { ingest_id: 'wgi_speaker2' } as never,
        driveChannels: [
          { id: 'drive-lf', source_ids: ['wgs-QBQ26C6122140K1H0555'] },
          { id: 'drive-hf', source_ids: ['wgs-NMG2W9KB8N7JXDGM8JQQ'] },
        ] as never,
      });
      importedMeshStore.setCad({ source: 'cad', ingestId: 'wgi_speaker2' } as never);
      payload = speaker2();
      publishJobs([job('speaker2', 1, true)]);
      compareSelection.setPrimary('speaker2');
    });

    it('marks the combined view unqualified, names the LF channel and hatches its bands', async () => {
      await render();

      expect(chip()!.textContent).toBe('Power check: unqualified ⚠');
      expect(chip()!.dataset.status).toBe('unqualified');
      await openChip();
      const detail = host.querySelector<HTMLElement>('.result-power-details')!.textContent!;
      expect(detail).toContain(UNQUALIFIED_MESSAGE);
      expect(detail).toContain('Affected: 200 Hz–1.21 kHz, 1.81 kHz');
      expect(detail).toContain('Unqualified contributing channel: LF.');
      // One channel's chip never hides another's standing.
      expect(detail).toContain('Channels: LF unqualified · HF qualified · Combined unqualified.');
      expect(detail).toContain('archived run was opened');

      const bands = splBands();
      expect(bands).toHaveLength(2);
      expect(bands[0][0]).toBe(200);
      expect(bands[1][0]).toBeLessThan(1_809.47);
      expect(bands[1][1]).toBeGreaterThan(1_809.47);
      // Every card of the channel says so, including one that cannot hatch.
      expect(badges()).toHaveLength(2);
      expect(badges()[0].textContent).toContain('Unqualified 200 Hz–1.21 kHz, 1.81 kHz');
      expect(badges()[0].title).toContain(UNQUALIFIED_MESSAGE);
    });

    it('reports the HF channel qualified with nothing marked, and LF unqualified with its own bands', async () => {
      await render();

      await chooseView('HF');
      expect(chip()!.textContent).toBe('Power check ✓');
      expect(splBands()).toEqual([]);
      expect(badges()).toEqual([]);

      await chooseView('LF');
      expect(chip()!.textContent).toBe('Power check: unqualified ⚠');
      expect(splBands()).toHaveLength(2);
      await openChip();
      const detail = host.querySelector<HTMLElement>('.result-power-details')!.textContent!;
      expect(detail).toContain('Power check: −7.46 dB at 1.81 kHz');
      expect(detail).toContain(UNQUALIFIED_MESSAGE);
    });
  });

  describe('parametric mode', () => {
    function parametric(agreement: number[], flagged: boolean): Record<string, unknown> {
      const frequencies = [200, 500, 1_000, 2_000];
      return {
        ...identity('parametric'),
        frequencies,
        spl_on_axis: { frequencies, spl: [90, 91, 92, 93], phase_degrees: [0, 0, 0, 0] },
        metadata: {
          metal: { formulation: 'complex_k', complex_k_shift: 0.005 },
          radiated_power: {
            surface_w: frequencies.map(() => 1e-5),
            sphere_w: agreement.map((value) => 1e-5 * 10 ** (value / 10)),
            sphere_coverage_sr: 4 * Math.PI,
            definition: 'test',
            agreement_db: agreement,
          },
          ...(flagged ? {} : {
            power_qualification: {
              version: 1, status: 'qualified', evaluated: 'solve', threshold_db: 0.5, validity_max_hz: null,
              frequency_status: frequencies.map(() => 'qualified'), frequency_reasons: frequencies.map(() => null),
              unqualified_ranges: [], reasons: [], unknown_reason: null, worst: null,
              provenance: { formulation: 'complex_k', complex_k_shift: 0.005, recorded: true }, message: null,
            },
          }),
        },
      };
    }

    beforeEach(() => {
      workspaceModeStore.setMode('parametric');
      publishJobs([job('parametric', 2, false)]);
      compareSelection.setPrimary('parametric');
    });

    it('uses the same chip and bands for an unqualified parametric run', async () => {
      payload = parametric([0.1, -0.9, -1.2, 0.2], true);
      await render();

      expect(chip()!.textContent).toBe('Power check: unqualified ⚠');
      expect(splBands()).toEqual([[Math.sqrt(200 * 500), Math.sqrt(1_000 * 2_000)]]);
      expect(badges()[0].textContent).toContain('Unqualified 500 Hz–1.00 kHz');
      await openChip();
      expect(host.querySelector('.result-power-details')!.textContent).toContain(UNQUALIFIED_MESSAGE);
    });

    it('shows a qualified parametric run as qualified, with nothing hatched', async () => {
      payload = parametric([0.1, 0.2, -0.3, 0.2], false);
      await render();

      expect(chip()!.textContent).toBe('Power check ✓');
      expect(splBands()).toEqual([]);
      expect(badges()).toEqual([]);
    });
  });
});
