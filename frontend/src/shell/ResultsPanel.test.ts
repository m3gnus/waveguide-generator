import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobItem } from '../api/jobsSocket';
import { preferencesStore, usePreferences, type ChartType } from '../prefs/preferences';
import type { ChartTokens } from '../results/EChart';
import type { NamedResult } from '../results/mappers';
import type { SummaryContext, SummaryGroup } from '../results/summary';
import type { ResultPayload } from '../results/types';
import { resultFrequencyValidity } from '../results/validity';
import { designForFamily, serializeDesign } from '../stores/design';
import { beamShapeMissingReason, chartImageFilename, chartUnit, COMPARABLE_CHARTS, comparisonContourPointToPixels, directivityIndexOption, directivityMapPanels, driverChartMissingReason, drivePowerOption, groupDelayMissingReason, groupDelayOption, heatmapOption, impedanceOption, phaseOption, polarOption, ResultsChartGrid, resolvedPolarStepNotice, resultExportSnapshot, resultLayoutClass, splOption } from './ResultsPanel';

const chartImageMocks = vi.hoisted(() => ({
  copy: vi.fn<() => Promise<void>>(),
  download: vi.fn<() => Promise<void>>(),
}));
vi.mock('../results/chartImage', async (importOriginal) => ({
  ...await importOriginal<typeof import('../results/chartImage')>(),
  copyChartPng: chartImageMocks.copy,
  downloadChartPng: chartImageMocks.download,
}));

const summaryMocks = vi.hoisted(() => ({
  groups: vi.fn<(context: SummaryContext) => SummaryGroup[]>(() => []),
  text: vi.fn<(groups: SummaryGroup[]) => string>(() => ''),
}));
vi.mock('../results/summary', () => ({ summaryGroups: summaryMocks.groups, summaryText: summaryMocks.text }));

const tokens: ChartTokens = { foreground: '#fff', muted: '#aaa', grid: '#333', gridMinor: '#222', accent: '#0ff', series: ['#0ff'], colormap: ['#000', '#fff'] };
const result = { frequencies: [], metadata: {} };
function named(id: string, label: string, payload: ResultPayload): NamedResult {
  return { id, label, result: payload };
}
function ResultsHarness() {
  const preferences = usePreferences();
  return createElement(ResultsChartGrid, { chartTypes: preferences.chartTypes, result, named: [], tokens });
}

describe('result comparison charts', () => {
  const comparisonTokens: ChartTokens = { ...tokens, series: ['#0ff', '#f90', '#f55'] };
  const primary = named('primary', 'Run A', {
    frequencies: [500, 1_000],
    directivity: {
      horizontal: [[[0, 0]], [[0, 0]]],
      vertical: [[[0, 0]], [[0, 0]]],
      diagonal: [[[0, 0]], [[0, 0]]],
    },
    di: { frequencies: [500, 1_000], di: { horizontal: [3, 5], vertical: [2, 4] } },
    impedance: { frequencies: [500, 1_000], real: [1, 2], imaginary: [.2, .4] },
  });
  const overlay = named('overlay', 'Run B', {
    frequencies: [500, 1_000],
    directivity: { horizontal: [[[0, 0]], [[0, 0]]] },
    di: { frequencies: [500, 1_000], di: { horizontal: [4, 6], vertical: [3, 5] } },
    impedance: { frequencies: [500, 1_000], real: [2, 3], imaginary: [.3, .5] },
  });
  const items = [primary, overlay];

  it('enables comparison for SPL, every directivity map, DI, impedance, phase, group delay and polar', () => {
    expect([...COMPARABLE_CHARTS]).toEqual([
      'frequency_response', 'directivity_map_h', 'directivity_map_v',
      'directivity_map_d', 'directivity_map', 'directivity_index', 'impedance',
      'phase_response', 'group_delay', 'polar_response',
    ]);
  });

  it('builds one heatmap per plane with runs assigned as contour overlays', () => {
    expect(directivityMapPanels(items, 'directivity_map_h').map(({ plane, primaryLabel, references, hasData }) => ({ plane, primaryLabel, references: references.map(({ label }) => label), hasData }))).toEqual([
      { plane: 'horizontal', primaryLabel: 'Run A', references: ['Run B'], hasData: true },
    ]);
    expect(directivityMapPanels(items, 'directivity_map_d').map(({ plane, primaryLabel, references, hasData }) => ({ plane, primaryLabel, references: references.map(({ label }) => label), hasData }))).toEqual([
      { plane: 'diagonal', primaryLabel: 'Run A', references: [], hasData: true },
    ]);
    expect(directivityMapPanels(items, 'directivity_map').map(({ plane, primaryLabel, references, hasData }) => ({ plane, primaryLabel, references: references.map(({ label }) => label), hasData }))).toEqual([
      { plane: 'horizontal', primaryLabel: 'Run A', references: ['Run B'], hasData: true },
      { plane: 'vertical', primaryLabel: 'Run A', references: [], hasData: true },
      { plane: 'diagonal', primaryLabel: 'Run A', references: [], hasData: true },
    ]);
  });

  it('draws comparison runs in the primary heatmap as labelled contour series', () => {
    const map = (edge: number): ResultPayload => ({
      frequencies: [500, 1_000],
      directivity: { horizontal: [
        [[0, 0], [90, edge]],
        [[0, 0], [90, edge - 2]],
      ] },
    });
    const option = heatmapOption(map(-12), comparisonTokens, 'horizontal', -6, 'full', false, 10, {
      primaryLabel: 'Run A',
      references: [{ label: 'Run B', result: map(-10) }],
    });
    const series = option.series as Array<{ name?: string; type?: string }>;
    expect((option.legend as { data: string[] }).data).toEqual(['Run A', 'Run B']);
    expect(series.filter(({ name }) => name === 'Run A' || name === 'Run B').map(({ name, type }) => ({ name, type }))).toEqual([
      { name: 'Run A', type: 'custom' },
      { name: 'Run B', type: 'custom' },
    ]);
  });

  it('projects a comparison contour by physical frequency instead of stretching its range', () => {
    const display = { frequencies: [100, 1_000, 10_000], angles: [0, 45, 90], values: [], factor: 1 };
    const source = { frequencies: [1_000, 10_000], angles: [0, 90], values: [], factor: 1 };
    const [x, y] = comparisonContourPointToPixels([0, 0], source, display, { x: 0, y: 0, width: 300, height: 300 });
    expect(x).toBeCloseTo(150);
    expect(y).toBe(250);
  });

  it('overlays DI with metric colours and run-specific solid/dashed lines', () => {
    const series = directivityIndexOption(items, comparisonTokens, 'none', 'full').series as Array<{
      name: string; lineStyle: { color: string; type: string }; data: number[][];
    }>;
    expect(series.map(({ name }) => name)).toEqual([
      'Run A · horizontal', 'Run A · vertical', 'Run B · horizontal', 'Run B · vertical',
    ]);
    expect(series[0].lineStyle).toMatchObject({ color: '#0ff', type: 'solid' });
    expect(series[1].lineStyle).toMatchObject({ color: '#f90', type: 'solid' });
    expect(series[2].lineStyle).toMatchObject({ color: '#0ff', type: 'dashed' });
    expect(series[3].lineStyle).toMatchObject({ color: '#f90', type: 'dashed' });
    expect(series[2].data).toEqual([[500, 4], [1_000, 6]]);
  });

  it('overlays impedance with one colour per run and solid Re/dashed Im traces', () => {
    const series = impedanceOption(items, comparisonTokens, 'none', 'full').series as Array<{
      name: string; lineStyle: { color: string; type: string }; data: number[][];
    }>;
    expect(series.map(({ name }) => name)).toEqual(['Run A · Re', 'Run A · Im', 'Run B · Re', 'Run B · Im']);
    expect(series.map(({ lineStyle }) => lineStyle.type)).toEqual(['solid', 'dashed', 'solid', 'dashed']);
    // Which palette entry each run draws is its label's business (see
    // seriesColors); what this chart owes is one colour per run, and two runs
    // that never share one.
    const [runA, runAImaginary, runB, runBImaginary] = series.map(({ lineStyle }) => lineStyle.color);
    expect(runAImaginary).toBe(runA);
    expect(runBImaginary).toBe(runB);
    expect(runB).not.toBe(runA);
  });

});

describe('impedance is drawn in the unit the result declares', () => {
  const acoustic = named('acoustic', 'Waveguide', {
    frequencies: [500, 1_000],
    impedance: { frequencies: [500, 1_000], real: [1, 2], imaginary: [.2, .4] },
    metadata: { impedance_units: 'Z/(rho*c)', impedance_quantity: 'specific_acoustic_impedance' },
  });
  const electrical = named('electrical', 'Driver', {
    frequencies: [500, 1_000],
    impedance: { frequencies: [500, 1_000], real: [7, 12], imaginary: [1, -2] },
    metadata: { impedance_units: 'ohms', impedance_quantity: 'electrical_input_impedance' },
  });

  it('labels a driver-coupled run in ohms rather than the acoustic Z/rho-c', () => {
    const option = impedanceOption([electrical], tokens, 'none', 'full');
    expect((option.yAxis as { name?: string }).name).toBe('Ω');
    expect(chartUnit('impedance', electrical.result as ResultPayload)).toBe('Ω');
  });

  it('keeps the normalized acoustic label for an unloaded waveguide solve', () => {
    const option = impedanceOption([acoustic], tokens, 'none', 'full');
    expect((option.yAxis as { name?: string }).name).toBe('Z/ρc');
    expect(chartUnit('impedance', acoustic.result as ResultPayload)).toBe('Z/ρc');
  });

  it('refuses to overlay ohms and Z/rho-c on one scale', () => {
    // They are different quantities. Drawing both against one axis is exactly
    // the misreading the unit label exists to prevent.
    const series = impedanceOption([electrical, acoustic], tokens, 'none', 'full').series as Array<{ name: string }>;
    expect(series.map(({ name }) => name)).toEqual(['Driver · Re', 'Driver · Im']);
  });

  it('takes the chip from the run that sets the axis, not from the primary', () => {
    // A primary with no impedance block plus an electrical overlay used to put
    // Ω on the axis and leave the chip blank -- the mislabelling at its
    // quietest.
    const bare = named('bare', 'Waveguide', { frequencies: [500, 1_000] });
    expect(chartUnit('impedance', bare.result as ResultPayload)).toBeUndefined();
    expect(chartUnit('impedance', bare.result as ResultPayload, [bare, electrical])).toBe('Ω');
    expect((impedanceOption([bare, electrical], tokens, 'none', 'full').yAxis as { name?: string }).name).toBe('Ω');
  });

  it('switches to magnitude and phase on a second axis when asked', () => {
    const option = impedanceOption([electrical], tokens, 'none', 'full', 'magnitude_phase');
    const series = option.series as Array<{ name: string; yAxisIndex?: number }>;
    expect(series.map(({ name }) => name)).toEqual(['Driver · |Z|', 'Driver · phase']);
    expect(series[1].yAxisIndex).toBe(1);
    expect((option.yAxis as Array<{ name?: string }>).map(({ name }) => name)).toEqual(['Ω', 'Phase [°]']);
  });
});

describe('phase, group delay and polar charts', () => {
  const sweep = Array.from({ length: 40 }, (_, index) => 300 * 1.06 ** index);
  const withPhase = named('phased', 'Run A', {
    frequencies: sweep,
    spl_on_axis: {
      frequencies: sweep,
      spl: sweep.map(() => 90),
      // A pure 0.3 ms excess delay on top of a 1 m path.
      phase_degrees: sweep.map((frequency) => {
        const radians = 2 * Math.PI * frequency * (1 / 343 + 0.0003);
        return (((radians + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI) * 180 / Math.PI;
      }),
    },
    directivity: { horizontal: sweep.map(() => [[-30, -6], [0, 0], [30, -6]]) },
    metadata: {
      phase_time_convention: 'exp(+ikr)',
      observation: { effective_distance_m: 1, sound_speed_m_per_s: 343 },
    },
  });
  const withoutPhase = named('bare', 'Run B', {
    frequencies: [500, 1_000],
    spl_on_axis: { frequencies: [500, 1_000], spl: [90, 90] },
  });

  it('draws the on-axis phase detrended flat for a constant delay', () => {
    const series = phaseOption([withPhase], tokens, 'full').series as Array<{ data: number[][] }>;
    expect(series).toHaveLength(1);
    series[0].data.forEach(([, value]) => expect(Math.abs(value)).toBeLessThan(1e-6));
  });

  it('recovers the excess delay in milliseconds', () => {
    const series = groupDelayOption([withPhase], tokens, 'full').series as Array<{ data: number[][] }>;
    series[0].data.forEach(([, value]) => expect(value).toBeCloseTo(0.3, 6));
  });

  it('has nothing to draw for a result that carries no phase', () => {
    expect(phaseOption([withoutPhase], tokens, 'full').series).toEqual([]);
    expect(groupDelayOption([withoutPhase], tokens, 'full').series).toEqual([]);
  });

  it('adds a phase trace and a second axis to the SPL chart only when asked', () => {
    const withoutTrace = splOption([withPhase], tokens, 'none', 'full', [], false);
    expect((withoutTrace.series as unknown[])).toHaveLength(1);
    expect(Array.isArray(withoutTrace.yAxis)).toBe(false);
    const withTrace = splOption([withPhase], tokens, 'none', 'full', [], true);
    const series = withTrace.series as Array<{ name: string; yAxisIndex?: number }>;
    expect(series.map(({ name }) => name)).toEqual(['Run A', 'Run A · phase']);
    expect(series[1].yAxisIndex).toBe(1);
    expect((withTrace.yAxis as Array<{ name?: string }>).map(({ name }) => name)).toEqual(['dB SPL', 'Phase [°]']);
  });

  it('normalizes a polar cut to its own on-axis sample and floors the radius', () => {
    const option = polarOption([withPhase], tokens, 'horizontal', 1_000, -30, 'full');
    const series = option.series as Array<{ data: Array<Array<number | null>> }>;
    // Radius first, angle second; on-axis sits at 0 dB and the -6 dB shoulders
    // keep their relative level rather than an absolute SPL.
    expect(series[0].data).toEqual([[-6, -30], [0, 0], [-6, 30]]);
    expect((option.radiusAxis as { min?: number }).min).toBe(-30);
  });

  it('keeps one chart degree per pattern degree whatever was sampled', () => {
    // Fitting the axis to the sampled span would draw a 0..180 half-space
    // around the whole circle and halve every apparent beamwidth.
    const option = polarOption([withPhase], tokens, 'horizontal', 1_000, -30, 'full');
    const axis = option.angleAxis as { min?: number; max?: number; startAngle?: number };
    expect([axis.min, axis.max]).toEqual([-180, 180]);
    expect(axis.startAngle).toBe(270);
  });
});

describe('driver-coupled chart stubs', () => {
  it('says a unit-acceleration solve has no drive, rather than implying a failure', () => {
    const acoustic = { frequencies: [100], metadata: { impedance_units: 'Z/(rho*c)' } } as ResultPayload;
    expect(driverChartMissingReason(acoustic, 'Power & Current Draw'))
      .toContain('unit-acceleration');
    expect(drivePowerOption(acoustic, tokens, 'full').series).toEqual([]);
  });

  it('distinguishes a missing drive voltage from a missing driver model', () => {
    const noVoltage = { frequencies: [100], metadata: { impedance_units: 'ohms' } } as ResultPayload;
    expect(driverChartMissingReason(noVoltage, 'Cone Excursion')).toContain('drive voltage');
  });

  it('quotes the server reason rather than calling a combined channel unit-acceleration', () => {
    // `_combined_channel_response` pops the impedance block but leaves
    // `impedance_units: "Z/(rho*c)"` behind, so the tag check reads an
    // LR4 combine -- which is voltage driven -- as an unloaded waveguide.
    const combined = {
      frequencies: [100],
      metadata: {
        impedance_units: 'Z/(rho*c)',
        impedance_omitted: 'combined channel: member drives differ; no single impedance exists',
      },
    } as ResultPayload;
    const reason = driverChartMissingReason(combined, 'Power & Current Draw');
    expect(reason).toContain('member drives differ');
    expect(reason).not.toContain('unit-acceleration');
  });

  it('does not claim excursion was derived from impedance samples', () => {
    const driven = {
      frequencies: [100],
      metadata: { impedance_units: 'ohms', drive: { voltage_v: 2.83 } },
    } as ResultPayload;
    expect(driverChartMissingReason(driven, 'Cone Excursion')).toBe(
      'Cone Excursion needs samples this driver-coupled result did not record.',
    );
  });
});

describe('group delay stub names the refusal it actually hit', () => {
  const sweep = [400, 800, 1_600];
  const phased = (metadata: Record<string, unknown>): ResultPayload => ({
    frequencies: sweep,
    spl_on_axis: { frequencies: sweep, spl: sweep.map(() => 90), phase_degrees: [0, 30, 60] },
    metadata,
  } as ResultPayload);

  it('says the phase block is missing when there is no phase at all', () => {
    expect(groupDelayMissingReason({ frequencies: sweep, spl_on_axis: { frequencies: sweep, spl: [] } } as ResultPayload))
      .toContain('spl_on_axis phase samples');
  });

  it('names the missing observation metadata instead of blaming the sample count', () => {
    // The old stub sent the user off to rerun a longer solve for a metadata
    // field a longer solve would not add.
    const reason = groupDelayMissingReason(phased({ phase_time_convention: 'exp(+ikr)' }));
    expect(reason).toContain('metadata.observation');
    expect(reason).not.toContain('at least three');
  });

  it('names an unrecognised phase convention as the thing it cannot read', () => {
    const reason = groupDelayMissingReason(phased({
      phase_time_convention: 'exp(+ikr) but sideways',
      observation: { effective_distance_m: 1, sound_speed_m_per_s: 343 },
    }));
    expect(reason).toContain('exp(+ikr) but sideways');
    expect(reason).toContain('spatial sign');
  });

  it('still asks for three samples when three is what is missing', () => {
    const two = [400, 800];
    expect(groupDelayMissingReason({
      frequencies: two,
      spl_on_axis: { frequencies: two, spl: [90, 90], phase_degrees: [0, 30] },
      metadata: {
        phase_time_convention: 'exp(+ikr)',
        observation: { effective_distance_m: 1, sound_speed_m_per_s: 343 },
      },
    } as ResultPayload)).toContain('at least three');
  });

  it('blames the sweep spacing when the unwrap cannot pick a branch', () => {
    // A 15.8 kHz gap carrying a ~63 us residual: the phase turns more than half
    // a cycle across that step, so no branch choice is defensible. Distance 0
    // keeps de-embedding out of it, leaving the spacing as the only refusal.
    const coarse = [100, 200, 16_000];
    const reason = groupDelayMissingReason({
      frequencies: coarse,
      spl_on_axis: { frequencies: coarse, spl: coarse.map(() => 90), phase_degrees: [0, 179, -2] },
      metadata: {
        phase_time_convention: 'exp(+ikr)',
        observation: { effective_distance_m: 0, sound_speed_m_per_s: 343 },
      },
    } as ResultPayload);
    expect(reason).toContain('finer frequency sweep');
  });

  it('puts power and current on separate axes for a driver-coupled run', () => {
    const driven = {
      frequencies: [100],
      impedance: { frequencies: [100], real: [8], imaginary: [0] },
      metadata: { impedance_units: 'ohms', drive: { voltage_v: 2.83, rg_ohm: 0 } },
    } as ResultPayload;
    const option = drivePowerOption(driven, tokens, 'full');
    const series = option.series as Array<{ name: string; yAxisIndex?: number; data: number[][] }>;
    expect(series.map(({ name }) => name)).toEqual(['Power', 'Current']);
    expect(series[1].yAxisIndex).toBe(1);
    expect(series[0].data[0][1]).toBeCloseTo(2.83 ** 2 / 8, 10);
    expect((option.yAxis as Array<{ name?: string }>).map(({ name }) => name)).toEqual(['Power [W]', 'Current [A]']);
  });
});

describe('result frequency validity joins', () => {
  it('uses missing channel membership only when the wrapper join is provably unambiguous', () => {
    const channel: ResultPayload = { frequencies: [100, 2_000] };
    const singleChannelWrapper: ResultPayload = {
      frequencies: [],
      channels: { drive: channel },
      metadata: { per_source_frequency_validity: {
        source: { effective_max_valid_frequency_hz: 1_000 },
        second: { effective_max_valid_frequency_hz: 1_500 },
      } },
    };
    const oneSourceWrapper: ResultPayload = {
      frequencies: [],
      channels: { drive: channel, other: { frequencies: [] } },
      metadata: { per_source_frequency_validity: { source: { effective_max_valid_frequency_hz: 1_000 } } },
    };
    const ambiguousWrapper: ResultPayload = {
      ...oneSourceWrapper,
      metadata: { per_source_frequency_validity: {
        source: { effective_max_valid_frequency_hz: 1_000 },
        other: { effective_max_valid_frequency_hz: 500 },
      } },
    };

    expect(resultFrequencyValidity(channel, singleChannelWrapper)?.sources.map(({ sourceId }) => sourceId))
      .toEqual(['source', 'second']);
    expect(resultFrequencyValidity(channel, oneSourceWrapper)?.sources.map(({ sourceId }) => sourceId))
      .toEqual(['source']);
    expect(resultFrequencyValidity(channel, ambiguousWrapper)).toBeNull();
  });
});

describe('Directivity Map resolved grid label', () => {
  it('shows a resolved step only when count-based sampling changed the request', () => {
    expect(resolvedPolarStepNotice({
      frequencies: [],
      metadata: { polar_grid: { requested_step: 7, resolved_step: 7.2 } },
    })).toBe('7.2° resolved (requested 7°)');
    expect(resolvedPolarStepNotice({
      frequencies: [],
      metadata: { polar_grid: { requested_step: 5, resolved_step: 5 } },
    })).toBeNull();
  });
});

describe('result export design snapshot', () => {
  it('uses the selected job snapshot and revision rather than the live editor design', () => {
    const solved = designForFamily('OSSE');
    solved.L = 321;
    const snapshot = resultExportSnapshot({
      design_revision: 47,
      script_snapshot: { version: 1, design: serializeDesign(solved) },
    });
    expect(snapshot.design?.formula).toBe('OSSE');
    expect(snapshot.design?.L).toBe(321);
    expect(snapshot.designRevision).toBe(47);
  });

  it('carries the selected run polar config into its parameter-config export', () => {
    const polarConfig = {
      angle_range: [0, 90, 19], distance: 3, norm_angle: 7,
      inclination: 30, enabled_axes: ['horizontal', 'diagonal'],
    };
    expect(resultExportSnapshot({
      design_revision: 47,
      script_snapshot: { version: 1, design: serializeDesign(designForFamily('OSSE')) },
      solve_options: { polar_config: polarConfig } as unknown as JobItem['solve_options'],
    }).polarConfig).toBe(polarConfig);
  });

  it('does not substitute the live design when an old job has no readable snapshot', () => {
    expect(resultExportSnapshot({ design_revision: 12, script_snapshot: null })).toEqual({
      design: undefined,
      designRevision: 12,
      // A job with no recorded solve options contributes no WG.Solve block, so
      // the exported config stays silent about the solve rather than
      // describing the settings that happen to be on screen.
      solveSettings: null,
    });
  });
});

describe('results chart layouts', () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.clear();
    preferencesStore.resetForTests();
    summaryMocks.groups.mockReset().mockReturnValue([]);
    summaryMocks.text.mockReset().mockReturnValue('');
    chartImageMocks.copy.mockReset().mockResolvedValue(undefined);
    chartImageMocks.download.mockReset().mockResolvedValue(undefined);
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); });

  it.each([1, 2, 3, 4, 6])('renders the %i-panel layout with the matching number of cards', (count) => {
    const chartTypes = Array.from({ length: count }, () => 'summary' as ChartType);
    act(() => root.render(createElement(ResultsChartGrid, { chartTypes, result, named: [], tokens })));
    expect(host.querySelector('.result-grid')?.classList.contains(resultLayoutClass(count))).toBe(true);
    expect(host.querySelectorAll('.result-card')).toHaveLength(count);
  });

  it('lets a panel select and persist a diagonal-only directivity heatmap', () => {
    preferencesStore.update({ chartTypes: ['summary'] });
    act(() => root.render(createElement(ResultsHarness)));
    const select = host.querySelector<HTMLSelectElement>('[aria-label="Panel 1 chart type"]')!;
    expect([...select.options].find(({ value }) => value === 'directivity_map_d')?.text).toBe('Directivity Map (Diagonal)');

    act(() => {
      select.value = 'directivity_map_d';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(preferencesStore.getSnapshot().chartTypes).toEqual(['directivity_map_d']);
    expect(host.querySelector('.result-title b')?.textContent).toBe('Directivity Diagonal');
    expect(host.querySelector('.chart-stub')?.textContent).toContain('diagonal polar plane');
  });

  it('renders summary groups, row titles, and a marked warning group', () => {
    const groups: SummaryGroup[] = [
      { title: 'Simulation', rows: [{ label: 'Engine', value: 'bempp-cl', title: 'Solver backend' }] },
      { title: 'Warnings', tone: 'warning', rows: [{ label: 'Mesh', value: 'Element quality fell below the requested threshold.' }] },
    ];
    const wrapper = { frequencies: [], channels: { hf: result } } as ResultPayload;
    summaryMocks.groups.mockReturnValue(groups);
    act(() => root.render(createElement(ResultsChartGrid, {
      chartTypes: ['summary'], result, named: [], tokens, wrapper, job: null, channelId: 'hf',
    })));

    expect([...host.querySelectorAll('.result-summary-group > h3')].map((heading) => heading.textContent)).toEqual(['Simulation', 'Warnings']);
    expect([...host.querySelectorAll('.result-summary dt')].map((label) => label.textContent)).toEqual(['Engine', 'Mesh']);
    expect([...host.querySelectorAll('.result-summary dd')].map((value) => value.textContent)).toEqual(['bempp-cl', 'Element quality fell below the requested threshold.']);
    expect(host.querySelector('.result-summary-row')?.getAttribute('title')).toBe('Solver backend');
    expect(host.querySelector('.result-summary-group[data-tone="warning"]')?.textContent).toContain('Warnings');
    expect(summaryMocks.groups).toHaveBeenCalledWith({ result, wrapper, job: null, channelId: 'hf' });
  });

  it('renders a quiet summary empty state when there are no groups', () => {
    act(() => root.render(createElement(ResultsChartGrid, { chartTypes: ['summary'], result, named: [], tokens })));
    expect(host.querySelector('.result-summary-empty')?.textContent).toBe('No summary details available.');
    expect(host.querySelector('.result-summary-copy')).toBeNull();
  });

  it('does not render validity caveats over result charts', () => {
    const above: ResultPayload = {
      frequencies: [100, 20_000],
      spl_on_axis: { frequencies: [100, 20_000], spl: [80, 85] },
      metadata: { source_ids: ['driver'] },
    };
    const wrapper = (channel: ResultPayload): ResultPayload => ({
      frequencies: [], channels: { drive: channel },
      metadata: { per_source_frequency_validity: { driver: { effective_max_valid_frequency_hz: 1_200 } } },
    });

    act(() => root.render(createElement(ResultsChartGrid, {
      chartTypes: ['frequency_response'], result: above, named: [], tokens, wrapper: wrapper(above), channelId: 'drive',
    })));
    expect(host.querySelector('.result-caveat')).toBeNull();
  });

  it('copies the formatted summary text and confirms success', async () => {
    const groups: SummaryGroup[] = [{ title: 'Run', rows: [{ label: 'Name', value: 'OSSE v4' }] }];
    const writeText = vi.fn(async () => undefined);
    const originalClipboard = navigator.clipboard;
    summaryMocks.groups.mockReturnValue(groups);
    summaryMocks.text.mockReturnValue('Run\nName: OSSE v4');
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    try {
      act(() => root.render(createElement(ResultsChartGrid, { chartTypes: ['summary'], result, named: [], tokens })));
      const copy = host.querySelector<HTMLButtonElement>('[aria-label="Copy simulation summary"]')!;
      await act(async () => { copy.click(); });
      expect(summaryMocks.text).toHaveBeenCalledWith(groups);
      expect(writeText).toHaveBeenCalledWith('Run\nName: OSSE v4');
      expect(copy.textContent).toBe('Copied');
      expect(copy.getAttribute('aria-label')).toBe('Simulation summary copied');
    } finally {
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: originalClipboard });
    }
  });

  it('copies and downloads one composited PNG from a graphical chart', async () => {
    const chartResult: ResultPayload = {
      frequencies: [500, 1_000],
      spl_on_axis: { frequencies: [500, 1_000], spl: [80, 82] },
    };
    act(() => root.render(createElement(ResultsChartGrid, {
      chartTypes: ['frequency_response'], result: chartResult, named: [], tokens,
    })));
    const target = host.querySelector<HTMLElement>('.chart-placeholder')!;
    await act(async () => { target.append(document.createElement('canvas')); await Promise.resolve(); });

    const copy = host.querySelector<HTMLButtonElement>('[aria-label="Copy panel 1 as PNG"]')!;
    const download = host.querySelector<HTMLButtonElement>('[aria-label="Download panel 1 as PNG"]')!;
    expect(copy).not.toBeNull();
    expect(download).not.toBeNull();
    await act(async () => { copy.click(); });
    expect(chartImageMocks.copy).toHaveBeenCalledWith(target, tokens.background);
    expect(host.querySelector('.result-image-status')?.textContent).toBe('Copied PNG');

    await act(async () => { download.click(); });
    expect(chartImageMocks.download).toHaveBeenCalledWith(target, 'result_frequency_response.png', tokens.background);
    expect(host.querySelector('.result-image-status')?.textContent).toBe('Downloaded PNG');
  });

  it('reports a blocked image clipboard without breaking the chart', async () => {
    const chartResult: ResultPayload = {
      frequencies: [500], spl_on_axis: { frequencies: [500], spl: [80] },
    };
    chartImageMocks.copy.mockRejectedValue(new Error('clipboard blocked'));
    act(() => root.render(createElement(ResultsChartGrid, {
      chartTypes: ['frequency_response'], result: chartResult, named: [], tokens,
    })));
    const target = host.querySelector<HTMLElement>('.chart-placeholder')!;
    await act(async () => { target.append(document.createElement('canvas')); await Promise.resolve(); });
    await act(async () => { host.querySelector<HTMLButtonElement>('[aria-label="Copy panel 1 as PNG"]')!.click(); });
    // Poll rather than count ticks. A rejected copy settles later than the
    // resolved one above -- the throw has to unwind into the handler's catch
    // before it can set the status -- and on a loaded machine that lands
    // several turns after the click, which is why sampling immediately failed
    // intermittently and only in the full suite.
    for (let i = 0; i < 100 && !host.querySelector('.result-image-status'); i += 1) {
      await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 5); }); });
    }
    expect(host.querySelector('.result-image-status')?.textContent).toBe('Copy failed');
    expect(host.querySelector('.result-card')).not.toBeNull();
  });

  it('builds a portable chart filename from the run, channel, and chart type', () => {
    const job = { run_number: 14, label: 'OSSE v4', config_summary: {} } as JobItem;
    expect(chartImageFilename('directivity_map_h', job, 'HF left')).toBe('14_OSSE_v4_HF_left_directivity_map_h.png');
  });

  it('closes one card and persists the shorter chart list', () => {
    preferencesStore.update({ chartTypes: ['summary', 'impedance'] });
    act(() => root.render(createElement(ResultsHarness)));
    act(() => host.querySelector<HTMLButtonElement>('[aria-label="Close panel 1"]')!.click());
    expect(preferencesStore.getSnapshot().chartTypes).toEqual(['impedance']);
    expect(host.querySelectorAll('.result-card')).toHaveLength(1);
    expect(JSON.parse(localStorage.getItem('waveguide-v2-g3-preferences') ?? '{}').preferences.chartTypes).toEqual(['impedance']);
  });

  it('offers an add-chart recovery when the last card is closed', () => {
    preferencesStore.update({ chartTypes: [] });
    act(() => root.render(createElement(ResultsHarness)));
    const add = host.querySelector<HTMLButtonElement>('.result-grid-empty button')!;
    expect(add.textContent).toContain('Add chart');
    act(() => add.click());
    expect(preferencesStore.getSnapshot().chartTypes).toHaveLength(1);
  });

  it('opens a full-size interactive detail view and closes it with Escape', () => {
    act(() => root.render(createElement(ResultsChartGrid, { chartTypes: ['summary'], result, named: [], tokens })));
    act(() => host.querySelector<HTMLButtonElement>('[aria-label="Expand panel 1"]')!.click());
    expect(document.body.querySelector('[role="dialog"]')).not.toBeNull();
    act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })));
    expect(document.body.querySelector('[role="dialog"]')).toBeNull();
  });

  it('names the active channel in a non-comparable chart badge', () => {
    const first = { frequencies: [500] };
    const active = { frequencies: [1_000] };
    act(() => root.render(createElement(ResultsChartGrid, {
      chartTypes: ['summary'],
      result: active,
      named: [named('job#hf', 'Run · drive-hf', first), named('job#mf', 'Run · drive-mf', active)],
      tokens,
    })));
    expect(host.querySelector('.result-single-run')?.getAttribute('title')).toContain('Showing Run · drive-mf.');
  });

  it('offers enable and rerun when beam shape is missing spherical sampling', () => {
    const onClick = vi.fn();
    act(() => root.render(createElement(ResultsChartGrid, {
      chartTypes: ['beam_shape'], result, named: [], tokens,
      beamShapeAction: { label: 'Enable & rerun', onClick },
    })));
    const button = host.querySelector<HTMLButtonElement>('.chart-stub button')!;
    expect(button.textContent).toBe('Enable & rerun');
    act(() => button.click());
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('does not offer a rerun when balloon data exists but the contour fit failed', () => {
    const balloonResult = {
      frequencies: [1_000], metadata: {},
      balloon: { frequencies: [1_000], theta_deg: [0, 90], phi_deg: [0, 120, 240], spl_norm_db: [[[0, 0, 0], [-2, -2, -2]]] },
    };
    expect(beamShapeMissingReason(balloonResult)).toEqual({
      reason: 'Spherical balloon data is available, but this result has no valid −6 dB contour fit.',
      canEnable: false,
    });
    act(() => root.render(createElement(ResultsChartGrid, {
      chartTypes: ['beam_shape'], result: balloonResult, named: [], tokens,
      beamShapeAction: { label: 'Enable & rerun', onClick: vi.fn() },
    })));
    expect(host.querySelector('.chart-stub button')).toBeNull();
  });
});
