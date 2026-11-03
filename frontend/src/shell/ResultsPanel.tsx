import { EmptyState } from './EmptyState';
import { CurrentModelStatus } from './CurrentModelStatus';
import type { ResultsMessage } from './ResultsMessages';
import { selectJob } from '../jobs/selectJob';
import { listedRuns, railRuns, useRunFilters } from '../jobs/runFilters';
import { hasMeshProvenance, useMeshFreshnessStore } from '../jobs/meshFreshness';
import { samplingNotes } from '../results/sampling';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import type { EChartsOption } from 'echarts';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { compareSelection, fetchJobResults, fetchRadiationImpedancePresentation, provisionalResults, type JobResults, type RadiationImpedancePresentation, type ResultData } from '../api/results';
import { EChart, useChartTokens, type ChartTokens } from '../results/EChart';
import { axes, CHART_FONT_FAMILY, LABEL_FONT, type ChartDensity } from '../results/chartAxes';
export type { ChartDensity } from '../results/chartAxes';
import { beamFitSeries, beamShapeSeries, directivityGrid, directivityIndexSeries, drivePowerChartSeries, excursionChartSeries, groupDelaySeries, hasZeroRadiationRear, impedanceComparable, impedanceSeries, impedanceSubtitle, isOutsideHalfSpace, nearestFrequencyIndex, OUTSIDE_HALF_SPACE_LABEL, phaseSeries, polarCut, powerResponseMethodCaption, powerResponseSeries, selectResultChannels, splSeries, type NamedResult } from '../results/mappers';
import { BalloonRenderer, ChartStub, ForwardBeamRenderer, hasBalloonData, type ChartStubAction } from '../results/balloon';
import { RunExportControl } from '../jobs/RunExportControl';
import {
  askForExportDestination,
  askToReplaceExports,
} from './exportDestinationPrompt';
import { ExportCancelledError } from '../api/exportDestination';
export { resultExportSnapshot } from '../results/exportContext';
import { copyChartPng, saveChartPng } from '../results/chartImage';
import { summaryGroups, summaryText, type SummaryGroup, type SummaryRow } from '../results/summary';
import { latestCombine } from '../results/latestCombine';
import { limitingSummary, maxOutputChartSeries, maxOutputMissingReason, maxOutputOf, memberLabelOf } from '../results/maxOutput';
import { reverseNullTraces, type ReverseNullTrace } from '../results/reverseNull';
import { combinedChannelId, combineMetadataOf, type ResultPayload } from '../results/types';
import { resolveNormalizationAngle, withNormalizationAngle } from '../results/normalization';
import { MAX_MEASUREMENT_ANGLES, measurementAnchorRefusal, measurementAngleLabel, measurementAngles, measurementPlanes, nearestMeasurementAngle, onAxisAngle, withMeasurementAngle, type MeasurementPlane } from '../results/measurementAngle';
import { ResultViewSwitch } from '../results/ResultViewSwitch';
import { FemPortsPanel } from '../results/FemPortsPanel';
import { resolveResultView, resultViewStore, useResultView } from '../stores/resultView';
import { hydrateJobDesign } from '../jobs/jobDesign';
import { workspaceNavigation } from './workspaceNavigation';
import { showJobModel } from '../jobs/showJobModel';
import { exportStemForJob, exportTitleSlug } from '../jobs/exportNaming';
import { CHART_TYPES, GROUP_DELAY_UNITS, MAX_RESULT_PANELS, POLAR_PLANES, RESULT_PANEL_COUNTS, preferencesStore, runDisplayName, useDeveloperDetails, usePreferences, type ChartType, type GroupDelayUnit, type PolarPlane } from '../prefs/preferences';
import { ResultsPreferencesSurface } from '../prefs/PreferencesSurface';
import { directivityFrequencyLabelIndices, directivityFrequencyTickLabel, directivityFrequencyTickLabels, logFrequencyTicks } from '../results/directivityFrequencyAxis';
import { directivityMapCanvas, solvedSampleSpan } from '../results/mapBitmap';
import { runOfLabel, seriesColorsByLabel } from '../results/seriesColors';
import { parseMeasuredTrace } from '../results/measuredTrace';
import { electricalDrive, excursionSeries, hasElectricalImpedance } from '../results/drivePower';
import { deEmbeddedPhaseRadians, hasOnAxisPhase, phaseSpatialSign, phaseUnwrapIsResolved, propagationReference } from '../results/phaseAnalysis';
import { Icon } from './icons';
import { ResultsHeader } from './ResultsHeader';
import { jobsCoordinatorBridge, useOptionalSolveControl } from './JobsCoordinator';
import { useVisibleRedraw } from './panelVisibility';
import { useModalDialogFocus } from './dialogFocus';
import { useRunNormAngle } from '../stores/solveOptionsContext';
import { useObservationStore } from '../viewport/observationStore';
import { MAX_MEASURED_OVERLAYS, useMeasuredOverlayStore, type MeasuredOverlay } from '../stores/measuredOverlays';
import { RUN_VERDICT_MARKER, RUN_VERDICT_SENTENCE, runContextMarker, runVerdict, modelForRun, shownResult, useCurrentModel } from '../model/currentModel';
import { AnchoredPanel } from '../prefs/AnchoredPanel';
import { radiationImpedanceTraces } from '../results/radiationImpedance';
import { powerAgreementHealth, powerCheckMessage } from '../results/radiatedPower';
import { channelLabel } from '../results/channelLabel';
import { unqualifiedMessage, channelQualificationSummary, chartUnqualifiedBands, powerChipLabel, powerQualificationDetail, powerQualificationOf, unqualifiedCaption, withUnqualifiedBands, type UnqualifiedBand } from '../results/powerQualification';
import { solveAttention } from './solveAttention';
import { failureWords } from '../api/httpError';

export function newRunBadgeLabel(runNumber: number | null): string {
  return runNumber === null ? 'New' : `New · #${runNumber}`;
}

/**
 * Where the SPL card's curves were measured.
 *
 * `angles` names the microphone positions when more than the on-axis one is
 * drawn. Without it a card showing four traces gives no clue which is which
 * beyond the legend, and the distance alone reads as if they share a position.
 *
 * A run that cannot put an absolute scale on the selected plane says so here
 * rather than drawing an empty card: the levels are declined on purpose, and
 * "absolute" would otherwise be the only thing the caption claimed.
 */
export function splSubtitle(result: ResultData | undefined, angles?: { plane: string; angles: number[]; onAxis: number | null }): string {
  const observation = result?.metadata?.observation;
  const record = observation && typeof observation === 'object' ? observation as Record<string, unknown> : {};
  const distance = Number(record.effective_distance_m ?? record.requested_distance_m);
  const position = Number.isFinite(distance) && distance > 0 ? `absolute · ${Number(distance.toPrecision(4))} m` : 'absolute · distance unspecified';
  const refusal = result && angles ? measurementAnchorRefusal(result, angles.plane as MeasurementPlane) : null;
  if (refusal) return refusal;
  const offAxis = angles && angles.angles.length > 1;
  if (!offAxis) return position;
  return `${position} · ${angles.plane.slice(0, 1).toUpperCase()} ${angles.angles.map((angle) => `${Number(angle.toFixed(3))}°`).join(' / ')}`;
}

function cleanedUpMessage(job: JobItem): string {
  const name = job.run_number === null ? runDisplayName(job, 'short') : `Run #${job.run_number}`;
  const when = job.results_discarded_at ? new Date(job.results_discarded_at) : null;
  const date = when && !Number.isNaN(when.getTime())
    ? ` on ${when.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}`
    : '';
  return `${name}'s results were cleaned up${date}. Solve it again to see them.`;
}

export function labelFor(id: string, jobs: ReturnType<typeof jobsSocket.getSnapshot>['jobs']): string {
  const job = jobs.find((item) => item.id === id);
  return runDisplayName(job ?? { id, run_number: null, label: null });
}

/**
 * The run a chart should pin to the accent slot, or nothing when the primary
 * selection is not among the entries this chart drew. Every chart derives it
 * from its own item list, so a chart that filters -- impedance drops runs with
 * no impedance block -- yields no pin rather than promoting a survivor.
 */
function primaryRunOf(items: NamedResult[]): string | undefined {
  const entry = items.find(({ primary }) => primary);
  return entry === undefined ? undefined : runOfLabel(entry.label);
}

/**
 * Truncate from the middle, because run names differ at the end.
 *
 * Names in this product are `<design>_v<NN>` and are generated from a shared
 * base, so end-truncation removes the only part that identifies the run:
 * comparing `260308tritonia-q_v02` against `_v03` rendered both chips as
 * `260308trit…` and both chart legend entries as `260…`. The version suffix is
 * the identity; it is the last thing that may be dropped, not the first.
 */
export function middleEllipsis(value: string, max = 20): string {
  if (value.length <= max) return value;
  const keepEnd = Math.max(5, Math.floor((max - 1) / 2));
  const keepStart = Math.max(1, max - 1 - keepEnd);
  return `${value.slice(0, keepStart)}…${value.slice(-keepEnd)}`;
}

/**
 * Chart types that can carry more than one run at once.
 *
 * Line charts overlay selected runs. Directivity maps keep the primary run as
 * the colour field and overlay every comparison run as a labelled contour,
 * matching the v1 result comparison rather than shrinking runs into tiles.
 */
export const COMPARABLE_CHARTS = new Set<ChartType>([
  'frequency_response',
  'directivity_map_h',
  'directivity_map_v',
  'directivity_map_d',
  'directivity_map',
  'directivity_index',
  'power_response',
  'impedance',
  'phase_response',
  'group_delay',
  'polar_response',
]);

/**
 * How much chrome a chart can afford. A card in the six-panel dock is barely
 * 100px tall: spending 20px on a legend row and 27px on a repeated
 * "Frequency [Hz]" caption leaves single digits of actual plot. Axis titles and
 * padding are therefore scaled to the panel, and the identifying labels the
 * compact charts drop are carried by the card's own title chip instead.
 */
export function chartDensity(width: number, height: number): ChartDensity {
  if (height >= 300 && width >= 440) return 'full';
  if (height >= 172 && width >= 300) return 'regular';
  return 'compact';
}

/** `top` is the band reserved for the floating title chip and the legend. */
const LINE_GRID: Record<ChartDensity, { left: number; right: number; top: number; bottom: number }> = {
  compact: { left: 30, right: 8, top: 16, bottom: 17 },
  regular: { left: 40, right: 10, top: 20, bottom: 22 },
  full: { left: 54, right: 16, top: 30, bottom: 42 },
};
/** `right` holds the colour ramp and its end labels clear of the plot. */
const MAP_GRID: Record<ChartDensity, { left: number; right: number; top: number; bottom: number }> = {
  compact: { left: 27, right: 36, top: 16, bottom: 16 },
  regular: { left: 34, right: 40, top: 20, bottom: 22 },
  full: { left: 46, right: 46, top: 14, bottom: 38 },
};
/** Colour ramp height, kept inside the shortest card it can appear in. */
const MAP_RAMP: Record<ChartDensity, number> = { compact: 38, regular: 74, full: 150 };
/** Legend clears copy/download/expand/close buttons in the same band. */
const LEGEND_INSET = 88;
/**
 * Legend keys are the line alone. ECharts adds the series symbol as a dot on
 * the line, and no curve here is drawn with markers; the dash pattern stays.
 */
const LEGEND_LINE_KEY = { itemStyle: { opacity: 0 } };
/** Height of a control rail (angle strip, unit switch) under the plot, with its 4px inset. */
const RAIL_CLEARANCE = 20;

/**
 * Lift the plot clear of a control rail in the detail view, where it would
 * otherwise cover the tick labels and "Frequency [Hz]". A card instead keeps
 * its rail hidden until hovered (app.css), because 20px is a sixth of a card.
 */
/**
 * A compact card's legend shares the title row with the chip and the card's
 * buttons, and there is no room left in it for a run name. With a single run
 * it only repeats the run already named above the cards, so it is dropped;
 * the tooltip and the detail view still name every series.
 */
export function withCompactLegend(option: EChartsOption, runs: number, density: ChartDensity): EChartsOption {
  if (density !== 'compact' || runs > 1 || !option.legend) return option;
  return { ...option, legend: { ...(option.legend as object), show: false } };
}

export function clearingRail(option: EChartsOption, density: ChartDensity): EChartsOption {
  if (density !== 'full') return option;
  const grid = option.grid as { bottom?: number } | undefined;
  return { ...option, grid: { ...grid, bottom: (grid?.bottom ?? 0) + RAIL_CLEARANCE } };
}

type CartesianSeries = { data?: Array<number[] | { value?: number[] }> };

export function frequencyBounds(series: EChartsOption['series']): [number, number] | undefined {
  const frequencies = (Array.isArray(series) ? series : [series])
    .flatMap((item) => ((item as CartesianSeries | undefined)?.data ?? []))
    .map((item) => Number(Array.isArray(item) ? item[0] : item.value?.[0]))
    .filter((value) => Number.isFinite(value) && value > 0);
  return frequencies.length ? [Math.min(...frequencies), Math.max(...frequencies)] : undefined;
}

/**
 * `domain` pins the frequency axis to something narrower than the drawn data.
 * Only the measured-overlay caller needs it; everywhere else the series *are*
 * the domain.
 */
export function lineOption(series: EChartsOption['series'], tokens: ChartTokens, yName: string, density: ChartDensity = 'regular', domain?: [number, number], secondaryName?: string): EChartsOption {
  const bounds = domain ?? frequencyBounds(series);
  const base = LINE_GRID[density];
  // A rotated axis title on the right needs room the single-axis insets never
  // reserved: label width plus the turned caption.
  const inset = secondaryName ? { ...base, right: base.left + (density === 'full' ? 16 : 0) } : base;
  const axis = axes(tokens, density);
  // Round frequencies, labelled the way the map labels them ("1k"). Left to
  // itself the log axis labelled only the decades, as "1,000".
  const ticks = bounds ? logFrequencyTicks(bounds[0], bounds[1], density) : [];
  const frequencyTicks = ticks.length >= 2 ? {
    axisLabel: { ...axis.axisLabel, customValues: ticks, formatter: directivityFrequencyTickLabel },
    axisTick: { ...axis.axisTick, customValues: ticks },
  } : {};
  // The top value label sits in the band the card's title chip floats in, and
  // printed over it ("25" under "SOUND PRESSURE LEVEL"). The detail view's
  // header is outside the plot, so it keeps the label.
  const valueAxis = { ...axis, axisLabel: { ...axis.axisLabel, ...(density === 'full' ? {} : { showMaxLabel: false }) } };
  // On a compact card the legend shares one row with the title chip. A single
  // name adds nothing the chip does not say, and more than about a third of the
  // row overprints the chip.
  const names = new Set((Array.isArray(series) ? series : [series]).map((entry) => (entry as { name?: string } | undefined)?.name));
  return {
    animationDuration: 180,
    backgroundColor: tokens.background,
    color: tokens.series,
    textStyle: { color: tokens.foreground, fontFamily: CHART_FONT_FAMILY },
    tooltip: { trigger: 'axis', confine: true, backgroundColor: tokens.background, borderColor: tokens.spine ?? tokens.grid, textStyle: { color: tokens.foreground, fontSize: 11 }, axisPointer: { type: 'cross', lineStyle: { color: tokens.muted } }, valueFormatter: formatChartValue },
    // Compact legends truncate rather than crowd the title chip, and scroll in
    // a single row rather than wrapping down over the plot on a narrow card.
    // The tooltip and the detail view still name every series in full.
    legend: {
      ...(density === 'compact' ? { show: names.size > 1, type: 'scroll' as const, width: '30%', pageIconSize: 9, pageIconColor: tokens.muted, pageIconInactiveColor: tokens.grid, pageTextStyle: { color: tokens.muted, fontSize: 11 } } : {}),
      top: density === 'full' ? 2 : 1,
      right: density === 'full' ? 8 : LEGEND_INSET,
      textStyle: { color: tokens.muted, fontSize: 11 },
      formatter: (name: string) => middleEllipsis(name, density === 'compact' ? 12 : 22),
      itemWidth: density === 'compact' ? 10 : 14,
      itemHeight: 2,
      itemGap: density === 'compact' ? 6 : 10,
      ...LEGEND_LINE_KEY,
    },
    grid: { ...inset, containLabel: false },
    // The tick labels already read as frequencies; the caption only earns its
    // 20px on a chart big enough to spare them.
    xAxis: { type: 'log', logBase: 10, min: bounds?.[0], max: bounds?.[1], ...(density === 'full' ? { name: 'Frequency [Hz]', nameLocation: 'middle' as const, nameGap: 24, nameTextStyle: { color: tokens.muted, fontSize: 11 } } : {}), minorTick: { show: true }, ...axis, ...frequencyTicks, axisPointer: { label: { formatter: ({ value }: { value: unknown }) => formatPolarFrequency(Number(value)) } } },
    // In-grid cards carry the unit in their title chip, which occupies exactly
    // the top-left corner an axis name would be drawn into.
    // A second axis is for a companion quantity in different units (phase
    // beside SPL, current beside power). Its split lines are suppressed:
    // two independent graticules over one grid read as a moiré, and the
    // primary axis is the one whose gridlines carry meaning.
    yAxis: secondaryName
      ? [
        { type: 'value' as const, ...(density === 'full' ? { name: yName, nameGap: 10, nameTextStyle: { color: tokens.muted, fontSize: 11, align: 'left' as const } } : {}), ...valueAxis },
        // Rotated along the right edge rather than sitting at the top of the
        // axis, which is where ECharts puts an axis name by default and where
        // this chart's legend already is -- the two overprinted each other.
        { type: 'value' as const, ...(density === 'full' ? { name: secondaryName, nameLocation: 'middle' as const, nameRotate: -90, nameGap: 38, nameTextStyle: { color: tokens.muted, fontSize: 11 } } : {}), ...valueAxis, splitLine: { show: false } },
      ]
      : { type: 'value', ...(density === 'full' ? { name: yName, nameGap: 10, nameTextStyle: { color: tokens.muted, fontSize: 11, align: 'left' as const } } : {}), ...valueAxis },
    dataZoom: [{ type: 'inside', xAxisIndex: 0, filterMode: 'none', zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: true }],
    series,
  };
}

/**
 * A line at y = 0 on a chart whose zero means something: no phase shift, no
 * delay beyond the time of flight. It rides on the first series, as the
 * unqualified bands do, and is drawn a step brighter than the grid.
 */
export function withZeroReference(option: EChartsOption, tokens: ChartTokens): EChartsOption {
  if (!Array.isArray(option.series) || !option.series.length) return option;
  const [first, ...rest] = option.series;
  const markLine = {
    silent: true,
    symbol: 'none',
    animation: false,
    label: { show: false },
    emphasis: { disabled: true },
    lineStyle: { color: tokens.muted, width: 1, type: 'solid' as const, opacity: .45 },
    data: [{ yAxis: 0 }],
  };
  return { ...option, series: [{ ...(first as object), markLine } as typeof first, ...rest] };
}

/** What a loaded measurement is called on the chart and in its legend. */
export function measuredSeriesName(label: string): string {
  return `${label} (measured)`;
}

/**
 * On-axis SPL, with any loaded measurements drawn beside the solve.
 *
 * Three deliberate choices about the measured traces:
 *
 * - The smoothing preference is **not** applied to them. A measurement is
 *   already smoothed by its own chain — REW's octave smoothing, and whatever
 *   the gate window imposes below it — so smoothing again would both
 *   double-count and quietly redescribe the file as something it is not. The
 *   points go to the chart exactly as the file states them; only the user's dB
 *   offset moves them.
 * - The frequency axis stays on the simulated sweep. A measurement usually
 *   spans 20 Hz–20 kHz while a solve covers part of that, and letting the
 *   measurement set the domain would squeeze the result being validated into a
 *   corner of its own chart. ECharts clips the measured curve outside those
 *   bounds, which is the intended behaviour.
 * - Colours come from the same label-keyed table as the runs, over the combined
 *   label set, so a measurement keeps its colour as runs join and leave.
 */
export function splOption(
  items: NamedResult[],
  tokens: ChartTokens,
  smoothing: ReturnType<typeof usePreferences>['smoothing'],
  density: ChartDensity,
  measured: MeasuredOverlay[] = [],
  showPhase = false,
  reverseNull: ReverseNullTrace[] = [],
): EChartsOption {
  const measuredNames = measured.map(({ label }) => measuredSeriesName(label));
  const colors = seriesColorsByLabel([...items.map(({ label }) => label), ...measuredNames], tokens.series, tokens.accent, primaryRunOf(items));
  const simulated = splSeries(items, smoothing).map((series, index) => {
    const color = colors.get(series.name) ?? tokens.accent;
    // A member of the combined sum is drawn beneath the curve it adds up to,
    // not beside it: thin, solid and faint, so the eye reads one response with
    // its branches showing rather than several competing responses.
    if (items[index]?.secondary) {
      return { ...series, z: 1, lineStyle: { color, width: 1, type: 'solid' as const, opacity: .45 }, itemStyle: { color, opacity: .45 } };
    }
    return { ...series, lineStyle: { color, width: index ? 1.2 : 2, type: index ? 'dashed' as const : 'solid' as const }, itemStyle: { color } };
  });
  const measuredSeries = measured.map((overlay, index) => {
    const color = colors.get(measuredNames[index]) ?? tokens.accent;
    return {
      name: measuredNames[index],
      type: 'line' as const,
      showSymbol: false,
      connectNulls: false,
      lineStyle: { color, width: 1.4, type: 'dotted' as const },
      itemStyle: { color },
      data: overlay.points.map(({ frequencyHz, splDb }) => [frequencyHz, splDb + overlay.offsetDb]),
    };
  });
  const phase = showPhase ? phaseSeries(items).map(({ name, points }) => {
    const color = colors.get(name) ?? tokens.accent;
    return {
      name: `${name} · phase`,
      type: 'line' as const,
      showSymbol: false,
      connectNulls: false,
      yAxisIndex: 1,
      // Thin and dashed: the phase rides under the magnitude it belongs to and
      // must never be mistaken for a second SPL trace.
      lineStyle: { color, width: 1, type: [4, 3] as number[], opacity: .75 },
      itemStyle: { color },
      data: points,
    };
  }) : [];
  // Muted and dashed, behind everything: the reverse null is a check on the
  // sum, not a response anybody listens to. It must never be mistaken for one
  // of the curves it is testing.
  const reverseNullSeries = reverseNull.map((trace) => ({
    name: trace.name,
    type: 'line' as const,
    showSymbol: false,
    connectNulls: false,
    z: 0,
    lineStyle: { color: tokens.muted, width: 1, type: 'dashed' as const, opacity: .7 },
    itemStyle: { color: tokens.muted },
    data: trace.points,
  }));
  return lineOption(
    [...simulated, ...measuredSeries, ...reverseNullSeries, ...phase],
    tokens,
    'dB SPL',
    density,
    frequencyBounds(simulated),
    phase.length ? 'Phase [°]' : undefined,
  );
}

export function heatmapFrequencyLabel(value: number): string {
  if (!Number.isFinite(value)) return '';
  if (value >= 1_000) return `${Number((value / 1_000).toPrecision(3))}k`;
  return String(Number(value.toPrecision(3)));
}

export interface InterpolatedDirectivityGrid {
  frequencies: number[];
  angles: number[];
  values: Array<Array<number | null>>;
  factor: number;
}

/** A settled chart can afford a contour grid close to the PNG export. */
const MAX_CONTOUR_CELLS = 180_000;
/** A live snapshot must finish painting before the next 250 ms publication. */
const MAX_LIVE_INTERPOLATED_CELLS = 16_000;

/** Bilinear interpolation in log-frequency/index space, for contours and axes. */
export function interpolateDirectivityGrid(result: ResultPayload, plane: string, requestedFactor = 4, maxCells = MAX_CONTOUR_CELLS): InterpolatedDirectivityGrid {
  const source = directivityGrid(result, plane);
  if (!source.frequencies.length || !source.angles.length) return { frequencies: [], angles: [], values: [], factor: 1 };
  const sourceValues = Array.from({ length: source.angles.length }, () => Array<number | null>(source.frequencies.length).fill(null));
  const columnOf = new Map(source.frequencies.map((frequency, index) => [frequency, index]));
  const rowOf = new Map(source.angles.map((angle, index) => [angle, index]));
  source.data.forEach(([frequency, angle, value]) => {
    const column = columnOf.get(frequency);
    const row = rowOf.get(angle);
    if (column !== undefined && row !== undefined) sourceValues[row][column] = value;
  });
  let factor = Math.max(1, Math.floor(requestedFactor));
  const cellCount = (candidate: number) => ((source.frequencies.length - 1) * candidate + 1) * ((source.angles.length - 1) * candidate + 1);
  while (factor > 1 && cellCount(factor) > maxCells) factor -= 1;
  const columns = (source.frequencies.length - 1) * factor + 1;
  const rows = (source.angles.length - 1) * factor + 1;
  const interpolateAxis = (values: number[], position: number, logarithmic = false) => {
    if (values.length === 1) return values[0];
    const left = Math.min(values.length - 2, Math.floor(position / factor));
    const t = Math.min(1, (position - left * factor) / factor);
    if (logarithmic && values[left] > 0 && values[left + 1] > 0) return values[left] * ((values[left + 1] / values[left]) ** t);
    return values[left] + (values[left + 1] - values[left]) * t;
  };
  const frequencies = Array.from({ length: columns }, (_, column) => interpolateAxis(source.frequencies, column, true));
  const angles = Array.from({ length: rows }, (_, row) => interpolateAxis(source.angles, row));
  const values = Array.from({ length: rows }, (_, row) => Array.from({ length: columns }, (_, column) => {
    if (source.frequencies.length === 1 && source.angles.length === 1) return sourceValues[0][0];
    const x0 = Math.min(source.frequencies.length - 1, Math.floor(column / factor));
    const y0 = Math.min(source.angles.length - 1, Math.floor(row / factor));
    const x1 = Math.min(source.frequencies.length - 1, x0 + 1);
    const y1 = Math.min(source.angles.length - 1, y0 + 1);
    const tx = x0 === x1 ? 0 : (column - x0 * factor) / factor;
    const ty = y0 === y1 ? 0 : (row - y0 * factor) / factor;
    const samples: Array<[number | null, number]> = [
      [sourceValues[y0][x0], (1 - tx) * (1 - ty)],
      [sourceValues[y0][x1], tx * (1 - ty)],
      [sourceValues[y1][x0], (1 - tx) * ty],
      [sourceValues[y1][x1], tx * ty],
    ];
    const usable = samples.filter((sample): sample is [number, number] => sample[0] !== null && Number.isFinite(sample[0]) && sample[1] > 0);
    const weight = usable.reduce((sum, sample) => sum + sample[1], 0);
    return weight ? usable.reduce((sum, sample) => sum + sample[0] * sample[1], 0) / weight : null;
  }));
  return { frequencies, angles, values, factor };
}

/**
 * Blank the rear-hemisphere rows of a zero-radiation result's grid.
 *
 * Their level is the engine's floor, not a solved pressure: drawn, they would
 * read as a genuine null lobe, and contoured they would trace a false edge at
 * 90 degrees. An old saved run with 0-180 data is masked from its own metadata.
 */
export function maskRearHemisphere(grid: InterpolatedDirectivityGrid, result: ResultPayload): InterpolatedDirectivityGrid {
  if (!hasZeroRadiationRear(result) || !grid.angles.some(isOutsideHalfSpace)) return grid;
  return {
    ...grid,
    values: grid.values.map((row, index) => (isOutsideHalfSpace(grid.angles[index]) ? row.map(() => null) : row)),
  };
}

type ContourSegment = [number, number, number, number];

/** Small marching-squares pass used for labeled engineering reference lines. */
export function contourSegments(values: Array<Array<number | null>>, level: number): ContourSegment[] {
  const segments: ContourSegment[] = [];
  const crossing = (a: number, b: number, x1: number, y1: number, x2: number, y2: number): [number, number] | null => {
    if (!((a < level && b >= level) || (a >= level && b < level))) return null;
    const t = (level - a) / (b - a);
    return [x1 + (x2 - x1) * t, y1 + (y2 - y1) * t];
  };
  for (let row = 0; row < values.length - 1; row += 1) {
    for (let column = 0; column < (values[row]?.length ?? 0) - 1; column += 1) {
      const topLeft = values[row][column];
      const topRight = values[row][column + 1];
      const bottomRight = values[row + 1][column + 1];
      const bottomLeft = values[row + 1][column];
      if ([topLeft, topRight, bottomRight, bottomLeft].some((value) => value === null || !Number.isFinite(value))) continue;
      const points = [
        crossing(topLeft!, topRight!, column, row, column + 1, row),
        crossing(topRight!, bottomRight!, column + 1, row, column + 1, row + 1),
        crossing(bottomLeft!, bottomRight!, column, row + 1, column + 1, row + 1),
        crossing(topLeft!, bottomLeft!, column, row, column, row + 1),
      ].filter((point): point is [number, number] => point !== null);
      if (points.length >= 2) segments.push([points[0][0], points[0][1], points[1][0], points[1][1]]);
      if (points.length === 4) segments.push([points[2][0], points[2][1], points[3][0], points[3][1]]);
    }
  }
  return segments;
}

type ContourPoint = [number, number];

/**
 * Shape settings for the live contour overlay.
 *
 * The PNG renderer benefits from a large, high-DPI Matplotlib canvas. On the
 * much smaller live chart the same marching-squares vertices need a stronger
 * cardinal curve to avoid reading as a chain of little elbows. Constraining
 * its control points to the path bounds prevents the curve from overshooting a
 * physically measured contour at the ends or around a tight turn.
 */
export function smoothContourShape(points: number[][]) {
  const bounds = points.reduce(([minX, minY, maxX, maxY], [x, y]) => [
    Math.min(minX, x), Math.min(minY, y), Math.max(maxX, x), Math.max(maxY, y),
  ], [Infinity, Infinity, -Infinity, -Infinity]);
  return {
    points,
    smooth: 0.5,
    smoothConstraint: [
      [bounds[0], bounds[1]],
      [bounds[2], bounds[3]],
    ],
  };
}

interface PlotRect { x: number; y: number; width: number; height: number }

/**
 * A contour line over a faint stroke in the chart ground. The -3 dB line in
 * the accent has under 2:1 contrast on the hot end of either ramp; the
 * under-stroke gives it a crisp edge there without a visible halo elsewhere.
 */
function contourPolyline(points: number[][], style: { lineWidth: number } & Record<string, unknown>, background: string | undefined): Array<Record<string, unknown>> {
  const shape = smoothContourShape(points);
  const line = { type: 'polyline', shape, style: { fill: null, lineCap: 'round', lineJoin: 'round', ...style } };
  if (!background) return [line];
  return [{ type: 'polyline', shape, style: { fill: null, stroke: background, lineWidth: style.lineWidth + 1.6, opacity: .4, lineCap: 'round', lineJoin: 'round' } }, line];
}

/**
 * Project a dense contour vertex directly into the plot rectangle.
 *
 * Passing a fractional index through `api.coord` on a category axis rounds it
 * to an ordinal category first. That snapped every vertex to a heatmap cell
 * centre and turned even dense contours back into staircases. The samples are
 * uniformly spaced in log-frequency and angle, so their continuous pixel
 * position is unambiguous and does not need the ordinal scale at all.
 */
export function contourPointToPixels(point: ContourPoint, grid: InterpolatedDirectivityGrid, rect: PlotRect): ContourPoint {
  const columns = grid.frequencies.length;
  const rows = grid.angles.length;
  return [
    rect.x + (point[0] + 0.5) * rect.width / Math.max(1, columns),
    rect.y + rect.height - (point[1] + 0.5) * rect.height / Math.max(1, rows),
  ];
}

/** Project a contour from another solve onto the primary map's physical axes. */
export function comparisonContourPointToPixels(
  point: ContourPoint,
  source: InterpolatedDirectivityGrid,
  display: InterpolatedDirectivityGrid,
  rect: PlotRect,
): ContourPoint {
  const interpolate = (values: number[], position: number, logarithmic = false): number => {
    if (values.length <= 1) return values[0] ?? 0;
    const left = Math.max(0, Math.min(values.length - 2, Math.floor(position)));
    const t = Math.max(0, Math.min(1, position - left));
    if (logarithmic && values[left] > 0 && values[left + 1] > 0) {
      return values[left] * ((values[left + 1] / values[left]) ** t);
    }
    return values[left] + (values[left + 1] - values[left]) * t;
  };
  const frequency = interpolate(source.frequencies, point[0], true);
  const angle = interpolate(source.angles, point[1]);
  const firstFrequency = display.frequencies[0] ?? frequency;
  const lastFrequency = display.frequencies.at(-1) ?? frequency;
  const firstAngle = display.angles[0] ?? angle;
  const lastAngle = display.angles.at(-1) ?? angle;
  const frequencySpan = Math.log(lastFrequency) - Math.log(firstFrequency);
  const angleSpan = lastAngle - firstAngle;
  const frequencyPosition = frequencySpan && frequency > 0
    ? (Math.log(frequency) - Math.log(firstFrequency)) / frequencySpan
    : .5;
  const anglePosition = angleSpan ? (angle - firstAngle) / angleSpan : .5;
  const columns = Math.max(1, display.frequencies.length);
  const rows = Math.max(1, display.angles.length);
  return [
    rect.x + (.5 + frequencyPosition * Math.max(0, columns - 1)) * rect.width / columns,
    rect.y + rect.height - (.5 + anglePosition * Math.max(0, rows - 1)) * rect.height / rows,
  ];
}

/** Join marching-squares fragments into continuous paths so contour lines can
 * be rounded and anti-aliased as curves rather than drawn as tiny segments. */
export function contourPolylines(segments: ContourSegment[]): ContourPoint[][] {
  // Endpoints are matched through a hash rather than by rescanning the
  // remaining segments on every join. That scan made this quadratic: measured
  // 1.4 ms at 200 segments, 13 ms at 1,400 and 62 ms at 3,000 -- and it runs
  // once per contour level, four levels per heatmap. The bucket key quantises
  // to the same 1e-7 tolerance `close` uses, and neighbouring buckets are
  // still checked so a pair straddling a bucket boundary cannot be missed.
  const QUANTUM = 1e-7;
  const key = (x: number, y: number) => `${Math.round(x / QUANTUM)}:${Math.round(y / QUANTUM)}`;
  const close = (a: ContourPoint, b: ContourPoint) =>
    Math.abs(a[0] - b[0]) < QUANTUM && Math.abs(a[1] - b[1]) < QUANTUM;

  const buckets = new Map<string, number[]>();
  const add = (x: number, y: number, index: number) => {
    const bucket = buckets.get(key(x, y));
    if (bucket) bucket.push(index);
    else buckets.set(key(x, y), [index]);
  };
  segments.forEach((segment, index) => {
    add(segment[0], segment[1], index);
    add(segment[2], segment[3], index);
  });

  const used = new Array<boolean>(segments.length).fill(false);
  const candidatesAt = (point: ContourPoint): number[] => {
    const found: number[] = [];
    const [cx, cy] = [Math.round(point[0] / QUANTUM), Math.round(point[1] / QUANTUM)];
    for (let dx = -1; dx <= 1; dx += 1) {
      for (let dy = -1; dy <= 1; dy += 1) {
        const bucket = buckets.get(`${cx + dx}:${cy + dy}`);
        if (bucket) found.push(...bucket);
      }
    }
    return found;
  };

  const polylines: ContourPoint[][] = [];
  for (let first = 0; first < segments.length; first += 1) {
    if (used[first]) continue;
    used[first] = true;
    const segment = segments[first];
    const points: ContourPoint[] = [[segment[0], segment[1]], [segment[2], segment[3]]];
    let joined = true;
    while (joined) {
      joined = false;
      for (const end of [true, false]) {
        const anchor = end ? points.at(-1)! : points[0];
        for (const index of candidatesAt(anchor)) {
          if (used[index]) continue;
          const candidate = segments[index];
          const start: ContourPoint = [candidate[0], candidate[1]];
          const finish: ContourPoint = [candidate[2], candidate[3]];
          if (end) {
            if (close(anchor, start)) points.push(finish);
            else if (close(anchor, finish)) points.push(start);
            else continue;
          } else if (close(anchor, finish)) points.unshift(start);
          else if (close(anchor, start)) points.unshift(finish);
          else continue;
          used[index] = true;
          joined = true;
          break;
        }
        if (joined) break;
      }
    }
    polylines.push(points);
  }
  return polylines;
}

/** Round angular steps a person reads without decoding: 5 deg up to quadrants. */
const ANGLE_LABEL_STEPS_DEG = [5, 10, 15, 20, 30, 45, 60, 90] as const;
/** How many angle labels each card size can carry without crowding. */
const ANGLE_LABEL_BUDGET: Record<ChartDensity, number> = { compact: 9, regular: 15, full: 37 };

/**
 * Which angle rows get a label.
 *
 * Leaving this to ECharts' `hideOverlap` produced an arbitrary, drifting set:
 * on a small card it kept every second tick, so the axis read 170, 150, 130 ...
 * and dropped 0 deg entirely -- the one angle a directivity map is read
 * against. Choosing round steps that divide zero instead keeps the axis
 * symmetric about the on-axis row and always labels it, at every card size.
 */
export function directivityAngleLabelIndices(angles: number[], density: ChartDensity): Set<number> {
  if (angles.length < 2) return new Set(angles.map((_angle, index) => index));
  const span = Math.abs(angles.at(-1)! - angles[0]);
  const budget = ANGLE_LABEL_BUDGET[density];
  const step = ANGLE_LABEL_STEPS_DEG.find((candidate) => span / candidate + 1 <= budget)
    ?? ANGLE_LABEL_STEPS_DEG.at(-1)!;
  const labelled = new Set<number>();
  angles.forEach((angle, index) => {
    if (Math.abs(angle - Math.round(angle / step) * step) < 1e-6) labelled.add(index);
  });
  return labelled;
}

/**
 * Return the interior angular graticule values for a directivity map.
 *
 * The plot border already marks the two endpoints, so guides at those values
 * would only thicken it. Starting from the first strict multiple also keeps a
 * configured interval anchored to physical 0° rather than to an arbitrary
 * sweep start.
 */
export function directivityAngleGuides(angles: number[], interval: number): number[] {
  if (angles.length < 2 || !Number.isFinite(interval) || interval <= 0) return [];
  const lower = Math.min(angles[0], angles.at(-1)!);
  const upper = Math.max(angles[0], angles.at(-1)!);
  const guides: number[] = [];
  const first = (Math.floor(lower / interval) + 1) * interval;
  for (let angle = first; angle < upper - 1e-9; angle += interval) {
    guides.push(Number(angle.toPrecision(12)));
  }
  return guides;
}

/**
 * The map's axes are *category* axes over the display grid: the contours, the
 * frequency ticks and the bitmap are all placed by grid index, so one index
 * space serves all three. The frequency axis still reads logarithmically
 * because the sweep itself is log-spaced.
 */
export interface DirectivityContourReference {
  label: string;
  result: ResultPayload;
}

interface DirectivityComparisonOverlay {
  primaryLabel: string;
  references: DirectivityContourReference[];
  showLegend?: boolean;
  showContourNames?: boolean;
}

export function heatmapOption(
  result: ResultPayload,
  tokens: ChartTokens,
  plane: string,
  mapReference: number,
  density: ChartDensity = 'regular',
  live = false,
  angleGuideInterval = 10,
  comparison?: DirectivityComparisonOverlay,
): EChartsOption {
  // The solved samples: the bitmap is interpolated from these per pixel, and
  // the tooltip reports them rather than an interpolated value.
  const solved = maskRearHemisphere(interpolateDirectivityGrid(result, plane, 1), result);
  // The display grid carries the category axes and the contours. The exported
  // renderer contours a canonical 500 x 361 interpolation, and a settled chart
  // contours a comparably dense grid: a typical 60 x 37 result resolves to
  // 532 x 325. Boundary Lab draws a lighter surface while a solve is changing,
  // so a live snapshot contours a grid capped to finish well inside the 250 ms
  // publication interval.
  const grid = maskRearHemisphere(live
    ? interpolateDirectivityGrid(result, plane, 2, MAX_LIVE_INTERPOLATED_CELLS)
    : interpolateDirectivityGrid(result, plane, 12, MAX_CONTOUR_CELLS), result);
  const floor = mapReference * 5;
  const comparing = Boolean(comparison?.references.length);
  // The overlaid contours are the same runs the line charts draw, so they take
  // their colour from the same label-keyed table rather than from their order
  // in this particular map.
  const comparisonColors = seriesColorsByLabel(
    comparison ? [comparison.primaryLabel, ...comparison.references.map(({ label }) => label)] : [],
    tokens.series,
    tokens.accent,
    comparison ? runOfLabel(comparison.primaryLabel) : undefined,
  );
  const baseInset = MAP_GRID[density];
  const inset = comparing && comparison?.showLegend !== false
    ? { ...baseInset, top: Math.max(baseInset.top, density === 'compact' ? 27 : 31) }
    : baseInset;
  // [column, row] index the solved grid; the clamped level is what the colour
  // ramp reads, the rest is the tooltip.
  const cells = solved.values.flatMap((rowValues, row) => rowValues.flatMap((level, column) => level === null ? [] : [[column, row, Math.max(floor, Math.min(0, level)), level, solved.frequencies[column], solved.angles[row]]]));
  const categoryAxis = { ...axes(tokens, density), axisTick: { alignWithLabel: true }, axisLabel: { ...axes(tokens, density).axisLabel, hideOverlap: true } };
  const frequencyTickLabels = directivityFrequencyTickLabels(grid.frequencies);
  const isFrequencyTick = (index: number) => frequencyTickLabels.has(index);
  // Every tick keeps its guide; only round values carry a label, so ECharts'
  // overlap test never gets to drop 1k or 10k.
  const frequencyLabels = directivityFrequencyLabelIndices(grid.frequencies, density);
  const angleLabels = directivityAngleLabelIndices(grid.angles, density);
  const angleGuides = directivityAngleGuides(grid.angles, angleGuideInterval);
  const angleGuideSeries = angleGuides.length ? [{
    name: `${angleGuideInterval}° angular guides`, type: 'custom', coordinateSystem: 'cartesian2d', silent: true, z: 4, clip: true,
    data: angleGuides,
    // The guide angle is read from `angleGuides` by index, not through
    // `api.value`. Both axes here are category axes, so ECharts expands a
    // bare-number datum to [dataIndex, value] and `api.value(0)` hands back
    // the ordinal instead of the angle -- which stacked every guide between
    // 0 deg and the guide count rather than spreading them over the sweep.
    renderItem: (params: { coordSys?: PlotRect; dataIndex: number }) => {
      if (!params.coordSys || grid.angles.length < 2) return null;
      const lower = grid.angles[0];
      const upper = grid.angles.at(-1)!;
      const fraction = (angleGuides[params.dataIndex] - lower) / (upper - lower);
      const y = params.coordSys.y + params.coordSys.height * (1 - fraction);
      return { type: 'line', shape: { x1: params.coordSys.x, y1: y, x2: params.coordSys.x + params.coordSys.width, y2: y }, style: { stroke: tokens.grid, lineWidth: 1, opacity: .9 } };
    },
  }] : [];
  // Grey band over the rear hemisphere of a zero-radiation result: the cells
  // there are blank (see `maskRearHemisphere`), and the band says why.
  const rearBand = hasZeroRadiationRear(result) && grid.angles.length >= 2 && grid.angles.some(isOutsideHalfSpace)
    ? [{
      name: OUTSIDE_HALF_SPACE_LABEL, type: 'custom', coordinateSystem: 'cartesian2d', silent: true, z: 5, clip: true,
      data: [0],
      renderItem: (params: { coordSys?: PlotRect }) => {
        if (!params.coordSys) return null;
        const lower = grid.angles[0];
        const upper = grid.angles.at(-1)!;
        const firstOutside = grid.angles.findIndex(isOutsideHalfSpace);
        // The band starts at the last solved row, so no solved cell is covered.
        const edge = grid.angles[Math.max(0, firstOutside - 1)];
        const top = params.coordSys.y;
        const bottom = params.coordSys.y + params.coordSys.height * (1 - (edge - lower) / (upper - lower));
        return {
          type: 'group',
          children: [
            { type: 'rect', shape: { x: params.coordSys.x, y: top, width: params.coordSys.width, height: Math.max(0, bottom - top) }, style: { fill: tokens.muted, opacity: .28 } },
            { type: 'text', style: { x: params.coordSys.x + params.coordSys.width / 2, y: top + Math.max(0, bottom - top) / 2, text: OUTSIDE_HALF_SPACE_LABEL, fill: tokens.foreground, font: '11px ui-monospace, monospace', textAlign: 'center', textVerticalAlign: 'middle', opacity: .85 } },
          ],
        };
      },
    }]
    : [];
  const contourLevels = [...new Set([-3, -6, -12, mapReference])].filter((level) => level >= floor).sort((a, b) => b - a);
  const contourSeries = contourLevels.flatMap((level, contourIndex) => {
    const polylines = contourPolylines(contourSegments(grid.values, level)).filter((points) => points.length > 1);
    if (!polylines.length) return [];
    const labelIndex = polylines.reduce((best, points, index) => points.length > polylines[best].length ? index : best, 0);
    const isComparisonContour = comparing && level === mapReference;
    const color = isComparisonContour
      ? comparisonColors.get(comparison!.primaryLabel) ?? tokens.accent
      : level === -6 ? tokens.foreground : level === -3 ? tokens.accent : tokens.series[Math.min(contourIndex, tokens.series.length - 1)] ?? tokens.muted;
    return [{
      name: isComparisonContour ? comparison!.primaryLabel : `${level} dB contour`, type: 'custom', coordinateSystem: 'cartesian2d', silent: true, z: 6, clip: true,
      ...(isComparisonContour ? { itemStyle: { color } } : {}),
      data: polylines.map((_points, index) => [index, index === labelIndex ? 1 : 0]),
      renderItem: (params: { coordSys?: PlotRect }, api: { value: (index: number) => unknown }) => {
        if (!params.coordSys) return null;
        const points = polylines[Number(api.value(0))].map((point) => contourPointToPixels(point, grid, params.coordSys!));
        const middle = points[Math.floor(points.length / 2)];
        const children = contourPolyline(points, { stroke: color, lineWidth: level === mapReference ? 1.6 : 1.05, opacity: .92, lineDash: level <= -12 ? [4, 3] : undefined }, tokens.background);
        if (Number(api.value(1)) && (density !== 'compact' || level === -6 || isComparisonContour)) children.push({ type: 'text', style: { x: middle[0] + 3, y: middle[1] - 3, text: isComparisonContour && comparison?.showContourNames ? middleEllipsis(comparison.primaryLabel, 14) : `${level} dB`, fill: color, font: '11px ui-monospace, monospace', backgroundColor: tokens.background, padding: [1, 2], borderRadius: 2 } });
        return { type: 'group', children };
      },
    }];
  });
  const comparisonSeries = comparison?.references.flatMap((reference, referenceIndex) => {
    const color = comparisonColors.get(reference.label) ?? tokens.accent;
    const referenceGrid = maskRearHemisphere(live
      ? interpolateDirectivityGrid(reference.result, plane, 2, MAX_LIVE_INTERPOLATED_CELLS)
      : interpolateDirectivityGrid(reference.result, plane, 12, MAX_CONTOUR_CELLS), reference.result);
    const polylines = contourPolylines(contourSegments(referenceGrid.values, mapReference)).filter((points) => points.length > 1);
    if (!polylines.length) return [];
    const labelIndex = polylines.reduce((best, points, index) => points.length > polylines[best].length ? index : best, 0);
    return [{
      name: reference.label,
      type: 'custom',
      coordinateSystem: 'cartesian2d',
      silent: true,
      z: 8 + referenceIndex,
      clip: true,
      itemStyle: { color },
      data: polylines.map((_points, index) => [index, index === labelIndex ? 1 : 0]),
      renderItem: (params: { coordSys?: PlotRect }, api: { value: (index: number) => unknown }) => {
        if (!params.coordSys) return null;
        const points = polylines[Number(api.value(0))].map((point) => comparisonContourPointToPixels(point, referenceGrid, grid, params.coordSys!));
        const middle = points[Math.floor(points.length / 2)];
        const children = contourPolyline(points, { stroke: color, lineWidth: 1.7, opacity: .96, lineDash: [6, 4] }, tokens.background);
        if (comparison.showContourNames && Number(api.value(1))) children.push({
          type: 'text',
          style: { x: middle[0] + 3, y: middle[1] - 3, text: middleEllipsis(reference.label, density === 'compact' ? 12 : 18), fill: color, font: '11px ui-monospace, monospace', backgroundColor: tokens.background, padding: [1, 2], borderRadius: 2 },
        });
        return { type: 'group', children };
      },
    }];
  }) ?? [];
  // One bitmap per plot size, drawn when ECharts first lays the plot out and
  // again only when a resize changes its device-pixel size.
  let bitmap: { key: string; canvas: HTMLCanvasElement | null } | undefined;
  const mapSeries = [{
    name: 'Directivity map', type: 'custom', coordinateSystem: 'cartesian2d', silent: true, z: 1, clip: true,
    data: cells.length ? [0] : [],
    renderItem: (params: { coordSys?: PlotRect }, api: { getDevicePixelRatio?: () => number }) => {
      if (!params.coordSys) return null;
      const rect = params.coordSys;
      const ratio = api.getDevicePixelRatio?.() ?? 1;
      const width = Math.max(1, Math.round(rect.width * ratio));
      const height = Math.max(1, Math.round(rect.height * ratio));
      const key = `${width}x${height}`;
      if (bitmap?.key !== key) bitmap = { key, canvas: directivityMapCanvas(solved.values, floor, tokens.colormap, width, height, grid.factor) };
      return bitmap.canvas ? { type: 'image', style: { image: bitmap.canvas, x: rect.x, y: rect.y, width: rect.width, height: rect.height } } : null;
    },
  }, {
    // Transparent hover targets, one per solved sample, so the tooltip states
    // a solved value. The colour ramp is attached here: its dimension 2 is the
    // same clamped level the bitmap is coloured by.
    name: 'Solved samples', type: 'custom', coordinateSystem: 'cartesian2d', z: 2, clip: true,
    data: cells,
    renderItem: (params: { coordSys?: PlotRect }, api: { value: (index: number) => unknown }) => {
      if (!params.coordSys) return null;
      const rect = params.coordSys;
      const [left, right] = solvedSampleSpan(Number(api.value(0)), grid.factor, grid.frequencies.length, rect.x, rect.width);
      // Rows count up from the bottom of the plot.
      const [lower, upper] = solvedSampleSpan(Number(api.value(1)), grid.factor, grid.angles.length, 0, rect.height);
      return {
        type: 'rect',
        shape: { x: left, y: rect.y + rect.height - upper, width: right - left, height: upper - lower },
        style: { fill: 'rgba(0, 0, 0, 0)' },
        emphasis: { style: { stroke: tokens.accent, lineWidth: 1.2 } },
      };
    },
  }];
  const comparisonLabels = comparison ? [comparison.primaryLabel, ...comparison.references.map(({ label }) => label)] : [];
  return {
    animationDuration: live ? 0 : 180,
    backgroundColor: tokens.background,
    textStyle: { color: tokens.foreground, fontFamily: CHART_FONT_FAMILY },
    ...(comparing && comparison?.showLegend !== false ? { legend: {
      data: comparisonLabels,
      top: 1,
      right: LEGEND_INSET,
      textStyle: { color: tokens.muted, fontSize: 11 },
      formatter: (name: string) => middleEllipsis(name, density === 'compact' ? 11 : 18),
      itemWidth: density === 'compact' ? 10 : 14,
      itemHeight: 2,
      itemGap: density === 'compact' ? 6 : 10,
      ...LEGEND_LINE_KEY,
    } } : {}),
    tooltip: { trigger: 'item', confine: true, backgroundColor: tokens.background, borderColor: tokens.spine ?? tokens.grid, textStyle: { color: tokens.foreground, fontSize: 11 }, formatter: (params) => {
      const [, , , level, frequencyValue, angleValue] = (Array.isArray(params) ? params[0] : params).value as number[];
      return `${heatmapFrequencyLabel(frequencyValue)}Hz · ${Number(angleValue.toFixed(2))}° · ${level.toFixed(1)} dB`;
    } },
    grid: inset,
    xAxis: {
      type: 'category',
      data: grid.frequencies.map((frequency) => heatmapFrequencyLabel(frequency)),
      ...(density === 'full' ? { name: 'Frequency [Hz]', nameLocation: 'middle' as const, nameGap: 22, nameTextStyle: { color: tokens.muted, fontSize: 11 } } : {}),
      ...categoryAxis,
      axisTick: { ...categoryAxis.axisTick, interval: isFrequencyTick },
      axisLabel: {
        ...categoryAxis.axisLabel,
        hideOverlap: false,
        interval: (index: number) => frequencyLabels.has(index),
        formatter: (_value: string, index: number) => frequencyTickLabels.get(index) ?? '',
      },
      splitLine: { ...categoryAxis.splitLine, show: true, interval: isFrequencyTick },
    },
    // `hideOverlap` is off here on purpose: the label set is already chosen to
    // fit this card size, and letting ECharts thin it again is what broke the
    // axis's symmetry and dropped 0 deg.
    yAxis: { type: 'category', data: grid.angles.map((angle) => String(Number(angle.toFixed(2)))), ...(density === 'full' ? { name: 'Angle [°]', nameLocation: 'start' as const, nameGap: 10, nameTextStyle: { color: tokens.muted, fontSize: 11, align: 'right' as const, verticalAlign: 'top' as const } } : {}), ...categoryAxis, axisLabel: { ...categoryAxis.axisLabel, hideOverlap: false, ...(density === 'full' ? {} : { showMaxLabel: false }), interval: (index: number) => angleLabels.has(index) } },
    visualMap: { min: floor, max: 0, dimension: 2, seriesIndex: 1, right: 2, top: 'middle', itemWidth: density === 'compact' ? 6 : 8, itemHeight: MAP_RAMP[density], text: ['0', `${floor}`], textStyle: { color: tokens.muted, fontSize: 11 }, inRange: { color: tokens.colormap } },
    series: [...mapSeries, ...angleGuideSeries, ...rearBand, ...contourSeries, ...comparisonSeries] as EChartsOption['series'],
  };
}

export function directivityIndexOption(items: NamedResult[], tokens: ChartTokens, smoothing: ReturnType<typeof usePreferences>['smoothing'], density: ChartDensity): EChartsOption {
  const groups = items.map((item) => directivityIndexSeries(item.result as ResultPayload, smoothing));
  const oneMetricPerRun = groups.every((series) => series.length <= 1);
  // Colour separates the runs while each draws a single curve, and the metrics
  // once any run draws several -- there, a run reads as one fan of dashes.
  const colors = seriesColorsByLabel(
    oneMetricPerRun
      ? items.map(({ label }) => label)
      : groups.flatMap((runSeries) => runSeries.map(({ name }) => name)),
    tokens.series,
    tokens.accent,
    primaryRunOf(items),
  );
  const series = groups.flatMap((runSeries, runIndex) => runSeries.map((entry) => {
    const color = colors.get(oneMetricPerRun ? items[runIndex].label : entry.name) ?? tokens.accent;
    return {
      ...entry,
      name: runSeries.length === 1 ? items[runIndex].label : `${items[runIndex].label} · ${entry.name}`,
      lineStyle: { color, width: runIndex ? 1.35 : 2, type: runIndex ? 'dashed' as const : 'solid' as const },
      itemStyle: { color },
    };
  }));
  return lineOption(series, tokens, 'DI [dB]', density);
}

export function powerResponseOption(items: NamedResult[], tokens: ChartTokens, smoothing: ReturnType<typeof usePreferences>['smoothing'], density: ChartDensity): EChartsOption {
  const colors = seriesColorsByLabel(items.map(({ label }) => label), tokens.series, tokens.accent, primaryRunOf(items));
  const series = items.flatMap((item, index) => powerResponseSeries(item.result as ResultPayload, smoothing).map((entry) => {
    const color = colors.get(item.label) ?? tokens.accent;
    return {
      ...entry,
      name: items.length === 1 ? entry.name : `${item.label} · ${entry.name}`,
      lineStyle: { color, width: index ? 1.35 : 2, type: index ? 'dashed' as const : 'solid' as const },
      itemStyle: { color },
    };
  }));
  return lineOption(series, tokens, 'Spatial-average level [dB SPL]', density);
}

/**
 * Impedance, in whatever quantity the result actually holds.
 *
 * A driver-modelled channel carries the driver's electrical terminal impedance
 * in ohms here, not the normalized acoustic Z/ρc, and says so in
 * `metadata.impedance_units`. This axis was hardcoded to Z/ρc, so a
 * driver-coupled run drew ohms under an acoustic label -- the ZMA exporter has
 * always read the tag and the chart was the last reader that did not. The two
 * are different quantities and cannot share a scale, so a comparison run in the
 * other unit is dropped rather than overlaid.
 */
export function impedanceOption(items: NamedResult[], tokens: ChartTokens, smoothing: ReturnType<typeof usePreferences>['smoothing'], density: ChartDensity, display: ReturnType<typeof usePreferences>['impedanceDisplay'] = 'real_imaginary'): EChartsOption {
  const { items: comparable, units } = impedanceComparable(items);
  const magnitudePhase = display === 'magnitude_phase';
  const colors = seriesColorsByLabel(comparable.map(({ label }) => label), tokens.series, tokens.accent, primaryRunOf(comparable));
  const series = comparable.flatMap((item) => {
    const color = colors.get(item.label) ?? tokens.accent;
    return impedanceSeries(item.result, magnitudePhase ? 'polar' : 'cartesian', smoothing).map((entry, componentIndex) => ({
      ...entry,
      name: `${item.label} · ${entry.name}`,
      lineStyle: { color, width: componentIndex ? 1.35 : 2, type: componentIndex ? 'dashed' as const : 'solid' as const },
      itemStyle: { color },
    }));
  });
  // impedanceSeries already assigns the phase trace to axis 1 in polar mode.
  return lineOption(series, tokens, units.axis, density, undefined, magnitudePhase ? 'Phase [°]' : undefined);
}

/** Engineering radiation load seen by each reduced passive-cardioid port. */
export function radiationImpedanceOption(
  presentation: RadiationImpedancePresentation,
  tokens: ChartTokens,
  density: ChartDensity,
): EChartsOption {
  const colors = seriesColorsByLabel(
    presentation.in_phase_termination.aperture_names,
    tokens.series,
    tokens.accent,
  );
  const series = radiationImpedanceTraces(presentation).map((trace) => {
    const aperture = trace.name.split(' · ')[0];
    const color = colors.get(aperture) ?? tokens.accent;
    return {
      name: trace.name,
      type: 'line' as const,
      showSymbol: false,
      connectNulls: false,
      data: trace.data,
      lineStyle: {
        color,
        width: trace.component === 'real' ? 2 : 1.35,
        type: trace.component === 'real' ? 'solid' as const : 'dashed' as const,
      },
      itemStyle: { color },
    };
  });
  return lineOption(series, tokens, 'Radiation Z [Pa·s/m³]', density);
}

/**
 * On-axis phase alone, for when it deserves a whole card.
 *
 * Same detrending as the trace overlaid on the SPL chart and as the exported
 * PNG: propagation removed so the unwrap can track, then the residual
 * group-delay slope removed weighted by output level, so the curve sits flat
 * where the speaker radiates and deviations stay visible.
 */
export function phaseOption(items: NamedResult[], tokens: ChartTokens, density: ChartDensity): EChartsOption {
  const traces = phaseSeries(items);
  const colors = seriesColorsByLabel(traces.map(({ name }) => name), tokens.series, tokens.accent, primaryRunOf(items));
  const series = traces.map(({ name, points }, index) => {
    const color = colors.get(name) ?? tokens.accent;
    return {
      name,
      type: 'line' as const,
      showSymbol: false,
      connectNulls: false,
      lineStyle: { color, width: index ? 1.2 : 2, type: index ? 'dashed' as const : 'solid' as const },
      itemStyle: { color },
      data: points,
    };
  });
  return withZeroReference(lineOption(series, tokens, 'Phase [°]', density), tokens);
}

/**
 * The number an axis tooltip hands over. Cartesian tooltips pass the value
 * dimension, but a series whose data are [frequency, value] pairs can pass the
 * pair; take the last element either way rather than reading a frequency.
 * `Number(null)` is 0, so a gap stays NaN instead of becoming a value.
 */
function tooltipNumber(value: unknown): number {
  const sample = Array.isArray(value) ? value[value.length - 1] : value;
  return typeof sample === 'number' || typeof sample === 'string' ? Number(sample) : Number.NaN;
}

/** A tooltip value rounded to what a reading needs: 11.93, 0.0421. */
export function formatChartValue(value: unknown): string {
  const numeric = tooltipNumber(value);
  if (!Number.isFinite(numeric)) return '—';
  return numeric === 0 || Math.abs(numeric) >= 1 ? numeric.toFixed(2) : String(Number(numeric.toPrecision(3)));
}

/** Decimals that keep a group delay readable in the unit it is drawn in. */
const GROUP_DELAY_DIGITS: Record<GroupDelayUnit, number> = { ms: 3, cycles: 2 };

/** How one group delay sample reads in the tooltip, unit included. */
export function formatGroupDelay(value: unknown, unit: GroupDelayUnit): string {
  // A gap printed as "0.000 ms" would be a delay the solve never reported.
  const numeric = tooltipNumber(value);
  return Number.isFinite(numeric) ? `${numeric.toFixed(GROUP_DELAY_DIGITS[unit])} ${unit}` : '—';
}

/**
 * Group delay, with the common time-of-flight to the mic removed.
 *
 * The unit is the user's: milliseconds, or periods of the frequency the delay
 * occurs at. Only the projection changes -- the curve, its trust gating and its
 * series names are the same either way -- and the axis autoscales in both, since
 * a constant delay rises linearly with frequency once it is counted in cycles.
 */
export function groupDelayOption(items: NamedResult[], tokens: ChartTokens, density: ChartDensity, unit: GroupDelayUnit = 'ms'): EChartsOption {
  const traces = groupDelaySeries(items, unit);
  const colors = seriesColorsByLabel(traces.map(({ name }) => name), tokens.series, tokens.accent, primaryRunOf(items));
  const series = traces.map((trace, index) => {
    const color = colors.get(trace.name) ?? tokens.accent;
    return {
      ...trace,
      lineStyle: { color, width: index ? 1.2 : 2, type: index ? 'dashed' as const : 'solid' as const },
      itemStyle: { color },
    };
  });
  const option = withZeroReference(lineOption(series, tokens, `Group delay [${unit}]`, density), tokens);
  // Compact and regular cards drop the axis title, so without this the tooltip
  // is the only place the unit can be stated -- and a bare number reads as ms
  // by habit.
  return {
    ...option,
    tooltip: {
      ...(option.tooltip as Record<string, unknown>),
      valueFormatter: (value: unknown) => formatGroupDelay(value, unit),
    },
  };
}

/**
 * What the amplifier has to supply, per frequency.
 *
 * Power and current share a card because they peak in different places -- power
 * follows the resistive part, current follows the impedance minimum -- and it
 * is the pair, not either alone, that sizes an amplifier.
 */
export function drivePowerOption(result: ResultPayload, tokens: ChartTokens, density: ChartDensity): EChartsOption {
  const series = drivePowerChartSeries(result).map((trace, index) => ({
    ...trace,
    lineStyle: { color: tokens.series[index] ?? tokens.accent, width: index ? 1.35 : 2, type: index ? 'dashed' as const : 'solid' as const },
    itemStyle: { color: tokens.series[index] ?? tokens.accent },
  }));
  return lineOption(series, tokens, 'Power [W]', density, undefined, series.length > 1 ? 'Current [A]' : undefined);
}

/**
 * Power and current for several channels at once.
 *
 * Only the Combined view reaches this: an LR4 sum has no electrical impedance
 * and no cone, so the card that would otherwise be empty draws the members that
 * do. Colour separates the channels and line weight separates the two
 * quantities, the same split the single-channel card makes.
 */
export function drivePowerOverlayOption(items: NamedResult[], tokens: ChartTokens, density: ChartDensity): EChartsOption {
  const colors = seriesColorsByLabel(items.map(({ label }) => label), tokens.series, tokens.accent, primaryRunOf(items));
  const series = items.flatMap(({ label, result }) => {
    const color = colors.get(label) ?? tokens.accent;
    return drivePowerChartSeries(result as ResultPayload).map((trace) => ({
      ...trace,
      name: `${label} · ${trace.name}`,
      lineStyle: { color, width: trace.yAxisIndex ? 1.35 : 2, type: trace.yAxisIndex ? 'dashed' as const : 'solid' as const },
      itemStyle: { color },
    }));
  });
  return lineOption(series, tokens, 'Power [W]', density, undefined, series.some((trace) => trace.yAxisIndex) ? 'Current [A]' : undefined);
}

/** Cone excursion for several channels at once, each against its own Xmax. */
export function excursionOverlayOption(items: NamedResult[], tokens: ChartTokens, density: ChartDensity): EChartsOption {
  const colors = seriesColorsByLabel(items.map(({ label }) => label), tokens.series, tokens.accent, primaryRunOf(items));
  const series = items.flatMap(({ label, result }) => {
    const color = colors.get(label) ?? tokens.accent;
    return excursionChartSeries(result as ResultPayload).map((trace, index) => ({
      ...trace,
      name: `${label} · ${trace.name}`,
      // The limit keeps its driver's colour so it cannot be read against the
      // wrong cone, and stays dotted so it cannot be read as a response.
      lineStyle: index ? { color, width: 1, type: 'dotted' as const } : { color, width: 2 },
      itemStyle: { color },
    }));
  });
  return lineOption(series, tokens, 'Excursion [mm peak]', density);
}

/**
 * How loud the chain can be run, per frequency.
 *
 * The system ceiling is the headline and is drawn as such; each member's own
 * ceiling sits behind it, lighter, because a single driver's capability is
 * context rather than the answer. The shown response is dotted for the same
 * reason the Xmax line is: it is where the run is now, not a maximum.
 */
export function maxOutputOption(
  result: ResultPayload,
  tokens: ChartTokens,
  density: ChartDensity,
  memberLabel?: (member: string) => string,
): EChartsOption {
  const traces = maxOutputChartSeries(result, memberLabel);
  let memberIndex = 0;
  const series = traces.map((trace) => {
    const color = trace.role === 'system'
      ? tokens.accent
      : trace.role === 'shown'
        ? tokens.muted
        : tokens.series[memberIndex++ % tokens.series.length] ?? tokens.accent;
    return {
      ...trace,
      lineStyle: {
        color,
        width: trace.role === 'system' ? 2.4 : 1.2,
        ...(trace.role === 'shown' ? { type: 'dotted' as const } : {}),
      },
      itemStyle: { color },
    };
  });
  return lineOption(series, tokens, 'SPL [dB]', density);
}

/** Cone excursion, with Xmax drawn flat across the sweep when the spec states it. */
export function excursionOption(result: ResultPayload, tokens: ChartTokens, density: ChartDensity): EChartsOption {
  const series = excursionChartSeries(result).map((trace, index) => ({
    ...trace,
    // The Xmax line is a limit, not a measurement: dotted, and in the warning
    // role rather than a series colour, so it cannot read as a second curve.
    lineStyle: index ? { color: tokens.accent, width: 1, type: 'dotted' as const } : { color: tokens.series[0] ?? tokens.accent, width: 2 },
    itemStyle: { color: index ? tokens.accent : tokens.series[0] ?? tokens.accent },
  }));
  // The server converts the RMS displacement phasor to one-way peak before it
  // compares or transports it, so the curve and the Xmax limit share a scale.
  return lineOption(series, tokens, 'Excursion [mm peak]', density);
}

/** Aspect ratio, superellipse exponent and the residual of the −6 dB fit. */
export function beamFitOption(result: ResultPayload, tokens: ChartTokens, density: ChartDensity): EChartsOption {
  const traces = beamFitSeries(result);
  const series = traces.map((trace, index) => ({
    ...trace,
    lineStyle: { color: tokens.series[index] ?? tokens.accent, width: trace.yAxisIndex ? 1.2 : 2, type: trace.yAxisIndex ? 'dashed' as const : 'solid' as const },
    itemStyle: { color: tokens.series[index] ?? tokens.accent },
  }));
  const hasResidual = traces.some((trace) => trace.yAxisIndex === 1);
  return lineOption(series, tokens, 'Ratio / exponent', density, undefined, hasResidual ? 'Residual [%]' : undefined);
}

/**
 * A conventional polar plot: level against angle at one frequency.
 *
 * Normalized per run to its own sample nearest the reference axis, which is
 * what a polar plot means -- absolute SPL would put two runs at different
 * levels on one radial scale and make the shapes incomparable, which is the
 * only thing this chart is for.
 */
export function polarOption(items: NamedResult[], tokens: ChartTokens, plane: PolarPlane, frequencyHz: number, floorDb: number, density: ChartDensity): EChartsOption {
  const colors = seriesColorsByLabel(items.map(({ label }) => label), tokens.series, tokens.accent, primaryRunOf(items));
  const series = items.flatMap(({ label, result, wrapper }) => {
    const index = nearestFrequencyIndex(result.frequencies, frequencyHz);
    const points = polarCut(result, index, plane, wrapper as ResultPayload | undefined);
    const onAxis = points.reduce<{ db: number | null; angle: number }>((best, [db, angle]) => (
      db !== null && Math.abs(angle) < Math.abs(best.angle) ? { db, angle } : best
    ), { db: null, angle: Infinity });
    const zeroRear = hasZeroRadiationRear(result);
    const reference = onAxis.db ?? Math.max(...points.map(([db]) => db ?? -Infinity));
    if (!Number.isFinite(reference)) return [];
    const color = colors.get(label) ?? tokens.accent;
    return [{
      name: label,
      type: 'line' as const,
      coordinateSystem: 'polar' as const,
      showSymbol: false,
      // Radius first, angle second: that is the polar series contract, and it
      // is the order polarSeries already returns.
      // A zero-radiation rear hemisphere holds the engine's floor, not a
      // pressure: leave a gap there instead of a trace collapsed to the centre.
      data: points.map(([db, angle]) => [db === null || (zeroRear && isOutsideHalfSpace(angle)) ? null : Math.max(floorDb, db - reference), angle]),
      lineStyle: { color, width: 2 },
      itemStyle: { color },
    }];
  });
  const labelSize = LABEL_FONT[density];
  // Only when every compared run has a zero-radiation rear: the wedge is drawn
  // once for the whole chart, so it must not cover another run's real rear trace.
  const shadeRear = items.length > 0 && items.every(({ result }) => hasZeroRadiationRear(result));
  // Two wedges, 90..180 and -180..-90, drawn as dense arcs on the outer ring:
  // a polar line is straight between samples, so a sparse pair would be a chord.
  const rearWedge = (from: number, to: number) => ({
    name: OUTSIDE_HALF_SPACE_LABEL,
    type: 'line' as const,
    coordinateSystem: 'polar' as const,
    silent: true,
    showSymbol: false,
    legendHoverLink: false,
    z: 0,
    lineStyle: { opacity: 0 },
    // origin 'start' fills from the axis minimum out to the ring; the default
    // ('auto') is 0 dB here, which is the ring itself, so nothing would show.
    areaStyle: { origin: 'start' as const, color: tokens.muted, opacity: .22 },
    data: Array.from({ length: 19 }, (_, step) => [0, from + ((to - from) * step) / 18]),
  });
  return {
    animationDuration: 180,
    backgroundColor: tokens.background,
    color: tokens.series,
    textStyle: { color: tokens.foreground, fontFamily: CHART_FONT_FAMILY },
    tooltip: { trigger: 'item', confine: true, backgroundColor: tokens.background, borderColor: tokens.spine ?? tokens.grid, textStyle: { color: tokens.foreground, fontSize: 11 } },
    legend: { top: 1, right: LEGEND_INSET, ...(shadeRear ? { data: items.map(({ label }) => label) } : {}), textStyle: { color: tokens.muted, fontSize: 11 }, formatter: (name: string) => middleEllipsis(name, density === 'compact' ? 12 : 22), itemWidth: density === 'compact' ? 10 : 14, itemHeight: 2, ...LEGEND_LINE_KEY },
    ...(shadeRear ? { graphic: [{ type: 'text', left: 'center', top: density === 'compact' ? '72%' : '82%', silent: true, style: { text: OUTSIDE_HALF_SPACE_LABEL, fill: tokens.muted, font: `${labelSize}px ui-monospace, monospace`, align: 'center' } }] } : {}),
    polar: { radius: density === 'compact' ? '68%' : '72%', center: ['50%', '54%'] },
    angleAxis: {
      type: 'value' as const,
      // Fixed at a full turn so one degree of pattern is one degree of chart.
      // Fitting the axis to the sampled span instead would stretch a 0..180
      // half-space around the whole circle and draw a 30 degree beamwidth as
      // 60 -- and the shape is the only thing this chart exists to show.
      min: -180,
      max: 180,
      // On-axis points up and positive angles go to the right, which is what a
      // polar response means everywhere else in loudspeaker work. ECharts
      // places the axis minimum at startAngle and runs clockwise from there.
      startAngle: 270,
      clockwise: true,
      interval: 30,
      axisLine: { lineStyle: { color: tokens.spine ?? tokens.grid } },
      axisLabel: { color: tokens.muted, fontSize: labelSize, formatter: (value: number) => `${value}°` },
      splitLine: { lineStyle: { color: tokens.grid, width: 1 } },
    },
    radiusAxis: {
      type: 'value' as const,
      min: floorDb,
      max: 0,
      interval: Math.abs(floorDb) >= 30 ? 10 : 5,
      // The ring labels sit over the plot, so they state their unit once and
      // drop the outermost 0 dB, which the curve itself already marks.
      axisLabel: { color: tokens.muted, fontSize: labelSize, showMinLabel: false, showMaxLabel: false, formatter: (value: number) => `${value} dB` },
      axisLine: { show: false },
      splitLine: { lineStyle: { color: tokens.grid, width: 1 } },
    },
    series: shadeRear ? [rearWedge(90, 180), rearWedge(-180, -90), ...series] : series,
  };
}

function chartLabel(chartType: ChartType): string {
  return CHART_TYPES.find(({ id }) => id === chartType)?.label ?? chartType;
}

/**
 * Names and units for the floating title chip. Both are far shorter than the
 * menu labels, which would truncate into the legend; the unit matters most,
 * because compact charts drop their axis titles and this states them instead.
 */
const CHART_BADGES: Record<ChartType, { short: string; long?: string; unit?: string }> = {
  frequency_response: { short: 'SPL', long: 'Sound pressure level', unit: 'dB' },
  directivity_map_h: { short: 'Dir H', long: 'Directivity H', unit: 'dB' },
  directivity_map_v: { short: 'Dir V', long: 'Directivity V', unit: 'dB' },
  directivity_map_d: { short: 'Dir D', long: 'Directivity Diagonal', unit: 'dB' },
  directivity_map: { short: 'Dir', long: 'Directivity', unit: 'dB' },
  directivity_index: { short: 'DI', long: 'Directivity index', unit: 'dB' },
  power_response: { short: 'Power resp', long: 'Power response', unit: 'dB SPL' },
  beam_shape: { short: 'Beam', long: 'Beam width', unit: '°' },
  beam_fit: { short: 'Beam fit', long: 'Beam shape fit' },
  beam_map: { short: 'Beam map' },
  balloon: { short: 'Balloon' },
  polar_response: { short: 'Polar', long: 'Polar response', unit: 'dB' },
  phase_response: { short: 'Phase', long: 'Pressure phase', unit: '°' },
  // The group delay unit follows a preference, not the chart: see chartUnit.
  group_delay: { short: 'GD', long: 'Group delay' },
  // The impedance unit depends on the result, not the chart: see chartUnit.
  impedance: { short: 'Impedance' },
  radiation_impedance: { short: 'Rad Z', long: 'Radiation load', unit: 'Pa·s/m³' },
  drive_power: { short: 'Power', long: 'Power & current', unit: 'W' },
  excursion: { short: 'Excursion', long: 'Cone excursion', unit: 'mm' },
  max_output: { short: 'Max SPL', long: 'Maximum output', unit: 'dB' },
  summary: { short: 'Summary' },
};

/**
 * The unit for the title chip.
 *
 * Every chart but one has a fixed unit. Impedance does not: the same chart
 * draws normalized Z/ρc for an unloaded waveguide and ohms for a
 * driver-modelled channel, and printing one label over the other is how the
 * chart came to claim a driver's ohms were acoustic.
 *
 * It reads `impedanceComparable`, not the primary result, because that is what
 * sets the axis. Judging the chip on the primary alone disagreed whenever the
 * primary had no impedance block: an electrical overlay then put Ω on the axis
 * while the chip went blank, which is the mislabelling in its quietest form.
 */
export function chartUnit(chartType: ChartType, result?: ResultPayload, items?: NamedResult[], groupDelayUnit: GroupDelayUnit = 'ms'): string | undefined {
  if (chartType === 'group_delay') return groupDelayUnit;
  if (chartType === 'impedance') {
    const candidates = items?.length ? items : result ? [{ id: 'primary', label: 'Primary', result, primary: true }] : [];
    const { items: comparable, units } = impedanceComparable(candidates);
    return comparable.length ? units.axis : undefined;
  }
  return CHART_BADGES[chartType]?.unit;
}

/** Longer name when the card can carry it, short name when it cannot. */
export function chartTitle(chartType: ChartType, wide: boolean): string {
  const badge = CHART_BADGES[chartType];
  if (!badge) return chartType;
  return wide ? badge.long ?? badge.short : badge.short;
}

export function chartImageFilename(chartType: ChartType, job?: JobItem | null, channelId?: string | null): string {
  const stem = job ? exportStemForJob(job) : 'result';
  const channel = channelId ? `_${exportTitleSlug(channelId)}` : '';
  return `${stem}${channel}_${chartType}.png`;
}

export function resolvedPolarStepNotice(result: ResultData): string | null {
  const grid = result.metadata?.polar_grid;
  if (!grid || typeof grid !== 'object') return null;
  const record = grid as Record<string, unknown>;
  const requested = Number(record.requested_step);
  const resolved = Number(record.resolved_step);
  if (!Number.isFinite(requested) || !Number.isFinite(resolved)) return null;
  if (Math.abs(requested - resolved) <= Math.max(1e-9, Math.abs(requested) * 1e-7)) return null;
  return `${Number(resolved.toPrecision(6))}° resolved (requested ${Number(requested.toPrecision(6))}°)`;
}

interface SummarySourceProps {
  job?: JobItem | null;
  wrapper?: ResultPayload;
  channelId?: string | null;
}

function SummaryGroupBlock({ group }: { group: SummaryGroup }) {
  return <section className="result-summary-group" data-tone={group.tone}>
    <h3>{group.title}</h3>
    <dl>{group.rows.map((row: SummaryRow) => <div className="result-summary-row" key={row.label} title={row.title}><dt>{row.label}</dt><dd>{row.value}</dd></div>)}</dl>
  </section>;
}

function Summary({ result, wrapper, job, channelId, density }: { result: ResultPayload; density: ChartDensity } & SummarySourceProps) {
  const developer = useDeveloperDetails();
  const groups = useMemo(() => summaryGroups({ result, wrapper, job, channelId, developer }), [channelId, developer, job, result, wrapper]);
  const [copied, setCopied] = useState(false);
  const copiedTimer = useRef<number | null>(null);
  useEffect(() => () => {
    if (copiedTimer.current !== null) window.clearTimeout(copiedTimer.current);
  }, []);
  const copy = useCallback(async () => {
    try {
      if (!navigator.clipboard?.writeText) return;
      await navigator.clipboard.writeText(summaryText(groups));
      setCopied(true);
      if (copiedTimer.current !== null) window.clearTimeout(copiedTimer.current);
      copiedTimer.current = window.setTimeout(() => setCopied(false), 1_600);
    } catch {
      // Clipboard permission is optional; the summary remains readable when it is blocked.
    }
  }, [groups]);
  return <div className="result-summary" data-density={density}>
    <div className={`result-summary-groups${groups.length ? '' : ' is-empty'}`}>
      {groups.length
        ? groups.map((group) => <SummaryGroupBlock group={group} key={group.title}/>)
        : <p className="result-summary-empty" role="status">No summary details available.</p>}
    </div>
    {groups.length > 0 && <button className="result-summary-copy" type="button" aria-label={copied ? 'Simulation summary copied' : 'Copy simulation summary'} title="Copy summary as text" onClick={() => void copy()}><span aria-live="polite">{copied ? 'Copied' : 'Copy'}</span></button>}
  </div>;
}

const NO_NAMED_RESULTS: NamedResult[] = [];
const NO_MEASURED_OVERLAYS: MeasuredOverlay[] = [];
const NO_REVERSE_NULL: ReverseNullTrace[] = [];

export interface DirectivityMapPanel {
  key: string;
  label: string;
  plane: string;
  result: ResultPayload;
  hasData: boolean;
  primaryLabel: string;
  references: DirectivityContourReference[];
}

function orderedPlanes(items: NamedResult[]): string[] {
  const found = new Set(items.flatMap(({ result }) => Object.entries(result.directivity ?? {})
    .filter(([, samples]) => Boolean(samples?.length))
    .map(([plane]) => plane)));
  const rank = (plane: string) => plane === 'horizontal' ? 0 : plane === 'vertical' ? 1 : 2;
  return [...found].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

type DirectivityMapChartType = 'directivity_map_h' | 'directivity_map_v' | 'directivity_map_d' | 'directivity_map';

function directivityPlaneForChart(chartType: DirectivityMapChartType): string | null {
  if (chartType === 'directivity_map_h') return 'horizontal';
  if (chartType === 'directivity_map_v') return 'vertical';
  if (chartType === 'directivity_map_d') return 'diagonal';
  return null;
}

export function directivityMapPanels(items: NamedResult[], chartType: DirectivityMapChartType): DirectivityMapPanel[] {
  const planes = chartType === 'directivity_map'
    ? orderedPlanes(items)
    : [directivityPlaneForChart(chartType)!];
  // A comparison run may be another model, so it only ever draws contours: the
  // colour field stays the primary's, or the plane says it has no data.
  const owners = items.some((item) => item.primary) ? items.filter((item) => item.primary) : items;
  return planes.map((plane) => {
    const available = items.filter((item) => Boolean((item.result.directivity as Record<string, unknown[]> | undefined)?.[plane]?.length));
    const primary = available.find((item) => owners.includes(item));
    return {
      key: plane,
      label: plane,
      plane,
      result: (primary ?? items[0])?.result as ResultPayload,
      hasData: Boolean(primary),
      primaryLabel: (primary ?? items[0])?.label ?? 'Primary',
      references: primary ? available.filter((item) => item !== primary).map(({ label, result }) => ({ label, result: result as ResultPayload })) : [],
    };
  });
}

function DirectivityComparisonMaps({ chartType, items, tokens, mapReference, normAngle, angleGuideInterval, showContourNames, density, live }: {
  chartType: DirectivityMapChartType;
  items: NamedResult[];
  tokens: ChartTokens;
  mapReference: number;
  normAngle: number;
  angleGuideInterval: number;
  showContourNames: boolean;
  density: ChartDensity;
  live: boolean;
}) {
  // Re-reference every run on the card, primary and comparison alike, before
  // the panels are built: a contour overlaid from a run solved at a different
  // normalization angle would otherwise be drawn against a different zero.
  const referenced = useMemo(
    () => items.map((item) => ({ ...item, result: withNormalizationAngle(item.result, normAngle) })),
    [items, normAngle],
  );
  const panels = directivityMapPanels(referenced, chartType);
  if (!panels.some(({ hasData }) => hasData)) {
    const plane = directivityPlaneForChart(chartType);
    const planeLabel = plane === 'horizontal' ? 'H' : plane === 'vertical' ? 'V' : 'Diagonal';
    return <ChartStub reason={plane ? `Directivity Map (${planeLabel}) needs the ${plane} polar plane. Turn it on under Directivity map in Simulation, then solve again.` : 'Directivity Map needs a polar plane. Turn one on under Directivity map in Simulation, then solve again.'}/>;
  }
  if (panels.length === 1 && panels[0].hasData) {
    const panel = panels[0];
    return <EChart option={heatmapOption(panel.result, tokens, panel.plane, mapReference, density, live, angleGuideInterval, {
      primaryLabel: panel.primaryLabel,
      references: panel.references,
      showContourNames,
    })} label={`Interactive HornLab ${panel.plane} directivity heatmap with comparison contours`} live={live}/>;
  }
  const legendPanelIndex = panels.findIndex(({ references }) => references.length > 0);
  return <div className="directivity-multiplane" data-density={density}>
    {panels.map((panel, index) => <div key={panel.key}>
      <span title={panel.label}>{middleEllipsis(panel.label, density === 'compact' ? 18 : 28)}</span>
      {panel.hasData
        ? <EChart option={heatmapOption(panel.result, tokens, panel.plane, mapReference, density, live, angleGuideInterval, {
          primaryLabel: panel.primaryLabel,
          references: panel.references,
          showContourNames,
          showLegend: index === legendPanelIndex,
        })} label={`Interactive ${panel.label} directivity heatmap with comparison contours`} live={live}/>
        : <div className="directivity-comparison-missing">No {panel.plane} data</div>}
    </div>)}
  </div>;
}

export function beamShapeMissingReason(result: ResultPayload): { reason: string; canEnable: boolean } {
  if (hasBalloonData(result)) return {
    reason: 'Spherical balloon data is available, but this result has no valid −6 dB contour fit.',
    canEnable: false,
  };
  const sampling = result.metadata?.balloon_sampling;
  const status = sampling && typeof sampling === 'object' ? String((sampling as Record<string, unknown>).status ?? '') : '';
  if (status === 'backend_unsupported') return {
    reason: 'Forward Beam Shape is not available from this engine. Choose an engine with 3D Balloon Sampling, then solve again.',
    canEnable: false,
  };
  if (status === 'missing_result') return {
    reason: 'Spherical sampling was enabled, but this solve returned no balloon data for the −6 dB contour fit.',
    canEnable: false,
  };
  return {
    reason: 'Forward Beam Shape needs 3D balloon sampling and a valid −6 dB contour fit.',
    canEnable: true,
  };
}

/** Radial span of the polar plot. −30 dB is where a waveguide's pattern lives. */
const POLAR_FLOOR_DB = -30;

/**
 * Polar response with its own frequency and plane scrubbers.
 *
 * A polar plot is a slice, so the card has to carry the choice of slice. Both
 * controls live in the same floating strip the balloon and beam-map cards
 * already use, so a short card spends its height on the plot rather than on
 * chrome.
 */
/** Where a polar card opens: mid-band, where a waveguide's pattern is settled. */
const POLAR_SEEK_HZ = 1_000;

function PolarResponseCard({ items, tokens, density, live }: { items: NamedResult[]; tokens: ChartTokens; density: ChartDensity; live: boolean }) {
  const frequencies = items[0]?.result.frequencies ?? [];
  const [index, setIndex] = useState(() => nearestFrequencyIndex(frequencies, POLAR_SEEK_HZ));
  const [plane, setPlane] = useState<PolarPlane>('horizontal');
  // Seeking 1 kHz once at mount lands on whatever the *provisional* grid held.
  // A card opened mid-solve sees a handful of frequencies, picks the nearest,
  // and then keeps that index as the sweep fills in behind it -- so the card
  // settles on an arbitrary frequency and looks deliberate about it.
  const chosen = useRef(false);
  const seeded = useRef(frequencies.length);
  useEffect(() => {
    // Only while the user has not chosen: once they have scrubbed, a growing
    // grid must not move the slider out from under them.
    if (!chosen.current && frequencies.length > seeded.current) {
      seeded.current = frequencies.length;
      setIndex(nearestFrequencyIndex(frequencies, POLAR_SEEK_HZ));
      return;
    }
    seeded.current = Math.min(seeded.current, frequencies.length);
    // A new sweep can be shorter than the last one; an index held past its end
    // would silently plot the final frequency while the readout claimed another.
    setIndex((current) => Math.min(current, Math.max(0, frequencies.length - 1)));
  }, [frequencies]);
  const available = POLAR_PLANES.filter((candidate) => items.some(({ result }) => result.directivity?.[candidate]?.length));
  const drawnPlane = available.includes(plane) ? plane : available[0];
  const option = useMemo(
    () => drawnPlane ? withCompactLegend(polarOption(items, tokens, drawnPlane, frequencies[index] ?? 0, POLAR_FLOOR_DB, density), items.length, density) : null,
    [density, drawnPlane, frequencies, index, items, tokens],
  );
  if (!drawnPlane || !option) return <ChartStub reason="Polar Response needs the horizontal or vertical plane. Turn one on under Directivity map in Simulation, then solve again."/>;
  return <div className="frequency-canvas">
    <EChart option={option} label={`Interactive HornLab ${drawnPlane} polar response`} live={live}/>
    <label className="frequency-scrub">
      <input aria-label="Polar frequency" type="range" min={0} max={Math.max(0, frequencies.length - 1)} value={index} onChange={(event) => { chosen.current = true; setIndex(Number(event.target.value)); }}/>
      <span>{formatPolarFrequency(frequencies[index])}</span>
      {available.length > 1 && <select aria-label="Polar plane" value={drawnPlane} onChange={(event) => setPlane(event.target.value as PolarPlane)}>
        {available.map((candidate) => <option key={candidate} value={candidate}>{candidate === 'horizontal' ? 'H' : 'V'}</option>)}
      </select>}
    </label>
  </div>;
}

export function formatPolarFrequency(value: number | undefined): string {
  if (!Number.isFinite(value)) return '—';
  return value! >= 1_000 ? `${(value! / 1_000).toFixed(value! >= 10_000 ? 1 : 2)} kHz` : `${Math.round(value!)} Hz`;
}

/**
 * Why a driver-coupled chart has nothing to draw.
 *
 * Power, current and excursion all exist only when a driver model was part of
 * the solve. The default waveguide solve is unit-acceleration -- no voltage, no
 * ohms, no cone -- so the honest answer is that this result has no drive, not
 * that something failed.
 *
 * `impedance_omitted` comes first because the unit-acceleration line is a lie
 * for the case that hits users most: an LR4 combined channel *is* voltage
 * driven, but `_combined_channel_response` pops its impedance block and leaves
 * `impedance_units: "Z/(rho*c)"` behind from `build_solver_response`, so the
 * tag check below reads it as an unloaded waveguide. The server already wrote
 * the real reason in that field; nothing had read it.
 */
export function driverChartMissingReason(result: ResultPayload, chart: string): string {
  const omitted = result.metadata?.impedance_omitted;
  if (typeof omitted === 'string' && omitted.trim()) {
    return `${chart} needs this channel's electrical impedance, which the solve did not produce -- ${omitted.trim()}.`;
  }
  if (!hasElectricalImpedance(result)) {
    return `${chart} needs a driver. Add one to this channel, then solve again.`;
  }
  if (!electricalDrive(result)) {
    return `This run did not record the drive voltage for ${chart}. Solve again to get it.`;
  }
  // Deliberately not "from this result's impedance samples": excursion shares
  // this branch and is read from the driver block, not derived from impedance.
  return `This run did not record the data for ${chart}. Solve again to get it.`;
}

/**
 * Why the group-delay chart has nothing to draw.
 *
 * `groupDelayMilliseconds` returns nothing for four different reasons and the
 * stub used to name only one of them. The propagation-reference case is the one
 * worth spelling out: it is a missing *metadata* field, not a missing sweep, so
 * a user told to add samples would rerun a longer solve and get the same stub.
 */
export function groupDelayMissingReason(result: ResultPayload): string {
  const samples = {
    frequencies: result.spl_on_axis?.frequencies?.length ? result.spl_on_axis.frequencies : result.frequencies,
    phaseDegrees: result.spl_on_axis?.phase_degrees ?? [],
  };
  if (!hasOnAxisPhase(samples.phaseDegrees)) {
    return 'This run has no on-axis phase, so Group Delay cannot be drawn. Solve again to get it.';
  }
  const reference = propagationReference(result);
  if (!reference) {
    const convention = result.metadata?.phase_time_convention;
    if (convention != null && phaseSpatialSign(convention) === null) {
      return 'This run was saved by another version of WG and its phase cannot be read, so Group Delay cannot be drawn. Solve again to get it.';
    }
    return 'This run did not record its measurement distance and speed of sound, so Group Delay cannot be drawn. Solve again to get it.';
  }
  const phase = deEmbeddedPhaseRadians(samples, reference);
  if (phase.length < 3) {
    return 'Group Delay needs at least three frequencies. Add frequencies to the sweep, then solve again.';
  }
  if (!phaseUnwrapIsResolved(phase, reference)) {
    return 'Group Delay needs a finer sweep: the phase turns too far between frequencies. Add frequencies, then solve again.';
  }
  return 'WG could not work out Group Delay from this run. Solve again to get it.';
}

/**
 * Cards about the drivers rather than about the radiated field.
 *
 * An LR4 sum has no electrical impedance, no drive and no cone, so in the
 * Combined view these three would draw their empty-state over a run that is
 * perfectly well driven. They show the members instead — which is also the more
 * useful reading in a single-driver view, where the member list is that driver
 * alone.
 */
const MEMBER_CHARTS = new Set<ChartType>(['impedance', 'drive_power', 'excursion']);

/**
 * The primary run's own entries: itself and, in the Combined view, its members.
 *
 * `named` is grouped by run in selection order and only a run's own channel is
 * ever primary, so the group ends at the next non-member entry. Power and
 * excursion are per-run cards; overlaying a comparison run's drivers on them
 * would be a comparison the card does not claim to be making.
 */
function primaryRunEntries(named: NamedResult[]): NamedResult[] {
  const next = named.findIndex((item, index) => index > 0 && !item.secondary);
  return next === -1 ? named : named.slice(0, next);
}

/**
 * Which of the selected entries a chart draws.
 *
 * The active view already chose one channel per run; what is left to decide is
 * what a card does with the members of a combined sum. Only the SPL chart draws
 * them beneath the sum (the classic crossover plot, and the one thing a
 * preference turns off), and only the driver cards treat them as ordinary
 * series. Everywhere else a member is a component of the curve on screen, not a
 * second opinion about it, and overlaying it would be drawing the same sound
 * twice.
 */
export function chartEntries(chartType: ChartType, named: NamedResult[], showMembers: boolean): NamedResult[] {
  if (chartType === 'frequency_response') return showMembers ? named : named.filter(({ secondary }) => !secondary);
  // Impedance is a comparable chart and overlays every selected run's drivers;
  // power and excursion describe one run, so they take the shown run's alone.
  if (chartType === 'impedance') return named;
  if (MEMBER_CHARTS.has(chartType)) return primaryRunEntries(named);
  return named.filter(({ secondary }) => !secondary);
}

/** Which cards read a measurement angle rather than the on-axis sample. */
const ANGLED_CHARTS = new Set<ChartType>(['frequency_response', 'phase_response']);

export interface MeasurementAngleSelection {
  plane: MeasurementPlane;
  /** Angles actually drawn, snapped to what this run sampled, on-axis first. */
  angles: number[];
  /** Every angle this run offers, for the control to list. */
  available: number[];
  planes: MeasurementPlane[];
  onAxis: number | null;
}

/**
 * Resolve a stored angle selection against the run in front of it.
 *
 * The preference outlives the result it was chosen for: a 30-degree pick made
 * against a 5-degree sweep must still mean something against a 10-degree one,
 * and against a run with no vertical plane at all. Snapping to the nearest
 * measured angle keeps a selection meaningful across re-solves instead of
 * silently emptying the chart every time the grid changes.
 */
export function resolveMeasurementSelection(
  result: ResultPayload,
  requestedPlane: MeasurementPlane,
  requestedAngles: number[],
): MeasurementAngleSelection {
  const planes = measurementPlanes(result);
  const plane = planes.includes(requestedPlane) ? requestedPlane : planes[0] ?? requestedPlane;
  const available = measurementAngles(result, plane);
  const axis = onAxisAngle(result, plane);
  const snapped = requestedAngles
    .map((angle) => nearestMeasurementAngle(result, plane, angle))
    .filter((angle): angle is number => angle !== null);
  const angles = [...new Set([...(axis === null ? [] : [axis]), ...snapped])].slice(0, MAX_MEASUREMENT_ANGLES);
  return { plane, angles, available, planes, onAxis: axis };
}

/**
 * One overlay entry per run per selected angle.
 *
 * On-axis alone is the default and keeps `items`' own labels, so the chart that
 * existed before this did is drawn by the same code path with the same names.
 * It still runs each result through `withMeasurementAngle`, which hands back
 * the identical object in every ordinary case -- but not for a secondary plane
 * on a grid that omits zero, where the stored `spl_on_axis` belongs to a
 * different observation point and must not be drawn as this plane's.
 *
 * Members of a combined sum are dropped as soon as a second angle is chosen:
 * the crossover branches at four angles each is eight curves answering two
 * different questions on one axis.
 */
export function measurementAngleEntries(
  items: NamedResult[],
  selection: MeasurementAngleSelection,
): NamedResult[] {
  const { plane, angles, onAxis } = selection;
  if (angles.length === 0) return items;
  if (angles.length === 1 && angles[0] === onAxis) {
    const anchored = items.map((item) => {
      const result = withMeasurementAngle(item.result, plane, angles[0]);
      return result === item.result ? item : { ...item, result };
    });
    return anchored.every((entry, index) => entry === items[index]) ? items : anchored;
  }
  return items.filter(({ secondary }) => !secondary).flatMap((item) => angles.map((angle) => ({
    ...item,
    id: `${item.id}@${plane}:${angle}`,
    label: `${item.label} · ${measurementAngleLabel(angle)}`,
    result: withMeasurementAngle(item.result, plane, angle),
  })));
}

/**
 * The angle strip under the SPL and phase cards.
 *
 * Angles are chips rather than a slider because the useful reading is a
 * comparison -- on-axis against 30 degrees is the question people actually ask
 * -- and a slider can only ever show one. The on-axis chip is always on: these
 * cards are the on-axis response with company, not a polar cut.
 */
function MeasurementAngleControls({ selection, onChange }: {
  selection: MeasurementAngleSelection;
  onChange: (next: { plane?: MeasurementPlane; angles?: number[] }) => void;
}) {
  const { plane, planes, available, angles, onAxis } = selection;
  if (available.length < 2) return null;
  const toggle = (angle: number) => {
    const next = angles.includes(angle)
      ? angles.filter((candidate) => candidate !== angle)
      : [...angles, angle].sort((left, right) => left - right);
    onChange({ angles: next.filter((candidate) => candidate !== onAxis).slice(0, MAX_MEASUREMENT_ANGLES - 1) });
  };
  const full = angles.length >= MAX_MEASUREMENT_ANGLES;
  return <div className="frequency-scrub measurement-angles">
    {planes.length > 1 && <select aria-label="Measurement plane" value={plane} onChange={(event) => onChange({ plane: event.target.value as MeasurementPlane })}>
      {planes.map((candidate) => <option key={candidate} value={candidate}>{candidate === 'horizontal' ? 'H' : candidate === 'vertical' ? 'V' : 'D'}</option>)}
    </select>}
    <div className="angle-chips" role="group" aria-label="Measurement angles">
      {available.map((angle) => {
        const active = angles.includes(angle);
        const locked = angle === onAxis;
        return <button
          key={angle}
          type="button"
          aria-pressed={active}
          disabled={locked || (!active && full)}
          title={locked ? 'On-axis is always drawn' : active ? `Hide ${measurementAngleLabel(angle)}` : `Overlay ${measurementAngleLabel(angle)}`}
          onClick={() => toggle(angle)}
        >{measurementAngleLabel(angle)}</button>;
      })}
    </div>
  </div>;
}

/**
 * The ms / cycles rail under the Group Delay card.
 *
 * The unit is still one preference -- the export, the report and the title chip
 * all read the same stored value, and the preferences popover still offers it --
 * but the place to *change* it is the card, next to the axis it renames. A unit
 * is chosen while looking at the curve, and a control two panels away in a
 * settings surface is one nobody finds while reading a chart.
 *
 * Chips rather than a select, because the choice is binary and both options fit:
 * this is the same glass rail as the measurement-angle strip, and pressing one
 * chip is the whole interaction.
 */
function GroupDelayUnitControl({ unit, onChange }: {
  unit: GroupDelayUnit;
  onChange: (next: GroupDelayUnit) => void;
}) {
  return <div className="frequency-scrub chart-unit-toggle">
    <div className="angle-chips" role="group" aria-label="Group delay unit">
      {GROUP_DELAY_UNITS.map(([id, label]) => <button
        key={id}
        type="button"
        aria-pressed={unit === id}
        // The chip is the axis unit itself; the long name lives in the title.
        title={`Read the group delay in ${label.toLowerCase()}`}
        onClick={() => onChange(id)}
      >{id}</button>)}
    </div>
  </div>;
}

interface RadiationArtifactView {
  status: 'absent' | 'failed' | 'loading' | 'loaded';
  message?: string;
  retry?: () => void;
}

function ResultChart({ chartType, result, named, tokens, density, live, beamShapeAction, radiationArtifact, wrapper, job, channelId }: { chartType: ChartType; result: ResultPayload; named: NamedResult[]; tokens: ChartTokens; density: ChartDensity; live: boolean; beamShapeAction?: ChartStubAction; radiationArtifact?: RadiationArtifactView } & SummarySourceProps) {
  const preferences = usePreferences();
  // The shown run owns its display reference, independently of live drafts.
  const normAngle = useRunNormAngle(job);
  // Only comparable and driver charts read the cross-job list. Keeping it in the
  // dependency array for every chart would rebuild expensive single-run
  // surfaces whenever the comparison selection changes.
  const overlays = useMemo(() => {
    if (!COMPARABLE_CHARTS.has(chartType) && !MEMBER_CHARTS.has(chartType)) return NO_NAMED_RESULTS;
    const entries = chartEntries(chartType, named, preferences.showMembersUnderCombined);
    if (entries.length) return entries;
    return COMPARABLE_CHARTS.has(chartType) ? [{ id: 'primary', label: 'Primary', result, primary: true }] : NO_NAMED_RESULTS;
  }, [chartType, named, preferences.showMembersUnderCombined, result]);
  // Which angles the SPL and phase cards read, resolved against this run.
  const measurementSelection = useMemo(
    () => resolveMeasurementSelection(result, preferences.measurementPlane, preferences.measurementAngles),
    [preferences.measurementAngles, preferences.measurementPlane, result],
  );
  const angled = useMemo(
    () => (ANGLED_CHARTS.has(chartType) ? measurementAngleEntries(overlays, measurementSelection) : overlays),
    [chartType, measurementSelection, overlays],
  );
  // Only the on-axis SPL chart carries measurements, and it is the only card
  // that should rebuild when one is loaded, hidden or nudged in level.
  const loadedMeasurements = useMeasuredOverlayStore((state) => state.overlays);
  const measured = useMemo(
    () => chartType === 'frequency_response'
      ? loadedMeasurements.filter(({ visible }) => visible)
      : NO_MEASURED_OVERLAYS,
    [chartType, loadedMeasurements],
  );
  // Only the Combined view has a reverse null to draw, and only while the
  // preference asks for it. `reverseNullTraces` withholds the overlay unless
  // it can rebuild the sum already on screen, so an empty list here is a
  // deliberate silence rather than a missing feature.
  const reverseNull = useMemo(
    () => (chartType === 'frequency_response' && preferences.showReverseNull
      ? reverseNullTraces(wrapper as ResultPayload | undefined, result, combineMetadataOf(result))
      : NO_REVERSE_NULL),
    [chartType, preferences.showReverseNull, result, wrapper],
  );
  // Frequencies whose power check failed are hatched on every frequency-axis
  // chart of the runs drawn; the curves themselves are never altered.
  const unqualified = useMemo<UnqualifiedBand[]>(
    () => chartUnqualifiedBands(overlays.length ? overlays : [{ id: 'primary', label: 'Primary', result, wrapper }]),
    [overlays, result, wrapper],
  );
  // Progress and log events replace the selected JobItem many times during a
  // solve. Keep those summary-only props outside the plot memo, otherwise a
  // new progress percentage rebuilds and repaints every EChart even when its
  // result snapshot has not changed.
  const plot = useMemo(() => {
    const mark = (option: EChartsOption) => withUnqualifiedBands(option, unqualified, tokens);
    // The angle strip is drawn only when there is more than one angle to offer.
    const railed = (option: EChartsOption) => (measurementSelection.available.length >= 2 ? clearingRail(option, density) : option);
    if (chartType === 'frequency_response') return result.spl_on_axis?.spl?.length
      ? <div className="frequency-canvas">
        <EChart option={railed(withCompactLegend(mark(splOption(angled, tokens, preferences.smoothing, density, measured, preferences.splPhase, reverseNull)), angled.length + measured.length, density))} label="Interactive HornLab sound pressure frequency response" live={live}/>
        <MeasurementAngleControls selection={measurementSelection} onChange={(next) => preferencesStore.update({
          ...(next.plane === undefined ? {} : { measurementPlane: next.plane }),
          ...(next.angles === undefined ? {} : { measurementAngles: next.angles }),
        })}/>
      </div>
      : <ChartStub reason="This run has no on-axis response yet. Wait for it to finish, or choose a completed run."/>;
    if (chartType === 'directivity_map_h' || chartType === 'directivity_map_v' || chartType === 'directivity_map_d' || chartType === 'directivity_map') return <DirectivityComparisonMaps chartType={chartType} items={overlays} tokens={tokens} mapReference={preferences.mapReference} normAngle={normAngle} angleGuideInterval={preferences.directivityGuideInterval} showContourNames={preferences.showContourNames} density={density} live={live}/>;
    if (chartType === 'directivity_index') {
      const option = withCompactLegend(mark(directivityIndexOption(overlays, tokens, preferences.smoothing, density)), overlays.length, density);
      return Array.isArray(option.series) && option.series.length ? <EChart option={option} label="Interactive HornLab directivity index by frequency" live={live}/> : <ChartStub reason="Directivity Index needs a complete spherical field. Choose an engine that provides one, then solve again."/>;
    }
    if (chartType === 'power_response') {
      const option = withCompactLegend(mark(powerResponseOption(overlays, tokens, preferences.smoothing, density)), overlays.length, density);
      return Array.isArray(option.series) && option.series.length
        ? <EChart option={option} label="Interactive HornLab spatially averaged power response from the solved spherical balloon" live={live}/>
        : <ChartStub reason="Power Response needs a complete spherical field. Choose an engine that provides one, then solve again."/>;
    }
    if (chartType === 'beam_shape') {
      if (result.beam_shape?.frequencies?.length) return <EChart option={mark(lineOption(beamShapeSeries(result), tokens, 'Beam width [°]', density))} label="Interactive HornLab horizontal and vertical forward beam width" live={live}/>;
      const missing = beamShapeMissingReason(result);
      return <ChartStub reason={missing.reason} action={missing.canEnable ? beamShapeAction : undefined}/>;
    }
    if (chartType === 'beam_fit') {
      const series = mark(beamFitOption(result, tokens, density));
      return Array.isArray(series.series) && series.series.length
        ? <EChart option={series} label="Interactive HornLab beam shape fit by frequency" live={live}/>
        : <ChartStub reason={beamShapeMissingReason(result).reason}/>;
    }
    if (chartType === 'beam_map') return <ForwardBeamRenderer result={result}/>;
    if (chartType === 'polar_response') return <PolarResponseCard items={overlays} tokens={tokens} density={density} live={live}/>;
    if (chartType === 'phase_response') {
      const option = railed(withCompactLegend(mark(phaseOption(angled, tokens, density)), angled.length, density));
      return Array.isArray(option.series) && option.series.length
        ? <div className="frequency-canvas">
          <EChart option={option} label="Interactive HornLab pressure phase by frequency" live={live}/>
          <MeasurementAngleControls selection={measurementSelection} onChange={(next) => preferencesStore.update({
          ...(next.plane === undefined ? {} : { measurementPlane: next.plane }),
          ...(next.angles === undefined ? {} : { measurementAngles: next.angles }),
        })}/>
        </div>
        : <ChartStub reason="This run has no directivity phase. Solve again to get it."/>;
    }
    if (chartType === 'group_delay') {
      const option = clearingRail(withCompactLegend(mark(groupDelayOption(overlays, tokens, density, preferences.groupDelayUnit)), overlays.length, density), density);
      // The unit rail only rides along with a curve: on the stub there is no
      // axis to rename, and offering the switch there would suggest the empty
      // card is the wrong projection rather than a result with no phase.
      return Array.isArray(option.series) && option.series.length
        ? <div className="frequency-canvas">
          <EChart option={option} label="Interactive HornLab on-axis group delay by frequency" live={live}/>
          <GroupDelayUnitControl unit={preferences.groupDelayUnit} onChange={(groupDelayUnit) => preferencesStore.update({ groupDelayUnit })}/>
        </div>
        : <ChartStub reason={groupDelayMissingReason(result)}/>;
    }
    if (chartType === 'drive_power') {
      // One member left standing is drawn as itself, so a two-way whose sum is
      // shown does not gain a legend entry it does not need.
      const option = mark(overlays.length > 1
        ? drivePowerOverlayOption(overlays, tokens, density)
        : drivePowerOption((overlays[0]?.result ?? result) as ResultPayload, tokens, density));
      return Array.isArray(option.series) && option.series.length
        ? <EChart option={option} label="Interactive HornLab electrical power and current draw by frequency" live={live}/>
        : <ChartStub reason={driverChartMissingReason(result, 'Power & Current Draw')}/>;
    }
    if (chartType === 'excursion') {
      const option = mark(overlays.length > 1
        ? excursionOverlayOption(overlays, tokens, density)
        : excursionOption((overlays[0]?.result ?? result) as ResultPayload, tokens, density));
      return Array.isArray(option.series) && option.series.length
        ? <EChart option={option} label="Interactive HornLab cone excursion by frequency" live={live}/>
        : <ChartStub reason={excursionSeries(result) ? 'WG could not read Cone Excursion from this run. Solve again to get it.' : driverChartMissingReason(result, 'Cone Excursion')}/>;
    }
    if (chartType === 'max_output') {
      const option = mark(maxOutputOption(result, tokens, density, memberLabelOf(result)));
      return Array.isArray(option.series) && option.series.length
        ? <EChart option={option} label="Interactive HornLab maximum on-axis SPL by frequency" live={live}/>
        : <ChartStub reason={maxOutputMissingReason(result)}/>;
    }
    if (chartType === 'balloon') return <BalloonRenderer result={result}/>;
    if (chartType === 'impedance') {
      const option = withCompactLegend(mark(impedanceOption(overlays, tokens, preferences.smoothing, density, preferences.impedanceDisplay)), overlays.length, density);
      // The spoken label follows the units for the same reason the axis does:
      // a driver-coupled run's ohms announced as "acoustic impedance" is the
      // screen-reader version of the mislabelled axis. The stub cannot follow
      // them -- with no impedance block there is no unit to read -- so it uses
      // the card's own unit-neutral name.
      const { units } = impedanceComparable(overlays);
      return Array.isArray(option.series) && option.series.length ? <EChart option={option} label={`Interactive HornLab ${units.spokenName} by frequency`} live={live}/> : <ChartStub reason="This run has no impedance. Add a driver to a channel, then solve again."/>;
    }
    if (chartType === 'radiation_impedance') {
      const presentation = wrapper?.radiation_impedance ?? result.radiation_impedance;
      if (radiationArtifact?.status === 'failed') return <ChartStub
        reason={`WG could not load Radiation Matrix Load. ${radiationArtifact.message ?? 'Select the run again.'}`}
        action={radiationArtifact.retry ? { label: 'Retry', onClick: radiationArtifact.retry } : undefined}
      />;
      if (radiationArtifact?.status === 'loading') return <ChartStub reason="Loading Radiation Matrix Load…"/>;
      if (!presentation) return <ChartStub reason="Radiation Matrix Load needs a passive cardioid solve. Run one to see this chart."/>;
      const option = radiationImpedanceOption(presentation, tokens, density);
      return Array.isArray(option.series) && option.series.length
        ? <EChart option={option} label="Interactive HornLab engineering-convention passive-cardioid radiation load by frequency" live={live}/>
        : <ChartStub reason="WG could not read the port data of this run. Solve it again."/>;
    }
    return null;
  }, [angled, beamShapeAction, chartType, density, live, measured, measurementSelection, normAngle, overlays, preferences.directivityGuideInterval, preferences.groupDelayUnit, preferences.impedanceDisplay, preferences.mapReference, preferences.showContourNames, preferences.smoothing, preferences.splPhase, radiationArtifact, result, tokens, unqualified, wrapper]);
  return plot ?? <Summary result={result} wrapper={wrapper} job={job} channelId={channelId} density={density}/>;
}

export interface CardMetrics {
  density: ChartDensity;
  /** Whether the card is wide enough for the chart's full name. */
  wide: boolean;
}

/**
 * Watches a card's box and reports only the two derived, discrete values the
 * chart cares about. Collapsing to an enum keeps drag-resizing from rebuilding
 * an ECharts option on every animation frame.
 */
export function useCardMetrics(target: React.RefObject<HTMLElement | null>): CardMetrics {
  const [metrics, setMetrics] = useState<CardMetrics>({ density: 'regular', wide: true });
  // Measured in a layout effect, before the browser paints, because the seed
  // state above is a guess: on a six-up grid every card is compact and narrow,
  // so waiting for the ResizeObserver's first callback painted one frame of
  // full-length titles ("Directivity index") and regular-density axes on cards
  // that cannot hold them, which read as a flicker on every mount.
  useLayoutEffect(() => {
    const element = target.current;
    if (!element) return;
    const apply = (width: number, height: number) => {
      if (!width || !height) return;
      const next: CardMetrics = { density: chartDensity(width, height), wide: width >= 300 };
      setMetrics((previous) => previous.density === next.density && previous.wide === next.wide ? previous : next);
    };
    const { width, height } = element.getBoundingClientRect();
    apply(width, height);
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => apply(entry.contentRect.width, entry.contentRect.height));
    observer.observe(element);
    return () => observer.disconnect();
  }, [target]);
  return metrics;
}

/** What a chart says about itself, on its card and in its detail view alike. */
function useChartDescription({ chartType, result, named, job }: { chartType: ChartType; result?: ResultPayload; named: NamedResult[] } & Pick<SummarySourceProps, 'job'>) {
  // The subtitle and map share the shown run's recorded reference.
  const normAngle = useRunNormAngle(job);
  const preferences = usePreferences();
  if (!result) return { samplingDisclosure: '', subtitle: null, unit: null };
  const samplingDisclosure = (COMPARABLE_CHARTS.has(chartType) && named.length
    ? named : [{ label: 'Shown result', result }])
    .flatMap(({ label, result: entry }) => samplingNotes(entry).length
      ? [`${label}: ${samplingNotes(entry)[1]}. ${samplingNotes(entry)[0]}`] : [])
    .join('\n');
  const polarStep = chartType.startsWith('directivity_map') ? resolvedPolarStepNotice(result) : null;
  // The same list ResultChart overlays, so the chip and the subtitle describe
  // the chart that is actually drawn rather than the primary run alone.
  const impedanceItems = chartType === 'impedance' ? (named.length ? named : [{ id: 'primary', label: 'Primary', result, primary: true }]) : undefined;
  // Naming the angle the map is referenced to is what makes the field on the
  // design side legible: the shift is a constant per row, so a heatmap gives no
  // other clue what its zero is. `clamped` is the one case the viewer cannot
  // infer at all -- an angle outside this run's sweep is answered at the
  // nearest measured one, not refused.
  const mapNorm = chartType.startsWith('directivity_map')
    ? resolveNormalizationAngle(result, normAngle)
    : null;
  const subtitle = chartType.startsWith('directivity_map')
    ? `ref ${preferencesStore.getSnapshot().mapReference} dB · 0 dB at ${Number(mapNorm!.angle.toFixed(3))}°${mapNorm!.clamped ? ' (nearest measured)' : ''}${polarStep ? ` · ${polarStep}` : ''}`
    : chartType === 'frequency_response'
      ? splSubtitle(result, resolveMeasurementSelection(result, preferences.measurementPlane, preferences.measurementAngles))
    : chartType === 'power_response' ? powerResponseMethodCaption(result)
    : impedanceItems ? impedanceSubtitle(impedanceItems)
    : chartType === 'radiation_impedance' ? 'engineering · exp(+jωt) · in-phase ports'
    // Which member holds the system back is the fact the curves cannot state,
    // and the small-signal caveat rides with it rather than being left for the
    // reader to infer from a ceiling drawn as a confident line.
    : chartType === 'max_output' ? limitingSummary(maxOutputOf(result), memberLabelOf(result))
    : null;
  const unit = chartUnit(chartType, result, impedanceItems, preferencesStore.getSnapshot().groupDelayUnit);
  return { samplingDisclosure, subtitle, unit };
}

/** Copy and export of whichever element holds the rendered chart. */
function useChartImage({ capture, chartType, job, channelId, background }: {
  capture: () => HTMLElement | null | undefined;
  chartType: ChartType;
  background: ChartTokens['background'];
} & Pick<SummarySourceProps, 'job' | 'channelId'>) {
  const [imageOperation, setImageOperation] = useState<'copy' | 'download' | null>(null);
  const [imageStatus, setImageStatus] = useState<string | null>(null);
  const imageStatusTimer = useRef<number | null>(null);
  useEffect(() => () => {
    if (imageStatusTimer.current !== null) window.clearTimeout(imageStatusTimer.current);
  }, []);
  const imageAction = async (operation: 'copy' | 'download') => {
    const target = capture();
    if (!target || imageOperation) return;
    setImageOperation(operation);
    setImageStatus(null);
    try {
      if (operation === 'copy') {
        await copyChartPng(target, background);
        setImageStatus('Copied PNG');
      } else {
        const filename = chartImageFilename(chartType, job, channelId);
        const destination = await askForExportDestination({ title: 'Export chart PNG', detail: filename });
        if (!destination) { setImageStatus('Export cancelled'); return; }
        await saveChartPng(
          target, filename, destination.token, background, askToReplaceExports,
        );
        setImageStatus('Saved PNG');
      }
    } catch (reason) {
      setImageStatus(reason instanceof ExportCancelledError
        ? 'Export cancelled'
        : operation === 'copy' ? 'WG could not copy this chart. Try again.' : 'WG could not export this chart. Try again.');
    } finally {
      setImageOperation(null);
      if (imageStatusTimer.current !== null) window.clearTimeout(imageStatusTimer.current);
      imageStatusTimer.current = window.setTimeout(() => setImageStatus(null), 1_600);
    }
  };
  return { imageOperation, imageStatus, imageAction };
}

function ChartCard({ index, chartType, result, named, tokens, live, beamShapeAction, radiationArtifact, wrapper, job, channelId, onExpand }: { index: number; chartType: ChartType; result: ResultPayload; named: NamedResult[]; tokens: ChartTokens; live: boolean; beamShapeAction?: ChartStubAction; radiationArtifact?: RadiationArtifactView; onExpand?: (index: number) => void } & SummarySourceProps) {
  // A comparison is active but this card cannot carry it. Saying so is the
  // whole point: silently drawing the primary run looks identical to drawing
  // both, so the user believes they are comparing when they are not. Members of
  // a combined sum are not a comparison and are not counted here.
  const compared = named.filter(({ secondary }) => !secondary);
  const comparisonIgnored = compared.length > 1 && !COMPARABLE_CHARTS.has(chartType);
  const { samplingDisclosure, subtitle, unit } = useChartDescription({ chartType, result, named, job });
  const [imageReady, setImageReady] = useState(false);
  const card = useRef<HTMLElement>(null);
  const { density, wide } = useCardMetrics(card);
  const { imageOperation, imageStatus, imageAction } = useChartImage({
    capture: () => card.current?.querySelector<HTMLElement>('.chart-placeholder'),
    chartType, job, channelId, background: tokens.background,
  });
  useLayoutEffect(() => {
    const element = card.current;
    if (!element || chartType === 'summary') { setImageReady(false); return; }
    const update = () => setImageReady(Boolean(element.querySelector('.chart-placeholder canvas')));
    update();
    const observer = new MutationObserver(update);
    observer.observe(element, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [chartType]);
  // Every card, not only the curves that can carry a hatched band: a map, a
  // polar cut or a balloon of an unqualified channel is just as unreliable.
  const unqualifiedNote = useMemo(
    () => unqualifiedCaption(named.length ? named : [{ id: 'primary', label: 'Primary', result, wrapper }]),
    [named, result, wrapper],
  );
  const unqualifiedFormulation = powerQualificationOf(result, wrapper ?? result)?.formulation ?? null;
  const activeLabel = compared.find((item) => item.result === result)?.label ?? compared[0]?.label ?? 'the primary run';
  return <section ref={card} className={`result-card result-${index}`} data-density={density}>
    <div className="chart-placeholder" title="Hover for values · double-click for detail" onDoubleClick={() => onExpand?.(index)}>
      <ResultChart chartType={chartType} result={result} named={named} tokens={tokens} density={density} live={live} beamShapeAction={beamShapeAction} radiationArtifact={radiationArtifact} wrapper={wrapper} job={job} channelId={channelId}/>
    </div>
    {/* Chrome floats over the plot rather than reserving a row of its own:
        a fixed header costs a quarter of a six-panel card's height. */}
    <div className="result-chrome">
      <span className="result-title">
        <b>{chartTitle(chartType, wide)}</b>
        {unit && <em>{unit}</em>}
        <Icon name="caret"/>
        <select aria-label={`Panel ${index + 1} chart type`} value={chartType} onChange={(event) => preferencesStore.setChartType(index, event.target.value as ChartType)}><ChartTypeOptions/></select>
      </span>
      {subtitle && density !== 'compact' && <span className="result-subtitle">{subtitle}</span>}
      {samplingDisclosure && <span className="result-unqualified" role="note" title={samplingDisclosure}>Adaptive · reconstructed estimates</span>}
      {unqualifiedNote && <span className="result-unqualified" role="note" title={`${unqualifiedNote}. ${unqualifiedMessage(unqualifiedFormulation)}`}>{density === 'compact' ? 'Unqualified' : unqualifiedNote}</span>}
      {comparisonIgnored && density !== 'compact' && <span className="result-single-run" title={`This chart shows one run at a time. Showing ${activeLabel}.`}>1 of {compared.length}</span>}
      <span className="result-chrome-spacer"/>
      {imageReady && <>
        <button className="result-card-image-action" type="button" disabled={imageOperation !== null} aria-label={`Copy panel ${index + 1} as PNG`} title="Copy chart image" onClick={() => void imageAction('copy')}><Icon name="copy"/></button>
        <button className="result-card-image-action" type="button" disabled={imageOperation !== null} aria-label={`Export panel ${index + 1} as PNG`} title="Export chart as PNG" onClick={() => void imageAction('download')}><Icon name="download"/></button>
      </>}
      <button className="result-card-expand" aria-label={`Expand panel ${index + 1}`} title="Open detail view" onClick={() => onExpand?.(index)}><Icon name="expand"/></button>
      <button className="result-card-close" aria-label={`Close panel ${index + 1}`} title="Close chart" onClick={() => preferencesStore.closeChart(index)}><Icon name="close"/></button>
      {imageStatus && <span className="result-image-status" role="status">{imageStatus}</span>}
    </div>
  </section>;
}

function ChartTypeOptions() {
  return <>{CHART_TYPES.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}</>;
}

const detailInitialFocus = (dialog: HTMLElement) => dialog.querySelector<HTMLElement>('.result-detail-close');

/** A step of a value that lives elsewhere: this view only asks for the next one. */
function DetailStep({ direction, disabled, label, onClick }: { direction: 'previous' | 'next'; disabled: boolean; label: string; onClick: () => void }) {
  return <button type="button" className={`result-detail-step ${direction}`} disabled={disabled} aria-label={label} title={label} onClick={onClick}><Icon name="caret"/></button>;
}

interface ChartDetailData extends SummarySourceProps {
  result: ResultPayload;
  named: NamedResult[];
  live: boolean;
  beamShapeAction?: ChartStubAction;
  radiationArtifact?: RadiationArtifactView;
}

/**
 * One chart panel, large. It owns no result of its own: the run is the compare
 * selection, the chart is the panel's entry in the preferences, and the dock's
 * snapshot is what it draws, so moving here moves the rail and the grid too.
 * It lives above the grid because the grid goes away for the moment a run from
 * another model takes to arrive, and this view has to stay open through that.
 */
export function ChartDetailDialog({ index, chartTypes, tokens, data, unavailable, runs, runId, runLabel, runError, viewSwitch, onShowRun, onShowPanel, onClose }: {
  index: number;
  chartTypes: ChartType[];
  tokens: ChartTokens;
  data: ChartDetailData | null;
  /** What to say instead of a chart; null while the run is on its way. */
  unavailable: string | null;
  runs: JobItem[];
  runId: string | null;
  runLabel: (job: JobItem) => string;
  /** Why the last move to another run did not happen. */
  runError?: string | null;
  viewSwitch?: ReactNode;
  onShowRun: (job: JobItem) => void;
  onShowPanel: (index: number) => void;
  onClose: () => void;
}) {
  const chartType = chartTypes[index];
  const dialog = useModalDialogFocus<HTMLElement>({ open: true, onClose, initialFocus: detailInitialFocus });
  const { samplingDisclosure, subtitle } = useChartDescription({ chartType, result: data?.result, named: data?.named ?? NO_NAMED_RESULTS, job: data?.job });
  // The dialog holds the rendered chart while it is open, so capture from here
  // rather than from the card behind it: a large view must hand back its own image.
  const { imageOperation, imageStatus, imageAction } = useChartImage({
    capture: () => dialog.current?.querySelector<HTMLElement>('.result-detail-chart'),
    chartType, job: data?.job, channelId: data?.channelId, background: tokens.background,
  });
  const runIndex = runs.findIndex((job) => job.id === runId);
  const showRun = (job: JobItem | undefined) => {
    if (job && job.id !== runId) onShowRun(job);
  };
  const stepRun = (delta: number) => { if (runIndex >= 0) showRun(runs[runIndex + delta]); };
  const stepPanel = (delta: number) => {
    const next = index + delta;
    if (next >= 0 && next < chartTypes.length) onShowPanel(next);
  };
  const step = useRef({ stepRun, stepPanel });
  step.current = { stepRun, stepPanel };
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      // A slider, a list and a text field already answer to the arrows.
      if (event.target instanceof Element && event.target.closest('input, select, textarea')) return;
      if (event.key === 'ArrowLeft') step.current.stepRun(-1);
      else if (event.key === 'ArrowRight') step.current.stepRun(1);
      else if (event.key === 'ArrowUp') step.current.stepPanel(-1);
      else if (event.key === 'ArrowDown') step.current.stepPanel(1);
      else return;
      event.preventDefault();
    };
    window.addEventListener('keydown', keydown);
    return () => window.removeEventListener('keydown', keydown);
  }, []);
  const shownRun = runs[runIndex];
  return createPortal(<div className="result-detail-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={dialog} className="result-detail" role="dialog" aria-modal="true" aria-label={`${chartLabel(chartType)} detail`}>
      <header>
        {runs.length > 0 && <span className="result-detail-nav" role="group" aria-label="Run">
          {runs.length > 1 && <DetailStep direction="previous" disabled={runIndex <= 0} label="Previous run (←)" onClick={() => stepRun(-1)}/>}
          <span className="result-detail-pick run">
            <span>{shownRun ? middleEllipsis(runLabel(shownRun)) : 'Choose a run'}</span>
            <Icon name="caret"/>
            <select aria-label="Run shown" value={runId ?? ''} onChange={(event) => showRun(runs.find((job) => job.id === event.target.value))}>
              {runIndex < 0 && <option value={runId ?? ''} disabled>Choose a run</option>}
              {runs.map((job) => <option key={job.id} value={job.id}>{runLabel(job)}</option>)}
            </select>
          </span>
          {runs.length > 1 && <DetailStep direction="next" disabled={runIndex < 0 || runIndex >= runs.length - 1} label="Next run (→)" onClick={() => stepRun(1)}/>}
        </span>}
        <span className="result-detail-nav" role="group" aria-label="Chart">
          {chartTypes.length > 1 && <DetailStep direction="previous" disabled={index === 0} label="Previous chart panel (↑)" onClick={() => stepPanel(-1)}/>}
          <span className="result-detail-pick">
            <b>{chartLabel(chartType)}</b>
            <Icon name="caret"/>
            <select aria-label="Chart shown in this panel" value={chartType} onChange={(event) => preferencesStore.setChartType(index, event.target.value as ChartType)}><ChartTypeOptions/></select>
          </span>
          {chartTypes.length > 1 && <DetailStep direction="next" disabled={index === chartTypes.length - 1} label="Next chart panel (↓)" onClick={() => stepPanel(1)}/>}
        </span>
        {viewSwitch}
        <div className="result-detail-summary">
          <span>{runError ?? subtitle}</span>
          <small>Hover to inspect · Ctrl/scroll to zoom lines · ←→ run · ↑↓ chart</small>
        </div>
        {/* The same actions the card carries: a view that shows the chart
            larger should not offer less to do with it. */}
        {data && chartType !== 'summary' && <>
          <button className="result-card-image-action" type="button" disabled={imageOperation !== null} aria-label="Copy chart as PNG" title="Copy chart image" onClick={() => void imageAction('copy')}><Icon name="copy"/></button>
          <button className="result-card-image-action" type="button" disabled={imageOperation !== null} aria-label="Export chart as PNG" title="Export chart as PNG" onClick={() => void imageAction('download')}><Icon name="download"/></button>
        </>}
        {imageStatus && <span className="result-image-status" role="status">{imageStatus}</span>}
        <button type="button" className="result-detail-close" aria-label="Close detail view" onClick={onClose}><Icon name="close"/></button>
      </header>
      <div className="result-detail-chart">
        {data
          ? <>{samplingDisclosure && <p role="note">{samplingDisclosure}</p>}<ResultChart key={chartType} chartType={chartType} result={data.result} named={data.named} tokens={tokens} density="full" live={data.live} beamShapeAction={data.beamShapeAction} radiationArtifact={data.radiationArtifact} wrapper={data.wrapper} job={data.job} channelId={data.channelId}/></>
          : <EmptyState className="result-detail-empty" title={unavailable ?? 'Loading this run…'}/>}
      </div>
    </section>
  </div>, document.body);
}

export function resultLayoutClass(count: number): string {
  return `result-layout-${Math.max(0, Math.min(MAX_RESULT_PANELS, Math.floor(count)))}`;
}

export function ResultsChartGrid({ chartTypes, result, named, tokens, live = false, beamShapeAction, radiationArtifact, wrapper, job, channelId, onExpand }: {
  chartTypes: ChartType[];
  result: ResultPayload;
  named: NamedResult[];
  tokens: ChartTokens;
  live?: boolean;
  beamShapeAction?: ChartStubAction;
  radiationArtifact?: RadiationArtifactView;
  onExpand?: (index: number) => void;
} & SummarySourceProps) {
  if (!chartTypes.length) {
    return <EmptyState className="result-grid-empty" title="No charts open" detail="Add a chart to rebuild the results workspace." action={<button className="btn-primary" onClick={() => preferencesStore.addChart()}>+ Add chart</button>}/>;
  }
  return <div className={`result-grid ${resultLayoutClass(chartTypes.length)}`} data-chart-count={chartTypes.length}>
    {chartTypes.map((chartType, index) => <ChartCard key={`${index}-${chartType}`} index={index} chartType={chartType} result={result} named={named} tokens={tokens} live={live} beamShapeAction={beamShapeAction} radiationArtifact={radiationArtifact} wrapper={wrapper} job={job} channelId={channelId} onExpand={onExpand}/>) }
  </div>;
}

interface ResultDisplaySnapshot {
  key: string;
  primaryId: string;
  ids: string[];
  results: Record<string, ResultPayload>;
  provisionalIds: string[];
}

interface ResultFetchError {
  key: string;
  message: string;
}

/**
 * One row per loaded measurement, in the same strip the crossover editor uses.
 *
 * The offset is a plain number input rather than a slider: matching a measured
 * curve to a simulated one is an arithmetic step ("this mic chain reads 6.2 dB
 * low at 2.83 V/1 m"), not something to find by dragging.
 */
function MeasuredOverlayRow({ overlay }: { overlay: MeasuredOverlay }) {
  const store = useMeasuredOverlayStore;
  // The field holds text so it can be emptied mid-edit; only a parseable value
  // reaches the store, so a half-typed "-" never redraws the chart at 0 dB.
  const [offsetText, setOffsetText] = useState(String(overlay.offsetDb));
  const span = `${Math.round(overlay.points[0].frequencyHz)}–${Math.round(overlay.points.at(-1)!.frequencyHz)} Hz · ${overlay.points.length} pts`;
  return <span className="result-measured-item" data-hidden={overlay.visible ? undefined : ''}>
    <button
      type="button"
      className="result-measured-toggle"
      aria-pressed={overlay.visible}
      title={overlay.visible ? `Hide ${overlay.label} on the on-axis SPL chart` : `Show ${overlay.label} on the on-axis SPL chart`}
      onClick={() => store.getState().toggleVisible(overlay.id)}
    ><i/><span title={`${overlay.label} — ${span}`}>{middleEllipsis(overlay.label, 22)}</span></button>
    <label className="result-measured-offset">
      <input
        type="number"
        step={0.5}
        value={offsetText}
        aria-label={`${overlay.label} level offset in decibels`}
        title="Shift this measurement vertically. The file is not modified."
        onChange={(event) => {
          setOffsetText(event.target.value);
          const offsetDb = Number(event.target.value);
          if (event.target.value.trim() && Number.isFinite(offsetDb)) store.getState().setOffsetDb(overlay.id, offsetDb);
        }}
      />
      <span>dB</span>
    </label>
    <button className="btn" type="button" aria-label={`Remove ${overlay.label} overlay`} title="Remove this overlay" onClick={() => store.getState().remove(overlay.id)}>×</button>
  </span>;
}

export function ResultsPanel() {
  const jobsSnapshot = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot);
  const jobs = jobsSnapshot.jobs;
  const provisional = useSyncExternalStore(provisionalResults.subscribe, provisionalResults.getSnapshot, provisionalResults.getSnapshot);
  const coordinator = useSyncExternalStore(jobsCoordinatorBridge.subscribe, jobsCoordinatorBridge.getSnapshot, jobsCoordinatorBridge.getSnapshot);
  const selection = useSyncExternalStore(compareSelection.subscribe, compareSelection.getSnapshot, compareSelection.getSnapshot);
  const coherenceContext = useCurrentModel();
  const preferences = usePreferences();
  const tokens = useChartTokens();
  const [display, setDisplay] = useState<ResultDisplaySnapshot | null>(null);
  const [fetchError, setFetchError] = useState<ResultFetchError | null>(null);
  const [fetchAttempt, setFetchAttempt] = useState(0);
  const [radiationArtifactLoads, setRadiationArtifactLoads] = useState<Record<string, RadiationArtifactView>>({});
  const [preferencesOpen, setPreferencesOpen] = useState(false);
  const preferencesAnchor = useRef<HTMLButtonElement | null>(null);
  const [beamRerunSubmitting, setBeamRerunSubmitting] = useState(false);
  const [coherenceOpen, setCoherenceOpen] = useState(false);
  const [powerOpen, setPowerOpen] = useState(false);
  const [modelLoad, setModelLoad] = useState<'idle' | 'loading' | 'failed'>('idle');
  const coherenceAnchor = useRef<HTMLButtonElement | null>(null);
  const [dismissedNewRun, setDismissedNewRun] = useState<string | null>(null);
  const [detailPanel, setDetailPanel] = useState<number | null>(null);
  const closeDetail = useCallback(() => setDetailPanel(null), []);
  /** The run whose model the detail view is putting on screen right now. */
  const [detailSwitch, setDetailSwitch] = useState<string | null>(null);
  const [detailRunError, setDetailRunError] = useState<string | null>(null);
  const runFilters = useRunFilters();
  const view = useResultView();
  const measuredOverlays = useMeasuredOverlayStore((state) => state.overlays);
  const [measuredError, setMeasuredError] = useState<string | null>(null);
  const measuredInput = useRef<HTMLInputElement | null>(null);

  const resultsSeenFor = useRef<string | null>(null);
  const held = jobs.find(job => job.id === selection.primary);
  if (held?.has_results) resultsSeenFor.current = held.id;
  else if (held?.id !== resultsSeenFor.current) resultsSeenFor.current = null;
  const resolved = shownResult(coherenceContext, { ...selection, resultsSeenFor: resultsSeenFor.current }, jobs, provisional.entries);
  const latest = resolved.latest;
  const effectivePrimary = resolved.job?.id ?? null;
  useEffect(() => {
    if (resolved.releaseSelection || ((!selection.primary || selection.following) && selection.primary !== effectivePrimary)) {
      resultsSeenFor.current = null;
      compareSelection.followLatest(effectivePrimary);
    }
  }, [effectivePrimary, selection.primary, selection.following, resolved.releaseSelection]);
  // Awaiting is lifecycle/attention state; result identity belongs to shownResult.
  useEffect(() => {
    if (!selection.awaiting) return;
    const awaited = jobs.find(job => job.id === selection.awaiting);
    if (awaited && (awaited.has_results || provisional.entries[awaited.id])) {
      if (runVerdict(awaited, coherenceContext) === 'current') compareSelection.followLatest(awaited.id);
      else compareSelection.setPrimary(awaited.id);
      solveAttention.resultsReady(awaited.id);
    } else if (awaited && (awaited.status === 'error' || awaited.status === 'cancelled' || awaited.status === 'held')) compareSelection.awaitRun(null);
  }, [selection.awaiting, jobs, provisional, coherenceContext.key, coherenceContext.viewportShowing]);
  const ids = resolved.ids;
  const selectionKey = ids.join('\u0000');
  const resultSourceKey = ids.map((id) => {
    const job = jobs.find((item) => item.id === id);
    const liveRevision = job && job.status !== 'complete' ? provisional.entries[id]?.revision ?? 0 : 0;
    return `${id}:${job?.status ?? 'missing'}:${job?.has_results ? 1 : 0}:${job?.has_radiation_impedance_artifact ? 1 : 0}:${liveRevision}`;
  }).join('\u0000');
  useEffect(() => {
    let live = true;
    const requestedIds = selectionKey ? selectionKey.split('\u0000') : [];
    if (!requestedIds.length || !effectivePrimary) { setDisplay(null); setFetchError(null); return; }
    setFetchError(null);
    // Results that were cleaned up are gone from disk: asking for them is a
    // 404. Clear the outgoing charts and let the dock say why instead.
    const primaryRun = jobs.find((item) => item.id === effectivePrimary);
    if (primaryRun && primaryRun.status === 'complete' && !primaryRun.has_results && !provisional.entries[primaryRun.id]) {
      setDisplay(null);
      return;
    }
    void Promise.all(requestedIds.map(async (id) => {
      const job = jobs.find((item) => item.id === id);
      const provisionalEntry = job?.status !== 'complete' ? provisional.entries[id] : undefined;
      if (provisionalEntry) return { id, result: provisionalEntry.result as ResultPayload, isProvisional: true, radiationArtifact: { status: 'absent' as const } };
      const result = await fetchJobResults(id) as ResultPayload;
      if (!job?.has_radiation_impedance_artifact) return { id, result, isProvisional: false, radiationArtifact: { status: 'absent' as const } };
      try {
        const presentation = await fetchRadiationImpedancePresentation(id);
        return {
          id,
          result: presentation ? { ...result, radiation_impedance: presentation } : result,
          isProvisional: false,
          radiationArtifact: { status: presentation ? 'loaded' as const : 'absent' as const },
        };
      } catch (reason) {
        // The matrix is optional. A corrupt or temporarily unavailable
        // artifact must not make otherwise valid SPL/directivity disappear.
        return {
          id,
          result,
          isProvisional: false,
          radiationArtifact: {
            status: 'failed' as const,
            message: failureWords(reason),
          },
        };
      }
    }))
      .then((pairs) => {
        if (!live) return;
        setDisplay({
          key: selectionKey,
          primaryId: effectivePrimary!,
          ids: requestedIds,
          results: Object.fromEntries(pairs.map(({ id, result }) => [id, result])),
          provisionalIds: pairs.filter(({ isProvisional }) => isProvisional).map(({ id }) => id),
        });
        setRadiationArtifactLoads((current) => ({
          ...current,
          ...Object.fromEntries(pairs.map(({ id, radiationArtifact }) => [id, radiationArtifact])),
        }));
        pairs.forEach(({ id, isProvisional }) => {
          if (!isProvisional) provisionalResults.remove(id);
        });
      })
      .catch((reason) => {
        if (!live) return;
        // Never leave the outgoing charts under the incoming job's toolbar.
        setDisplay(null);
        setFetchError({
          key: selectionKey,
          message: failureWords(reason),
        });
      });
    return () => { live = false; };
  }, [effectivePrimary, selectionKey, resultSourceKey, fetchAttempt]);

  const currentDisplay = display?.key === selectionKey ? display : null;
  const primaryRaw = effectivePrimary && currentDisplay
    ? currentDisplay.results[effectivePrimary]
    : undefined;
  // One view for the whole dock: the chosen channel where the run has it, its
  // combined sum otherwise, and its first channel when it has neither. The
  // fallback is resolved per run so a comparison keeps drawing something and
  // says on the label which channel it substituted.
  const activeChannel = primaryRaw ? resolveResultView(primaryRaw, view) : null;
  const primary = activeChannel && primaryRaw?.channels
    ? primaryRaw.channels[activeChannel] as ResultPayload
    : primaryRaw;
  // One keyed snapshot owns the primary and every overlay. During a transition
  // the complete outgoing set may remain visible, but no incoming result is
  // combined with it; the whole set swaps only after Promise.all succeeds.
  const displayed = shownResult(coherenceContext, { primary: display?.primaryId ?? null, overlays: display?.ids.slice(1) }, jobs, provisional.entries);
  const shownRaw = effectivePrimary && display && displayed.canRender && displayed.job?.id === display.primaryId
    ? display.results[display.primaryId]
    : undefined;
  const shownActiveChannel = shownRaw ? resolveResultView(shownRaw, view) : null;
  const shown = shownActiveChannel && shownRaw?.channels
    ? shownRaw.channels[shownActiveChannel] as ResultPayload
    : shownRaw;
  // The pre-solve rail shows what "auto" chose for gain and delay, and only a
  // solved result knows those numbers. The dock already holds one, so it hands
  // it over rather than making the rail fetch results of its own.
  //
  // Read from the run's combined channel rather than from the channel the dock
  // happens to be displaying: looking at one member's excursion is no reason
  // for the rail's automatic gains and delays to blank out, and the recombine
  // this bridge carries has to name the combined channel in any case.
  const shownCombinedChannel = shownRaw ? combinedChannelId(shownRaw) : null;
  const shownCombine = combineMetadataOf(
    shownCombinedChannel && shownRaw?.channels
      ? shownRaw.channels[shownCombinedChannel] as ResultPayload
      : shownRaw,
  );
  // The viewport draws its measurement rig on the frame the shown run
  // published. The dock already holds that payload, so it hands the frame over
  // rather than making the viewport fetch a result of its own -- the same
  // arrangement the pre-solve rail uses just above.
  const shownLabel = display?.primaryId ? labelFor(display.primaryId, jobs) : null;
  const applyRecombined = useCallback((jobId: string, updated: JobResults) => {
    setDisplay((current) => current && current.results[jobId]
      ? { ...current, results: { ...current.results, [jobId]: updated as ResultPayload } }
      : current);
  }, []);
  const displayLabels = display?.ids.map((id) => labelFor(id, jobs)).join('\u0000') ?? '';
  const named = useMemo(
    () => display?.ids.flatMap((id, index) => {
      if (!shownRaw || !displayed.ids.includes(id)) return [];
      // `ids` is [primary, ...overlays], so index 0 is the run the panel is
      // built around. Flagging its entries here is what lets every chart find
      // the primary without trusting its own, possibly filtered, ordering.
      const channels = selectResultChannels(
        id,
        displayLabels.split('\u0000')[index],
        display.results[id],
        view,
        jobs.find(job => job.id === id)?.config_summary.import_origin,
      );
      return index === 0 ? channels.map((entry) => ({ ...entry, primary: true })) : channels;
    }) ?? NO_NAMED_RESULTS,
    [display, displayLabels, view, jobs, selectionKey, coherenceContext.key, coherenceContext.kind, coherenceContext.viewportShowing, coherenceContext.unloadedRunId],
  );
  const error = fetchError?.key === selectionKey ? fetchError.message : null;
  const showingPrevious = Boolean(selection.primary && display && display.key !== selectionKey && !error);
  const available = useMemo(() => jobs.filter((job) => job.status === 'complete' && job.has_results && !ids.includes(job.id)), [ids, jobs]);
  const primaryJob = jobs.find(job => job.id === selection.primary) ?? resolved.job;
  const meshFreshness = useMeshFreshnessStore(state => state.jobs);
  useEffect(() => {
    if (primaryJob) useMeshFreshnessStore.getState().check(primaryJob, false, jobsSnapshot.epoch);
    if (resolved.job) useMeshFreshnessStore.getState().check(resolved.job, false, jobsSnapshot.epoch);
    for (const id of selection.overlays ?? []) {
      const job = jobs.find(candidate => candidate.id === id);
      if (job) useMeshFreshnessStore.getState().check(job, false, jobsSnapshot.epoch);
    }
  }, [primaryJob, resolved.job, jobs, selection.overlays, jobsSnapshot.epoch]);
  const staleMeshes = [...new Set([primaryJob?.id, resolved.job?.id, ...(selection.overlays ?? [])])]
    .flatMap(id => {
      const job = jobs.find(candidate => candidate.id === id);
      return job && hasMeshProvenance(job) && meshFreshness[job.id] === 'stale' ? [job] : [];
    });
  const primaryVerdict = primaryJob ? runVerdict(primaryJob, coherenceContext) : 'current';
  // The selected run finished but its results were cleaned up: nothing to
  // fetch or draw, and the dock says so rather than showing another run.
  const cleanedUpRun = primaryJob && primaryJob.status === 'complete' && !primaryJob.has_results && !provisional.entries[primaryJob.id]
    ? primaryJob
    : null;
  // The menu belongs to one run under one verdict; solving, restoring or
  // switching runs answers it, so it must not stay open over its own answer.
  useEffect(() => { setCoherenceOpen(false); }, [primaryVerdict, selection.primary]);
  useEffect(() => { setModelLoad('idle'); }, [selection.primary]);
  // A finished run nobody here asked for is offered, not imposed. Both orders
  // are checked because run numbers restart with a fresh jobs database, and
  // both are needed because two runs can share a timestamp to the second.
  const newRun = useMemo(() => {
    if (!latest || !primaryJob || latest.id === selection.primary || latest.id === dismissedNewRun) return null;
    const newer = (latest.run_number ?? 0) > (primaryJob.run_number ?? 0)
      || Date.parse(latest.created_at) > Date.parse(primaryJob.created_at);
    return newer ? latest : null;
  }, [dismissedNewRun, latest, primaryJob, selection.primary]);
  const selectedJob = useMemo(() => jobs.find((job) => job.id === display?.primaryId) ?? null, [display?.primaryId, jobs]);
  const cadResultMatchesViewport = Boolean(resolved.job && shownRaw);
  useEffect(() => {
    useObservationStore.getState().adopt(shownRaw, shownRaw ? shownLabel : null);
  }, [shownLabel, shownRaw]);
  const primaryIsProvisional = Boolean(display?.provisionalIds.includes(display.primaryId));
  const shownIsActiveReturn = Boolean(shownRaw && resolved.job && coherenceContext.kind !== 'parametric' && !resolved.modelNotLoaded);
  const shownCanApply = selectedJob?.status === 'complete'
    && !primaryIsProvisional
    && shownIsActiveReturn;
  const showPrimaryModel = useCallback(() => {
    if (!primaryJob) return;
    setModelLoad('loading');
    workspaceNavigation.navigate('geometry');
    void showJobModel(primaryJob, coordinator.reportError)
      .then((ok) => setModelLoad(ok ? 'idle' : 'failed'))
      .catch((reason) => { coordinator.reportError(failureWords(reason)); setModelLoad('failed'); });
  }, [coordinator.reportError, primaryJob]);
  // Why the rail may not repaint this run. Stated from the dock because the
  // facts are the dock's -- run status, live preview, whose ingestion it is --
  // and because only one of the three has an action attached to it.
  const shownBlock = useMemo<{ reason: string; recall: (() => void) | null } | null>(() => {
    if (shownCanApply) return null;
    if (selectedJob && selectedJob.status !== 'complete') {
      return { reason: 'The shown run is still solving; crossover changes apply once it finishes.', recall: null };
    }
    if (primaryIsProvisional) {
      return { reason: 'The shown run is a live preview of a solve in progress; crossover changes apply to it once that solve finishes.', recall: null };
    }
    return {
      reason: 'The shown run was solved from a prepared model this session has not loaded, so the rail is not its crossover yet. Load its model and the changes apply without re-solving.',
      recall: primaryJob ? showPrimaryModel : null,
    };
  }, [primaryIsProvisional, primaryJob, selectedJob, showPrimaryModel, shownCanApply]);
  useEffect(() => {
    latestCombine.publish(shownCombine && shownCombinedChannel && display ? {
      jobId: display.primaryId,
      channelId: shownCombinedChannel,
      combine: shownCombine,
      solvedBandHz: shownRaw?.frequencies?.length
        ? [shownRaw.frequencies[0], shownRaw.frequencies[shownRaw.frequencies.length - 1]]
        : undefined,
      canApply: shownCanApply,
      blockedReason: shownBlock?.reason ?? null,
      recall: shownBlock?.recall ?? null,
      onApplied: applyRecombined,
    } : null);
  }, [shownCombine, shownCombinedChannel, display, shownCanApply, shownBlock, applyRecombined]);
  const provisionalMetadata = primaryRaw?.metadata?.provisional;
  const provisionalRecord = provisionalMetadata && typeof provisionalMetadata === 'object'
    ? provisionalMetadata as Record<string, unknown>
    : {};
  const liveCompleted = Number(provisionalRecord.completed_frequency_count)
    || (primaryRaw?.frequency_status ? primaryRaw.frequency_status.filter((status) => status === 'solved').length : 0)
    || (primaryRaw?.frequency_status ? 0 : (display?.primaryId ? provisional.entries[display.primaryId]?.revision : 0))
    || (primaryRaw?.frequency_status ? 0 : primaryRaw?.frequencies?.length)
    || 0;
  const liveExpected = Number(provisionalRecord.expected_frequency_count)
    || Number(selectedJob?.solve_options.num_frequencies)
    || Number(selectedJob?.config_summary.num_frequencies)
    || 0;
  const selectedJobCanRerun = useMemo(
    // Imported runs are rerun through the model solve path, not here.
    () => Boolean(selectedJob && !selectedJob.cad_source && !selectedJob.cad_setup && hydrateJobDesign(selectedJob)),
    [selectedJob],
  );
  const enableBalloonAndRerun = useCallback(() => {
    if (!selectedJob) return;
    // The new solve is a separate job. Follow the latest result so this card
    // replaces the old empty state as soon as that job completes, even when
    // the source result had been pinned for comparison.
    setBeamRerunSubmitting(true);
    const sourceId = selectedJob.id;
    void coordinator.continueRun(sourceId, { options: { polar_config: { spherical_sampling: true } } })
      .catch(() => undefined) // The coordinator reports submission errors.
      .finally(() => setBeamRerunSubmitting(false));
  }, [coordinator, selectedJob]);
  const beamShapeAction = useMemo<ChartStubAction | undefined>(() => selectedJobCanRerun ? {
    label: 'Enable & rerun',
    onClick: enableBalloonAndRerun,
    disabled: beamRerunSubmitting,
    busy: beamRerunSubmitting,
  } : undefined, [beamRerunSubmitting, enableBalloonAndRerun, selectedJobCanRerun]);
  const retryRadiationArtifact = useCallback((jobId: string) => {
    setRadiationArtifactLoads((current) => ({ ...current, [jobId]: { status: 'loading' } }));
    void fetchRadiationImpedancePresentation(jobId)
      .then((presentation) => {
        if (presentation) {
          setDisplay((current) => current?.results[jobId]
            ? { ...current, results: { ...current.results, [jobId]: { ...current.results[jobId], radiation_impedance: presentation } } }
            : current);
        }
        setRadiationArtifactLoads((current) => ({
          ...current,
          [jobId]: { status: presentation ? 'loaded' : 'absent' },
        }));
      })
      .catch((reason) => setRadiationArtifactLoads((current) => ({
        ...current,
        [jobId]: { status: 'failed', message: failureWords(reason) },
      })));
  }, []);
  const radiationArtifact = selectedJob ? radiationArtifactLoads[selectedJob.id] : undefined;
  const radiationArtifactView = useMemo<RadiationArtifactView | undefined>(() => radiationArtifact ? {
    ...radiationArtifact,
    ...(radiationArtifact.status === 'failed' && selectedJob
      ? { retry: () => retryRadiationArtifact(selectedJob.id) }
      : {}),
  } : undefined, [radiationArtifact, retryRadiationArtifact, selectedJob]);
  const powerHealth = shown
    ? powerAgreementHealth(shown, shownRaw ?? shown)
    : null;
  const powerFlags = shown ? powerQualificationOf(shown, shownRaw ?? shown) : null;
  const powerChannelName = useCallback((id: string) => shownRaw ? channelLabel(shownRaw, id, selectedJob?.config_summary.import_origin) : id, [shownRaw, selectedJob]);
  const solve = useOptionalSolveControl();
  // Everything above keeps following the solve while this panel is covered --
  // results are still fetched, combined and labelled, so the panel is never
  // stale when it comes back. Only the drawing is held: a covered tab that is
  // handed the same element keeps its whole chart subtree, so no ECharts option
  // is rebuilt and no canvas is touched until the tab is in front again, and
  // then exactly once from the newest snapshot.
  const charts = useVisibleRedraw(shown && cadResultMatchesViewport
    ? <><FemPortsPanel result={shown}/><ResultsChartGrid chartTypes={preferences.chartTypes} result={shown} named={named} tokens={tokens} live={primaryIsProvisional} beamShapeAction={beamShapeAction} radiationArtifact={radiationArtifactView} wrapper={shownRaw} job={selectedJob} channelId={shownActiveChannel} onExpand={setDetailPanel}/></>
    : null);
  // The view names the run it is going to, or else the one the dock resolved:
  // a selection that cannot show its model must not name a chart it is not.
  const detailRunId = detailSwitch ?? (resolved.arriving ? selection.primary : resolved.job?.id) ?? selection.primary ?? null;
  // Nothing will ever arrive: the run was deleted or failed and nothing replaces
  // it. A cleaned-up run and a failed fetch have words of their own to show.
  const detailRunGone = detailPanel !== null && !detailSwitch && !resolved.arriving && !resolved.canRender && !cleanedUpRun && !error;
  useEffect(() => { if (detailRunGone) setDetailPanel(null); }, [detailRunGone]);
  const showDetailRun = useCallback((job: JobItem) => {
    setDetailRunError(null);
    selectJob(job, (message) => {
      setDetailRunError(message);
      coordinator.reportError(message);
    }, (next, onError) => {
      setDetailSwitch(next.id);
      return showJobModel(next, onError).finally(() => setDetailSwitch((current) => current === next.id ? null : current));
    });
  }, [coordinator.reportError]);
  const detailRuns = useMemo(() => {
    if (detailPanel === null) return [];
    const listed = listedRuns(railRuns(jobs), runFilters, preferences.jobSort, preferences.minRating)
      .filter((job) => job.status === 'complete' && job.has_results);
    // A run the rail's filters hide can still be the one on screen.
    const shownRun = jobs.find((job) => job.id === detailRunId);
    return shownRun && !listed.includes(shownRun) ? [shownRun, ...listed] : listed;
  }, [detailPanel, detailRunId, jobs, preferences.jobSort, preferences.minRating, runFilters]);
  /** Every file is attempted; the ones that fail are named rather than
   * cancelling the ones that parsed. */
  const loadMeasured = useCallback(async (files: File[]) => {
    const store = useMeasuredOverlayStore.getState();
    const problems: string[] = [];
    for (const file of files) {
      try {
        const trace = parseMeasuredTrace(await file.text(), file.name);
        if (!store.add(trace)) {
          problems.push(`${trace.label} was not loaded: at most ${MAX_MEASURED_OVERLAYS} measurement overlays fit on the SPL chart. Remove one first.`);
          break;
        }
      } catch (reason) {
        problems.push(failureWords(reason));
      }
    }
    setMeasuredError(problems.length ? problems.join(' ') : null);
  }, []);

  // Every results notice, warning and error, and the solve progress line: one
  // row each in the one message area under the toolbar, never a box above
  // the charts (Magnus, 2026-11-03).
  const messages: ResultsMessage[] = [
    ...(error ? [{
      key: 'fetch-error', severity: 'error' as const, content: error,
      actions: <button type="button" className="btn" onClick={() => { setFetchError(null); setFetchAttempt((value) => value + 1); }}>Retry</button>,
    }] : []),
    ...(modelLoad === 'failed' ? [{ key: 'model-load', severity: 'error' as const, content: 'Could not load run model. Try Show this model again.' }]
      : modelLoad === 'loading' ? [{ key: 'model-load', severity: 'progress' as const, content: 'Loading run model…' }] : []),
    ...(measuredError ? [{ key: 'measured-error', severity: 'error' as const, content: measuredError }] : []),
    ...staleMeshes.map((job) => ({
      key: `stale-mesh:${job.id}`, severity: 'warning' as const,
      content: <>The mesh for {runDisplayName(job)} may differ from the one used to solve it. WG or the geometry has changed since. Solve again to update these results.</>,
    })),
    ...(showingPrevious ? [{ key: 'showing-previous', severity: 'progress' as const, content: 'Showing previous results while fetching the selected run…' }] : []),
    ...(resolved.modelNotLoaded ? [{ key: 'model-not-loaded', severity: 'info' as const, content: 'Run model not loaded' }] : []),
  ];

  const emptyModel = <div className="empty-state" role="status">
    {primaryJob && resolved.verdict !== 'current' && <button type="button" className="btn" onClick={showPrimaryModel}>Show that run's model</button>}
  </div>;

  return <div
    className="results-panel panel-scroll"
    data-result-primary={shown && cadResultMatchesViewport ? display?.primaryId : undefined}
    data-result-set={shown && cadResultMatchesViewport ? display?.key : undefined}
  >
    <ResultsHeader identity={<>
      {(selection.primary ? [selection.primary, ...selection.overlays] : ids).map((id, index) => {
        const job = jobs.find((item) => item.id === id);
        // The shown run is the only one whose coherence is actionable, and its
        // own chip is where it belongs: the banner this replaces spent the
        // widest slot in the toolbar naming the run the chip beside it named.
        const stale = index === 0 && Boolean(primaryJob) && primaryVerdict !== 'current';
        const marker = job && !stale ? runContextMarker(job, coherenceContext) : null;
        const chip = <button
          key={id}
          className={`result-chip ${index ? 'muted' : ''}${stale ? ' stale' : ''}`}
          onClick={() => compareSelection.remove(id)}
          title={stale
            ? `${labelFor(id, jobs)} — ${RUN_VERDICT_SENTENCE[primaryVerdict]} Click to remove it from the comparison.`
            : `${labelFor(id, jobs)} — remove from comparison`}
        ><i/><span>{middleEllipsis(labelFor(id, jobs))}</span>{marker && <span className="result-context-marker">{marker}</span>} ×</button>;
        if (!stale) return chip;
        return <span key={id} className="result-chip-group">
          {chip}
          <button
            ref={coherenceAnchor}
            type="button"
            className="result-context-marker stale"
            aria-expanded={coherenceOpen}
            aria-haspopup="dialog"
            title={`${RUN_VERDICT_SENTENCE[primaryVerdict]} Click for what to do about it.`}
            onClick={() => setCoherenceOpen((value) => !value)}
          ><i/>{RUN_VERDICT_MARKER[primaryVerdict]}</button>
        </span>;
      })}
      {powerFlags && cadResultMatchesViewport && <button type="button" className="pill result-power-check" data-status={powerFlags.status} aria-expanded={powerOpen} onClick={() => setPowerOpen((value) => !value)}>{powerChipLabel(powerFlags)}</button>}
      {primaryRaw && cadResultMatchesViewport && <ResultViewSwitch result={primaryRaw} view={view} importOrigin={selectedJob?.config_summary.import_origin} onSelect={(next) => resultViewStore.setView(next)}/>}
      {/* How much of the dock is actually comparing. Five of the six default
          charts describe one run by nature, so a comparison that silently
          applies to one card looked identical to one that applied to all six.
          Stated once here, because per-card it collides with the compact
          legend. */}
      {ids.length > 1 && (() => {
        const comparing = preferences.chartTypes.filter((chart) => COMPARABLE_CHARTS.has(chart)).length;
        return <span className="result-single-run" title={`${comparing} of ${preferences.chartTypes.length} charts overlay every selected run. The rest describe one run at a time and show ${labelFor(ids[0], jobs)}.`}>{comparing}/{preferences.chartTypes.length} compare</span>;
      })()}
      {shown && samplingNotes(shown).length > 0 && <span className="pill accent" role="note" title={samplingNotes(shown).join("\n")}>{samplingNotes(shown)[1]} · reconstructed estimates; narrow resonances can be missed</span>}
      {primaryIsProvisional && <span className="pill accent" role="status">Live · {liveCompleted}{liveExpected ? `/${liveExpected}` : ''} frequencies</span>}
      <select className="result-compare-add" aria-label="Add comparison result" value="" onChange={(event) => { if (event.target.value) compareSelection.toggleOverlay(event.target.value); }}><option value="">+ compare</option>{available.map((job) => {
        const marker = runContextMarker(job, coherenceContext);
        return <option key={job.id} value={job.id}>{labelFor(job.id, jobs)}{marker ? ` · ${marker}` : ''}</option>;
      })}</select>
      {newRun && <button
        type="button"
        className="result-new-run"
        title={`${runDisplayName(newRun)} finished. Click to show it; nothing else replaces the run on screen.`}
        onClick={() => { setDismissedNewRun(newRun.id); selectJob(newRun); }}
      ><i/>{newRunBadgeLabel(newRun.run_number)} → Show</button>}
      {/* Left of the spacer on purpose: this chip comes and goes on its own,
          and the controls on the right must not move under the cursor. */}
    </>} controls={<>
      <label className="result-count-control" title="Number of chart panels">Charts<select aria-label="Results panel count" value={RESULT_PANEL_COUNTS.includes(preferences.chartTypes.length as never) ? preferences.chartTypes.length : ''} onChange={(event) => preferencesStore.setChartCount(Number(event.target.value))}><option value="" disabled>{preferences.chartTypes.length}</option>{RESULT_PANEL_COUNTS.map((count) => <option key={count} value={count}>{count}</option>)}</select></label>
      <button className="toolbar-icon" disabled={preferences.chartTypes.length >= MAX_RESULT_PANELS} aria-label="Add chart" title="Add chart panel" onClick={() => preferencesStore.addChart()}><Icon name="plus"/></button>
      <input
        ref={measuredInput}
        hidden
        tabIndex={-1}
        multiple
        type="file"
        accept=".frd,.txt,.csv"
        aria-label="Measured response file"
        onChange={(event) => {
          const files = [...(event.target.files ?? [])];
          event.target.value = '';
          void loadMeasured(files);
        }}
      />
      <button className="btn"
        disabled={measuredOverlays.length >= MAX_MEASURED_OVERLAYS}
        title={measuredOverlays.length >= MAX_MEASURED_OVERLAYS
          ? `At most ${MAX_MEASURED_OVERLAYS} measurement overlays fit on the SPL chart. Remove one first.`
          : 'Draw a measured response (FRD, or a REW text export) on the on-axis SPL chart'}
        onClick={() => measuredInput.current?.click()}
      >Overlay measured…</button>
      {primaryJob ? <RunExportControl
        job={resolved.job ?? primaryJob}
        result={primaryRaw ?? primary}
        channelId={activeChannel ?? undefined}
        disabledReason={primaryIsProvisional ? 'Export is available when the solve finishes'
          : !cadResultMatchesViewport && primaryVerdict !== 'current' ? "Show this run's model to export its results"
            : !primary || !cadResultMatchesViewport ? 'Select a run with results to export' : undefined}
        onOpenExportSettings={() => setPreferencesOpen(true)}
      /> : <button className="btn" disabled title="Select a run to export">Export</button>}
      <button ref={preferencesAnchor} className={`panel-preferences-trigger${preferencesOpen ? ' on' : ''}`} aria-label="Results preferences" aria-expanded={preferencesOpen} title="Results & export preferences" onClick={() => setPreferencesOpen((value) => !value)}><Icon name="settings"/></button>
    </>}/>
    <CurrentModelStatus messages={messages} actions solveStatus activeOnly={!!(charts && resolved.job) || !!error || !!cleanedUpRun}/>
    {powerFlags && cadResultMatchesViewport && powerOpen && <div className="result-diagnostics">
      <div role="status" className="result-power-details">
        <b>{shownActiveChannel ?? 'Result'} · {powerHealth ? powerCheckMessage(powerHealth).label : powerChipLabel(powerFlags)}</b>
        {[...powerQualificationDetail(powerFlags, powerChannelName), channelQualificationSummary(shownRaw ?? undefined, powerChannelName)].filter((line): line is string => Boolean(line)).map((line) => <p key={line}>{line}</p>)}
        {powerHealth && <p>{powerCheckMessage(powerHealth).title}</p>}
      </div>
    </div>}
    {coherenceOpen && primaryJob && primaryVerdict !== 'current' && <AnchoredPanel
      anchorRef={coherenceAnchor}
      onClose={() => setCoherenceOpen(false)}
      className="result-coherence-popover"
      label={`${runDisplayName(primaryJob)} coherence`}
    >
      <p>{RUN_VERDICT_SENTENCE[primaryVerdict]}</p>
      {primaryVerdict === 'earlier-version' && modelForRun(primaryJob).kind === 'parametric'
        ? <button className="btn" type="button" onClick={() => { setCoherenceOpen(false); showPrimaryModel(); }}>Restore this run&apos;s design</button>
        : <><button className="btn" type="button" onClick={() => { setCoherenceOpen(false); showPrimaryModel(); }} disabled={modelLoad === 'loading'}>{modelLoad === 'loading' ? 'Loading…' : 'Show this model'}</button><button className="btn" type="button" onClick={() => { setCoherenceOpen(false); compareSelection.followLatest(latest?.id ?? null); }}>Show newest run</button></>}
      <button className="btn" type="button" disabled={!solve || solve.disabled} title={solve?.title ?? 'Solve is not available here.'} onClick={() => { setCoherenceOpen(false); void solve?.solve(); }}>Solve this version</button>
    </AnchoredPanel>}
    {measuredOverlays.length > 0 && <div className="results-toolbar result-measured">
      <span className="result-measured-caption">Measured</span>
      {measuredOverlays.map((overlay) => <MeasuredOverlayRow key={overlay.id} overlay={overlay}/>)}
    </div>}
    {preferencesOpen && <ResultsPreferencesSurface popover anchorRef={preferencesAnchor} onClose={() => setPreferencesOpen(false)}/>}
    {detailPanel !== null && detailPanel < preferences.chartTypes.length && <ChartDetailDialog
      index={detailPanel}
      chartTypes={preferences.chartTypes}
      tokens={tokens}
      data={shown && cadResultMatchesViewport && display?.primaryId === detailRunId ? { result: shown, named, live: primaryIsProvisional, beamShapeAction, radiationArtifact: radiationArtifactView, wrapper: shownRaw, job: selectedJob, channelId: shownActiveChannel } : null}
      unavailable={error ?? (cleanedUpRun ? cleanedUpMessage(cleanedUpRun) : null)}
      runs={detailRuns}
      runId={detailRunId}
      runLabel={(job) => {
        const marker = runContextMarker(job, coherenceContext);
        return `${runDisplayName(job)}${marker ? ` · ${marker}` : ''}`;
      }}
      runError={detailRunError}
      viewSwitch={primaryRaw && cadResultMatchesViewport && <ResultViewSwitch result={primaryRaw} view={view} importOrigin={selectedJob?.config_summary.import_origin} onSelect={(next) => resultViewStore.setView(next)}/>}
      onShowRun={showDetailRun}
      onShowPanel={setDetailPanel}
      onClose={closeDetail}
    />}
    {/* Three different empty docks, said apart: a failed fetch, a fetch still
        in flight, and nothing selected at all. The last used to claim it was
        loading, which is how a dock with no selection looked like a hang. */}
    {!resolved.job ? emptyModel : charts ?? (cleanedUpRun ? <EmptyState title="Results cleaned up" detail={cleanedUpMessage(cleanedUpRun)}/> : error ? <EmptyState title="Results unavailable" detail="Retry above, or select another run in the Jobs rail."/> : emptyModel)}
  </div>;
}
