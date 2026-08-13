import type { ImportedSolveSubmission } from './actions';
import {
  acknowledgedFindingWire,
  channelDriverWire,
  combineWire,
  unacknowledgedBlocking,
  useCadReturnStore,
} from '../stores/cadReturn';
import { parseFrequencyList, useSolveOptionsStore } from '../stores/solveOptions';

const POLAR_AXIS_ORDER = ['horizontal', 'vertical', 'diagonal'] as const;

/** The ingestion record's derivation may be widened but never narrowed, so the
 * submission starts FROM it: pinned axes (rejected mirror planes) force a full
 * -180..180 sweep and stay enabled, at the user's angular step. */
export function widenPolarToDerivation(
  options: ImportedSolveSubmission['options'],
  derivation: Record<string, unknown>,
): void {
  const axes = (derivation as { axes?: Record<string, { minimum_deg?: number; maximum_deg?: number }> })?.axes ?? {};
  const polar = options.polar_config as
    | { angle_range: [number, number, number]; enabled_axes: string[] }
    | undefined;
  if (!polar || !Object.keys(axes).length) return;
  const [start, end, count] = polar.angle_range;
  const step = count > 1 ? (end - start) / (count - 1) : 5;
  let widenedStart = start;
  let widenedEnd = end;
  const enabled = new Set(polar.enabled_axes);
  for (const [axis, spec] of Object.entries(axes)) {
    const minimum = typeof spec.minimum_deg === 'number' ? spec.minimum_deg : 0;
    const maximum = typeof spec.maximum_deg === 'number' ? spec.maximum_deg : 180;
    if (minimum <= -180 && maximum >= 180) enabled.add(axis);
    widenedStart = Math.min(widenedStart, minimum);
    widenedEnd = Math.max(widenedEnd, maximum);
  }
  if (widenedStart !== start || widenedEnd !== end) {
    const widenedCount = Math.max(count, Math.round((widenedEnd - widenedStart) / step) + 1);
    polar.angle_range = [widenedStart, widenedEnd, widenedCount];
  }
  polar.enabled_axes = POLAR_AXIS_ORDER.filter((axis) => enabled.has(axis));
}

export type CadReturnSnapshot = ReturnType<typeof useCadReturnStore.getState>;
export type SolveOptionsSnapshot = ReturnType<typeof useSolveOptionsStore.getState>;

/** One readiness rule shared by the CAD-panel button and the global Solve
 * command. Keeping it here prevents the two entry points from drifting back
 * into submitting different geometry or accepting different evidence. */
export function importedSubmissionBlocker(
  state: CadReturnSnapshot = useCadReturnStore.getState(),
  solveStore: SolveOptionsSnapshot = useSolveOptionsStore.getState(),
): string | null {
  const unacknowledged = unacknowledgedBlocking(state);
  const rangeInvalid = solveStore.frequencyMode === 'range' && (
    !(state.frequencyStartHz > 0)
    || state.frequencyEndHz <= state.frequencyStartHz
    || state.frequencyCount < 1
    || state.frequencyCount > 401
  );
  const listInvalid = solveStore.frequencyMode === 'list'
    && parseFrequencyList(solveStore.frequencyListText).frequencies === null;
  const femVolumes = state.ingestRecord?.evidence?.fem_air_volumes ?? [];
  const requiredFem = femVolumes.some((volume) => (
    !volume
    || typeof volume !== 'object'
    || (volume as Record<string, unknown>).required !== false
  ));
  if (!state.ingestRecord) return 'Ingest a CAD return before solving.';
  if (state.needsIngest) return state.ingestStaleReason ?? 'Sizing or source selection changed. Re-ingest before solving.';
  if (unacknowledged.length) {
    return `Acknowledge ${unacknowledged.length} blocking finding${unacknowledged.length === 1 ? '' : 's'} before solving.`;
  }
  if (requiredFem && !state.exteriorOnly) {
    return 'This return includes FEM air volumes. Explicitly choose an exterior-only Phase 2 solve.';
  }
  if (rangeInvalid || listInvalid) return 'Enter a valid explicit frequency sweep.';
  if (!state.driveChannels.length) return 'At least one drive channel is required.';
  return null;
}

export function buildImportedSubmission(
  state: CadReturnSnapshot = useCadReturnStore.getState(),
): ImportedSolveSubmission {
  const record = state.ingestRecord;
  if (!record) throw new Error('Ingest a CAD return before solving.');
  const solveStore = useSolveOptionsStore.getState();
  const options = solveStore.options() as ImportedSolveSubmission['options'];
  if (solveStore.frequencyMode === 'range') {
    options.frequency_range = [state.frequencyStartHz, state.frequencyEndHz];
    options.num_frequencies = state.frequencyCount;
  }
  options.engine = 'metal';
  options.symmetry = 'auto';
  widenPolarToDerivation(options, record.polar_grid_derivation);
  const combine = combineWire(state);
  return {
    geometry: {
      type: 'imported',
      ingest_id: record.ingest_id,
      manifest_sha256: record.manifest_sha256,
      artifact_sha256: record.artifact_sha256,
      drive_channels: state.driveChannels.map((channel) => {
        const driver = channelDriverWire(state.channelDrivers[channel.id]);
        return { ...channel, source_ids: [...channel.source_ids], ...(driver ? { driver } : {}) };
      }),
      ...(state.driveChannels.some((channel) => channelDriverWire(state.channelDrivers[channel.id]))
        ? { drive_voltage_v: state.driveVoltageV }
        : {}),
      mesh: {
        rigid_size_mm: state.rigidSizeMm,
        transition_mm: state.transitionMm,
        source_size_mm: Object.fromEntries(
          Object.entries(state.sourceSizesMm).filter(([id]) => !state.skippedSourceIds.includes(id)),
        ),
      },
      acknowledged_findings: acknowledgedFindingWire(record, state.acknowledgedFindingIds),
      skipped_source_ids: [...state.skippedSourceIds],
      exterior_only: state.exteriorOnly,
      ...(combine ? { combine } : {}),
    },
    options,
  };
}
