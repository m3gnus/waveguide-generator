import type { ImportedSolveSubmission } from './actions';
import {
  blockingFindingWire,
  channelAcceptsDriver,
  channelDriverPresent,
  channelDriverWire,
  combineEnabledEffective,
  combineSpecEffective,
  combineWire,
  hasPassiveCardioidSurface,
  incompleteDriverChannels,
  passiveCardioidBlocker,
  passiveCardioidWire,
  useCadReturnStore,
} from '../stores/cadReturn';
import type { CadDriveChannel } from '../stores/cadReturn';
import { parseFrequencyList, polarValidationError, useSolveOptionsStore } from '../stores/solveOptions';
import { frequencyText } from '../results/crossoverSpec';

const POLAR_AXIS_ORDER = ['horizontal', 'vertical', 'diagonal'] as const;
/** The only diagonal inclination Phase 2 imported solves accept. */
const DEFAULT_DIAGONAL_INCLINATION_DEG = 45;
const MANUAL_SOLVE_OPERATION_PREFIX = 'wg2.cad.manual-solve.v1:';

export interface ManualSolveIdentity {
  operationId: string;
  prepareAcknowledged: boolean;
  completionAcknowledged: boolean;
  designName: string;
  label: string;
}

function readManualSolveIdentity(
  ingestId: string,
  storage: Pick<Storage, 'getItem'> | null,
): ManualSolveIdentity | null {
  const held = storage?.getItem(`${MANUAL_SOLVE_OPERATION_PREFIX}${ingestId}`);
  if (!held) return null;
  try {
    const parsed = JSON.parse(held) as Partial<ManualSolveIdentity>;
    if (typeof parsed.operationId === 'string' && parsed.operationId) {
      return {
        operationId: parsed.operationId,
        prepareAcknowledged: parsed.prepareAcknowledged === true,
        completionAcknowledged: parsed.completionAcknowledged === true,
        designName: typeof parsed.designName === 'string' ? parsed.designName : '',
        label: typeof parsed.label === 'string' ? parsed.label : '',
      };
    }
  } catch { /* Legacy plain ids are unfinished and therefore unacknowledged. */ }
  return {
    operationId: held, prepareAcknowledged: false, completionAcknowledged: false,
    designName: '', label: '',
  };
}

/**
 * The ingestion a manual solve operation was created for.
 *
 * Its completion must be recognised against that ingestion, not the one on
 * screen when the job finishes: a newer CAD snapshot prepared while the solve
 * runs is a different model, and the run still belongs to the one submitted.
 */
export function manualCadSolveIngestFor(
  operationId: string,
  storage: Pick<Storage, 'getItem' | 'key' | 'length'> | null = typeof sessionStorage === 'undefined' ? null : sessionStorage,
): string | null {
  if (!storage) return null;
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (!key?.startsWith(MANUAL_SOLVE_OPERATION_PREFIX)) continue;
    const ingestId = key.slice(MANUAL_SOLVE_OPERATION_PREFIX.length);
    if (readManualSolveIdentity(ingestId, storage)?.operationId === operationId) return ingestId;
  }
  return null;
}

/** Whether an operation id is a solve WG created itself, rather than a waiting
 * request a WG Solve continued. */
export function isManualCadSolveOperationId(operationId: string): boolean {
  return operationId.startsWith('manual-solve:');
}

/**
 * The identity a WG Solve of this ingestion uses, held across retries and
 * reloads. ``createRun`` names a new run; when it also names an
 * ``operationId`` -- a waiting request for this snapshot -- a new identity
 * continues that operation instead of creating one.
 */
export function manualCadSolveIdentity(
  ingestId: string,
  createRun: () => { designName: string; label: string; operationId?: string },
  storage: Pick<Storage, 'getItem' | 'setItem'> | null = typeof sessionStorage === 'undefined' ? null : sessionStorage,
): ManualSolveIdentity {
  const key = `${MANUAL_SOLVE_OPERATION_PREFIX}${ingestId}`;
  const held = readManualSolveIdentity(ingestId, storage);
  if (held?.designName && held.label) return held;
  const random = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  const { operationId: continued, ...run } = createRun();
  const created = {
    operationId: held?.operationId ?? continued ?? `manual-solve:${random}`,
    prepareAcknowledged: held?.prepareAcknowledged ?? false,
    completionAcknowledged: held?.completionAcknowledged ?? false,
    ...run,
  };
  storage?.setItem(key, JSON.stringify(created));
  return created;
}

/**
 * Keep one manual-solve identity per retained ingest across retries and reloads.
 *
 * The backend makes this idempotent: if a response is lost at any of the three
 * route boundaries, the next click names the same operation instead of making
 * another job. Session storage deliberately scopes the identity to this WG
 * window; selecting another ingest gets another identity.
 */
export function manualCadSolveOperationId(
  ingestId: string,
  storage: Pick<Storage, 'getItem' | 'setItem'> | null = typeof sessionStorage === 'undefined' ? null : sessionStorage,
): string {
  const key = `${MANUAL_SOLVE_OPERATION_PREFIX}${ingestId}`;
  const held = readManualSolveIdentity(ingestId, storage);
  if (held) return held.operationId;
  const random = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  const operationId = `manual-solve:${random}`;
  storage?.setItem(key, JSON.stringify({
    operationId, prepareAcknowledged: false, completionAcknowledged: false,
    designName: '', label: '',
  }));
  return operationId;
}

/** The client received the prepare response, so a later terminal row is old work. */
export function acknowledgeManualCadSolvePreparation(
  ingestId: string,
  operationId: string,
  storage: Pick<Storage, 'getItem' | 'setItem'> | null = typeof sessionStorage === 'undefined' ? null : sessionStorage,
): void {
  const held = readManualSolveIdentity(ingestId, storage);
  if (held?.operationId === operationId) {
    storage?.setItem(
      `${MANUAL_SOLVE_OPERATION_PREFIX}${ingestId}`,
      JSON.stringify({ ...held, prepareAcknowledged: true }),
    );
  }
}

/** Mark completion before applying its one-shot run-list side effects. */
export function acknowledgeManualCadSolveCompletion(
  ingestId: string,
  operationId: string,
  storage: Pick<Storage, 'getItem' | 'setItem'> | null = typeof sessionStorage === 'undefined' ? null : sessionStorage,
): ManualSolveIdentity | null {
  const held = readManualSolveIdentity(ingestId, storage);
  if (!held || held.operationId !== operationId || held.completionAcknowledged) return null;
  storage?.setItem(
    `${MANUAL_SOLVE_OPERATION_PREFIX}${ingestId}`,
    JSON.stringify({ ...held, completionAcknowledged: true }),
  );
  return held;
}

export function manualCadSolvePreparationAcknowledged(
  ingestId: string,
  operationId: string,
  storage: Pick<Storage, 'getItem'> | null = typeof sessionStorage === 'undefined' ? null : sessionStorage,
): boolean {
  const held = readManualSolveIdentity(ingestId, storage);
  return held?.operationId === operationId && held.prepareAcknowledged;
}

/** Forget an operation only after the backend says it is terminal. */
export function forgetManualCadSolveOperationId(
  ingestId: string,
  operationId: string,
  storage: Pick<Storage, 'getItem' | 'removeItem'> | null = typeof sessionStorage === 'undefined' ? null : sessionStorage,
): void {
  const key = `${MANUAL_SOLVE_OPERATION_PREFIX}${ingestId}`;
  if (readManualSolveIdentity(ingestId, storage)?.operationId === operationId) storage?.removeItem(key);
}

/** The ingestion record's derivation may be widened but never narrowed, so the
 * submission starts FROM it: pinned axes (rejected mirror planes) force a full
 * -180..180 sweep and stay enabled, at the user's angular step. */
export function widenPolarToDerivation(
  options: ImportedSolveSubmission['options'],
  derivation: Record<string, unknown>,
): void {
  const axes = (derivation as { axes?: Record<string, { minimum_deg?: number; maximum_deg?: number }> })?.axes ?? {};
  const polar = options.polar_config as
    | { angle_range: [number, number, number]; enabled_axes: string[]; inclination?: number }
    | undefined;
  if (!polar || !Object.keys(axes).length) return;
  const [start, end, count] = polar.angle_range;
  const step = count > 1 ? (end - start) / (count - 1) : 5;
  let widenedStart = start;
  let widenedEnd = end;
  const requested = new Set(polar.enabled_axes);
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
  // A diagonal the caller never enabled carries no inclination intent: the form
  // disables that field while the plane is off, so whatever angle is stored
  // there is one the user can neither see nor change. Submitting it refuses the
  // whole solve (imported_diagonal_inclination_unsupported) over a plane they
  // did not choose, so a forced diagonal takes the supported default instead.
  if (enabled.has('diagonal') && !requested.has('diagonal')) polar.inclination = DEFAULT_DIAGONAL_INCLINATION_DEG;
}

export type CadReturnSnapshot = ReturnType<typeof useCadReturnStore.getState>;
export type SolveOptionsSnapshot = ReturnType<typeof useSolveOptionsStore.getState>;

/**
 * Whether this return can carry a cardioid campaign at all.
 *
 * The form persists in the solve profile, and a profile is reused across
 * models. So an enabled campaign can outlive the geometry it was configured
 * for: the rail hides the section when the port aperture is gone, and this is
 * the matching half -- the form contributes neither a refusal the user cannot
 * act on nor, worse, wire keys for an aperture the mesh does not contain.
 */
function cardioidSurfacePresent(state: CadReturnSnapshot): boolean {
  return hasPassiveCardioidSurface(state.selectedBundle?.sources ?? []);
}

/** One readiness rule shared by the CAD-panel button and the global Solve
 * command. Keeping it here prevents the two entry points from drifting back
 * into submitting different geometry or accepting different evidence. */
export function importedSubmissionBlocker(
  state: CadReturnSnapshot = useCadReturnStore.getState(),
  solveStore: SolveOptionsSnapshot = useSolveOptionsStore.getState(),
): string | null {
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
  const directivityError = polarValidationError(solveStore.polar);
  if (directivityError) return directivityError;
  if (!state.ingestRecord) return 'Ingest a CAD return before solving.';
  if (state.needsIngest) return state.ingestStaleReason ?? 'Sizing or source selection changed. Re-ingest before solving.';
  if (requiredFem && !state.exteriorOnly) {
    return 'This return includes FEM air volumes. Explicitly choose an exterior-only Phase 2 solve.';
  }
  if (rangeInvalid || listInvalid) return 'Enter a valid explicit frequency sweep.';
  if (!state.driveChannels.length) return 'At least one drive channel is required.';
  // Match SolveRequest.validate_combine_band for the exact spec sent on the
  // wire, including independently edited HP and LP corners.
  const combine = combineWire(state);
  if (combine) {
    const frequencies = solveStore.frequencyMode === 'list'
      ? parseFrequencyList(solveStore.frequencyListText).frequencies
      : null;
    const start = frequencies?.[0] ?? state.frequencyStartHz;
    const end = frequencies?.[frequencies.length - 1] ?? state.frequencyEndHz;
    for (const channel of Object.values(combine.channels)) {
      for (const corner of [channel.hp?.fc_hz, channel.lp?.fc_hz]) {
        if (corner === undefined || corner === null) continue;
        if (corner < start) {
          return `The ${frequencyText(corner)} crossover is below the ${frequencyText(start)} sweep start. `
            + (solveStore.frequencyMode === 'list'
              ? `Add ${frequencyText(corner)} or a lower frequency at the start of the frequency list, or raise the crossover.`
              : `In Frequency Sweep, set Sweep start to ${frequencyText(corner)} or lower, or raise the crossover.`);
        }
        if (corner > end) {
          return `The ${frequencyText(corner)} crossover is above the ${frequencyText(end)} sweep end. `
            + (solveStore.frequencyMode === 'list'
              ? `Add ${frequencyText(corner)} or a higher frequency at the end of the frequency list, or lower the crossover.`
              : `In Frequency Sweep, set Sweep end to ${frequencyText(corner)} or higher, or lower the crossover.`);
        }
      }
    }
  }
  // Same rule as the cardioid form below, for the same reason: a driver the
  // user asked for and did not finish is refused here rather than dropped on
  // the way to the wire. Dropping it solved the channel unit-driven under a
  // rail that named a driver, and the run's missing power, current and
  // excursion were the first sign of it.
  const incomplete = incompleteDriverChannels(state);
  if (incomplete.length) {
    return `${incomplete.map(({ channelId, missing }) => `${channelId} still needs ${missing}`).join('; ')}. `
      + 'Complete the driver, or clear it to solve that channel unit-driven.';
  }
  // A half-filled cardioid form is refused here rather than dropped silently.
  // Dropping it would submit the pre-campaign solve under a rail that says a
  // campaign is configured, which is the one failure mode this feature cannot
  // afford: the curve would look plausible and be the wrong physics.
  // Only while this model actually has the port aperture: a stale enabled form
  // on a model without one is dropped, not turned into a blocker for a section
  // the rail no longer shows.
  if (cardioidSurfacePresent(state)) {
    const cardioid = passiveCardioidBlocker(state);
    if (cardioid) return cardioid;
  }
  return null;
}

/**
 * Channels this solve will run without a driver model.
 *
 * A channel with no driver is solved at unit acceleration -- no voltage, no
 * ohms, no cone -- which is what WG did for every channel before drivers
 * existed and is a perfectly good basis. It is only worth saying because the
 * consequences are invisible until afterwards: that channel has no power,
 * current or excursion to show, and in a combined output its level was matched
 * rather than derived from a driver.
 *
 * A channel whose driver is present but incomplete is deliberately NOT here:
 * it is refused by `importedSubmissionBlocker`, not solved unit-driven, and
 * listing it both ways put "drive-hf solves unit-driven" on screen right
 * beside the blocker refusing drive-hf.
 */
export function undrivenChannels(
  state: Pick<CadReturnSnapshot, 'driveChannels' | 'channelDrivers'>,
): string[] {
  return state.driveChannels
    .filter((channel) => !(channelAcceptsDriver(channel) && channelDriverPresent(state.channelDrivers[channel.id])))
    .map((channel) => channel.id);
}

function list(items: string[]): string {
  return items.length > 1
    ? `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`
    : items[0] ?? '';
}

/**
 * What this submission will do that the user cannot see from the rail.
 *
 * Advisory, not refusals: everything here is a solve worth running, said out
 * loud before it runs rather than discovered in a chart that has nothing to
 * draw. `importedSubmissionBlocker` remains the only thing that stops a solve.
 */
export function importedSubmissionNotices(
  state: CadReturnSnapshot = useCadReturnStore.getState(),
): string[] {
  const undriven = undrivenChannels(state);
  // Counted from the submittable wire, not as "everything else": a channel
  // whose present driver is still incomplete is in neither list — it is
  // refused, and a refusal must not tip these notices into existing.
  const driven = state.driveChannels.filter((channel) =>
    channelAcceptsDriver(channel) && channelDriverWire(state.channelDrivers[channel.id]) !== undefined).length;
  const combining = combineEnabledEffective(state);
  const members = new Set(combineSpecEffective(state)?.members ?? []);
  // Only the mixture is worth a word. A solve with no drivers at all is the
  // ordinary unit-acceleration solve this product did for years and still
  // does; saying "no power or current" about every channel of it would be
  // noise on the majority of runs, and noise is what gets a notice ignored on
  // the run where it matters.
  if (!undriven.length || !driven) return [];
  const notices: string[] = [];
  notices.push(
    `${list(undriven)} ${undriven.length === 1 ? 'solves' : 'solve'} unit-driven: `
    + `no driver model, so ${undriven.length === 1 ? 'it has' : 'they have'} no power, current or excursion to report.`,
  );
  // The mixed case is the one worth spelling out. A driven channel's field is
  // scaled to the cone acceleration its voltage produces; an undriven one is
  // left at unit acceleration. The two are not in the same units, and it is
  // level matching -- not physics -- that puts them on one chart.
  if (combining) {
    const mixedMembers = undriven.filter((id) => members.has(id));
    if (mixedMembers.length) {
      notices.push(
        `The combined output mixes driver-coupled and unit-driven channels. `
        + `${list(mixedMembers)} ${mixedMembers.length === 1 ? 'is' : 'are'} level-matched into the sum, `
        + 'so the combined shape is meaningful but its absolute SPL is not the voltage-referenced level '
        + 'the driven channels report on their own.',
      );
    }
  }
  return notices;
}

/**
 * The driver spec to submit for a channel, or undefined for none.
 *
 * Eligibility is re-checked here rather than trusted from the form. The store
 * drops forms their channel can no longer carry, but this builder is the last
 * thing between the panel and a submission the server would reject outright
 * (`DriveChannel.validate_driver_applicability`), and a driver attached to the
 * wrong kind of channel is exactly the mistake worth refusing twice.
 */
function submittedDriver(
  state: Pick<CadReturnSnapshot, 'channelDrivers'>,
  channel: CadDriveChannel,
): Record<string, number | string> | undefined {
  return channelAcceptsDriver(channel) ? channelDriverWire(state.channelDrivers[channel.id]) : undefined;
}

export function buildImportedSubmission(
  state: CadReturnSnapshot = useCadReturnStore.getState(),
): ImportedSolveSubmission {
  const record = state.ingestRecord;
  if (!record) throw new Error('Ingest a CAD return before solving.');
  // The readiness gate already refuses this, but the builder is the last thing
  // between the rail and the wire and an enabled-yet-incomplete form must never
  // become a silently pre-campaign submission.
  const cardioidPresent = cardioidSurfacePresent(state);
  if (cardioidPresent) {
    const cardioidBlocker = passiveCardioidBlocker(state);
    if (cardioidBlocker) throw new Error(cardioidBlocker);
  }
  const passiveCardioid = cardioidPresent ? passiveCardioidWire(state.passiveCardioid) : null;
  const solveStore = useSolveOptionsStore.getState();
  const options = solveStore.options() as ImportedSolveSubmission['options'];
  if (solveStore.frequencyMode === 'range') {
    options.frequency_range = [state.frequencyStartHz, state.frequencyEndHz];
    options.num_frequencies = state.frequencyCount;
  }
  // The engine stays the user's choice, as for a parametric design. Imported
  // geometry solves in full 3-D only, whatever the parametric solver path is,
  // and the domain is the one the ingestion record describes.
  options.solver_mode = 'full_3d';
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
        const driver = submittedDriver(state, channel);
        return { ...channel, source_ids: [...channel.source_ids], ...(driver ? { driver } : {}) };
      }),
      ...(state.driveChannels.some((channel) => submittedDriver(state, channel))
        ? {
          drive_voltage_v: state.driveVoltageV,
          // Only ever a ceiling for the maximum-output pass, so it rides
          // along with the voltage it is a ceiling on and is omitted when
          // there is no amplifier to state.
          ...(state.maxDriveVoltageV ? { max_drive_voltage_v: state.maxDriveVoltageV } : {}),
        }
        : {}),
      mesh: {
        rigid_size_mm: state.rigidSizeMm,
        transition_mm: state.transitionMm,
        source_size_mm: Object.fromEntries(
          Object.entries(state.sourceSizesMm).filter(([id]) => !state.skippedSourceIds.includes(id)),
        ),
      },
      acknowledged_findings: blockingFindingWire(record),
      skipped_source_ids: [...state.skippedSourceIds],
      exterior_only: state.exteriorOnly,
      ...(combine ? { combine } : {}),
      ...(passiveCardioid ?? {}),
    },
    options,
  };
}
